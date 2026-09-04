# Ledger Format (Workflow Ledger JSON)

The workflow ledger is the **single source of truth** for a work item's lifecycle. The coding
agent records every state transition as an event in the ledger; the scripts in `../scripts/`
read it to validate, visualize, and render reports. This document is the authoritative schema.

- Supply a ledger JSON path to the validation scripts when checking a workflow run.
- The state machine this format encodes: [`workflow-state-machine.md`](workflow-state-machine.md)
  — a single-layer, 5-state machine (`active` / `reviewing` / `needs_fix` / `done` / `escalated`).
- Gate/budget rules the validator enforces: [`severity-and-gates.md`](severity-and-gates.md).

---

## Default ledger location

Ledgers are stored **system-level, out of the project tree** (they are process records, not
deliverables), organized as one directory per project:

```
${XDG_DATA_HOME:-$HOME/.local/share}/dev-workflow/
├── tasks.json                 global structured task index
├── tasks.html                 global aggregated report
├── <project-name>/            readable project directory, no hash
│   └── <work-item-id>.json    one ledger per task (agent-recorded state machine)
└── ...
```

- `<project-name>` is the basename of the project root — the nearest `git rev-parse
  --show-toplevel`, falling back to the current working directory for non-git projects.
- Multiple tasks share their project's directory, one ledger file each. Name the file after the
  work item id (e.g. `wi-42.json`) so it is self-describing.
- The global `tasks.json` and `tasks.html` include all readable projects, so the HTML is the
  historical overview rather than a project-local page. If two projects have the same directory
  name, they share a project bucket by design.

How the scripts use this layout:

- `validate_workflow.py` and `visualize_workflow.py` operate on the current project's ledger
  when an explicit path is supplied (or when exactly one ledger exists); with multiple ledgers
  they list the files and ask for an explicit path.
- `index_workflow.py` scans every project directory and generates the global `tasks.json` at the
  workflow root. Its shape is `projects[]` plus a flattened `tasks[]` list.
- `render_workflow.py --all` scans every project and generates the global `tasks.html` at the
  workflow root. It embeds the global data into the template; the browser does not fetch JSON.

---

## Top-level shape

```jsonc
{
  "work_item": { "id": "wi-1" },
  "events": [ /* one object per state transition, in order */ ]
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `work_item` | object | yes | Identifies the work item and carries optional task metadata (below). |
| `work_item.id` | string | yes | Work item identifier, shown in report titles and validator output. |
| `events` | array | yes | Ordered list of state transitions for the work item. |

### Optional `work_item` metadata (for the task list / statistics)

The scripts ignore these fields, but the HTML report and task index surface them, and they are
what a "what did I work on this week" summary needs. Record them when starting a work item:

| Field | Type | Meaning |
|---|---|---|
| `title` | string | One-line human summary of the task (shown in the task list). |
| `kind` | string | Task category for statistics, e.g. `feature` / `bugfix` / `refactor` / `chore` / `standalone-script`. |
| `started_at` | number | Unix epoch seconds when the agent started the item. Falls back to the first event's `t`. |
| `completed_at` | number | Unix epoch seconds when the item reached `done`. Falls back to the last `done` event's `t`. |
| `changed_files` | array of string | Files touched by the task (for change-scope stats). |
| `routing` | object | Structured subskill-routing decision and handoffs. Required when any event declares `has_code_change: true`; status-only may use tier `L1-status-only` without subskill handoffs. |
| `agent_resolution` | array | Optional, sanitized record of built-in-versus-skill role resolution attempts; observational only and never a gate. |
| `change_class` | string | `none`, `status-only`, `standalone-script`, or `code`; explicit classification distinguishes independent scripts and no-behavior changes from code changes. |
| `status_only` | object | Required for `change_class: "status-only"`; records the reason and all promotion checks, which must be explicitly false. |
| `standalone_script` | object | Required for `change_class: "standalone-script"`; records the arbitrary script path, actual command, exit code, outcome, result, and promotion checks. |

### `work_item.routing` schema

The routing object makes subskill invocation machine-visible. A prose reference such as
"follow scenario-standards" does not satisfy this metadata.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `scenario` | string | yes for code changes | `greenfield` / `feature` / `bug-fix` / `refactor` / `deploy` |
| `phase` | string | yes for code changes | `requirement` / `architecture` / `backend` / `frontend` / `devops` |
| `tier` | string | yes for routed changes | `L1` / `L1-status-only` / `L1-standalone-script` / `L2` / `L2-observability` / `L3`; L3 is the default for logic or dependency changes |
| `architecture_required` | boolean | yes for code changes | Whether architecture-principles is required for this item |
| `required` | array of string | yes for code changes | Required subskills; always includes `scenario-standards` and `coding-tactics`; L3 also includes `qa-suite`; architecture-required items include `architecture-principles` (legacy names accepted for compatibility) |
| `used` | array of string | yes for code changes | Subskills actually routed; must contain every item in `required` |
| `handoffs` | object | yes for code changes | One complete structured handoff per required subskill |
| `observability` | object | required when `tier` is `L2-observability` | Safety classification for lightweight log/metric/trace-only changes (schema below) |

Each required handoff must contain `status: "complete"` and the fields below. The `scenario-standards`
handoff also includes `tier`, which MUST match `routing.tier`.

- `scenario-standards`: `tier`, `scenario`, `phase`, `scope_boundary`, `affected_files`, `acceptance_criteria`, `non_goals`.
- `coding-tactics`: `method` (`tdd`, `bdd`, `api-first`, `security-first`, or `direct`) and a non-empty `checkpoints` array.
- `qa-suite`: `project_profile`, non-empty `test_types`, `frameworks`, and `verify_commands` arrays, plus `gaps`.
- `architecture-principles`: non-empty `decision`, plus `principles_applied`, `tradeoffs`, and `open_questions` arrays.

When `change_class` is `standalone-script`, `work_item.kind` MUST be `standalone-script` and
`work_item.standalone_script` MUST contain non-empty `path`, actual `run_command`, integer
`exit_code`, `outcome` (`pass`, `fail`, or `reproduced`), non-empty sanitized `result`, and exactly
these boolean `promotion_checks`: `production_code_changed`, `dependency_changed`,
`dependency_used`, `persistent_side_effect`, `api_touched`, `security_touched`,
`concurrency_touched`, `deployment_touched`, `ci_touched`, `explicit_project_test_request`, and
`uncertain`. Every check must be present and `false`; missing, invalid, or true checks promote the
work to L3. The path may be anywhere; it is not restricted to a repository directory. A standalone
script does not require subskill routing, tests, or review, but it must record an actual run and
pass ledger/coverage validation. Adding or using a dependency, changing production code, or
uncertain scope is never standalone-script.

When `change_class` is `status-only`, `work_item.changed_files` MUST be non-empty and
`work_item.status_only` MUST contain exactly `reason` plus these boolean fields:
`behavior_changed`, `control_flow_changed`, `public_api_changed`, `data_model_changed`,
`dependency_changed`, `import_changed`, `auth_security_changed`, `concurrency_changed`,
`transaction_changed`, `retry_changed`, `external_call_changed`, `explicit_test_request`, and
`uncertain`. `reason` must be `format`, `text`, `comment`, or `version-metadata`; every boolean
must be present and `false`. Dependency or import changes, including adding or newly using a
package, are not status-only and must be routed as `L3`.

When `tier` is `L2-observability`, `routing.observability` is required and MUST contain exactly
these fields: `kind` (`log`, `metric`, `trace`, or `mixed`), `files` (integer `1..2`), and the
boolean safety fields `control_flow_changed`, `error_path_changed`, `public_api_changed`,
`data_model_changed`, `dependency_changed`, `auth_security_changed`, `concurrency_changed`,
`transaction_changed`, `retry_changed`, `external_call_changed`, `sensitive_data_logged`,
`hot_path`, and `explicit_test_request`. Every safety field MUST be present and `false`. Any true
value, invalid type, extra field, or promotion
condition means the work MUST be promoted to `L3` (and routed through the full QA blueprint as
applicable), not recorded as `L2-observability`.

Example:

```json
{
  "routing": {
    "scenario": "feature",
    "phase": "backend",
    "tier": "L3",
    "architecture_required": false,
    "required": ["scenario-standards", "qa-suite", "coding-tactics"],
    "used": ["scenario-standards", "qa-suite", "coding-tactics"],
    "handoffs": {
      "scenario-standards": {
        "status": "complete",
        "tier": "L3",
        "scenario": "feature",
        "phase": "backend",
        "scope_boundary": ["src/api/users.ts"],
        "affected_files": ["src/api/users.ts", "src/api/users.test.ts"],
        "acceptance_criteria": ["returns a validated user response"],
        "non_goals": ["no database schema change"]
      },
      "qa-suite": {
        "status": "complete",
        "project_profile": {"language": "TypeScript", "platform": "backend"},
        "test_types": ["unit", "integration"],
        "frameworks": ["vitest"],
        "verify_commands": ["npm test"],
        "gaps": []
      },
      "coding-tactics": {
        "status": "complete",
        "method": "tdd",
        "checkpoints": ["red", "green", "refactor"]
      }
    }
  }
}
```

For code-change ledgers, `validate_workflow.py check-ledger` and `check-coverage` reject missing,
unknown, incomplete, or contradictory routing metadata. `L2-observability` routing normally lists
only `scenario-standards` and `coding-tactics` in `required` and `used`; it skips `qa-suite` and
`architecture-principles` unless promoted. Old no-code ledgers remain readable.

### Optional `work_item.agent_resolution` schema

When role dispatch considers runtime-provided subagents, record each attempted resolution in an
optional `agent_resolution` array. This metadata is runtime-neutral and must not contain secrets,
full sensitive prompts, or private credentials.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `role` | string | yes | One canonical role id: `implementer`, `completion-evaluator`, `self-reviewer`, `independent-reviewer`, `review-judge`, or `fixer`. |
| `source` | string | yes | `builtin` or `skill`; identifies the execution source, not a replacement role name. |
| `candidate` | string | yes | Sanitized or opaque runtime candidate label, or the skill contract path. |
| `probe` | string | yes | `unsupported`, `eligible`, `verified`, `failed`, or `not_needed`. |
| `adapter` | string \| `null` | yes | `identity`, a named deterministic adapter, or `null`; adapters may not invent facts or gate results. |
| `outcome` | string | yes | `verified`, `fallback`, `failed`, or `blocked`. |
| `failure_reason` | string \| `null` | yes | Sanitized reason such as `unsupported`, `probe_failed`, `contract_invalid`, `runtime_error`, or `unsafe_side_effect`. |
| `evidence` | array of string | yes | Short, sanitized facts supporting the probe/outcome. |

A candidate must be resolved independently for each role. A successful probe is not a workflow step,
and this metadata cannot replace `step`, completion-evaluator, review-judge, reviewer fan-out,
or `check-ledger`/`check-coverage` validation. Existing validators may ignore this optional metadata;
state-machine and gate semantics remain unchanged.

---

## Event object

```jsonc
{
  "t": 0,          // optional: sequence/timestamp, display ordering only
  "from": null,    // required: previous state, or null for the ledger's first event
  "to": "active",  // required: the state being entered
  // ... fields required by the specific transition, see below
}
```

### Core fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `t` | number | optional | **Unix epoch seconds** (float ok) of the transition, for display ordering and the report timeline. Legacy ledgers may use plain sequence numbers — still accepted for ordering, but the timeline will show them raw. |
| `from` | string \| `null` | **yes** | The state being left. `null` only on the ledger's very first event. |
| `to` | string | **yes** | The state being entered. Must be one of `active`, `reviewing`, `needs_fix`, `done`, `escalated`. |

### Fields required per transition

| Transition | Required fields | Notes |
|---|---|---|
| `null -> active` | — | Ledger start. There is no `pending`/`backlog` state — the ledger only exists once an agent is actively on the work item. |
| `active -> active` | `reason` ∈ `{retry, answer, fix}` | `answer`/`fix` also require `is_complete: false`. Each reason draws from its own budget (see below). |
| `active -> reviewing` | `is_complete: true`, `has_code_change: true`, `reviewers: [...]` | `reviewers` is the fan-out roster: `"self"` for self-review, `"indep:<lens>"` per independent reviewer. No duplicate `indep:<lens>` in one list. |
| `active -> done` | `is_complete: true`, `has_code_change: false` | Work finished without producing file changes, or is status-only. Status-only additionally requires `change_class: "status-only"`, `status_only: true`, and a non-empty `changed_files` list. |
| `active -> escalated` | `needs_user_decision: true` or a `reason` | Automation cannot proceed. |
| `reviewing -> needs_fix` | — | Judge found unresolved P0/P1. |
| `reviewing -> done` | — | Judge passed; MUST NOT also carry `p0`/`p1: true` (contradiction, rejected by the validator). |
| `reviewing -> escalated` | — | Fix-loop budget exhausted, or two rounds show no convergence. |
| `needs_fix -> reviewing` | `reviewers: [...]` | Re-review after a fix; consumes one `review_fix_rounds` unit. |
| `done -> active` | `explicit: true` | Rollback — MUST be an explicit, recorded decision, never silent. |
| `reviewing -> active` | `explicit: true` | Same. |
| `escalated -> active` | `explicit: true` | Same. |

### Other optional fields

| Field | Type | Meaning |
|---|---|---|
| `p0` / `p1` | boolean | Set on a `reviewing`-outcome event to flag a verified P0/P1 finding. A `true` value alongside `to: "done"` is an error (P0/P1 must block). |
| `in_flight` | number | Global concurrency telemetry; warned if > 15. |
| `role_in_flight` | object | Per-role concurrency telemetry; per-role > 6 warns, > 10 errors. |
| `step` | string \| string[] | **Protocol step(s)** this event records (see "Protocol Steps" below). Optional but strongly recommended — it is what the `check-coverage` subcommand and the live dashboard use to prove no mandatory gate was skipped. A single event may cross several steps (e.g. `active -> reviewing` closes `evaluate` and opens `review`), so a list is allowed. |
| `evaluation` | object | On `active -> reviewing`: a snapshot of the `completion-evaluator` result, e.g. `{"is_complete": true, "confidence": 0.95}`. Optional; `check-coverage` warns when absent. |
| `judge` | object | On `reviewing -> done` (and after fix rounds): a snapshot of the `review-judge` result, e.g. `{"passed": true, "round": 1}`. Optional; `check-coverage` warns when absent. |
| `change_class` | string | Event classification; status-only terminal events must repeat `"status-only"`. |
| `status_only` | boolean | Must be `true` on a status-only terminal event; this is a state-recording path, not a review gate. |
| `standalone_script` | boolean | Must be `true` on a standalone-script terminal event; this is an execution record, not a review gate. |

---

## Budgets

| Budget | Cap | Consumed by |
|---|---|---|
| `exec_retries` | 3 | `active -> active`, `reason: retry` |
| `answer_follow_up` | 2 (consecutive) | `active -> active`, `reason: answer` |
| `fix_follow_up` | 2 (consecutive) | `active -> active`, `reason: fix` |
| `review_fix_rounds` | 3 | `needs_fix -> reviewing` |

Full rationale: `severity-and-gates.md` §4.

---

## Writing a valid ledger (agent guidance)

- Record every transition, in order. Do not skip states — a fan-out that leads to a fix round
  must show `active -> reviewing -> needs_fix -> reviewing -> done`, not a shortcut.
- Start the ledger at `active`, not at some pre-work "pending"/"backlog" state — anything before
  an agent is actively on the item is outside this ledger's scope (that is the concern of
  whatever task-tracking system assigned the work, not of this methodology).
- Always include `reviewers` on every event that enters `reviewing` (fresh fan-out or re-review),
  with one `self` and one or more distinct `indep:<lens>` entries.
- Always include `is_complete` (and `has_code_change` when complete) on `active`-outgoing events
  that leave the self-loop — this is what the validator uses to route the branch.
- Never rewind silently — any `-> active` rollback from `done`/`reviewing`/`escalated` MUST carry
  `explicit: true`.

After writing the ledger, run `python3 ../scripts/validate_workflow.py check-ledger <ledger.json>`
to verify it against the state machine and the gate/budget rules, and
`python3 ../scripts/validate_workflow.py check-coverage <ledger.json>` to prove no mandatory step
was skipped.

---

## Protocol Steps

The 5-state machine records *what happened*; the **step** field records *which gate was hit*.
Together they make skips detectable: `check-ledger` validates the states, `check-coverage`
validates the gates.

### Step list (canonical order)

| Step | Description | Conditional? |
|---|---|---|
| `investigate` | Understand repository context before editing | no |
| `implement` | Make the code change (reproduce → implement → verify) | no (code-change flows) |
| `evaluate` | `completion-evaluator` gate passes | no |
| `review` | `self-reviewer` + `independent-reviewer` fan-out | no (code-change flows) |
| `judge` | `review-judge` Mode A merges reports | no (code-change flows) |
| `fix` | `fixer` closes judge's must-fix issues | yes (only after `needs_fix`) |
| `rejudge` | `review-judge` Mode B after a fix | yes (only after a fix round) |
| `final-evaluate` | Final `completion-evaluator` pass before `done` | no (code-change flows) |
| `script-record` | Record an actual standalone test/debug script run without dispatching code agents | no (standalone-script flow) |
| `status-record` | Record an explicitly verified no-behavior file change without dispatching code agents | no (status-only flow) |
| `validate` | `check-ledger` exits 0 before declaring completion | no |

### Recording steps

Each event may carry a `step` field naming the step(s) it records — a single string, or a list when
one transition closes one gate and opens the next. Gate steps should also carry their evidence
snapshot (`evaluation` / `judge`), e.g.:

```jsonc
{ "t": 1755000010, "from": "active", "to": "reviewing",
  "is_complete": true, "has_code_change": true,
  "step": ["evaluate", "review"],
  "evaluation": { "is_complete": true, "confidence": 0.95 },
  "reviewers": ["self", "indep:security", "indep:correctness"] },
{ "t": 1755000040, "from": "reviewing", "to": "done",
  "step": ["judge", "final-evaluate"],
  "judge": { "passed": true, "round": 1 } }
```

A minimal status-only ledger records the changed file and explicit promotion checks:

```json
{
  "work_item": {
    "id": "wi-doc-1",
    "change_class": "status-only",
    "changed_files": ["docs/usage.md"],
    "status_only": {
      "reason": "text",
      "behavior_changed": false,
      "control_flow_changed": false,
      "public_api_changed": false,
      "data_model_changed": false,
      "dependency_changed": false,
      "import_changed": false,
      "auth_security_changed": false,
      "concurrency_changed": false,
      "transaction_changed": false,
      "retry_changed": false,
      "external_call_changed": false,
      "explicit_test_request": false,
      "uncertain": false
    }
  },
  "events": [
    {"from": null, "to": "active", "step": "investigate"},
    {"from": "active", "to": "done", "is_complete": true,
     "has_code_change": false, "change_class": "status-only", "status_only": true,
     "step": ["status-record", "validate"]}
  ]
}
```

**Convention for steps without their own transition.** Some steps (e.g. `implement`, `validate`)
complete *inside* a state and have no dedicated transition. Record them on the transition that
proves them done:

- `implement` rides on `active -> reviewing` (that transition can only fire once implementation is
  finished), so a typical L3 entry is `"step": ["implement", "evaluate", "review"]`.
- `validate` rides on the final `-> done` event (the ledger is validated before completion is
  declared), e.g. `"step": ["rejudge", "final-evaluate", "validate"]`.

### Coverage rule

`check-coverage` asserts the mandatory steps for the work item's flow are all present:

| Flow | Mandatory steps |
|---|---|
| code change | `investigate`, `implement`, `evaluate`, `review`, `judge`, `final-evaluate`, `validate` |
| standalone test/debug script | `investigate`, `script-record`, `validate` |
| status-only file change | `investigate`, `status-record`, `validate` |
| no file change | `investigate`, `evaluate`, `validate` |

`fix` / `rejudge` are conditional and checked implicitly (a `needs_fix` round without a matching
`fix` step is a state error, not a coverage error). A ledger with **no** `step` fields at all is
reported as un-checkable (exit 2), which is the prompt to backfill the fields — the whole point of
this field is to make skipping a gate visible rather than silent.
