---
name: workflow-orchestrator
description: >-
  Deliver a requested software change using a risk-appropriate light, standard or strict
  execution profile. Use for implementation, bug fixes, features and refactoring, including
  verification and review. Follow scenario-standards for applicable technical checks and
  coding-tactics for the implementation method. Do not trigger for standalone investigation,
  planning, explanation or repository questions without an implementation task.
metadata:
  author: shipmate
  version: "2.0.0"
  openclaw:
    emoji: "🧭"
    requires:
      bins:
        - python3
---

# Workflow Orchestrator

## Choose the execution profile

Inspect the relevant code, call paths, existing checks and acceptance criteria before editing.
Then choose the shortest path that satisfies the actual risk and verification needs. New ledgers
use **root `schema_version: 2`** and `work_item.execution_profile`. No-version and explicit v1
ledgers retain their original tier, role and validation protocol; never silently migrate or
reinterpret them as light. Legacy `routing.tier` is informational in v2 and cannot select gates.

Keep three decisions separate: `scenario` determines applicable technical concerns,
`execution_profile` determines steps and roles, and `method` determines how to implement and
verify. A local bug fix can use `light` with TDD. Selecting Direct does not imply light.

| Profile | Admission | Required path | Mandatory subagents |
|---|---|---|---|
| `light` | Clear acceptance, inspected bounded impact, direct verification, reversible; every risk check explicitly false with evidence | investigate → implement → verify → diff-check → validate | none; main agent executes directly |
| `standard` | Ordinary bounded work without strict risk triggers; default when light admission is not established | investigate → implement → verify → diff-check → independent review → validate | one `independent-reviewer` with `general` lens |
| `strict` | Any critical risk present, or critical uncertainty remains after inspection | investigate → implement → verify → diff-check → evaluate → parallel review → judge → final-evaluate → validate | implementer, completion-evaluator, self-reviewer, independent security and correctness reviewers, review-judge; fixer on failure |

Read [execution-profiles.md](references/execution-profiles.md) for the fixed risk/admission
checks, upgrades, fix loops and evidence rules. Missing evidence is not a false risk check.
File count, line count, a test request or a Markdown extension alone never select the profile.
Behavior-changing Skill/AGENTS/CI edits require the same risk investigation as code.

Explicit `standalone-script` and `status-only` classifications retain `investigate →
script-record/status-record → validate` and their existing factual promotion checks. They never
claim a review ran. A v2 record still requires a valid profile decision. No-change work omits
`implement`; the profile's remaining verification/review gates apply.

## Scope and execution discipline

1. Execute the authorized task only. Establish scope and acceptance from the user's instructions
   and repository evidence; ask only when a material ambiguity cannot be resolved there.
2. State intended mutations and the rollback path before editing or other side effects. Obtain
   explicit authorization for irreversible actions. Preserve unrelated user changes.
3. Use `inspect/reproduce → implement → verify`. Choose verification that actually establishes
   acceptance, and report real command output. Never fabricate results or report skipped roles
   as executed. `diff-check` means inspect scope, correctness and verification sufficiency.
4. Protect secrets. Never print passwords, tokens, private keys or sensitive environment values.
5. Do not call an out-of-scope phase or create a formal artifact merely to satisfy a template.
   Record an inapplicable technical checklist item with its concrete reason.
6. Keep one writer unless file sets are proven disjoint. Read-only strict reviewers may run in
   parallel without seeing peer reports; only the judge receives all raw reports.

## Subskill routing

For light/standard, the main agent records `routing.scenario`, `phase` and `method` directly.
Load scenario/method guidance as needed; no separate routing report or role dispatch is required.
Invoke QA when the actual testing task requires strategy, setup or integration/E2E execution.
Running a relevant existing test does not itself require a QA strategy artifact.

Strict code work requires completed structured handoffs from `scenario-standards`,
`coding-tactics` and `qa-suite` before implementation. Architecture impact additionally requires
`architecture-principles`; it is also a reason to recheck the risk profile. Handoffs retain their
existing fields as documented in [ledger-format.md](references/ledger-format.md), with v2 method
also recorded at `routing.method`. Internal calls inherit the authorized scope and acceptance;
they do not repeat a confirmation already supplied by the user. Method selection and execution
remain separate decisions. Missing required strict handoffs block implementation.

## Roles and gates

Load a role contract only when that profile requires its dispatch. The shared dispatch and identity
rules in [agents/README.md](agents/README.md) apply to actual subagents, not to main-agent work in
light/standard. Record who actually performed each step. The main agent can implement, verify,
self-check and fix a standard item; its independent reviewer provides a fresh review of the current
revision. Completion-evaluator, separate self-reviewer, review-judge and fixer roles are mandatory
only in strict (and in the unchanged legacy code pipeline).

For strict, preserve the barriers: implementer → evaluator pass → self-review plus independent
security/correctness reviews → judge → optional exclusive fixer → Mode B rejudge → final evaluator.
All dispatched roles receive their full canonical contracts and required inputs. Built-in role
resolution must pass the capability probe; otherwise use the canonical skill contract. Serial
subagent dispatch is the fallback when parallel dispatch is unavailable. If a required subagent
cannot be run, record the failure and escalate; never claim its gate passed.

**P0/P1 block completion; P2/P3 never block.** In standard, actionable review failures lead to a
narrow main-agent fix, new verification and re-review by the same independent reviewer. Conflicting
or unresolved opinions needing adjudication require an upgrade to strict. In strict, the judge
owns findings and fix acceptance. Preserve review-fix budget 3, execution retries 3, and consecutive
autonomous answer/fix budgets 2. An upgrade must not reset those counters.

## Evidence and completion

Keep an append-only event history. Every mutation advances `change_revision`; current verification
and review evidence must identify that revision. Upgrades only move light → standard → strict
(or light → strict), with reason, source evidence and a new risk decision. Keep valid existing work;
strict implementers may explicitly inspect/adopt it without claiming they originally wrote it.
After relevant edits, repeat affected checks and review gates. Earlier reports remain history and
cannot prove the new diff passed. Never rebuild the ledger to erase a failure or downgrade.

The shared resolver in `scripts/_workflow_steps.py` determines required steps, evidence, transitions,
roles and subskills for validation and progress. Omitted profile steps are `skipped` with a reason,
never `done`. The schema and examples are in [ledger-format.md](references/ledger-format.md).
Before declaring completion, both commands must exit 0:

```bash
python3 workflow-orchestrator/scripts/validate_workflow.py check-ledger <ledger.json>
python3 workflow-orchestrator/scripts/validate_workflow.py check-coverage <ledger.json>
```

The scripts use Python 3.9+ standard library; no environment setup or installation is required.
Use `self-check` to check tables, `index_workflow.py` for the task index, `serve_workflow.py` for the
live dashboard, and `visualize_workflow.py` / `render_workflow.py` for ledger-derived exports.
Start a dashboard only when it helps the requested delivery. Ledger storage and invocation details
are in [ledger-format.md](references/ledger-format.md); do not invent a separate visualization.

Return a concise result, changed-file/diff summary, actual verification commands and outcomes,
remaining material limitations, and a ✓/✗ checklist against every requested requirement. Do not
claim complete while any required item or gate is outstanding.

Further detail is loaded on demand: [execution profiles](references/execution-profiles.md),
[state machine](references/workflow-state-machine.md), [severity and budgets](references/severity-and-gates.md),
[parallel dispatch](references/parallel-dispatch.md), and [role contracts](agents/README.md).
