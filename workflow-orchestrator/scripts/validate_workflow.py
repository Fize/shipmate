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
New ledgers use root schema_version:2 and work_item.execution_profile; both validation commands
enforce the shared profile/evidence contract. No-version and version 1 retain legacy validation.

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
    resolve_execution_path,
    profile_decision_errors,
    nonempty_strings,
    event_steps,
    PROFILE_FLOWS,
    PROFILE_EVIDENCE_ACTORS,
    evidence_satisfies,
)

LEGACY_STEPS = [step for step in STEPS if step not in ("verify", "diff-check")]

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
    for profile, flow in PROFILE_FLOWS.items():
        if not set(flow) <= set(STEPS) or set(flow) - set(PROFILE_EVIDENCE_ACTORS[profile]):
            problems.append("profile %s has unknown steps or missing evidence actors" % profile)
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


def _v2_routing_errors(work_item, contract, code_change):
    """Internal routing is compact for light/standard; strict retains handoffs."""
    if not code_change:
        return []
    routing = work_item.get("routing")
    if not isinstance(routing, dict):
        return ["v2 code changes require work_item.routing"]
    errors = []
    for field, choices in (("scenario", ROUTING_SCENARIOS), ("phase", ROUTING_PHASES), ("method", ROUTING_METHODS)):
        if routing.get(field) not in choices:
            errors.append("routing.%s must be a known value" % field)
    if not isinstance(routing.get("architecture_required"), bool):
        errors.append("routing.architecture_required must be boolean")
    required, used = routing.get("required"), routing.get("used")
    for name, values in (("required", required), ("used", used)):
        if (not isinstance(values, list) or any(not isinstance(x, str) for x in values)
                or len(values) != len(set(values)) or set(values) - CANONICAL_SUBSKILLS):
            errors.append("routing.%s must contain distinct canonical skill names" % name)
    if errors:
        return errors
    if set(contract["required_subskills"]) - set(required):
        errors.append("routing.required lacks profile-required subskills")
    if set(required) - set(used):
        errors.append("routing.used lacks required subskills")
    handoffs = routing.get("handoffs")
    if not isinstance(handoffs, dict):
        return errors + ["routing.handoffs must be an object"]
    for name in required:
        handoff = handoffs.get(name)
        if not isinstance(handoff, dict) or handoff.get("status") != "complete":
            errors.append("routing.handoffs[%s] must be complete" % name)
    if contract["profile"] == "strict":
        # Adapt only the copied input to the legacy handoff field validator. The
        # ledger's tier is informational and never selects a v2 path or gate.
        adapted = json.loads(json.dumps(work_item))
        adapted["routing"]["tier"] = "L3"
        adapted["routing"].pop("observability", None)
        scenario = adapted["routing"]["handoffs"].get("scenario-standards")
        if isinstance(scenario, dict):
            scenario["tier"] = "L3"
        errors.extend(validate_routing(adapted, True))
    return errors


def check_v2(ledger, coverage=False):
    """Validate one versioned path, including the evidence coverage gate.

    Both CLI commands use this function: a ledger cannot pass state checks while
    missing profile evidence, or pass coverage with illegal transitions.
    """
    errors = []
    wi, events = ledger.get("work_item"), ledger.get("events")
    if not isinstance(wi, dict) or not isinstance(events, list) or not events:
        sys.stderr.write("ERROR v2 requires a work_item object and non-empty events array\n")
        return 1
    if any(not isinstance(ev, dict) for ev in events):
        sys.stderr.write("ERROR every v2 event must be an object\n")
        return 1
    if not isinstance(wi.get("routing", {}), dict):
        sys.stderr.write("ERROR work_item.routing must be an object\n")
        return 1
    try:
        final_contract = resolve_execution_path(events, wi, 2)
    except (ValueError, TypeError) as error:
        sys.stderr.write("ERROR %s\n" % error)
        return 1
    code = has_code_change(events)
    errors.extend(validate_change_class(wi, events))
    # Classification paths retain their factual checks; old tiers never select
    # gates in v2, including these two specialized paths.
    classified = json.loads(json.dumps(wi))
    if is_standalone_script(events, wi):
        classified.setdefault("routing", {})["tier"] = "L1-standalone-script"
        errors.extend(validate_standalone_script(classified, events))
    if is_status_only(events, wi):
        classified.setdefault("routing", {})["tier"] = "L1-status-only"
        errors.extend(validate_status_only(classified, events))
    errors.extend(_v2_routing_errors(wi, final_contract, code))
    revision = None
    profile = None
    decision = None
    current = None
    seen = set()
    current_evidence = {}
    counters = {name: 0 for name in BUDGET}
    last_reason = None
    last_roster = None
    requires_strict = False
    strict_fix = False
    upgraded = False
    for index, ev in enumerate(events):
        n = index + 1
        prefix = "event %d: " % n
        frm, to = ev.get("from"), ev.get("to")
        ev_profile, ev_rev = ev.get("execution_profile"), ev.get("change_revision")
        if not isinstance(ev_profile, str) or ev_profile not in PROFILE_FLOWS:
            errors.append(prefix + "execution_profile must be light, standard or strict")
            continue
        if type(ev_rev) is not int or ev_rev < 0:
            errors.append(prefix + "change_revision must be a non-negative integer")
            continue
        if index == 0:
            if frm is not None or to != "active" or ev_rev != 0:
                errors.append(prefix + "v2 starts null->active at change_revision 0")
            decision = ev.get("profile_decision")
            errors.extend(prefix + msg for msg in profile_decision_errors(ev_profile, decision))
        elif frm != current or frm is None:
            errors.append(prefix + "reachability mismatch or silent restart")
        if profile is not None and ev_profile != profile:
            upgrade = ev.get("profile_upgrade")
            ranks = {"light": 0, "standard": 1, "strict": 2}
            if ranks[ev_profile] <= ranks[profile]:
                errors.append(prefix + "execution profiles may only upgrade")
            if frm != "active" or to != "active" or ev.get("reason") != "profile-upgrade":
                errors.append(prefix + "upgrade requires active->active reason=profile-upgrade")
            if (not isinstance(upgrade, dict) or upgrade.get("from") != profile
                    or upgrade.get("to") != ev_profile
                    or not isinstance(upgrade.get("reason"), str) or not upgrade["reason"].strip()
                    or not nonempty_strings(upgrade.get("evidence"))):
                errors.append(prefix + "profile_upgrade requires matching from/to, reason and evidence")
            else:
                decision = upgrade.get("decision")
                errors.extend(prefix + msg for msg in profile_decision_errors(ev_profile, decision))
            upgraded = True
        elif "profile_upgrade" in ev or ev.get("reason") == "profile-upgrade":
            errors.append(prefix + "profile_upgrade must change the execution profile")
        profile = ev_profile
        local_wi = dict(wi, execution_profile=profile, profile_decision=decision, change_revision=ev_rev)
        try:
            contract = resolve_execution_path(events, local_wi, 2)
        except (ValueError, TypeError) as error:
            errors.append(prefix + str(error))
            current, revision = to, ev_rev
            continue
        raw_steps = ev.get("step")
        named = [] if raw_steps is None else raw_steps if isinstance(raw_steps, list) else [raw_steps]
        if any(not isinstance(s, str) or s not in STEPS for s in named) or len(named) != len(set(named)):
            errors.append(prefix + "explicit step must name distinct known steps")
            named = []
        if index == 0 and named != ["investigate"]:
            errors.append(prefix + "first event must record investigate only")
        snapshots = ev.get("evidence", {})
        if not isinstance(snapshots, dict) or set(snapshots) != set(named):
            errors.append(prefix + "evidence must have exactly one snapshot for every recorded step")
            snapshots = {}
        if revision is not None:
            if ev_rev < revision or ev_rev > revision + 1:
                errors.append(prefix + "change_revision must stay fixed or increase by one")
            if ev_rev > revision:
                if not set(named) & {"implement", "fix", "script-record", "status-record"}:
                    errors.append(prefix + "a revision advance must record the actual mutation")
                current_evidence = {s: value for s, value in current_evidence.items() if s == "investigate"}
                if "fix" not in named:
                    strict_fix = False
            if (set(named) & {"implement", "fix"}) and ev_rev == revision:
                adopted = snapshots.get("implement", {})
                if not (upgraded and profile == "strict" and adopted.get("action") == "adopt"
                        and adopted.get("actor") == "implementer" and "fix" not in named):
                    errors.append(prefix + "implementation/fix must advance change_revision; strict adoption must be explicit")
        edge = edge_key(frm, to)
        if index > 0 and edge not in contract["allowed_transitions"]:
            errors.append(prefix + "transition %s is not allowed by this profile" % edge)
        if to not in STATES:
            errors.append(prefix + "unknown state")
        if edge in {"done->active", "reviewing->active", "escalated->active"} and ev.get("explicit") is not True:
            errors.append(prefix + "rollback requires explicit:true")
        if edge == "active->active" and ev.get("reason") != "profile-upgrade":
            reason = ev.get("reason")
            if reason not in REASON_BUDGET:
                errors.append(prefix + "active self-loop requires retry, answer, fix or profile-upgrade")
            else:
                key = REASON_BUDGET[reason]
                counters[key] = counters[key] + 1 if last_reason == reason else 1
                if counters[key] > BUDGET[key]:
                    errors.append(prefix + "autonomous follow-up budget exceeded")
                if reason in ("answer", "fix") and ev.get("is_complete") is not False:
                    errors.append(prefix + "answer/fix requires is_complete:false")
                last_reason = reason
        else:
            last_reason = None
        if edge == "needs_fix->reviewing":
            counters["review_fix_rounds"] += 1
            if counters["review_fix_rounds"] > BUDGET["review_fix_rounds"]:
                errors.append(prefix + "review fix budget exceeded")
            if "fix" not in named:
                errors.append(prefix + "re-entry from needs_fix requires a recorded fix")
            strict_fix = profile == "strict"
        if frm == "active" and to in ("reviewing", "done"):
            if ev.get("is_complete") is not True:
                errors.append(prefix + "active completion requires is_complete:true")
            if ev.get("has_code_change") is not code:
                errors.append(prefix + "active completion has_code_change must match the work item")
        if edge in ("active->escalated", "needs_fix->escalated") and not ev.get("reason") and ev.get("needs_user_decision") is not True:
            errors.append(prefix + "escalation requires reason or needs_user_decision:true")
        if to == "reviewing":
            roster = ev.get("reviewers")
            expected = contract["review_requirements"]
            if not isinstance(roster, list) or not all(isinstance(x, str) for x in roster) or len(roster) != len(set(roster)):
                errors.append(prefix + "reviewers must be a distinct roster")
                roster = []
            if profile == "standard":
                if roster != expected:
                    errors.append(prefix + "standard requires exactly one indep:general reviewer")
            elif profile == "strict":
                if not set(expected or []) <= set(roster) or any(r != "self" and r not in {"indep:" + x for x in ALLOWED_LENSES} for r in roster):
                    errors.append(prefix + "strict requires self plus security and correctness reviewers")
            if edge == "needs_fix->reviewing" and profile == "standard" and last_roster != roster:
                errors.append(prefix + "standard fixes return to the original reviewer")
            last_roster = roster
        elif ev.get("reviewers"):
            errors.append(prefix + "reviewer roster belongs only on entry to reviewing")
        if profile != "strict" and any(key in ev for key in ("evaluation", "judge", "final_evaluation")):
            errors.append(prefix + "light/standard must not claim evaluator or judge dispatch")
        allowed = set(contract["required_steps"])
        if profile in ("standard", "strict") and not is_status_only(events, wi) and not is_standalone_script(events, wi):
            allowed.add("fix")
        if profile == "strict":
            allowed.add("rejudge")
        positions = {step: i for i, step in enumerate(contract["step_sequence"])}
        event_order = [positions[s] for s in named if s in positions]
        if event_order != sorted(event_order):
            errors.append(prefix + "explicit steps must follow the profile sequence")
        for step in named:
            if step not in allowed:
                errors.append(prefix + "step %s is not applicable to this profile" % step)
                continue
            snap = snapshots.get(step)
            if (not isinstance(snap, dict) or snap.get("revision") != ev_rev
                    or type(snap.get("revision")) is not int
                    or not nonempty_strings(snap.get("references"))
                    or snap.get("actor") not in contract["evidence_actors"].get(step, ())) :
                errors.append(prefix + "step %s requires current revision, source references and the correct actor" % step)
                continue
            outcome = snap.get("outcome")
            if outcome != "pass" and not (step in ("review", "judge", "rejudge") and outcome == "fail" and to == "needs_fix"):
                errors.append(prefix + "step %s must have a passing outcome" % step)
            if step in ("judge", "rejudge") and outcome == "fail" and not (snap.get("p0") is True or snap.get("p1") is True):
                errors.append(prefix + "failing judge requires a verified P0/P1")
            if step in ("judge", "rejudge") and (snap.get("p0") is True or snap.get("p1") is True) and outcome == "pass":
                errors.append(prefix + "P0/P1 cannot accompany a passing adjudication")
            if step == "review":
                if frm != "reviewing":
                    errors.append(prefix + "review result must follow entry to reviewing")
                if any(type(snap.get(key)) is not bool for key in ("p0", "p1", "requires_adjudication")):
                    errors.append(prefix + "review requires explicit p0/p1/requires_adjudication booleans")
                if snap.get("p0") is True or snap.get("p1") is True:
                    if to != "needs_fix" or outcome != "fail":
                        errors.append(prefix + "P0/P1 findings must block completion")
                elif outcome == "fail":
                    errors.append(prefix + "P2/P3 findings never block")
                if profile == "standard" and snap.get("requires_adjudication") is True:
                    requires_strict = True
                if profile == "strict":
                    reports = snap.get("reports")
                    if not isinstance(reports, dict) or set(reports) != set(last_roster or []):
                        errors.append(prefix + "strict review requires one report per reviewer in the roster")
                    else:
                        for reviewer, report in reports.items():
                            if (not isinstance(report, dict) or report.get("revision") != ev_rev
                                    or type(report.get("revision")) is not int
                                    or report.get("outcome") not in ("pass", "fail")
                                    or not nonempty_strings(report.get("references"))):
                                errors.append(prefix + "review report %s must reference the current revision" % reviewer)
                        if any(isinstance(r, dict) and r.get("outcome") == "fail" for r in reports.values()) and outcome == "pass":
                            errors.append(prefix + "failed reviewer reports cannot be collected as a passing review")
            for prerequisite in contract["step_prerequisites"].get(step, []):
                if prerequisite not in current_evidence:
                    errors.append(prefix + "%s requires current %s evidence first" % (step, prerequisite))
            if step == "fix" and edge != "needs_fix->reviewing":
                errors.append(prefix + "fix must record needs_fix->reviewing")
            if step == "rejudge" and not strict_fix:
                errors.append(prefix + "rejudge requires a strict fix round")
            if step == "validate" and to != "done":
                errors.append(prefix + "validate is recorded on the terminal done event")
            seen.add(step)
            current_evidence[step] = snap
        if to == "reviewing":
            pre_review = "evaluate" if profile == "strict" else "diff-check"
            if edge != "needs_fix->reviewing" and pre_review not in current_evidence:
                errors.append(prefix + "reviewing requires current %s evidence" % pre_review)
        if edge == "reviewing->needs_fix" and "review" not in named and not (profile == "strict" and set(named) & {"judge", "rejudge"}):
            errors.append(prefix + "needs_fix requires the failing review/judge evidence")
        if to == "done":
            if ev.get("p0") is True or ev.get("p1") is True:
                errors.append(prefix + "P0/P1 must block done")
            required_current = set(contract["required_evidence"]) - {"investigate"}
            missing = {s for s in required_current
                       if not evidence_satisfies(s, current_evidence.get(s), contract, ev_rev, last_roster)}
            if missing:
                errors.append(prefix + "missing current evidence: " + ", ".join(sorted(missing)))
            if (profile == "strict" and "implement" in required_current
                    and current_evidence.get("implement", {}).get("actor") != "implementer"):
                errors.append(prefix + "strict requires implementation or explicit adoption by implementer")
        current, revision = to, ev_rev
    if profile != wi.get("execution_profile") or decision != wi.get("profile_decision"):
        errors.append("work_item final profile/decision must match the event history")
    if type(wi.get("change_revision")) is not int or wi["change_revision"] != revision:
        errors.append("work_item.change_revision must match the final event revision")
    if requires_strict and profile != "strict":
        errors.append("conflicting/unresolved review findings require a strict profile upgrade")
    if current not in TERMINALS:
        errors.append("ledger must end done or escalated")
    if current == "done":
        missing = set(final_contract["required_steps"]) - seen
        if missing:
            errors.append("missing mandatory steps: " + ", ".join(sorted(missing)))
    if errors:
        for error in errors:
            sys.stderr.write("ERROR %s\n" % error)
        return 1
    print("%s %s profile=%s revision=%s state=%s" % (
        "COVERAGE OK" if coverage else "VALID", wi.get("id", "<unknown>"), profile, revision, current))
    return 0


def check_ledger(ledger):
    if "schema_version" in ledger and (type(ledger["schema_version"]) is not int or ledger["schema_version"] not in (1, 2)):
        sys.stderr.write("ERROR schema_version must be integer 1 or 2\n")
        return 1
    if ledger.get("schema_version") == 2:
        return check_v2(ledger)
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
                if s not in LEGACY_STEPS:
                    errors.append('event %d: unknown step "%s" (allowed: %s)' % (n, s, ", ".join(LEGACY_STEPS)))

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
    if "schema_version" in ledger and (type(ledger["schema_version"]) is not int or ledger["schema_version"] not in (1, 2)):
        sys.stderr.write("ERROR schema_version must be integer 1 or 2\n")
        return 1
    if ledger.get("schema_version") == 2:
        return check_v2(ledger, coverage=True)
    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    work_id = (ledger.get("work_item") or {}).get("id") or "<unknown>"

    # Collect explicit step fields only (no state inference): coverage checks what
    # the agent actually recorded, not what states happen to imply.
    seen = []
    for ev in events:
        s = ev.get("step")
        if isinstance(s, list):
            for x in s:
                if x in LEGACY_STEPS and x not in seen:
                    seen.append(x)
        elif s in LEGACY_STEPS and s not in seen:
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
