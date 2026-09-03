#!/usr/bin/env python3
"""
validate_workflow.py

Portable, zero-dependency validator for the agent-orchestration workflow ledger.
Encodes a single-layer, 5-state machine (active / reviewing / needs_fix / done / escalated),
the P0/P1 gate rule, the iteration budgets, and the lens-diversity rule exactly as documented in:
  references/workflow-state-machine.md
  references/severity-and-gates.md
  references/parallel-dispatch.md

This machine encodes a single-layer, 5-state model (active / reviewing / needs_fix / done /
escalated) down from the full lifecycle: execution/evaluation plumbing (queued, dispatched,
pending, evaluating, ...) is scheduler-internal noise that carries no decision value for a coding
agent and has been removed; parallel-review fan-out detail (reviewers, lens, iteration, p0/p1)
is preserved but demoted from states to event metadata, since it is a record of what happened,
not a phase the agent needs to branch on.

Tool-agnostic: Python 3.9+ standard library only, no runtime dependency.

Ledger input schema: references/ledger-format.md; provide a ledger JSON path when validating.

Usage:
  python3 validate_workflow.py check-ledger <ledger.json>   validate a workflow trace
  python3 validate_workflow.py check-coverage <ledger.json> verify no mandatory step was skipped
  python3 validate_workflow.py self-check                   validate the encoded tables themselves

Exit codes:
  0  valid
  1  invalid (illegal transition, budget exceeded, duplicate lens, silent rollback,
     or check-coverage found a missing mandatory step)
  2  malformed input (bad JSON, unknown state, missing field, or no step data to check)
"""

import json
import sys

from _workflow_paths import default_ledger_path, project_ledger_hint
from _workflow_steps import (
    STEPS,
    REQUIRED_CODE,
    REQUIRED_NO_CODE,
    REQUIRED_STATUS_ONLY,
    REQUIRED_STANDALONE_SCRIPT,
    has_code_change,
    is_status_only,
    is_standalone_script,
)

STATES = ["active", "reviewing", "needs_fix", "done", "escalated"]

TERMINALS = ["done", "escalated"]

# Allowed transitions. {"explicit": True} = rollback requiring the event to carry explicit=True.
# {"budget": "<key>"} = transition consumed by a fixed budget counter (dynamic-reason edges are
# handled separately in check_ledger, since active->active's budget depends on ev["reason"]).
TRANSITIONS = {
    "active->reviewing": {},
    "active->done": {},
    "active->active": {"budget": "dynamic"},
    "active->escalated": {},
    "reviewing->needs_fix": {},
    "reviewing->done": {},
    "reviewing->escalated": {},
    "needs_fix->reviewing": {"budget": "review_fix_rounds"},
    "done->active": {"explicit": True},
    "reviewing->active": {"explicit": True},
    "escalated->active": {"explicit": True},
}

# Budgets (severity-and-gates §4). Consecutive auto-chain / retry / fix-round caps.
BUDGET = {
    "exec_retries": 3,
    "answer_follow_up": 2,
    "fix_follow_up": 2,
    "review_fix_rounds": 3,
}

# Maps active->active "reason" to its budget key.
REASON_BUDGET = {
    "retry": "exec_retries",
    "answer": "answer_follow_up",
    "fix": "fix_follow_up",
}

CAP = {"global": 15, "per_role": 6, "per_role_hard": 10}

# Allowed independent-reviewer lenses (agents/README.md placeholder glossary).
ALLOWED_LENSES = {"security", "correctness", "architecture", "tests", "performance"}

# Structured subskill-routing vocabulary. These values are intentionally finite so a
# typo cannot silently turn a required routing gate into an untracked one.
CANONICAL_SUBSKILLS = {
    "scenario-standards",
    "architecture-principles",
    "qa-suite",
    "coding-tactics",
}
LEGACY_SUBSKILL_MAP = {
    "fullstack-dev": "scenario-standards",
    "qa-blueprint": "qa-suite",
    "dev-method": "coding-tactics",
}
ROUTING_SUBSKILLS = CANONICAL_SUBSKILLS | set(LEGACY_SUBSKILL_MAP.keys())
ROUTING_SCENARIOS = {"greenfield", "feature", "bug-fix", "refactor", "deploy"}
ROUTING_PHASES = {"requirement", "architecture", "backend", "frontend", "devops"}
ROUTING_TIERS = {"L1", "L1-status-only", "L1-standalone-script", "L2", "L2-observability", "L3"}
CHANGE_CLASSES = {"none", "status-only", "standalone-script", "code"}
STANDALONE_SCRIPT_OUTCOMES = {"pass", "fail", "reproduced"}
STANDALONE_SCRIPT_CHECKS = {
    "production_code_changed",
    "dependency_changed",
    "dependency_used",
    "persistent_side_effect",
    "api_touched",
    "security_touched",
    "concurrency_touched",
    "deployment_touched",
    "ci_touched",
    "explicit_project_test_request",
    "uncertain",
}
STATUS_ONLY_REASONS = {"format", "text", "comment", "version-metadata"}
STATUS_ONLY_BOOLEAN_FIELDS = (
    "behavior_changed",
    "control_flow_changed",
    "public_api_changed",
    "data_model_changed",
    "dependency_changed",
    "import_changed",
    "auth_security_changed",
    "concurrency_changed",
    "transaction_changed",
    "retry_changed",
    "external_call_changed",
    "explicit_test_request",
    "uncertain",
)
STATUS_ONLY_FIELDS = {"reason", *STATUS_ONLY_BOOLEAN_FIELDS}
ROUTING_METHODS = {"tdd", "bdd", "api-first", "security-first", "direct"}
OBSERVABILITY_KINDS = {"log", "metric", "trace", "mixed"}
OBSERVABILITY_BOOLEAN_FIELDS = (
    "control_flow_changed",
    "error_path_changed",
    "public_api_changed",
    "data_model_changed",
    "dependency_changed",
    "auth_security_changed",
    "concurrency_changed",
    "transaction_changed",
    "retry_changed",
    "external_call_changed",
    "sensitive_data_logged",
    "hot_path",
    "explicit_test_request",
)
OBSERVABILITY_FIELDS = {"kind", "files", *OBSERVABILITY_BOOLEAN_FIELDS}


def edge_key(from_state, to_state):
    return "%s->%s" % (from_state, to_state)


def _non_empty_list(value):
    return isinstance(value, list) and len(value) > 0


def _list_field(mapping, key, errors, label):
    value = mapping.get(key)
    if not isinstance(value, list):
        errors.append('%s.%s must be an array' % (label, key))
    return value


def validate_routing(work_item, code_change):
    """Validate the structured subskill handoff required for code changes."""
    if not code_change:
        return []

    errors = []
    routing = work_item.get("routing") if isinstance(work_item, dict) else None
    if not isinstance(routing, dict):
        return ['work_item.routing is required for code-change ledgers']

    scenario = routing.get("scenario")
    if scenario not in ROUTING_SCENARIOS:
        errors.append('work_item.routing.scenario must be one of: %s' % ", ".join(sorted(ROUTING_SCENARIOS)))
    phase = routing.get("phase")
    if phase not in ROUTING_PHASES:
        errors.append('work_item.routing.phase must be one of: %s' % ", ".join(sorted(ROUTING_PHASES)))
    tier = routing.get("tier")
    if tier not in ROUTING_TIERS:
        errors.append('work_item.routing.tier must be one of: L1, L1-status-only, L1-standalone-script, L2, L2-observability, L3')
    architecture_required = routing.get("architecture_required")
    if not isinstance(architecture_required, bool):
        errors.append('work_item.routing.architecture_required must be boolean')

    required = routing.get("required")
    used = routing.get("used")
    for key, value in (("required", required), ("used", used)):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append('work_item.routing.%s must be an array of skill names' % key)
        elif len(value) != len(set(value)):
            errors.append('work_item.routing.%s must not contain duplicates' % key)
        else:
            unknown = sorted(set(value) - ROUTING_SUBSKILLS)
            if unknown:
                errors.append('work_item.routing.%s contains unknown subskills: %s' % (key, ", ".join(unknown)))

    if isinstance(required, list) and isinstance(used, list):
        missing_used = sorted(set(required) - set(used))
        if missing_used:
            errors.append('work_item.routing.used is missing required subskills: %s' % ", ".join(missing_used))
        has_scenario = "scenario-standards" in required or "fullstack-dev" in required
        if not has_scenario:
            errors.append('work_item.routing.required must include scenario-standards')
        has_method = "coding-tactics" in required or "dev-method" in required
        if not has_method:
            errors.append('work_item.routing.required must include coding-tactics')
        has_qa = "qa-suite" in required or "qa-blueprint" in required
        if tier == "L3" and not has_qa:
            errors.append('L3 routing.required must include qa-suite')
        if tier == "L2-observability":
            if has_qa or "qa-suite" in used or "qa-blueprint" in used:
                errors.append('L2-observability must skip qa-suite; promote to L3 for full QA routing')
            if architecture_required is True:
                errors.append('L2-observability with architecture_required=true must be promoted to L3')
        if architecture_required is True and "architecture-principles" not in required:
            errors.append('architecture_required=true requires architecture-principles in routing.required')

    if tier == "L2-observability":
        observability = routing.get("observability")
        if not isinstance(observability, dict):
            errors.append('L2-observability requires work_item.routing.observability; promote to L3')
        else:
            extra_fields = sorted(set(observability) - OBSERVABILITY_FIELDS)
            if extra_fields:
                errors.append('work_item.routing.observability contains unknown fields: %s; promote to L3'
                              % ", ".join(extra_fields))
            kind = observability.get("kind")
            if kind not in OBSERVABILITY_KINDS:
                errors.append('work_item.routing.observability.kind must be one of: log, metric, trace, mixed')
            files = observability.get("files")
            if isinstance(files, bool) or not isinstance(files, int) or not 1 <= files <= 2:
                errors.append('work_item.routing.observability.files must be an integer from 1 to 2; promote to L3')
            for field in OBSERVABILITY_BOOLEAN_FIELDS:
                value = observability.get(field)
                if not isinstance(value, bool):
                    errors.append('work_item.routing.observability.%s must be boolean' % field)
                elif value is True:
                    errors.append('L2-observability promotion condition %s=true; promote to L3' % field)
    elif isinstance(routing.get("observability"), dict):
        errors.append('work_item.routing.observability is only allowed for tier L2-observability')

    handoffs = routing.get("handoffs")
    if not isinstance(handoffs, dict):
        errors.append('work_item.routing.handoffs must be an object')
        return errors

    if isinstance(required, list):
        for name in required:
            label = 'work_item.routing.handoffs[%s]' % name
            handoff = handoffs.get(name)
            if not isinstance(handoff, dict):
                errors.append('%s must be an object' % label)
                continue
            if handoff.get("status") != "complete":
                errors.append('%s.status must be complete' % label)
            canonical_name = LEGACY_SUBSKILL_MAP.get(name, name)
            if canonical_name == "scenario-standards":
                if handoff.get("tier") not in ROUTING_TIERS:
                    errors.append('%s.tier must be a known routing tier' % label)
                elif handoff.get("tier") != tier:
                    errors.append('%s.tier must match work_item.routing.tier' % label)
                if handoff.get("scenario") not in ROUTING_SCENARIOS:
                    errors.append('%s.scenario must be a known routing scenario' % label)
                elif handoff.get("scenario") != scenario:
                    errors.append('%s.scenario must match work_item.routing.scenario' % label)
                if handoff.get("phase") not in ROUTING_PHASES:
                    errors.append('%s.phase must be a known routing phase' % label)
                elif handoff.get("phase") != phase:
                    errors.append('%s.phase must match work_item.routing.phase' % label)
                for field in ("scope_boundary", "affected_files", "acceptance_criteria", "non_goals"):
                    _list_field(handoff, field, errors, label)
                if isinstance(handoff.get("acceptance_criteria"), list) and not handoff["acceptance_criteria"]:
                    errors.append('%s.acceptance_criteria must not be empty' % label)
            elif canonical_name == "coding-tactics":
                if handoff.get("method") not in ROUTING_METHODS:
                    errors.append('%s.method must be one of: %s' % (label, ", ".join(sorted(ROUTING_METHODS))))
                if not _non_empty_list(handoff.get("checkpoints")):
                    errors.append('%s.checkpoints must be a non-empty array' % label)
            elif canonical_name == "qa-suite":
                if not isinstance(handoff.get("project_profile"), dict):
                    errors.append('%s.project_profile must be an object' % label)
                for field in ("test_types", "frameworks", "verify_commands"):
                    if not _non_empty_list(handoff.get(field)):
                        errors.append('%s.%s must be a non-empty array' % (label, field))
                _list_field(handoff, "gaps", errors, label)
            elif canonical_name == "architecture-principles":
                decision = handoff.get("decision")
                if not isinstance(decision, str) or not decision.strip():
                    errors.append('%s.decision must be a non-empty string' % label)
                for field in ("principles_applied", "tradeoffs", "open_questions"):
                    _list_field(handoff, field, errors, label)

    return errors


def validate_change_class(work_item, events):
    """Validate the optional explicit classification without breaking legacy ledgers."""
    errors = []
    change_class = work_item.get("change_class")
    if change_class is not None and change_class not in CHANGE_CLASSES:
        errors.append('work_item.change_class must be one of: code, none, status-only')
        return errors
    if change_class == "code" and not has_code_change(events):
        errors.append('work_item.change_class="code" requires has_code_change:true')
    if change_class == "standalone-script" and has_code_change(events):
        errors.append('work_item.change_class="standalone-script" cannot declare has_code_change:true')
    if change_class == "none" and work_item.get("changed_files"):
        errors.append('work_item.change_class="none" cannot list changed_files')
    return errors


def validate_standalone_script(work_item, events):
    """Validate a standalone test/debug script that intentionally skips code review."""
    errors = []
    if work_item.get("change_class") != "standalone-script":
        if not any(isinstance(ev, dict) and ev.get("change_class") == "standalone-script" for ev in events):
            return errors
        errors.append('standalone-script event requires work_item.change_class="standalone-script"')

    if work_item.get("kind") != "standalone-script":
        errors.append('standalone-script requires work_item.kind="standalone-script"')
    script = work_item.get("standalone_script")
    if not isinstance(script, dict):
        return errors + ['standalone-script requires work_item.standalone_script']
    required = {"path", "run_command", "exit_code", "outcome", "result", "promotion_checks"}
    missing = sorted(required - set(script))
    if missing:
        errors.append('standalone_script is missing fields: %s' % ", ".join(missing))
    for field in ("path", "run_command", "result"):
        value = script.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append('standalone_script.%s must be a non-empty string' % field)
    if isinstance(script.get("exit_code"), bool) or not isinstance(script.get("exit_code"), int):
        errors.append('standalone_script.exit_code must be an integer from the actual run')
    if script.get("outcome") not in STANDALONE_SCRIPT_OUTCOMES:
        errors.append('standalone_script.outcome must be one of: %s' % ", ".join(sorted(STANDALONE_SCRIPT_OUTCOMES)))
    checks = script.get("promotion_checks")
    if not isinstance(checks, dict):
        errors.append('standalone_script.promotion_checks must be an object')
    else:
        unknown = sorted(set(checks) - STANDALONE_SCRIPT_CHECKS)
        missing = sorted(STANDALONE_SCRIPT_CHECKS - set(checks))
        if unknown:
            errors.append('standalone_script.promotion_checks contains unknown fields: %s' % ", ".join(unknown))
        if missing:
            errors.append('standalone_script.promotion_checks is missing fields: %s' % ", ".join(missing))
        for field in STANDALONE_SCRIPT_CHECKS:
            value = checks.get(field)
            if not isinstance(value, bool):
                errors.append('standalone_script.promotion_checks.%s must be boolean' % field)
            elif value is True:
                errors.append('standalone script promotion condition %s=true; classify as L3' % field)

    routing = work_item.get("routing")
    if isinstance(routing, dict) and routing.get("tier") != "L1-standalone-script":
        errors.append('standalone-script routing.tier must be L1-standalone-script')
    if any(ev.get("has_code_change") is True for ev in events if isinstance(ev, dict)):
        errors.append('standalone-script must not declare has_code_change:true; classify as L3')
    for idx, ev in enumerate(events, 1):
        if not isinstance(ev, dict):
            continue
        if ev.get("to") in ("reviewing", "needs_fix"):
            errors.append('standalone-script event %d must not enter reviewing or needs_fix' % idx)
        steps = ev.get("step")
        step_list = steps if isinstance(steps, list) else [steps]
        forbidden = sorted(set(step_list) & {"implement", "evaluate", "review", "judge", "fix", "rejudge", "final-evaluate"})
        if forbidden:
            errors.append('standalone-script event %d must not record steps: %s' % (idx, ", ".join(forbidden)))
    terminal = next((ev for ev in reversed(events) if ev.get("to") == "done"), None)
    if terminal is None:
        errors.append('standalone-script ledger must end with an active->done record')
    elif terminal.get("from") != "active":
        errors.append('standalone-script terminal record must transition active->done')
    elif terminal.get("standalone_script") is not True or terminal.get("change_class") != "standalone-script":
        errors.append('standalone-script terminal event requires standalone_script:true and change_class="standalone-script"')
    return errors


def validate_status_only(work_item, events):
    """Validate the explicit evidence required for a status-only change."""
    errors = []
    change_class = work_item.get("change_class")
    event_marks = [ev for ev in events if isinstance(ev, dict) and ev.get("change_class") == "status-only"]
    if change_class != "status-only" and not event_marks:
        return errors
    if change_class != "status-only":
        errors.append('status-only event requires work_item.change_class="status-only"')
    if any(ev.get("has_code_change") is True for ev in events if isinstance(ev, dict)):
        errors.append('status-only change must not declare has_code_change:true')

    changed_files = work_item.get("changed_files")
    if not _non_empty_list(changed_files) or any(not isinstance(path, str) or not path.strip() for path in changed_files):
        errors.append('status-only requires a non-empty work_item.changed_files array')

    evidence = work_item.get("status_only")
    if not isinstance(evidence, dict):
        errors.append('status-only requires work_item.status_only evidence')
        return errors
    unknown = sorted(set(evidence) - STATUS_ONLY_FIELDS)
    if unknown:
        errors.append('work_item.status_only contains unknown fields: %s' % ", ".join(unknown))
    if evidence.get("reason") not in STATUS_ONLY_REASONS:
        errors.append('work_item.status_only.reason must be one of: %s' % ", ".join(sorted(STATUS_ONLY_REASONS)))
    for field in STATUS_ONLY_BOOLEAN_FIELDS:
        value = evidence.get(field)
        if not isinstance(value, bool):
            errors.append('work_item.status_only.%s must be boolean' % field)
        elif value is True:
            errors.append('status-only promotion condition %s=true; classify as L3' % field)

    routing = work_item.get("routing")
    if isinstance(routing, dict) and routing.get("tier") != "L1-status-only":
        errors.append('status-only routing.tier must be L1-status-only')
    for idx, ev in enumerate(events, 1):
        if not isinstance(ev, dict):
            continue
        if ev.get("to") in ("reviewing", "needs_fix"):
            errors.append('status-only event %d must not enter reviewing or needs_fix' % idx)
        steps = ev.get("step")
        step_list = steps if isinstance(steps, list) else [steps]
        if "fix" in step_list or "review" in step_list or "judge" in step_list:
            errors.append('status-only event %d must not record review/fix steps' % idx)
    terminal = next((ev for ev in reversed(events) if ev.get("to") == "done"), None)
    if terminal is None:
        errors.append('status-only ledger must end with an active->done status record')
    else:
        if terminal.get("status_only") is not True or terminal.get("change_class") != "status-only":
            errors.append('status-only terminal event requires status_only:true and change_class="status-only"')
    return errors


def self_check():
    problems = []
    for key in TRANSITIONS:
        from_state, to_state = key.split("->")
        if from_state not in STATES:
            problems.append('transition "%s" has unknown source "%s"' % (key, from_state))
        if to_state not in STATES:
            problems.append('transition "%s" has unknown target "%s"' % (key, to_state))
    for key, rule in TRANSITIONS.items():
        budget = rule.get("budget")
        if budget and budget != "dynamic" and budget not in BUDGET:
            problems.append('transition "%s" references unknown budget "%s"' % (key, budget))
    for reason, budget in REASON_BUDGET.items():
        if budget not in BUDGET:
            problems.append('reason "%s" references unknown budget "%s"' % (reason, budget))
    if not ROUTING_SUBSKILLS:
        problems.append("routing subskill vocabulary must not be empty")
    if not ROUTING_METHODS.issubset({"tdd", "bdd", "api-first", "security-first", "direct"}):
        problems.append("routing method vocabulary contains an unknown method")
    for state in STATES:
        has_out = any(k.startswith(state + "->") for k in TRANSITIONS)
        if not has_out and state not in TERMINALS:
            problems.append('state "%s" has no out-edge but is not declared terminal' % state)
    if problems:
        for p in problems:
            sys.stderr.write("SELF-CHECK ERROR: %s\n" % p)
        return 1
    print("SELF-CHECK OK: 5-state table consistent (active/reviewing/needs_fix/done/escalated)")
    return 0


def check_ledger(ledger):
    errors = []
    warnings = []
    work_item = ledger.get("work_item") or {}
    work_id = work_item.get("id") or "<unknown>"
    events = ledger.get("events")

    if not isinstance(events, list):
        sys.stderr.write("ERROR: ledger.events must be an array\n")
        return 2

    current = None
    counters = {"exec_retries": 0, "answer_follow_up": 0, "fix_follow_up": 0, "review_fix_rounds": 0}
    last_reason = None  # tracks the active->active chain type for consecutive-budget counting

    code_change = has_code_change(events)
    status_only = is_status_only(events, work_item)
    standalone_script = is_standalone_script(events, work_item)
    errors.extend(validate_change_class(work_item, events))
    errors.extend(validate_standalone_script(work_item, events))
    errors.extend(validate_status_only(work_item, events))
    if status_only and code_change:
        errors.append('status-only and code-change classifications cannot be combined')
    if standalone_script and (status_only or code_change):
        errors.append('standalone-script cannot be combined with status-only or code-change')
    errors.extend(validate_routing(work_item, code_change))

    for idx, ev in enumerate(events):
        n = idx + 1

        # --- field validation (malformed => exit 2) ---
        if not isinstance(ev, dict):
            sys.stderr.write("ERROR [input] event %d: not an object\n" % n)
            return 2
        if ev.get("to") is None:
            sys.stderr.write('ERROR [input] event %d: missing "to"\n' % n)
            return 2
        to_state = ev["to"]
        if to_state not in STATES:
            sys.stderr.write('ERROR [input] event %d: "%s" is not a known state\n' % (n, to_state))
            return 2
        from_state = ev.get("from") if ev.get("from") is not None else None
        if from_state is not None and from_state not in STATES:
            sys.stderr.write('ERROR [input] event %d: "%s" is not a known state\n' % (n, from_state))
            return 2

        # --- protocol step (optional, but if present must name known steps) ---
        step = ev.get("step")
        if step is not None:
            step_list = step if isinstance(step, list) else [step]
            for s in step_list:
                if s not in STEPS:
                    errors.append('event %d: unknown step "%s" (allowed: %s)' % (n, s, ", ".join(STEPS)))

        # --- reviewing roster: exactly one "self" + >=1 distinct valid "indep:<lens>" ---
        if to_state == "reviewing":
            reviewers = ev.get("reviewers")
            if not isinstance(reviewers, list) or not reviewers:
                errors.append('event %d: entering reviewing requires a non-empty "reviewers" list (fan-out roster)' % n)
            else:
                self_count = sum(1 for r in reviewers if r == "self")
                if self_count != 1:
                    errors.append('event %d: reviewers must contain exactly one "self" entry, got %d' % (n, self_count))
                lenses = []
                for r in reviewers:
                    if isinstance(r, str) and r.startswith("indep:"):
                        lenses.append(r[len("indep:"):])
                    elif r != "self":
                        errors.append('event %d: unknown reviewer entry %r (expected "self" or "indep:<lens>")' % (n, r))
                if not lenses:
                    errors.append('event %d: reviewers must include at least one "indep:<lens>" entry' % n)
                seen = set()
                for lens in lenses:
                    if lens in seen:
                        errors.append('event %d: duplicate lens "%s" in reviewers (MUST NOT repeat a lens)' % (n, lens))
                    seen.add(lens)
                    if not lens:
                        errors.append('event %d: empty lens in "indep:" entry' % n)
                    elif lens not in ALLOWED_LENSES:
                        errors.append('event %d: unknown lens "%s" (allowed: %s)' % (n, lens, ", ".join(sorted(ALLOWED_LENSES))))

        # --- review gate (P0/P1 block, P2/P3 never block); scoped to this single outcome event ---
        if to_state == "done" and from_state == "reviewing" and (ev.get("p0") is True or ev.get("p1") is True):
            errors.append('event %d: reviewing->done with a verified P0/P1 present - P0/P1 must block, P2/P3 never block' % n)

        # --- reachability: from:null is only allowed on the very first event (no silent restart) ---
        if from_state is None and idx > 0:
            errors.append("event %d: from:null is only allowed on the ledger's first event (restart is forbidden)" % n)
            continue
        if from_state is not None and current != from_state:
            errors.append('event %d: reachability mismatch, expected current state "%s" but "from" is "%s"'
                          % (n, current, from_state))
            continue

        if from_state is None:
            current = to_state
            continue

        rule = TRANSITIONS.get(edge_key(from_state, to_state))
        if rule is None:
            errors.append('event %d: illegal transition %s -> %s' % (n, from_state, to_state))
            continue
        if rule.get("explicit") and ev.get("explicit") is not True:
            errors.append('event %d: silent rollback %s -> %s requires explicit:true (explicit user decision must be recorded)'
                          % (n, from_state, to_state))
            continue

        # --- active->active branch validation + dynamic-reason budget ---
        if from_state == "active" and to_state == "active":
            reason = ev.get("reason")
            if reason not in REASON_BUDGET:
                errors.append('event %d: active->active requires "reason" in {retry, answer, fix}' % n)
            else:
                if reason in ("answer", "fix") and ev.get("is_complete") is not False:
                    errors.append('event %d: active->active reason="%s" requires is_complete:false' % (n, reason))
                budget_key = REASON_BUDGET[reason]
                counters[budget_key] = counters[budget_key] + 1 if last_reason == reason else 1
                last_reason = reason
                if counters[budget_key] > BUDGET[budget_key]:
                    errors.append('event %d: %s chain exceeds cap %d (must escalate)' % (n, budget_key, BUDGET[budget_key]))
        else:
            last_reason = None

        # --- active-outgoing branch validation ---
        if from_state == "active" and to_state == "reviewing":
            if ev.get("is_complete") is not True or ev.get("has_code_change") is not True:
                errors.append('event %d: active->reviewing requires is_complete:true and has_code_change:true' % n)
        if from_state == "active" and to_state == "done":
            if ev.get("is_complete") is not True or ev.get("has_code_change") is not False:
                errors.append('event %d: active->done requires is_complete:true and has_code_change:false' % n)
        if from_state == "active" and to_state == "escalated":
            if ev.get("needs_user_decision") is not True and not ev.get("reason"):
                errors.append('event %d: active->escalated requires needs_user_decision:true or a "reason"' % n)

        # --- needs_fix -> reviewing: review fix round budget ---
        if from_state == "needs_fix" and to_state == "reviewing":
            counters["review_fix_rounds"] += 1
            if counters["review_fix_rounds"] > BUDGET["review_fix_rounds"]:
                errors.append('event %d: review fix rounds exceed cap %d (must go escalated)' % (n, BUDGET["review_fix_rounds"]))

        # --- concurrency caps (optional in-flight telemetry) ---
        if isinstance(ev.get("in_flight"), int) and ev["in_flight"] > CAP["global"]:
            warnings.append('event %d: global in_flight %d exceeds cap %d' % (n, ev["in_flight"], CAP["global"]))
        role_in_flight = ev.get("role_in_flight")
        if isinstance(role_in_flight, dict):
            for role, count in role_in_flight.items():
                if count > CAP["per_role_hard"]:
                    errors.append('event %d: role "%s" in_flight %d exceeds hard cap %d' % (n, role, count, CAP["per_role_hard"]))
                elif count > CAP["per_role"]:
                    warnings.append('event %d: role "%s" in_flight %d exceeds soft cap %d' % (n, role, count, CAP["per_role"]))

        current = to_state

    # --- terminal-state gate: a ledger that never reached done/escalated is not valid ---
    if current not in TERMINALS:
        errors.append('ledger must end in a terminal state ("done" or "escalated"), got "%s"' % (current or "-"))

    for w in warnings:
        sys.stderr.write("WARN %s\n" % w)
    if errors:
        for e in errors:
            sys.stderr.write("ERROR %s\n" % e)
        return 1
    print("VALID %s state=%s review_fix_rounds=%d" % (work_id, current or "-", counters["review_fix_rounds"]))
    return 0


def check_coverage(ledger):
    """Verify no mandatory protocol step was skipped.

    This is the anti-skip gate: the 5-state machine validates *what happened*,
    this check validates *which gates were actually hit*. It reads the optional
    ``step`` field on each event and asserts every mandatory step for the work
    item's flow (code-change vs no-code-change) is present.

    Exit codes:
      0  all mandatory steps covered
      1  one or more mandatory steps missing (workflow skipped a gate)
      2  ledger records no step fields at all (nothing to check)
    """
    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    work_id = (ledger.get("work_item") or {}).get("id") or "<unknown>"

    # Collect explicit step fields only (no state inference): coverage checks what
    # the agent actually recorded, not what states happen to imply.
    seen = []
    for ev in events:
        s = ev.get("step")
        if isinstance(s, list):
            for x in s:
                if x in STEPS and x not in seen:
                    seen.append(x)
        elif s in STEPS and s not in seen:
            seen.append(s)
    if not seen:
        sys.stderr.write(
            "COVERAGE: ledger records no `step` fields; cannot verify step coverage "
            "(add a `step` field to each event per ledger-format.md)\n"
        )
        return 2

    work_item = ledger.get("work_item") or {}
    code_change = has_code_change(events)
    status_only = is_status_only(events, work_item)
    standalone_script = is_standalone_script(events, work_item)
    routing_errors = validate_change_class(work_item, events)
    routing_errors.extend(validate_standalone_script(work_item, events))
    routing_errors.extend(validate_status_only(work_item, events))
    routing_errors.extend(validate_routing(work_item, code_change))
    if routing_errors:
        for error in routing_errors:
            sys.stderr.write("COVERAGE ERROR: routing: %s\n" % error)
        return 1

    required = (REQUIRED_STANDALONE_SCRIPT if standalone_script else
                (REQUIRED_STATUS_ONLY if status_only else (REQUIRED_CODE if code_change else REQUIRED_NO_CODE)))
    missing = [s for s in required if s not in seen]

    warnings = []
    if code_change:
        # Gate evidence (soft): evaluate/judge gates should leave a verifiable mark.
        for ev in events:
            if ev.get("from") == "active" and ev.get("to") == "reviewing" and "evaluation" not in ev:
                warnings.append("event active->reviewing lacks `evaluation` snapshot (completion-evaluator result)")
            if ev.get("from") == "reviewing" and ev.get("to") == "done" and "judge" not in ev:
                warnings.append("event reviewing->done lacks `judge` snapshot (passed/round)")

    for w in warnings:
        sys.stderr.write("COVERAGE WARN %s\n" % w)
    if missing:
        for m in missing:
            sys.stderr.write('COVERAGE ERROR: mandatory step "%s" not recorded (was it skipped?)\n' % m)
        return 1

    flow_name = ("standalone-script" if standalone_script else
                 ("status-only" if status_only else ("code" if code_change else "no-code")))
    print("COVERAGE OK %s flow=%s steps=%d" % (work_id, flow_name, len(seen)))
    return 0


def _load_ledger(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    if not argv:
        sys.stderr.write(
            "Usage:\n"
            "  python3 validate_workflow.py check-ledger <ledger.json>\n"
            "  python3 validate_workflow.py check-coverage <ledger.json>\n"
            "  python3 validate_workflow.py self-check\n"
        )
        return 2
    mode = argv[0]
    if mode == "self-check":
        return self_check()

    if mode not in ("check-ledger", "check-coverage"):
        sys.stderr.write('ERROR: unknown mode "%s" (expected check-ledger|check-coverage|self-check)\n' % mode)
        return 2

    if len(argv) < 2:
        path = default_ledger_path()
        if not path:
            sys.stderr.write("Usage: python3 validate_workflow.py %s <ledger.json>\n" % mode)
            sys.stderr.write(project_ledger_hint() + "\n")
            return 2
        sys.stderr.write('using default ledger "%s"\n' % path)
    else:
        path = argv[1]

    try:
        ledger = _load_ledger(path)
    except Exception as e:
        sys.stderr.write('ERROR: cannot read/parse "%s": %s\n' % (path, e))
        return 2

    if mode == "check-ledger":
        return check_ledger(ledger)
    return check_coverage(ledger)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
