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
    "verify",           # 按验收要求验证当前变更
    "diff-check",       # 主代理检查范围、diff 和验证充分性
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
    "verify": "验证变更",
    "diff-check": "检查变更",
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

# Schema v2 owns its gates independently of the legacy tier vocabulary. These
# definitions are consumed by validation, coverage and dashboard progress alike.
PROFILE_FLOWS = {
    "light": ["investigate", "implement", "verify", "diff-check", "validate"],
    "standard": ["investigate", "implement", "verify", "diff-check", "review", "validate"],
    "strict": ["investigate", "implement", "verify", "diff-check", "evaluate", "review",
               "judge", "final-evaluate", "validate"],
}
PROFILE_RISK_CHECKS = frozenset({
    "security", "funds", "sensitive_data", "migration", "concurrency", "transactions",
    "retry_behavior", "public_contract", "runtime_dependencies", "production_behavior",
    "external_side_effects", "irreversible", "large_blast_radius", "uncertainty",
})
LIGHT_ELIGIBILITY = frozenset({"clear_acceptance", "bounded_impact", "direct_verification", "reversible"})
PROFILE_EVIDENCE_ACTORS = {
    "light": {step: ("main",) for step in PROFILE_FLOWS["light"]},
    "standard": {step: (("indep:general",) if step == "review" else ("main",))
                 for step in PROFILE_FLOWS["standard"] + ["fix"]},
    "strict": {
        "investigate": ("main",), "implement": ("implementer",),
        "verify": ("implementer", "fixer"), "diff-check": ("main",),
        "evaluate": ("completion-evaluator",), "review": ("main",),
        "judge": ("review-judge",), "fix": ("fixer",), "rejudge": ("review-judge",),
        "final-evaluate": ("completion-evaluator",), "validate": ("main",),
    },
}
PROFILE_TRANSITIONS = {
    "active->active", "active->reviewing", "active->done", "active->escalated",
    "reviewing->needs_fix", "reviewing->done", "reviewing->escalated",
    "needs_fix->reviewing", "done->active", "reviewing->active", "escalated->active",
}


def nonempty_strings(value):
    return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value)


def v2_event_steps(ev):
    """Read explicit v2 metadata, never infer executed gates from state edges."""
    if not isinstance(ev, dict):
        raise ValueError("v2 event must be an object")
    if not isinstance(ev.get("execution_profile"), str) or ev["execution_profile"] not in PROFILE_FLOWS:
        raise ValueError("v2 event requires a known execution_profile")
    if type(ev.get("change_revision")) is not int or ev["change_revision"] < 0:
        raise ValueError("v2 event requires a non-negative integer change_revision")
    raw = ev.get("step")
    named = [] if raw is None else raw if isinstance(raw, list) else [raw]
    if (any(not isinstance(s, str) or s not in STEPS for s in named)
            or len(named) != len(set(named))):
        raise ValueError("v2 step must explicitly name distinct known steps")
    snapshots = ev.get("evidence", {})
    if not isinstance(snapshots, dict) or set(snapshots) != set(named):
        raise ValueError("v2 evidence must match every explicit step")
    if not named:
        control = (ev.get("to") == "escalated"
                   or (ev.get("to") == "active" and ev.get("from") in ("reviewing", "done", "escalated"))
                   or (ev.get("from") == ev.get("to") == "active"
                       and ev.get("reason") in ("profile-upgrade", "retry", "answer", "fix")))
        if not control:
            raise ValueError("v2 gate event requires explicit step and evidence metadata")
    for step in named:
        snapshot = snapshots[step]
        actors = (("main",) if step in ("script-record", "status-record") else
                  PROFILE_EVIDENCE_ACTORS.get(ev.get("execution_profile"), {}).get(step, ()))
        if (not isinstance(snapshot, dict) or type(snapshot.get("revision")) is not int
                or snapshot["revision"] != ev.get("change_revision")
                or snapshot.get("outcome") not in ("pass", "fail")
                or not nonempty_strings(snapshot.get("references"))
                or snapshot.get("actor") not in actors):
            raise ValueError("v2 step %s requires explicit current evidence metadata" % step)
    return named


def profile_decision_errors(profile, decision):
    """Fail closed: every fixed risk check includes a boolean and source evidence."""
    errors = []
    if not isinstance(profile, str) or profile not in PROFILE_FLOWS:
        return ["execution_profile must be light, standard or strict"]
    if not isinstance(decision, dict):
        return ["profile_decision must be an object"]
    if not isinstance(decision.get("rationale"), str) or not decision["rationale"].strip():
        errors.append("profile_decision.rationale must be a non-empty string")
    if not nonempty_strings(decision.get("evidence")):
        errors.append("profile_decision.evidence must contain source references")
    checks = decision.get("risk_checks")
    if not isinstance(checks, dict) or set(checks) != PROFILE_RISK_CHECKS:
        errors.append("profile_decision.risk_checks must contain exactly: " + ", ".join(sorted(PROFILE_RISK_CHECKS)))
    if isinstance(checks, dict):
        for name, check in checks.items():
            if (not isinstance(check, dict) or set(check) != {"present", "evidence"}
                    or not isinstance(check.get("present"), bool)
                    or not nonempty_strings(check.get("evidence"))):
                errors.append("risk check %s requires boolean present and non-empty evidence" % name)
            elif check["present"] and profile != "strict":
                errors.append("risk check %s=true requires strict" % name)
    if profile == "light":
        eligible = decision.get("light_eligibility")
        if (not isinstance(eligible, dict) or set(eligible) != LIGHT_ELIGIBILITY
                or any(value is not True for value in eligible.values())):
            errors.append("light requires all light_eligibility checks true: " + ", ".join(sorted(LIGHT_ELIGIBILITY)))
    return errors


def resolve_execution_path(events, work_item=None, schema_version=1):
    """Return the complete path contract; v1 never infers a profile from fields."""
    events = events or []
    work_item = work_item or {}
    if schema_version == 1 and type(schema_version) is int:
        flow = (FLOW_STANDALONE_SCRIPT if is_standalone_script(events, work_item) else
                FLOW_STATUS_ONLY if is_status_only(events, work_item) else
                FLOW_CODE if has_code_change(events) else FLOW_NO_CODE)
        return {"profile": None, "required_steps": list(flow), "required_evidence": [],
                "allowed_transitions": set(PROFILE_TRANSITIONS), "review_requirements": None,
                "required_subskills": [], "evidence_actors": {}}
    if schema_version != 2 or type(schema_version) is not int:
        raise ValueError("schema_version must be integer 1 or 2")
    if "schema_version" in work_item:
        raise ValueError("schema_version belongs only at the ledger root")
    if not isinstance(work_item.get("routing", {}), dict):
        raise ValueError("work_item.routing must be an object")
    if type(work_item.get("change_revision")) is not int or work_item["change_revision"] < 0:
        raise ValueError("work_item.change_revision must be a non-negative integer")
    profile = work_item.get("execution_profile")
    errors = profile_decision_errors(profile, work_item.get("profile_decision"))
    if errors:
        raise ValueError("; ".join(errors))
    special = is_status_only(events, work_item) or is_standalone_script(events, work_item)
    flow = list(FLOW_STANDALONE_SCRIPT if is_standalone_script(events, work_item) else
                FLOW_STATUS_ONLY if is_status_only(events, work_item) else PROFILE_FLOWS[profile])
    if not special and not has_code_change(events) and "implement" in flow:
        flow.remove("implement")
    transitions = set(PROFILE_TRANSITIONS) | {"needs_fix->escalated"}
    roster = None
    if special or profile == "light":
        transitions -= {edge for edge in transitions if "reviewing" in edge or "needs_fix" in edge}
    else:
        transitions.discard("active->done")
        roster = (["indep:general"] if profile == "standard" else
                  ["self", "indep:security", "indep:correctness"])
    skills = (["scenario-standards", "coding-tactics", "qa-suite"]
              if profile == "strict" and has_code_change(events) else [])
    if work_item.get("routing", {}).get("architecture_required") is True:
        skills.append("architecture-principles")
    actors = ({step: ("main",) for step in flow} if special else PROFILE_EVIDENCE_ACTORS[profile])
    current_required = list(flow)
    # A strict fix is accepted by current re-adjudication and final evaluation.
    # Earlier reviews remain history, never current evidence for the edited diff.
    fixes = [i for i, ev in enumerate(events) if "fix" in v2_event_steps(ev)
             and ev.get("change_revision") == work_item.get("change_revision")]
    if fixes and not special:
        current_required = ["fix" if s == "implement" else s for s in current_required]
        if profile == "strict":
            current_required = [s for s in current_required if s not in ("evaluate", "review", "judge")]
            current_required.insert(current_required.index("final-evaluate"), "rejudge")
    prerequisites = {step: current_required[:i] for i, step in enumerate(current_required)}
    return {"profile": profile, "required_steps": flow, "required_evidence": current_required,
            "allowed_transitions": transitions, "review_requirements": roster,
            "required_subskills": skills, "evidence_actors": actors,
            "step_sequence": current_required, "step_prerequisites": prerequisites,
            "allowed_reviewers": ({"indep:general"} if profile == "standard" else
                {"self", "indep:security", "indep:correctness", "indep:architecture", "indep:tests", "indep:performance"})}


def evidence_satisfies(step, snapshot, contract, revision, reviewers=None):
    """Whether a pass satisfies the final profile's current evidence obligations."""
    if (not isinstance(snapshot, dict) or snapshot.get("outcome") != "pass"
            or snapshot.get("actor") not in contract["evidence_actors"].get(step, ())
            or type(snapshot.get("revision")) is not int
            or (step != "investigate" and snapshot["revision"] != revision)
            or not nonempty_strings(snapshot.get("references"))):
        return False
    if step in ("review", "judge", "rejudge") and (snapshot.get("p0") is True or snapshot.get("p1") is True):
        return False
    if step != "review":
        return True
    if (any(snapshot.get(key) is not False for key in ("p0", "p1"))
            or type(snapshot.get("requires_adjudication")) is not bool
            or (contract["profile"] == "standard" and snapshot["requires_adjudication"])):
        return False
    if (not isinstance(reviewers, list) or not all(isinstance(r, str) for r in reviewers)
            or len(reviewers) != len(set(reviewers))
            or not set(contract["review_requirements"] or []) <= set(reviewers)
            or not set(reviewers) <= contract["allowed_reviewers"]):
        return False
    if contract["profile"] == "standard":
        return reviewers == contract["review_requirements"]
    reports = snapshot.get("reports")
    return (isinstance(reports, dict) and set(reports) == set(reviewers)
            and all(isinstance(report, dict) and type(report.get("revision")) is int
                    and report["revision"] == revision and report.get("outcome") == "pass"
                    and nonempty_strings(report.get("references")) for report in reports.values()))


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


def flow_for(events, work_item=None, schema_version=1):
    """Return the ordered step list applicable to this work item's flow."""
    return resolve_execution_path(events, work_item, schema_version)["required_steps"]


def ordered_steps(events, work_item=None, schema_version=1):
    """Applicable flow, with `fix`/`rejudge` spliced in when the ledger actually
    went through a needs_fix round."""
    base = flow_for(events, work_item, schema_version)
    read_steps = v2_event_steps if schema_version == 2 else event_steps
    saw_fix = any("fix" in read_steps(ev) for ev in events)
    saw_rejudge = any("rejudge" in read_steps(ev) for ev in events)
    out = []
    for s in base:
        out.append(s)
        if s == "judge" and saw_fix:
            out.append("fix")
        if s == "judge" and saw_rejudge:
            out.append("rejudge")
        if s == "review" and saw_fix and schema_version == 2 and work_item.get("execution_profile") == "standard":
            out.append("fix")
    return out


def derive_step_progress(events, work_item=None, schema_version=1):
    """Derive per-step progress for display and coverage.

    Returns a dict:
      {
        "current_step": <step id | null when terminal>,
        "completed": [<step id>, ...],
        "steps": [ {"id", "label", "status": done|current|pending|skipped}, ... ],
      }
    """
    events = events or []
    try:
        # Only v1 may infer steps. V2 errors stay visible instead of fabricating
        # completed investigation/review gates from their surrounding states.
        read_steps = v2_event_steps if schema_version == 2 else event_steps
        seen = []
        for ev in events:
            for s in read_steps(ev):
                if s and (not seen or seen[-1] != s):
                    seen.append(s)
        flow = ordered_steps(events, work_item, schema_version)
    except ValueError as error:
        return {"current_step": None, "completed": [], "steps": [], "error": str(error)}
    if schema_version == 2:
        revision = (work_item or {}).get("change_revision")
        contract = resolve_execution_path(events, work_item, schema_version)
        flow = contract["step_sequence"]
        accepted = {}
        reviewers = None
        for ev in events:
            if ev.get("to") == "reviewing":
                reviewers = ev.get("reviewers")
            for step in v2_event_steps(ev):
                snapshot = ev["evidence"][step]
                if (evidence_satisfies(step, snapshot, contract, revision, reviewers)
                        and all(prior in accepted for prior in contract["step_prerequisites"].get(step, []))):
                    accepted[step] = snapshot
        seen = list(accepted)
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
    if schema_version == 2:
        steps.extend({"id": s, "label": STEP_LABELS.get(s, s), "status": "skipped",
                      "reason": "not applicable to selected execution profile and current revision"}
                     for s in STEPS if s not in flow)
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
