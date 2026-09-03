---
name: workflow-orchestrator
version: 1.0.0
description: >-
  Concrete development workflow for delivering a software change. Use this skill when the user asks
  to add a feature, fix a bug, refactor existing behavior, or complete the implementation,
  verification, review, and deployment of a change. Follow scenario-standards for the concrete scenario
  phases and checklists, while enforcing this skill's scope control, evidence-based verification,
  review gates, orchestration rules, and completion contract. Do not trigger for standalone
  investigation, planning, explanation, or repository questions without an implementation task.
metadata:
  author: shipmate
  version: "1.0.0"
  openclaw:
    emoji: "🧭"
    requires:
      bins:
        - python3
---

# Workflow Orchestrator

## Methodology Integration

workflow-orchestrator defines the discipline governing all repository work; it does not replace
task-specific methodology. When the task matches a scenario-standards scenario flow (Greenfield,
Feature, Bug Fix, Refactor, Deploy), follow that scenario flow for concrete phases and
checklists. This skill's Working Rules, Agent Orchestration, and Completion Contract remain
in force throughout.

## Mandatory Subskill Routing

`workflow-orchestrator` is the orchestration entry point for code-changing work. References to another
skill are not optional prose and do not count as invocation. First classify whether the work is an
explicit standalone test/debug script or status-only change. Standalone scripts and status-only work
record their evidence and validate the ledger without entering the code-change subagent pipeline.
Before dispatching `implementer`, the orchestrator MUST route every other code change through
`scenario-standards` and `coding-tactics`, retain the structured outputs below, and pass them to both
`implementer` and `completion-evaluator`.

For `L2-observability`, the orchestrator MUST classify the change against the observability safety
contract before choosing the lightweight path. Any promotion condition or uncertainty MUST promote
the work to `L3` before implementation.

1. **`scenario-standards` — scenario routing (mandatory for every code change).** Classify the work as
   Greenfield, Feature, Bug Fix, Refactor, or Deploy, assign `L1`, `L1-status-only`,
   `L1-standalone-script`, `L2`, `L2-observability`, or `L3`, then return `status`, `tier`,
   `scenario`, `phase`, `scope_boundary`, `affected_files`,
   `acceptance_criteria`, and `non_goals`.
2. **`coding-tactics` — implementation-method gate (mandatory for every code change).** Select TDD,
   BDD, API-First, Security-First, or Direct Implementation and return `status`, `method`, and
   non-empty `checkpoints`.
3. **`qa-suite` — test-strategy and execution gate (mandatory for L3 and explicit test requests).**
   Produce `status`, `project_profile`, `test_types`, `frameworks`, `verify_commands`, and `gaps` before
   implementation. When tests are executed (integration/E2E), include the optional `qa_report` (coverage
   matrix, execution results, regression baseline, recommendations, and `gate_verdict`). If `qa-suite`
   reports a `BLOCKED` gate verdict (via `validate_qa_gate.py`), `workflow-orchestrator` does NOT blindly halt:
   the `completion-evaluator` and `review-judge` autonomously determine whether the failure represents a
   genuine blocker requiring a `fixer` dispatch (transitioning to `needs_fix`) or can be waived/skipped
   based on task tier, scope boundary, and risk profile. `L1-standalone-script`, `L1-status-only`, and
   safe `L2-observability` work use their respective lightweight paths; a project-code test request or
   uncertain scope promotes the work to L3.
4. **`architecture-principles` — architecture gate (mandatory when `architecture_required=true`).**
   Set this flag when the work changes a public API, data model, dependency direction, technology
   choice, or system boundary. Safe `L2-observability` work skips this gate unless a promotion
   condition applies. Return `status`, `decision`, `principles_applied`, `tradeoffs`, and
   `open_questions` as required design context.

### Canonical structured handoff contract

The orchestrator MUST build a machine-visible `SUBSKILL_HANDOFFS` block containing one JSON object
per required subskill. Each object MUST use the exact fields named by that subskill's internal
routing contract, plus `status` set to `complete`; `architecture-principles` is required only when
`architecture_required=true` (which promotes observability work to L3), and `qa-suite` is
required for L3 or an explicit test request (which also promotes observability work to L3).
The block MUST be passed unchanged to `implementer`, and the equivalent `ROUTING_CONTEXT` block
MUST be passed to `completion-evaluator`. The orchestrator MUST block on missing, incomplete,
unverifiable, or contradictory routing or handoffs; it MUST NOT infer omitted fields or silently
continue. Record the same routing metadata and handoffs in the work-item ledger.

### Internal invocation mode

When a subskill is reached from `workflow-orchestrator`, it runs in **internal routing mode**: it may not
wait for a second user confirmation before returning its contracted output. Standalone invocation
keeps the subskill's interactive behavior. In particular, `coding-tactics` MUST return its selected
method directly when called by this workflow; its standalone "recommend and wait" behavior MUST
NOT stall the implementation pipeline.

`scenario-standards`'s "explicit phase only" rule applies to standalone use. When the orchestrator has
already classified the work item, it may enter the corresponding scenario phase on the user's
behalf, but it MUST NOT create an unrequested phase or artifact.

### Runtime-neutral role resolution

The role ids in this skill are semantic workflow contracts, not names of a particular agent tool.
The orchestrator MUST resolve an execution source independently for every role dispatch, using this
priority order:

```
capable built-in subagent -> skill role contract in agents/<role>.md -> blocked/escalated
```

"Built-in" means any subagent made available by the current runtime (including an installed or
host-provided agent); this skill MUST NOT assume a particular platform, registry API, model, agent
name, or tool namespace. The existence of an agent, a matching name/description, or a capable model
is not evidence that it can replace a role.

Before using a built-in candidate, the orchestrator MUST perform a role-specific capability probe
covering all of the following:

- can receive the complete role prompt, Shared Hard Gate, and required placeholders;
- has the role's required repository tools and permissions (writers may write only their scope;
  read-only roles MUST NOT write);
- can obey the role's ordering, barrier, isolation, anti-anchoring, and stop-after-role rules;
- can return the role's complete output contract, including strict JSON where required; and
- can provide verifiable evidence required by that role rather than relying on prose or inference.

A probe is not a workflow step and MUST NOT advance the ledger. Resolve roles one at a time: a
candidate that qualifies for one role is not thereby qualified for another. Generic coding agents,
reviewers, or read-only agents MUST NOT be treated as universal substitutes. A candidate that
cannot satisfy the exact role contract is `unsupported`, even if it is otherwise useful.

The six files under `agents/` remain the canonical, platform-neutral role contracts. A built-in
candidate MUST receive that contract unchanged. A deterministic adapter may normalize an otherwise
valid result into the contract only when it preserves every field, fact, location, severity, lens,
and verification claim. An adapter MUST NOT invent missing fields, convert uncertainty to success,
merge independent reports, or turn a reviewer result into an evaluator/judge result.

If a built-in probe is unsupported, dispatch fails before writing, or the runtime cannot safely
provide the required isolation, use the skill contract for the same role. Record both the failed
built-in attempt and the fallback. If a built-in writer has started and then fails after touching the
worktree—even when the touched files appear in scope—freeze the workflow and inspect the complete
working-tree diff and verification state first. Only when the orchestrator can verify that the partial
changes are in scope, understood, and safe to continue may it dispatch the same role's skill
contract; otherwise escalate without a silent fallback. Any out-of-scope or unknown change always
requires escalation. If the skill contract is also unavailable or invalid, block/escalate; never
skip or substitute a different role. Built-in selection and fallback MUST NOT change the execution
order, reviewer fan-out, gate conditions, iteration budgets, or final validation requirements.

For every attempted role resolution, record sanitized evidence in optional
`work_item.agent_resolution` ledger metadata: canonical `role`, `source` (`builtin` or `skill`),
opaque candidate/runtime label, probe status, adapter (if any), outcome, and failure reason when
applicable. This metadata is observational only; `step`, completion-evaluator, review-judge,
and `check-ledger`/`check-coverage` remain the authoritative gates.

## Execution Checklist

Every work item is a sequence of protocol **steps**. The orchestrator MUST walk them in order and
record each one as a `step` field on the ledger event that reaches it (schema:
`references/ledger-format.md`). A step may not be skipped, merged, or reordered; skipping a
mandatory step is a workflow failure that `validate_workflow.py check-coverage` reports — the check
turns "the agent forgot a gate" from a silent shortcut into a machine-visible error.

| # | Step | Meaning | Role | Gate? |
|---|------|---------|------|-------|
| 1 | `investigate` | Repository investigation | orchestrator | no |
| 2 | `script-record` | Record standalone script run | orchestrator | no (L1-standalone-script only) |
| 3 | `status-record` | Record no-behavior change | orchestrator | no (L1-status-only only) |
| 4 | `implement` | Implement change | `implementer` | no |
| 5 | `evaluate` | Evaluate completion | `completion-evaluator` | **yes** |
| 6 | `review` | Parallel review | `self-reviewer` + `independent-reviewer` × N | no |
| 7 | `judge` | Adjudication | `review-judge` Mode A | **yes** |
| 8 | `fix` | Fix defects | `fixer` (`needs_fix` only) | no |
| 9 | `rejudge` | Re-adjudication | `review-judge` Mode B (post-fix only) | **yes** |
| 10 | `final-evaluate` | Final evaluation | `completion-evaluator` | **yes** |
| 11 | `validate` | Validate ledger | `check-ledger` + `check-coverage` | **yes** |

### Protocol tiers (L1 / L1-status-only / L1-standalone-script / L2 / L2-observability / L3)

Six tiers keep the workflow proportionate while never silently dropping a gate. The tier is
chosen by objective facts — not by an agent's feeling that a change is "small".

| Tier | Applies when | Steps | Review fan-out |
|------|-------------|-------|----------------|
| **L1** no file change | Question answered or command run with no files changed | `investigate` → `evaluate` → `validate` → `done` | none |
| **L1-status-only** | One or more files changed, but the diff is only format, text, comments, or non-dependency version metadata; all risk checks are explicitly false | `investigate` → `status-record` → `validate` → `done` | none |
| **L1-standalone-script** | An independent test/diagnostic/reproduction/debug script at any path; no production-code, dependency, persistent-side-effect, CI/deployment, or security change | `investigate` → `script-record` → `validate` → `done` | none |
| **L2** legacy minor code change | Existing compatible L2 ledger; new ambiguous changes must not use this tier | `investigate` → `implement` → `evaluate` → `review`(self + 1 lens) → `judge` → `final-evaluate` → `validate` → `done` | 1 lens |
| **L2-observability** lightweight instrumentation | Safe log, metric, or trace addition under the observability contract | same lightweight flow as L2 | 1 correctness lens |
| **L3** standard (default) | Any behavior, dependency/import, API, security, concurrency, external-call, test, or uncertain change | full flow above; `fix` / `rejudge` inserted when judge returns `needs_fix` | security + correctness |

`L1-standalone-script` is a test/debug recording path, not a reduced code-review path. It records
`path`, the actual `run_command`, integer `exit_code`, `outcome`, and a sanitized `result`. It does
not dispatch `implementer`, `completion-evaluator`, reviewers, judge, or fixer, and does not invoke
`scenario-standards`, `coding-tactics`, `qa-suite`, or `architecture-principles`. It is allowed even when
this task is explicitly described as testing, because the script itself is the test/debug artifact.
Any production-code, dependency, persistent-side-effect, API, security, concurrency, transaction,
retry, CI, deployment, or uncertain condition promotes the work to L3.

`L1-status-only` is a state-recording path, not a reduced code-review path. It does not dispatch
`implementer`, `completion-evaluator`, reviewers, judge, or fixer, and does not invoke
`qa-suite` or `architecture-principles`. It requires an explicit `status_only` evidence object
with a reason of `format`, `text`, `comment`, or `version-metadata`; changed files must be listed.
Any behavior, control-flow, import/dependency, public API, data model, security, concurrency,
transaction, retry, external-call, explicit test, or uncertain condition promotes the work to L3.
In particular, adding, upgrading, replacing, or newly using a package is never status-only.

`L2-observability` still requires `scenario-standards` and `coding-tactics` (normally Direct
Implementation), but skips `qa-suite` and `architecture-principles` unless promoted.
Automatically promote observability work to **L3** for sensitive data, a hot path, control-flow or
error-path changes, public API/data model/dependency changes, external-call behavior, an explicit
test request, more than two files, or uncertainty. The L2-observability review uses self-review plus
one correctness lens; no security lens is required by default.

To choose L2-observability vs L3, answer these — any "yes" or uncertainty forces **L3**:

- Does the change log sensitive data or touch an auth/security surface?
- Is it on a hot/high-frequency path, or does it change control flow or an error path?
- Does it change a public API, data model, dependency, or external-call behavior?
- Does it change concurrency, transactions, or retry behavior?
- Is there an explicit test request, more than two files, or any uncertainty?

When in doubt, use L3. A skipped tier is a workflow failure; a downgraded tier grounded in the
checklist above is not.

## Working Rules

1. Execute only the work explicitly requested. Do not start unrequested architecture, backend,
   frontend, DevOps, documentation, or cleanup phases; surface useful out-of-scope ideas as
   suggestions instead.
2. Investigate the relevant files, dependencies, conventions, call paths, and tests before editing
   or making a technical judgment. Prefer repository evidence over memory or assumptions.
3. Verify against the codebase, configuration, tests, and documents before asking; when asking,
   state what was checked and why the repository cannot answer.
4. Confirm scope, technical direction, and acceptance criteria before irreversible work. Do not
   make product or architecture decisions on the user's behalf.
5. Prefer the smallest viable solution (YAGNI). For side effects such as database writes, file
   mutations, API calls, or deployments, state the intended effect and rollback path first.
6. Convert goals into verifiable evidence: `Reproduce or inspect -> Implement -> Verify`. Never
   claim completion from a plausible explanation or self-report alone.
7. Protect context and secrets. Never expose passwords, tokens, private keys, or sensitive
   environment values; use placeholders and environment variables for secrets.
8. Escalate deliberately. Automate only when the next step is well-defined and supported by
   evidence; pause for human input when requirements, permissions, or unresolved risk prevent a
   safe decision.

## Agent Orchestration

The work is performed by **subagents**, not by the orchestrator inline. Each role named in the
Execution Checklist — `implementer`, `completion-evaluator`, `self-reviewer`, `independent-reviewer`,
`review-judge`, `fixer` — is a **separate semantic role** with a canonical contract in
`agents/<role>.md`. The runtime may execute that contract with a capable built-in subagent or, when
no built-in candidate passes the role-specific probe, with the skill contract itself; the runtime
agent name MUST NOT replace the canonical role id. The orchestrator's job is to resolve the source,
fill its placeholders from the work item and prior outputs, and **dispatch the prompt contract**;
the subagent then does the actual implementation, evaluation, or review and returns a structured
report. The orchestrator never performs a role's work itself, and roles never call each other — all
state moves through the orchestrator.

### How to dispatch a subagent (contract structure)

Every dispatch uses the same structure, mirroring a spawn-subagent task block. The orchestrator MUST assign a short, readable runtime name before dispatching:

- **Runtime identity**: `Name: {AGENT_NAME}`. Use the role id by default (`implementer`, `fixer`, `self-reviewer`, …); add only a short qualifier when needed to distinguish instances, such as `independent-reviewer-security`, `completion-evaluator-2`, or `review-judge-a` / `review-judge-b`.
- **Task**: the role id (`implementer`, `review-judge`, …) plus the concrete work item / phase. The role id remains the contract identity; it is not replaced by the runtime name.
- **Inspect directions**: exactly which files, diffs, or prior reports the subagent MUST read.
  Never let it judge from prose alone — see each role's Verification Guidelines.
- **Output requirements**: the role's output contract verbatim (strict JSON for evaluator/judge,
  disposition table for fixer, Markdown report for reviewers).
- **Constraints (the injected "blacklist")**: the Shared Hard Gate (agents/README.md) prepended to
  every prompt, plus the role's own hard constraints — non-negotiable; any violation invalidates the dispatch.
- **Tool/skill availability**: tell the subagent which repository inspection, file-writing,
  verification, and optional integration capabilities are actually present, using the current
  runtime's names. A tool named by a role contract is a capability requirement, not a platform
  dependency; map it only to a demonstrably equivalent runtime capability, otherwise mark the
  candidate unsupported rather than guessing.

The runtime name MUST be present for every subagent dispatch, including the initial and final
`completion-evaluator`, parallel reviewers, `review-judge` Mode A/B, and `fixer`. When the same role
is dispatched more than once, append a short index or mode suffix instead of reusing an ambiguous
name. Do not encode work-item ids, protocol steps, or review rounds into the name; those remain in
the Task and workflow context. A missing name or a name that conflicts with the role, lens, or mode
is a blocked dispatch, not a reason to fall back to the bare role silently.

### Non-skippable Execution Protocol

The following is the **only legal execution flow** for this skill. The orchestrator MUST drive
the phases in exactly this order. It MUST NOT skip, merge, reorder, or substitute any phase.

**When code changes exist:**

```
active
-> implementer
-> completion-evaluator
-> self-reviewer + independent-reviewer x N
-> review-judge Mode A
-> fixer (only when judge returns passed=false)
-> review-judge Mode B (after every fixer round)
-> final completion-evaluator
-> validate ledger
-> done
```

**When no file change exists:**

```
active
-> completion-evaluator
-> validate ledger
-> done
```

**When an independent test/debug script exists:**

```
active
-> script-record
-> validate ledger
-> done
```

Standalone scripts use `active -> done` with `has_code_change:false`,
`change_class:"standalone-script"`, `standalone_script:true`, and the script execution evidence.
Repeated local runs are normal; record the run that establishes the work item's outcome. This path
never enters `reviewing` or `needs_fix`.

**When an explicit status-only change exists:**

```
active
-> status-record
-> validate ledger
-> done
```

Status-only uses `active -> done` with `has_code_change:false`, `change_class:"status-only"`,
`status_only:true`, and a non-empty changed-file list. This is the only path allowed to finish
without a completion-evaluator or review-judge; `check-ledger` and `check-coverage` remain mandatory.

**Hard rules for every phase:**

1. MUST NOT enter the next phase before the current phase completes successfully.
2. When required inputs, outputs, or verification evidence are missing, MUST NOT guess, fabricate,
   or skip. Stay in the current phase and report `blocked`, or transition to `escalated`.
3. For code-change work, without a `completion-evaluator` pass, MUST NOT enter review or `done`.
   The status-only path is the explicit exception and uses `status-record` instead.
4. For code-change work, without a `review-judge` `passed=true`, MUST NOT enter `done`.
   Status-only work must never enter review or judge.
5. For code-change work, without the final `completion-evaluator` pass, MUST NOT enter `done`.
6. Without a successful `validate_workflow.py check-ledger` run (exit code 0), MUST NOT declare
   completion to the user.
7. "Not executed", "could not verify", or "probably not needed" never count as "done" or
   "not applicable". `not applicable` requires a concrete reason grounded in task facts.
8. Any phase failure MUST NOT be silently bypassed. Record the failure, then retry, apply a
   narrowly scoped fix, or escalate to the user — nothing else.
9. Every phase MUST be recorded as a `step` field on the ledger event that reaches it (see the
   Execution Checklist above). Before declaring completion, run BOTH
   `check-ledger` (states/gates/budgets) and `check-coverage` (no skipped step); a non-zero exit
   from either is a workflow failure, not a "done".

When multiple agent roles or an automated loop are available, parallel fan-out is the required
execution detail: read-only roles run concurrently, writer roles are exclusive, and independent
results merge only in the adjudicator. Parallelism MUST NOT change the phase order or remove any
gate.

**Fallback when subagents / parallelism are unsupported.** If the runtime cannot spawn parallel
subagents or blocks on background tasks, degrade to **serial dispatch**: dispatch one subagent at a
time and wait for its report (or file write) before dispatching the next — never hang waiting on a
background notification. The same phase order, gates, and budgets MUST be preserved; only the
concurrency changes. (If even a single subagent cannot be spawned, the orchestrator MUST escalate
to the user rather than perform the role's work inline.)

### Forbidden Shortcuts

The orchestrator MUST NOT:

- dispatch reviewers before `completion-evaluator` confirms completion;
- declare completion based only on the implementer's report;
- skip self-review, independent review, or review-judge for code-change work because the change is small;
  explicit status-only work is classified before the code-change protocol and uses its own status-record path;
- let the implementer perform its own review-judge decision;
- let the fixer transition directly to `done`;
- treat a missing report as a passing report;
- treat a failed command, an unreadable file, or a missing test as evidence of success;
- mark a skipped phase as `not applicable` without recording a concrete reason;
- proceed after a role violates its output contract;
- produce the final response before every required gate has passed.

Any violation above is a workflow failure: stop execution and escalate. Do not continue from the
next phase.

1. **Execute** the requested work in the correct project directory with the relevant context.
2. **Evaluate** the result against every goal, constraint, completion criterion, and changed-file
   claim. Read the files or run checks instead of trusting the textual report.
3. If incomplete:
   - Answer an execution agent's question only when the answer is derivable from existing context.
   - Generate a narrowly scoped follow-up instruction when a missing item can be fixed safely.
   - Limit consecutive automatic answer/fix cycles to two; then stop and ask the user.
4. If code changes exist, enter review before declaring the work done (parallel form is primary):
   - **Fan out** the self-reviewer and independent reviewers × N concurrently; each independent
     instance gets exactly one lens, and no reviewer sees any other reviewer's report (anti-anchoring).
   - **Merge** all reports only in the review-judge (Mode A).
   - **Evaluate**: accept only when no verified P0/P1 issue remains.
   - **Fix loop**: `needs_fix -> fixer (exclusive) -> review-judge Mode B` → re-judge, capping the
     loop at three iterations unless the user explicitly chooses otherwise.
   - Serial fallback: when parallel fan-out preconditions fail, run first review then an independent
     review then adjudication, preserving the same gate and budget rules.
5. Escalate to the user when the review budget is exhausted, the evidence conflicts, or a
   permission/requirement decision cannot be inferred safely.
6. Mark the work complete only after verification and a final item-by-item checklist.

### Agent Orchestration index (role dispatch & gates)

The role contracts live in `agents/` (load one per dispatch):
[`agents/README.md`](agents/README.md) is the roster index.

- Role roster, dispatch matrix, and shared placeholder glossary: `agents/README.md`
- Parallel fan-out, writer exclusivity, barriers, concurrency caps, lens diversity:
  `references/parallel-dispatch.md`
- P0–P3 severity taxonomy, the single gate rule ("P0/P1 block, P2/P3 never block"), iteration budgets:
  `references/severity-and-gates.md`
- The single-layer, 5-state workflow machine (`active`/`reviewing`/`needs_fix`/`done`/`escalated`),
  rollback rules, recovery: `references/workflow-state-machine.md`

Parallelism rules of thumb:

- Read-only roles may run in parallel; any writer is exclusive unless file-sets are proven disjoint.
- Barrier only at: implement → reviewers, reviewers → judge, judge → fixer, fixer → judge (Mode B).
- Global cap 15; per-role cap 6 (hard 10); one lens per independent reviewer, never duplicate a lens
  in a batch.
- Iteration budgets: answer follow-up 2, fix follow-up 2, review fix rounds 3.

### Validation tooling

The workflow ledger is the single source of truth. The coding agent records each state transition as
an event in the ledger; the scripts below are the **only** sanctioned way to validate and visualize it.
The ledger JSON schema is defined in [`references/ledger-format.md`](references/ledger-format.md).

**Environment prerequisite:** the scripts require **Python 3.9+** and use **only the standard library**
(`json`, `sys`, `os`, `html`) — there are **no third-party packages to install**. Python 3 is bundled
with macOS and most Linux distributions, so it is available even where no other runtime is installed.

**Direct invocation — no environment setup required.** The scripts are pure standard library
(`json`, `sys`, `os`, `html`), so run them with the system `python3` directly — there is no
`pip install` step and no `uv sync` needed. A virtual environment is **optional**, only if you need
isolation from user-site packages:

```bash
# Default: run directly (no setup)
python3 scripts/validate_workflow.py self-check

# Optional isolation (only if you must avoid user-site packages):
# uv venv .venv && source .venv/bin/activate
```

Do not make environment setup a precondition for running the scripts — that friction is exactly
what leads agents to skip the validation gate.

**MUST directive:** when a workflow must be validated or visualized, the agent MUST call the scripts
below (with `python3`) — it MUST NOT hand-write mermaid, ASCII flow, or HTML that could be
derived from the ledger. The agent's job is to write an accurate ledger; rendering is the scripts' job.
Hand-authored visualizations are non-reproducible and MUST be avoided.

Available scripts (ledgers live at
`${XDG_DATA_HOME:-~/.local/share}/dev-workflow/<project>/`; the global `tasks.json` and
`tasks.html` live directly under `dev-workflow/`; see `references/ledger-format.md`.
`<ledger.json>` is optional — with exactly one ledger in the current project it is the default
target; with zero or multiple the scripts list them and ask for an explicit file):

- `python3 scripts/validate_workflow.py check-ledger [<ledger.json>]` — validate a workflow
  ledger against the encoded state machines and the gate/budget rules.
- `python3 scripts/validate_workflow.py check-coverage [<ledger.json>]` — verify no mandatory
  protocol step was skipped (anti-skip gate; exit 0 = all gates hit, 1 = a step missing,
  2 = no step data recorded).
- `python3 scripts/validate_workflow.py self-check` — validate the documented tables
  themselves are self-consistent.
- `python3 scripts/visualize_workflow.py [<ledger.json>] [--format mermaid|ascii|both]` — render
  the workflow state as a mermaid `stateDiagram` and/or an ASCII flow (terminal output).
- `python3 scripts/serve_workflow.py [--host 127.0.0.1] [--port 18929]` — **live dashboard
  (preferred)**. Starts a zero-dependency HTTP server that reads ledgers from disk on every
  request — no static pre-rendering — and serves a browser dashboard showing per-task step
  progress, current step, state, and event timeline, auto-refreshing every 10s (skipping re-render
  when data is unchanged). The final step of a
  completed workflow SHOULD start this server so the user can watch the workflow live:
  `python3 scripts/serve_workflow.py` then open the printed URL (default port 18929, an uncommon
  port chosen to avoid clashes with typical dev servers). Endpoints: `/` (dashboard),
  `/api/index` (global index JSON; optional `?from=YYYY-MM-DD&to=YYYY-MM-DD` filters tasks by
  start date), `/api/ledger/<project>/<file>` (single ledger JSON),
  `/api/health`. If a task ledger contains an optional `qa_report` (from `qa-suite`), it is
  rendered in the task detail view; if absent, the dashboard works normally with zero dependencies.
- `python3 scripts/render_workflow.py --all [--output tasks.html]` — (legacy, static) aggregate
  every ledger into one self-contained HTML page. Prefer `serve_workflow.py`; use this only for a
  snapshot to archive or share.
- `python3 scripts/index_workflow.py` — scan every project's ledgers and generate the global
  `tasks.json` under the workflow data root (also used by `serve_workflow.py`). The structured
  data includes `projects[]` and a flattened `tasks[]` list (project / id / title / kind / state /
  current_step / steps / terminal / events / times / changed files).

## Pitfalls

- Treating `follow scenario-standards` or `invoke /qa-suite` as an automatic call. A textual
  reference is advisory unless the orchestrator makes the subskill output a precondition.
- Letting standalone subskill contracts leak into internal routing. Internal calls need an explicit
  non-interactive handoff mode; otherwise `coding-tactics` can pause for user confirmation and the
  pipeline will skip or stall the implementation gate.
- Keeping scenario state, test strategy, and implementation method only in prose. Pass structured
  handoffs into the next role and record the routing decision so evaluators can detect omissions.

## Completion Contract

Before the final response, provide a concise result summary; an item-by-item checklist
(`done`/`not done`/`not applicable` with reason) for every requested goal and constraint; the exact
verification commands, tests, and files inspected; remaining risks and recommended follow-up; and,
for code changes, a concise diff summary of every changed file. Never imply code was modified when it
was only inspected. Use explicit status language — replace vague claims like "should work" or "mostly done" with concrete evidence.

## Common Failure Modes to Prevent

- Starting implementation before understanding the repository.
- Expanding a small request into an unsolicited refactor or product redesign.
- Asking the user for information already present in code or configuration.
- Declaring success because a subprocess exited successfully without a usable result.
- Treating a task as complete without checking all requested items.
- Letting an automatic follow-up loop run indefinitely.
- Allowing a failed review to disappear into a generic "looks good" summary.
