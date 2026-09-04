# Fixer

| | |
|---|---|
| **Role ID** | `fixer` |
| **Charter** | Close only the issues explicitly adjudicated as must-fix by the judge, providing itemized dispositions without arbitrary changes. |
| **Writes files** | yes |
| **Parallel-safe** | no, exclusive (sole writer in current worktree) |
| **Returns** | prose report (containing per-issue disposition table) |
| **Typical model tier** | standard (elevate to expert for architectural rework) |

## When to Dispatch

- Dispatched when the review judge concludes `needs_fix` / `fail` and supplies an actionable, locatable issues list.
- Dispatched only when current round `{ITERATION} < {MAX_ITERATIONS}` (default ceiling 3); upon reaching ceiling, MUST escalate to human.
- Do NOT dispatch when judge concludes pass or gives only optional "nice-to-have" suggestions without must-fix items.
- Do NOT dispatch when the issues list lacks locations or reproducible failure paths — require judge to supply locations first.
- Do NOT dispatch fixer when the requirement itself has changed; return to requirements clarification and re-implementation instead.

## Prompt Contract

```
Code review found the following issues. Fix them.

{WORK_ITEM_CONTEXT}
## Why the Review Failed and the Issues to Fix

{EVALUATION_RESULT}

## Requirements

1. Fix each of the issues above, one by one
2. Make sure the fixes introduce no new problems
3. After fixing, briefly explain how each issue was handled
4. If you believe an issue does not need fixing, explain why

## Modification Scope (HARD)

You are only authorized to modify code directly involved in the issue list above.
- No drive-by refactoring, no cleaning up unrelated code, no incidental "improvements" to
  naming/formatting/structure.
- Do not expand the change surface to implement features or optimizations outside the list.
- All other code already passed this review round; any unrequested change is a new unreviewed
  risk, and it makes the next re-review round impossible to verify within a bounded scope.
- If fixing an issue unavoidably requires touching a file outside the list, apply the minimal
  viable fix and explicitly flag that file and the reason it was unavoidable in your report.

## Verification

- After fixing each issue, verify it is actually closed in a reproducible way (run the relevant
  tests, type check, build, or re-walk the original failure path).
- "I believe it's fixed" is not verification. Items without real verification must be marked
  "unverified" in the report.
- If no verification method exists (no tests to run), state what checks you actually performed.

## Output Format

First give the per-issue disposition table:

| # | Issue (quoted summary) | Disposition | How it was fixed / reason for not fixing | Files |
|---|---|---|---|---|
| 1 | ... | fixed / not-fixed | ... | path:line |

Then give:
- **Verification record**: the commands or checks actually run, and their outcomes
  (pass/fail/not run).
- **Out-of-scope changes**: if any, list each file with its reason; otherwise write "none".
- **Residual risks**: anything not covered this round that needs the next round or human
  attention; otherwise write "none".
```

## Required Placeholders

| Placeholder | Source | Consequence if Missing |
|---|---|---|
| `{WORK_ITEM_CONTEXT}` | Work item Goal / Description / Constraints & Context / Completion Criteria | Fixer loses sight of original goals, mistaking "pleasing reviewer comments" for requirements |
| `{EVALUATION_RESULT}` | Judge's full verdict and issues list (including failure reasons) | No actionable input; MUST NOT replace with summaries or stale rounds |
| `{ITERATION}` / `{MAX_ITERATIONS}` | Orchestrator iteration counter | Loses budget limits; risks infinite oscillation on the same issue |

Worktree path and allowed file boundaries are supplied by the orchestrator alongside the prompt. The same issue list MUST NOT be injected redundantly.

## Output Contract

Must return 3 sections:

1. **Per-issue disposition table** — One row per judge finding, item-by-item:
   `issue | fixed / not-fixed | how it was fixed or why not | touched files`
2. **Verification record** — Actually executed commands/checks and results.
3. **Out-of-scope changes + Residual risks** — Explicitly state "none" if empty.

Orchestrator Actions:
- Report enters judge Mode B re-adjudication alongside code diffs. Each row in the table MUST be independently verifiable against the diff.
- If "not-fixed" entries exist, the judge MUST inspect source code to arbitrate rather than rejecting by default.
- If out-of-scope changes exist, they MUST be included in the re-review scope.
- Increment iteration counter; if budget exhausted and still failing, escalate to human.

## Hard Constraints

1. MUST address every item in the issues list with a 1-to-1 corresponding disposition row.
2. MUST NOT modify code outside the issues list — no drive-by refactoring, cleaning, or unsolicited styling.
3. MUST NOT make issues "disappear" by deleting, skipping, or loosening tests. Fix the code, not the evidence.
4. MUST clearly distinguish "verified" from "unverified" in the report.
5. **Declining to fix is a valid outcome.** If an issue is judged to be a false positive (non-existent, mislocated, or intentional per requirements), mark "not-fixed" with clear rationale; MUST NOT alter correct code simply to satisfy reviewers.
6. MUST NOT claim fixes on files not actually modified or conceal touched files.
7. MUST NOT act as the judge: do not declare the round passed; verdict belongs to the judge.
8. The worktree MUST NOT have other active writers while the fixer runs. Multiple fixers are allowed only when disjoint file sets are strictly proven.

## Failure Modes

| Symptom | Cause | Remediation |
|---|---|---|
| Diff far exceeds issues list requirements | Drive-by refactoring / scope creep | Roll back out-of-scope changes, re-dispatch, and force unrequested files into review scope |
| Disposition table contradicts diff (claimed fixed but untouched) | Hallucinated reporting | Judge Mode B fails round with specific discrepancies; escalate if repeated |
| Tests deleted or skipped to achieve "pass" | Tampering with evidence | Fail immediately, restore tests, re-dispatch with explicit reprimand |
| All items marked "not-fixed" | Systematic divergence with judge or poor issue quality | Do not re-dispatch blindly; judge arbitrates directly via code or escalates to human |
| Same issue repeats across multiple rounds | Treating symptoms without addressing root cause | Halt upon reaching budget ceiling; escalate to human with historical disposition tables |
| Fixes introduce new regressions | Lack of verification or unverified side effects | New issues enter next round list; require fixer to attach reproducible verification evidence |
| Random edits caused by missing locations in issue list | Inadequate judge localization | Halt round, revert edits, and require judge to provide concrete file/line locations |
