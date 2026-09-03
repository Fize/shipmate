"""Shared protocol step model for dev-workflow.

Single source of truth for the execution checklist (the concrete phases an
orchestrator walks through), used by:

  - validate_workflow.py  (check-coverage: verifies no step was skipped)
  - index_workflow.py     (summarize: derives per-task step progress)
  - serve_workflow.py     (live dashboard: step timeline)

The step list is finer-grained than the 5-state machine (active / reviewing /
needs_fix / done / escalated): states are *what* phase a work item is in, steps
are *which protocol gate* the agent has reached. Every event may carry an
optional ``step`` field naming the step it records; the coverage check then
proves the mandatory gates were actually hit.

Steps are ordered by the canonical protocol flow. ``fix`` / ``rejudge`` are
conditional (only when the judge returns passed=false), so they are not part of
the base flow but are spliced in when present.
"""

# Canonical steps, in protocol order.
STEPS = [
    "investigate",      # 调查仓库 / 理解上下文
    "script-record",    # 记录独立脚本运行
    "status-record",    # 记录无行为变更
    "implement",        # 实现变更（改代码）
    "evaluate",         # 完成评估（completion-evaluator，gate）
    "review",           # 并行审查扇出（self + independent）
    "judge",            # 裁决（review-judge Mode A）
    "fix",              # 修复（fixer，仅 needs_fix 时）
    "rejudge",          # 复裁（review-judge Mode B，仅修复后）
    "final-evaluate",   # 最终完成评估（gate）
    "validate",         # 验证 ledger（check-ledger，gate）
]

STEP_LABELS = {
    "investigate": "调查仓库",
    "script-record": "记录脚本运行",
    "status-record": "记录状态",
    "implement": "实现变更",
    "evaluate": "完成评估",
    "review": "并行审查",
    "judge": "裁决",
    "fix": "修复",
    "rejudge": "复裁",
    "final-evaluate": "最终评估",
    "validate": "验证账本",
}

# Base (non-conditional) flows. `fix`/`rejudge` are spliced after `judge` when
# the ledger actually went through a needs_fix round.
FLOW_CODE = ["investigate", "implement", "evaluate", "review", "judge",
             "final-evaluate", "validate"]
FLOW_NO_CODE = ["investigate", "evaluate", "validate"]
FLOW_STATUS_ONLY = ["investigate", "status-record", "validate"]
FLOW_STANDALONE_SCRIPT = ["investigate", "script-record", "validate"]

# Mandatory steps for each flow. These are what check-coverage enforces.
REQUIRED_CODE = FLOW_CODE
REQUIRED_NO_CODE = FLOW_NO_CODE
REQUIRED_STATUS_ONLY = FLOW_STATUS_ONLY
REQUIRED_STANDALONE_SCRIPT = FLOW_STANDALONE_SCRIPT


def has_code_change(events):
    """True when any event declares the work produced code changes."""
    for ev in events:
        if ev.get("has_code_change") is True:
            return True
    return False


def is_status_only(events, work_item=None):
    """True when the ledger explicitly records a no-behavior file change."""
    if isinstance(work_item, dict) and work_item.get("change_class") == "status-only":
        return True
    return any(isinstance(ev, dict) and ev.get("change_class") == "status-only"
               for ev in events)


def is_standalone_script(events, work_item=None):
    """True when the ledger explicitly records a standalone test/debug script."""
    if isinstance(work_item, dict) and work_item.get("change_class") == "standalone-script":
        return True
    return any(isinstance(ev, dict) and ev.get("change_class") == "standalone-script"
               for ev in events)


def _infer_steps(ev):
    """Best-effort step inference for legacy ledgers without a ``step`` field.

    Only state transitions that unambiguously imply a step are inferred; gate
    steps (evaluate / judge / final-evaluate / validate) are NOT inferred because
    they cannot be recovered from states alone.
    """
    frm = ev.get("from")
    to = ev.get("to")
    steps = []
    if frm is None and to == "active":
        steps.append("investigate")
    if frm == "active" and to == "reviewing":
        steps.append("review")
    if to == "needs_fix":
        steps.append("judge")
    if frm == "needs_fix" and to == "reviewing":
        steps.append("fix")
    if to == "done" and frm == "reviewing":
        steps.append("judge")
    if to == "done":
        steps.append("validate")
    if frm == "active" and to == "active":
        steps.append("implement")
    return steps


def event_steps(ev):
    """Return the protocol steps an event records, normalized to a list.

    Accepts a single step string, a list of steps, or (when absent) an inference
    from the state transition so legacy ledgers still show a best-effort step
    timeline.
    """
    s = ev.get("step")
    if s is None:
        return _infer_steps(ev)
    if isinstance(s, str):
        return [s] if s in STEPS else []
    if isinstance(s, list):
        return [x for x in s if x in STEPS]
    return []


def event_step(ev):
    """First recorded step (representative value for display)."""
    steps = event_steps(ev)
    return steps[0] if steps else None


def flow_for(events, work_item=None):
    """Return the ordered step list applicable to this work item's flow."""
    if is_standalone_script(events, work_item):
        return list(FLOW_STANDALONE_SCRIPT)
    if is_status_only(events, work_item):
        return list(FLOW_STATUS_ONLY)
    return list(FLOW_CODE if has_code_change(events) else FLOW_NO_CODE)


def ordered_steps(events, work_item=None):
    """Applicable flow, with `fix`/`rejudge` spliced in when the ledger actually
    went through a needs_fix round."""
    base = flow_for(events, work_item)
    saw_fix = any("fix" in event_steps(ev) for ev in events)
    saw_rejudge = any("rejudge" in event_steps(ev) for ev in events)
    out = []
    for s in base:
        out.append(s)
        if s == "judge" and saw_fix:
            out.append("fix")
        if s == "judge" and saw_rejudge:
            out.append("rejudge")
    return out


def derive_step_progress(events, work_item=None):
    """Derive per-step progress for display and coverage.

    Returns a dict:
      {
        "current_step": <step id | null when terminal>,
        "completed": [<step id>, ...],
        "steps": [ {"id", "label", "status": done|current|pending|skipped}, ... ],
      }
    """
    events = events or []
    # Ordered distinct steps actually recorded.
    seen = []
    for ev in events:
        for s in event_steps(ev):
            if s and (not seen or seen[-1] != s):
                seen.append(s)

    flow = ordered_steps(events, work_item)
    terminal = bool(events) and events[-1].get("to") in ("done", "escalated")
    current = None if terminal else (seen[-1] if seen else None)

    steps = []
    completed = []
    for s in flow:
        status = "pending"
        if s in seen:
            status = "done"
            completed.append(s)
        steps.append({"id": s, "label": STEP_LABELS.get(s, s), "status": status})
    if current is not None:
        # Mark the last recorded step as current rather than done (in-flight).
        for st in steps:
            if st["id"] == current:
                st["status"] = "current"
                if current in completed:
                    completed.remove(current)
                break

    return {
        "current_step": current,
        "completed": completed,
        "steps": steps,
    }
