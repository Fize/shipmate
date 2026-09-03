# Agent Roster (Role Contract Index)

The files in this directory are **directly dispatchable role contracts**, not design documents. Each file is self-contained: it includes "When to Dispatch / MUST NOT Dispatch", copy-pasteable prompt contracts, required placeholder tables, output contracts, hard constraints, and failure modes. The orchestrator's job is to select a role, populate placeholders, dispatch the complete prompt to a subagent (fanning out in parallel when the matrix permits), and close according to the gates defined in `../SKILL.md`. Roles **do not invoke each other and do not share memory**: all state is transported by the orchestrator between injection and collection. Reading a single file is sufficient to dispatch that role; reading the entire roster upfront is not required.

## Shared Hard Gate (applies to every role; treat as a prepended clause of every dispatched prompt)

Every dispatched role MUST obey the following rules. Any violation invalidates the dispatch; the
orchestrator must re-dispatch or escalate:

1. You may only execute the phase your role is responsible for. You MUST NOT execute or trigger
   any subsequent phase on the orchestrator's behalf.
2. Before starting, check every required input. If any of the following holds, you MUST NOT guess,
   fabricate, or proceed — return `blocked`, list the missing items, and stop:
   - any required placeholder is missing or empty;
   - the previous phase produced no valid output;
   - the previous phase's verification evidence is insufficient;
   - the current phase's precondition is unmet (e.g. review requested without a
     completion-evaluator pass).
   - the runtime identity placeholder `{AGENT_NAME}` is missing, empty, or inconsistent with the
     dispatched role, lens, or judge mode.
   Standalone test/debug scripts classified as `L1-standalone-script` do not dispatch a subagent;
record the script path, actual command, exit code, outcome, and sanitized result in the ledger.
Only after promotion to `L3` may the normal role pipeline be dispatched.

Runtime identity: the orchestrator MUST assign a short, readable runtime name to every dispatch.
   Use the role id by default; add only a short qualifier or index when needed (for example,
   `independent-reviewer-security`, `completion-evaluator-2`, or `review-judge-a`). A subagent MUST
   treat the injected name as an identity label, MUST NOT rename or impersonate another instance,
   and MUST return `blocked` if the name conflicts with the prompt. Do not encode work-item ids,
   protocol steps, or review rounds into the name.
3. Anything that cannot be verified counts as failure. A "best guess" MUST NOT be used as grounds
   for approval.
4. After finishing your role's duty, MUST STOP. You MUST NOT invoke other roles, and MUST NOT
   declare the whole work item complete — terminal-state decisions belong to the orchestrator only.
5. Your output MUST conform to your role's output contract; if you cannot produce compliant
   output, return `blocked` instead of degrading to prose.

## Runtime-neutral role resolution

These role IDs are semantic identities of the workflow, not platform-specific agent names. In each dispatch, the orchestrator MUST resolve execution sources in the following sequence:

```text
Built-in subagent in current runtime matching role capability probe
  -> skills/workflow-orchestrator/agents/<role>.md canonical contract
  -> blocked/escalated
```

"Built-in subagent" refers generically to any agent provided by the current execution environment capable of receiving subagent tasks; this skill assumes no fixed registry, naming scheme, model, or API. Agent names, descriptions, model tiers, or "looks like a developer/reviewer" alone do not qualify as substitutable.

### Role-specific capability matrix

| Role ID | Built-in candidate required capabilities | Inadequate substitution grounds |
|---|---|---|
| `implementer` | Write to `{ALLOWED_PATHS}`, run `{VERIFY_COMMANDS}`, return full completion contract/JSON | Generic conversational or read-only agent |
| `completion-evaluator` | Read actual worktree, verify criteria item-by-item, return strict 9-field JSON | Trusting implementer's self-reported success |
| `self-reviewer` | Read frozen change set, return P0–P3 self-review report | Unverified high-level code summaries |
| `independent-reviewer` | Receive single lens, no peer reports, return structured independent review report | Reviewer having visibility into peer findings |
| `review-judge` | Exclusive read of all reports and source code, return strict 4-field JSON adjudication | Trusting reviewer pass or implementer self-eval |
| `fixer` | Fix only issues explicitly identified by judge, record itemized disposition and verification | Unconstrained coding agent modifying arbitrary code |

Capability probing must be conducted per role and per dispatch, verifying: complete prompt/placeholder injection, required read/write permissions, verification tools, order and barriers, writer exclusivity, reviewer anti-anchoring, stop behavior, output contracts, and verifiable evidence. Passing probe for one role does not imply qualification for another. Even if two roles utilize the same underlying agent, they must be dispatched separately with their own contracts and isolated contexts.

Canonical role files must remain platform-neutral as fallbacks for all runtimes. Adapters may normalize formats deterministically, but MUST NOT guess missing fields, convert uncertainties into successes, merge independent reports, or re-engineer reviewer outputs into evaluator/judge verdicts.

```text
resolve(role):
  candidate = discover a built-in candidate for this exact role
  if candidate is unavailable or probe fails before writing:
      record unsupported/failed and dispatch the same role's skill contract
  else:
      dispatch the canonical role contract to candidate
      if contract, permission, and evidence checks pass:
          record builtin success and continue
      if a writer touched the worktree before failing:
          freeze and inspect the complete diff and verification state
          fallback only when changes are verified in-scope and safe to continue
          otherwise escalate; do not silently fallback
      otherwise:
          record failure and dispatch the same role's skill contract
  if the fallback is unavailable or invalid:
      block/escalate; never skip or use a different role
```

Each resolution should record sanitized `role`, `source`, candidate/runtime identifier, probe, adapter, outcome, and failure reason into the optional `work_item.agent_resolution` ledger field; this does not replace evaluator, judge, `step`, or ledger validation gates.

## Role Catalog

| Role ID | File | Core Duty | Writes files | Parallel-safe | Returns |
|---|---|---|---|---|---|
| `implementer` | [implementer.md](implementer.md) | Implement code changes within boundary and prove correctness (reproduce → implement → verify) | **yes** | no, exclusive by default (N-way only with proven disjoint files) | prose report + single terminating JSON completion report |
| `self-reviewer` | [self-reviewer.md](self-reviewer.md) | Re-read changes from implementer perspective; cheap first-pass read-only self-review | NO | yes, N-way | Markdown report (P0–P3 taxonomy + overall assessment) |
| `independent-reviewer` | [independent-reviewer.md](independent-reviewer.md) | Adversarial review via single assigned lens without seeing any peer reports | NO | yes, N-way (one instance per lens, zero communication) | Markdown report (structured format ending in `pass` / `need-fix`) |
| `review-judge` | [review-judge.md](review-judge.md) | Synthesize N independent reports into a singular gate decision; re-adjudicate post-fix | NO | **no, exclusive** (one judge per work item round) | strict JSON (4 fields: passed/summary/issues_to_fix/confidence) |
| `fixer` | [fixer.md](fixer.md) | Fix only issues explicitly flagged as must-fix by judge, providing itemized dispositions | **yes** | no, exclusive (sole writer to worktree) | prose report (disposition table + verification + out-of-scope/remaining risks) |
| `completion-evaluator` | [completion-evaluator.md](completion-evaluator.md) | Determine whether work item is **actually** complete — gate against false completion | NO | yes, N-way (read-only) but single instance by default as gate | strict JSON (9 fields, including missing_items and autonomous switches) |

The two writing roles (`implementer`, `fixer`) are strictly exclusive by default. The four read-only roles can fan out in parallel, though `review-judge` and `completion-evaluator` are constrained to concurrency = 1 (gate decisions must be singular).

## Which Role to Pick

- **Need code modifications (clear acceptance criteria)** → `implementer`. If requirements are vague, clarify first.
- **Implementation done, verify whether requested items are complete** → `completion-evaluator`. Completion gate; work item cannot be marked done without it.
- **Code changes need quality gating** → `self-reviewer` + `independent-reviewer` × N (fanned out by lens, default `security` + `correctness`), dispatched **concurrently**; pass raw reports to `review-judge` for synthesis.
- **Judge decides `passed=false` with locatable issues** → `fixer`, followed by `review-judge` Mode B re-adjudication.
- **Diagnostic / research inspection only** → Dispatch read-only roles; MUST NOT dispatch file-writing roles.
- **Iteration budget reached / product decision needed / requirement changed** → Do not dispatch; escalate to human developer.

## Shared Placeholder Glossary

All `{PLACEHOLDER}`s are populated by the orchestrator:

| Semantic Group | Placeholder Names | Required Content |
|---|---|---|
| Work item facts (goal/description/constraints/criteria) | `{GOAL}` `{DESCRIPTION}` `{CONSTRAINTS}` `{COMPLETION_CRITERIA}` (evaluator); `{ITEM_GOAL}` `{ITEM_DESCRIPTION}` + `{CONSTRAINT_CONTEXT_SECTION}` `{COMPLETION_CRITERIA_SECTION}` (judge); `{WORK_ITEM_TITLE}` + `{WORK_ITEM_BODY}` + `{ACCEPTANCE_CRITERIA}` (implementer); `{WORK_ITEM_CONTEXT}` (reviewers, fixer) | Verbatim work item content without lossy paraphrasing |
| Repository path | `{REPO_ROOT}` (implementer); `{WORKTREE_PATH}` (evaluator, judge) | Absolute worktree path |
| Change set / summary | `{CHANGED_FILES}` (self-reviewer); `{CHANGE_SET}` (independent-reviewer); `{CODE_CHANGES_SUMMARY}` (evaluator, judge) | Added/modified/deleted files in current round |
| Implementer output | `{IMPLEMENTATION_RESULT}` (self-reviewer); `{IMPLEMENTER_RESULT}` (evaluator, judge Mode A) | Full implementer delivery report verbatim |
| Review reports | `{REVIEW_REPORTS}` (judge Mode A) | Verbatim concatenation of all parallel reports with source headers (`### Review Report N (Source: ...)`). MUST NOT pre-summarize |
| Prior findings / fix input | `{PREVIOUS_ISSUES}` + `{FIX_RESULT}` (judge Mode B); `{EVALUATION_RESULT}` (fixer); `{PRIOR_FINDINGS}` (implementer, first round `none`) | Verbatim previous `issues_to_fix` + fix report |
| Lens | `{LENS}` (independent-reviewer only) | Single lens name (`security` / `correctness` / `architecture` / `tests` / `performance`) |
| Runtime identity | `{AGENT_NAME}` (all roles) | Short readable name generated by orchestrator |
| Boundaries & verification | `{ALLOWED_PATHS}` `{VERIFY_COMMANDS}` (implementer only) | File boundary paths; existing test/lint/build commands |
| Subskill handoffs | `{SUBSKILL_HANDOFFS}` (implementer); `{ROUTING_CONTEXT}` (evaluator) | Structured JSON handoffs from orchestrator |
| Named concerns | `{REQUESTED_CONCERNS}` (independent-reviewer only) | Specific user-requested audit points |
| History & counters | `{TODO_LIST}` `{CONVERSATION_HISTORY}` (evaluator); `{ITERATION}` `{MAX_ITERATIONS}` (fixer) | Checklist state, exchange history, iteration counters |

## Composition & Architecture References

- Gate semantics, P0–P3 taxonomy, pass thresholds: see `../SKILL.md` and `../references/severity-and-gates.md`.
- Fan-out rules, disjoint file proofs, isolation timing: see `../references/parallel-dispatch.md`.
- State transitions, iteration limits, escalation paths: see `../references/workflow-state-machine.md`.
