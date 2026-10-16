# Workflow State Machine

## Versioned applicability

Schema v2 retains the five states but resolves legal edges and evidence from the selected
[execution profile](execution-profiles.md). The tables below describe the legacy/strict full
pipeline unless qualified here. No-version and explicit v1 ledgers keep their original checks.

| V2 path | State trace | Gates |
|---|---|---|
| light | active → done | Current implement, verify, main diff-check and validate evidence; code edits are legal on this edge |
| standard | active → reviewing → done | Main verification/diff-check before entry, one independent general reviewer, current passing review before done |
| standard repair | reviewing → needs_fix → reviewing → done | Narrow main fix advances revision, repeat verification/diff-check and return to same independent reviewer |
| review/fix escalation (standard/strict) | needs_fix → escalated | Review/fix budget exhausted or a required human decision remains; record a reason or needs_user_decision=true |
| strict | active → reviewing → done, optional needs_fix loop | Completion-evaluator before review, self plus independent security/correctness reviews, judge, final evaluator |
| explicit script/status record | active → done | Existing factual classification/run evidence and three-step path |

V2 active → done with code is valid only for light. Standard/strict cannot use that shortcut.
Profile upgrades use active → active, reason=profile-upgrade, reason/evidence/new complete decision,
without resetting budgets. Only monotonic upgrades are legal. An explicit rollback from reviewing
to active may precede an upgrade; it does not erase prior findings. Mutation revisions advance;
verification/review snapshots for the old diff cannot pass current gates. Strict post-fix current
evidence uses Mode B rejudge and final evaluation as described in execution-profiles.md.
Every event records its profile/revision and explicit step evidence. A terminal state alone is
insufficient. Standard conflicting/unresolvable findings require a strict upgrade.

V2 explicitly permits `needs_fix → escalated`, including when the last permitted repair failed
and another fix would exceed the review-fix budget. Keep the unresolved findings and exhausted
budget evidence in the history; escalation never marks them fixed or resets counters. This is
a v2 edge addition: legacy v1 transitions and their validation remain unchanged.

Progress uses the final selected profile's actors, reviewer roster/report obligations and ordered
current-revision evidence requirements. Prior light/standard passes cannot display strict
implementation, verification or review as complete when the required actors or reports differ.
Validation is pending until all applicable current gates pass. After a repair, replacement fix/
rejudge gates are shown in execution order; superseded historical gates are skipped for the
current revision, rather than displayed as fresh passes.

A work item's entire lifecycle, from the moment a coding agent starts on it to acceptance,
expressed as a **single-layer, 5-state machine**: `active`, `reviewing`, `needs_fix`, `done`,
`escalated`. `done` and `escalated` are terminal.

```text
              ┌────────────┐
   null ─────>│   active   │──────────────┐
              └─────┬──────┘              │
         (self-loop: retry/               │
          answer/fix, budgeted)           │
                    │                     │
       code change  │  status-only change │  can't resolve
       + complete   │  + complete         │  autonomously
                    ▼          ▼          │
             ┌────────────┐  ┌────────┐    │
             │ reviewing  │  │  done  │<───┘
             └─────┬──────┘  └────────┘
                   │  ▲
         needs_fix │  │ re-judged
                   ▼  │
             ┌────────────┐
             │ needs_fix  │
             └────────────┘
                   │
       (any state) └──────────> escalated  (budget exhausted / needs human decision)
```

## Why one layer, not four

A multi-process runtime tracks a work item across four independent layers — macro lifecycle,
execution substates, evaluation substates, and review substates — because its scheduler,
evaluator, and review orchestrator are separate services that each need their own bookkeeping.
That is a **distributed-systems concern**: multiple processes need to agree on state without
racing each other.

A coding agent following this philosophy has none of that problem. It is a single actor writing
its own ledger; there is no cross-process race to resolve. Splitting the record into four layers
here would just be replaying an implementation detail that no longer applies, at the cost of
forcing every reader (and every future maintainer of this skill) to track four moving parts to
answer questions that only need one.

So this machine collapses the four layers down to the five states an agent actually branches on:
"am I working", "is this being reviewed", "does it need a fix", "is it done", "does a human need
to step in". Sub-phase detail that a scheduler needed (queued vs dispatched, pending vs
evaluating) carried no decision value here and was dropped entirely. Detail that IS part of the
methodology — who reviewed it, which lens, which iteration, whether a P0/P1 was found — is kept,
but demoted from a *state* to *metadata on the event that entered that state*. A parallel review
fan-out is not five different phases; it is one state (`reviewing`) with a roster attached to it.

## States

| State | Meaning | Terminal? |
|---|---|---|
| `active` | An agent is working: implementing, or running an autonomous follow-up (retry, answering a question, applying a narrow fix). | no |
| `reviewing` | Code changes exist and are under review — parallel fan-out (self + independent reviewers) and adjudication both happen inside this one state; see `parallel-dispatch.md` for the fan-out shape. | no |
| `needs_fix` | The reviewer/judge found unresolved P0/P1 issues; a fix is owed before the next `reviewing` round. | no |
| `done` | The requested outcome is verified and accepted. | **yes** |
| `escalated` | Automation cannot proceed: a budget is exhausted, or a decision needs a human. | **yes** |

## Transitions

| Edge | Meaning | Required event fields |
|---|---|---|
| `null -> active` | Ledger start. | — |
| `active -> active` | Bounded self-loop: `retry` (execution failed, retrying), `answer` (autonomous answer to a clarifying question), or `fix` (autonomous narrow fix). Each reason has its own budget. | `reason` ∈ {retry, answer, fix}; `answer`/`fix` also require `is_complete: false` |
| `active -> reviewing` | Work is complete and produced code changes. | `is_complete: true`, `has_code_change: true`, `reviewers: [...]` (non-empty fan-out roster) |
| `active -> done` | Work is complete with no file changes, an explicitly classified status-only file change, or a standalone test/debug script run. | `is_complete: true`, `has_code_change: false`; status-only requires its classification fields, standalone scripts require `change_class: "standalone-script"`, `standalone_script: true`, and run evidence |
| `active -> escalated` | The active phase cannot resolve itself: a budget ran out, or the gap needs a human decision. | `needs_user_decision: true` or a `reason` |
| `reviewing -> needs_fix` | The judge found unresolved P0/P1 issues. | — |
| `reviewing -> done` | The judge passed the change: no unresolved P0/P1 in this round. | — (a `p0`/`p1` flag on this event is a contradiction and is rejected — see the gate rule) |
| `reviewing -> escalated` | The fix-loop budget is exhausted, or two consecutive rounds show no convergence. | — |
| `needs_fix -> reviewing` | A fix was applied; re-review with a fresh roster. Consumes one `review_fix_rounds` budget unit. | `reviewers: [...]` |
| `done -> active` *(explicit)* | Re-open completed work — a human decision, not an automatic step. | `explicit: true` |
| `reviewing -> active` *(explicit)* | Send back to re-implementation instead of a fix round — a human decision. | `explicit: true` |
| `escalated -> active` *(explicit)* | Resume after a human decision. | `explicit: true` |

A standalone-script work item uses only `null -> active -> done`, with explicit `investigate`,
`script-record`, and `validate` steps. It records the actual script path, command, exit code,
outcome, and sanitized result, but never enters `reviewing` or `needs_fix` and never dispatches
subagents. Any production-code, dependency, persistent-side-effect, API, security, CI, deployment,
external-behavior, or uncertain condition promotes it to L3. A status-only work item uses only
`null -> active -> done`, with explicit `investigate`,
`status-record`, and `validate` steps. It never enters `reviewing` or `needs_fix`, and it does not
receive subagent or review gates. If any status-only risk check is true, missing, or uncertain,
reclassify it as L3 before editing. Adding, upgrading, replacing, or newly using a package is not
status-only.

Every edge not listed above is illegal. Rollback edges (`done->active`, `reviewing->active`,
`escalated->active`) additionally require `"explicit": true` on the event — the ledger MUST NOT
rewind silently; a rollback is always a recorded human decision, never an automatic one.

## Budgets

Single source of truth: `severity-and-gates.md` §4. Restated here as it applies to this machine's
edges:

| Budget | Cap | Consumed by |
|---|---|---|
| `exec_retries` | 3 | `active -> active` with `reason: retry` |
| `answer_follow_up` | 2 (consecutive) | `active -> active` with `reason: answer` |
| `fix_follow_up` | 2 (consecutive) | `active -> active` with `reason: fix` |
| `review_fix_rounds` | 3 | `needs_fix -> reviewing` |

Exceeding any cap is not itself a new state — it is the trigger for `-> escalated`. The chain
counters for `answer`/`fix` reset as soon as a *different* reason interrupts the chain (a human
input resets it too); `review_fix_rounds` counts every re-entry into `reviewing` from `needs_fix`
without resetting, since each round is spending the same fixed budget.

## Parallel review, folded into `reviewing`

The reviewer fan-out (self-reviewer + independent reviewers × N, one lens each, judged by a single
adjudicator) is unchanged in substance from `parallel-dispatch.md` and `severity-and-gates.md` —
only its *representation* changed: this model records the whole fan-out-and-judge cycle as a single
`reviewing` state, with the roster captured as the `reviewers` field on the event that entered it.
The gate rule (`P0/P1 block, P2/P3 never block`), the anti-anchoring rule (reviewers never see each
other's reports), and the lens-diversity rule (no duplicate `indep:<lens>` in one roster) all still
apply exactly as documented in those two files — they constrain what happens *inside* `reviewing`,
not which state it is.

## Recovery: resuming after an interruption

If the process building the ledger is interrupted (session end, crash), the first rule of
resuming is:

> **Re-derive state from the ledger's last event, never from memory of what you intended to do.**

The ledger is the only source of truth. Read the last event's `to` field; that is the current
state. If the last event looks incomplete (e.g. a `reviewing` entry with no matching outcome
yet), do not guess — either re-run the review step, or if truly ambiguous, escalate rather than
inventing a state that was never recorded.
