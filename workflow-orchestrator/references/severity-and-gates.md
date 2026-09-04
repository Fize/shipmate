# Severity & Gates

All role agent evaluations must be comparable. Comparability requires: **the same severity taxonomy + the same gate rule + the same evidence threshold**.
This document is the sole source of truth for these standards. If any role defines "severity" independently, the workflow loses convergence.

---

## 1. Severity taxonomy

### 🔴 P0 — Security (Must Fix)

| Check item | Description |
|---|---|
| Injection | SQL injection, XSS, command injection |
| Secrets | Hardcoded secrets, keys, or passwords; leakage paths (logs, error messages, client bundles) |
| Auth / Authz | Missing or bypassable authentication, privilege escalation, unauthorized access |
| Arbitrary code execution | Unsafe deserialization, file upload vulnerabilities leading to remote code execution |

### 🟠 P1 — Correctness (Strongly Recommended)

| Check item | Description |
|---|---|
| Branch coverage | Logic does not cover critical branches |
| Boundary / Types | Null pointers, off-by-one, boundary overflows, type mismatches |
| Concurrency | Asynchronous race conditions, unhandled exceptions in concurrency |
| Error handling | Swallowing critical exceptions (empty catch blocks, indiscriminate fallback) |
| External calls | Missing timeouts, lack of backoff/retry |
| Data consistency | Missing database transactions, lack of optimistic locking on state updates |

### 🟡 P2 — Performance (Recommended)

| Check item | Description |
|---|---|
| N+1 | N+1 database queries |
| I/O inside loops | Executing database or network calls inside loops |
| High data volume | Missing pagination or stream handling for large datasets |
| View layer | Unnecessary redundant re-rendering |
| Bundle size | Introducing excessively large dependencies |

### 🟢 P3 — Maintainability (Consider)

| Check item | Description |
|---|---|
| Function granularity | Functions overly long or handling multiple responsibilities (do NOT force arbitrary splitting) |
| Comments | Critical decision points missing "why" explanations |
| Dependency direction | Inverted or improper module dependencies |
| Abstraction | Unnecessary indirection layers |

### ⚪ Style

- Raised **only when** severely conflicting with established project conventions and harming readability.
- MUST NOT raise personal taste preferences: quote style, arrow functions vs regular functions, naming flavors, whitespace formatting.
- If no objective project convention exists, style comments MUST NOT be raised.

---

## 2. The gate rule

> **P0/P1 block. P2/P3 never block.**

There is only one gate rule; no local role variations are permitted.

**Convergence Proof**: If the gate accepts P2/P3, the workflow never terminates — any codebase can produce endless "advisory" suggestions, resulting in an infinite fix → re-review → new P2 → fix loop where token budget is exhausted without quality gains. P0/P1 forms a finite set (objective attack vectors or behavioral defects); P2/P3 is an open set (taste has no lower bound). Gates must be anchored to finite sets.

**P2/P3 must be reported, not silently discarded**: They appear in a dedicated section of the review report for human triage. "Non-blocking" ≠ "unimportant"; non-blocking simply means it does not enter pass/fail arithmetic.

| Severity | Counted in gate | Appears in report | Deciding authority |
|---|---|---|---|
| P0 | yes | yes (pinned to top) | Must fix, no waiver |
| P1 | yes | yes | Must fix, unless explicit human waiver |
| P2 | **no** | yes (dedicated section) | Human |
| P3 | **no** | yes (dedicated section) | Human |
| Style | no | only when matching ⚪ conditions | Human |

---

## 3. Evidence requirements

A finding qualifies to block a gate only when it is **locatable**. Findings that cannot be located receive `UNVERIFIED` status and MUST NOT participate in gate calculations.

| Severity | Required evidence | Verified by | Handling when evidence is inadequate |
|---|---|---|---|
| P0 | file + line, plus **concrete attack path or failure path** (input → propagation → impact) | Review judge reads code independently | Downgraded to `UNVERIFIED`, non-blocking; recorded in report with "requires human review" |
| P1 | file + line, plus trigger conditions (which branch / input / timing) and expected vs actual behavior | Review judge reads code independently | Downgraded to `UNVERIFIED`, non-blocking; recorded in report |
| P2 | location + observable cost (order of magnitude, loop iterations, scale assumptions) | Submitting reviewer | Retained in report, marked as speculative |
| P3 | location + one-sentence rationale on why it is worth changing now | Submitting reviewer | Retained in report |
| Style | location + reference to violated project convention | Submitting reviewer | Dropped if no objective convention anchor exists |

**Verification Discipline (Hard constraints for Review Judge)**:

1. MUST NOT decide based solely on textual reviewer descriptions. For each issue raised, MUST actively inspect the corresponding code file to confirm the defect exists.
2. When implementer or fixer claims "fixed" / "verified" / "no issue", MUST inspect the code directly; MUST NOT trust self-reports.
3. Focus audit on security, correctness, and boundary conditions.
4. Judgment outcomes MUST include confidence levels; low confidence pass signals require human escalation.

---

## 4. Iteration budgets

Single source of truth — the orchestrator checks this table exclusively:

| Scenario | Maximum limit | Action upon exhaustion |
|---|---|---|
| autonomous answer follow-up (clarifications) | 2 consecutive times | Escalate to human |
| autonomous fix follow-up (post-fix evaluation) | 2 consecutive times | Escalate to human |
| review fix rounds (review → fix → rejudge loop) | 3 rounds (default) | Escalate to human |

**Why limits are mandatory**: Fixer and judge are independent decision sources. They can perpetually disagree on whether a defect is fixed — the fixer believes the change is sufficient while the judge believes the root cause remains unaddressed. Without a hard budget limit, the loop oscillates indefinitely, exhausting budget without achieving convergence. A hard limit transforms divergence into an explicit event handed to the only authority with discretion: the human developer.

**Escalation Contract** (MUST supply all items upon exhaustion):

1. Remaining unresolved findings list with severity and evidence.
2. Summary of attempted fix rounds: what was modified each round and why the judge rejected it.
3. Explicit options, e.g.: `Accept current state with waiver for P1-x` / `Proceed with Plan A` / `Rollback current change` / `Split into new work item`.

MUST NOT silently pass. MUST NOT continue looping past the budget limit.

---

## 5. Gate anti-patterns

| Anti-pattern | Why it is fatal |
|---|---|
| Softening P0 to P1/P2 to pass the gate | Replaces actual fixes with taxonomy cheating; if severity is negotiable, the standard is void |
| Reviewer claims "clean" and passes without independent judge review | Self-reporting is not evidence; common-source bias between implementer and reviewer |
| Treating "no defects found" as "verified correct" | Not found = unreached or overlooked; neither proves correctness |
| Blocking on unlocatable findings | Unlocatable → unfixable → gate never passes |
| Allowing P2/P3 into pass/fail gates | Open-ended set as exit condition = infinite loop |
| Trying "one more round" after budget exhaustion | Negotiable limits are not limits; re-introduces oscillation |
| Escalating with vague statements like "cannot fix" | Lacking specific findings and actionable options forces humans to rebuild context from scratch |
