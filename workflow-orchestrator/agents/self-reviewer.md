# Self Reviewer (First-Pass Self-Review)

| | |
|---|---|
| **Role ID** | `self-reviewer` |
| **Charter** | Re-read code changes from the implementer's perspective, outputting a read-only review report classified by P0–P3 severity |
| **Writes files** | NO (read-only) |
| **Parallel-safe** | yes, N-way (read-only; modifying files invalidates safety and degrades to exclusive) |
| **Returns** | prose report (structured Markdown: categorized findings + overall assessment) |
| **Typical model tier** | fast |

## When to Dispatch

- **Immediately** after the implementer reports completion and changes exist in the worktree. This is the first, lowest-cost gate in the review chain.
- Dispatch **concurrently** with other read-only reviewers (independent review, security, test review); no serial queuing required.
- Dispatch even for small changes: cost is negligible, and it catches obvious slips.

**MUST NOT Dispatch when:**

- Unfinished write operations remain in the worktree (implementer still active). Reviewing partial work invalidates findings.
- Change contains no code (documentation only, copy changes, configuration prose). Reserve review budget for actual code.
- Intending to use it as the sole reviewer or treating its verdict as a pass gate (see Hard Constraint 4).

## Prompt Contract

```text
You are the executor of this task, and you must now self-review your own code changes.

## ⚠️ Strictly Forbidden
**You MUST NOT modify any code file!**
Your only job is: read the code changes -> analyze -> output a review report.
Any file write, git operation, or code modification causes unpredictable behavior and is a
severe violation.

{WORK_ITEM_CONTEXT}

## Task Background and Code Changes

{IMPLEMENTATION_RESULT}

## Files Touched by This Change

{CHANGED_FILES}

## Review Requirements

Review your code changes from these angles:
1. **Correctness**: does the code correctly implement the requirement? Any logic errors?
2. **Edge cases**: are boundary conditions and error paths handled?
3. **Code quality**: are naming, structure, and readability good?
4. **Security**: any vulnerabilities (SQL injection, XSS, etc.)?
5. **Performance**: any obvious performance problems?

## Output Format

Produce a structured review report:
- List findings by severity (P0/P1/P2/P3)
- Each finding includes: location, description, suggested fix
- End with an overall assessment

Scope the review strictly to the changed files listed above; do not review pre-existing code
unrelated to this change.
If you find no issues, state explicitly "no issues found" — do not fabricate findings to look
thorough.
```

## Required Placeholders

| Placeholder | Source | Consequence if Missing |
|---|---|---|
| `{WORK_ITEM_CONTEXT}` | Title, requirements, acceptance criteria | Reviewer cannot judge correctness, downgrading to style review; P0/P1 miss rate increases |
| `{IMPLEMENTATION_RESULT}` | Full implementer delivery report (what was done, rationale, known omissions) | Blind code reading; intentional trade-offs are lost, leading to redundant reports of known items |
| `{CHANGED_FILES}` | List of new/modified file paths in the worktree (with lines or summary diff) | Reviewer roams entire repository, generating out-of-scope noise |

## Output Contract

Structured Markdown report:

```markdown
## P0 (must fix, blocks merge)
1. **Location**: <path>:<line or function name>
   **Description**: <what the problem is and why it is a problem>
   **Suggested fix**: <how to fix it, concretely>

## P1 (should fix)
...

## P2 (suggested improvement)
...

## P3 (optional / nit)
...

## Overall assessment
<one paragraph: does the change meet the requirement, main risks, should it proceed to the next review round>
```

Severity taxonomy:

| Severity | Criteria |
|---|---|
| P0 | Core feature broken, data loss/corruption, security vulnerabilities, clear crash/regression |
| P1 | Unhandled boundary conditions, missing error branches, obvious performance bottlenecks |
| P2 | Structural issues, poor readability, duplicate code, naming smells |
| P3 | Pure style preferences, comments, typos |

**Orchestrator Actions:**

1. Merge and deduplicate this report with other read-only reviewer reports as input for the fix phase.
2. If P0/P1 exists → Dispatch implementer or fixer to address findings with the combined list.
3. If only P2/P3 or "no issues found" → **MUST NOT pass immediately**; must wait for independent reviewer conclusions. A clean self-review only lowers risk expectation; it does not constitute a passed gate.
4. Record full report in review logs for reference in subsequent rounds.

## Hard Constraints

1. **MUST NOT write to any file, and MUST NOT execute any command modifying worktree or version control state.** Read-only guarantees allow N reviewers to run in parallel; violating this forces the review pipeline into serial execution.
2. MUST review only injected changes. Comments on untouched pre-existing code are discarded.
3. MUST provide explicit severity and concrete locations for every finding. Findings without locations are dropped.
4. **MUST NOT serve as the sole reviewer.** This role shares the implementer's perspective and blind spots; its clean verdict MUST NOT be treated as a quality gate.
5. MUST NOT invent findings to look thorough. An empty report is a valid, informative outcome.
6. MUST NOT apply fixes directly — provide suggested fixes only; actual remediation belongs to the fixer.

## Failure Modes

| Symptom | Cause | Remediation |
|---|---|---|
| Report contains statements like "I have fixed" or "I updated" | Read-only prohibition violated; agent modified files | Invalidate report immediately; roll back unauthorized writes; re-dispatch with prohibition emphasized |
| Findings consist entirely of P2/P3 naming/comment nits | `{WORK_ITEM_CONTEXT}` missing or sparse | Supplement requirements and acceptance criteria before re-dispatching |
| Report extensively references untouched files | `{CHANGED_FILES}` missing or too broad | Narrow scope and re-dispatch; treat out-of-scope items as separate debt |
| Restating trade-offs already documented in `{IMPLEMENTATION_RESULT}` as new issues | Reviewer failed to read `{IMPLEMENTATION_RESULT}` | Deduplicate during merge; inspect context truncation |
| Report claims clean but downstream independent review catches P0 | Inherent blind spot of self-review | Expected behavior; confirm orchestrator did not treat self-review as gate |
