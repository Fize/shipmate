# Parallel Dispatch Rules

The orchestration layer consults this document before **every** parallel dispatch. The rules are strict and non-negotiable.

## The Sole First Principle

> **Read-only roles can run in parallel freely; any role that writes to the working tree is strictly exclusive, unless file sets are proven mutually disjoint.**

"Proven mutually disjoint" means you can enumerate the exact file paths each instance will write to before dispatch, and the sets have zero intersection. If you cannot enumerate them = not disjoint = strictly exclusive. Do not substitute proof with "they probably won't conflict."

## 1. Parallel-Safety Matrix

| Role | Writes files | Parallel-safe | Safe concurrency | Preconditions |
|---|---|---|---|---|
| `implementer` | **yes** | **no, exclusive** | 1 by default; N-way allowed only when file sets are **proven** disjoint | Working tree has no other writers; for N-way, the exact allowed file list is fixed in each instance's prompt |
| `self-reviewer` | NO | yes | 1 (cheap self-reflection; fan-out yields no benefit) | All working tree writes complete; changes are frozen |
| `independent-reviewer` | NO | yes | 2 (default `security` + `correctness`, each with a distinct lens) | Changes are non-empty and frozen; zero inter-instance communication |
| `completion-evaluator` | NO | yes (technically) | **1** (it is a gate; conflicting multi-evaluator verdicts cannot be merged) | Implementer has reported completion with evidence |
| `review-judge` | NO | **no, exclusive** | 1 | **All** review reports received; only one judge per work item |
| `fixer` | **yes** | **no, exclusive** | 1 | Judge provided must-fix list with locations; no other writers |

Key takeaway: `completion-evaluator` and `review-judge` are read-only, yet constrained to concurrency = 1. This limit stems from **semantics** (gate verdicts must be singular and definitive), not file write conflicts.

## 2. Canonical Shape

```text
                     ┌──────────────────────────────────────┐
                     │ implement (exclusive │ N-way disjoint)│
                     └──────────────────────────────────────┘
                                     │
                               ══ BARRIER ══   (Changes must be frozen)
                                     │
          ┌──────────┬───────────────┼───────────────┬──────────┐
          ▼          ▼               ▼               ▼          ▼
   self-reviewer  indep:security  indep:correctness  indep:arch  indep:tests
     (fast)         (expert)         (expert)        (expert)   (expert)
          └──────────┴───────────────┼───────────────┴──────────┘
                               ══ BARRIER ══   (Judge requires all reports for cross-check)
                                     ▼
                            review-judge  Mode A (1)
                                     │
                       pass ─────────┴───────── needs_fix
                        │                          │
                        ▼                          ▼
              completion-evaluator (1)        fixer (exclusive)
                                                   │
                                          review-judge Mode B (1)
                                                   │
                                     ┌─────────────┴─────────────┐
                                  pass                 iteration < cap ? Return to fixer
                                                       otherwise → Escalate to human
```

### Barrier Discipline

The **only** valid justification for a barrier: the next step requires **all** prior results in hand simultaneously.

| Boundary | True barrier? | Rationale |
|---|---|---|
| implement → reviewers | **YES** | Reviewing half-finished work invalidates findings; writers must complete before reviews start |
| reviewers → judge | **YES** | Synthesis and overlap counting require every report; a missing report skews confidence scoring |
| judge → fixer | **YES** | Fixer requires the adjudicated must-fix findings |
| fixer → judge Mode B | **YES** | Re-adjudication requires the complete post-fix working tree |
| self-reviewer → independent-reviewer | **NO** | Dispatched concurrently in the same batch; MUST NOT queue sequentially |
| between multiple reviewer lenses | **NO** | Zero inter-communication, no dependency |

Outside the four marked "YES", this workflow has **no** other barriers. Any additional waiting is artificial latency and MUST be removed.

## 3. Concurrency Limits

| Scope | Recommended value | Meaning |
|---|---|---|
| Global cap | 15 | Total concurrent instances across all roles and all work items |
| Per-role cap | 6, hard ceiling 10 | Max simultaneous instances for a single role ID, preventing pool starvation |
| Per-work-item | See matrix | Dictated by role semantics; the tightest constraint |

Dispatch protocol (sequence is strict):

1. Acquire global slot. If unavailable → **halt remaining dispatches in this batch and defer**; MUST NOT degrade into unbounded dispatch.
2. Check the role's per-role cap. If exceeded → **release the global slot immediately**, skip this instance, and proceed to the next candidate.
3. Dispatch.
4. Slots MUST be released on **every** exit path: success, failure, exception, timeout, cancellation, or aborted pre-checks.

**Why limits are mandatory**: Unbounded fan-out causes instances to contend for the same context and quota, resulting in hangs that exceed serial execution time. Furthermore, a failure storm crashes all in-flight instances simultaneously without salvageable partial results.

## 4. Runtime Name Allocation

Before dispatching an agent, the orchestrator MUST generate and inject a concise `{AGENT_NAME}` for runtime identification. It does not replace the role ID or alter output contracts.

Format:
```text
<role>[-<qualifier>][-<index>]
```

Rules:
- Default to role ID directly: `implementer`, `self-reviewer`, `fixer`.
- When multiple instances of the same role run concurrently, append a numeric suffix: `completion-evaluator-1`, `completion-evaluator-2`.
- Independent reviewers use their lens as qualifier: `independent-reviewer-security`, `independent-reviewer-correctness`. A batch MUST NOT duplicate lenses or names.
- Review-judge Mode A/B uses `review-judge-a` / `review-judge-b`.
- Re-dispatching the same role increments the index; MUST NOT reuse names that could conflict with prior runs.

## 5. Lens Diversity over Redundancy

N identical reviewers follow the same search path and produce N redundant reports (Cost × N, Information × 1). Fan-out gains arise **entirely** from diverse perspectives.

| Lens | Charter |
|---|---|
| `security` | Injections, auth bypass, secret/token leaks, unsafe deserialization/file uploads |
| `correctness` | Branch/boundary errors, null/overflow, races, swallowed exceptions, timeouts, transaction consistency |
| `architecture` | Consistency with existing patterns, module boundaries, dependency inversion, YAGNI minimalism |
| `tests` | Missing coverage for new logic, weak assertions, unverified error paths, over-coupling to implementation |
| `performance` | N+1 queries, remote calls in loops, missing pagination/streaming, unbounded memory growth |

| Fan-out mode | Reviewer N | Combination |
|---|---|---|
| Default | 2 | `security` + `correctness` |
| Extended (based on risk) | 3–5 | Above two + new module/cross-module → `architecture`; core business logic → `tests`; batch I/O → `performance` |

**MUST NOT** dispatch duplicate lenses in the same batch. Each instance MUST receive exactly one assigned lens in its prompt.

## 6. Anti-Anchoring (Hard Constraints)

1. Parallel reviewer instances **MUST NOT** receive each other's outputs: no reports, no summaries, no prior findings list.
2. Applies equally between `self-reviewer` and `independent-reviewer`.
3. Synthesis and cross-validation occur **exclusively** within `review-judge`.
4. The only shared information across instances is the common **input**: the frozen diff, work item context, and explicitly named areas of interest.

| Overlap source | Meaning |
|---|---|
| Independent reviewers identify the same location + same severity | **High confidence signal**: The issue is almost certainly genuine |
| Reviewers read each other's reports | **Zero information**: Likely an echo chamber; cannot be counted toward confidence |

## 7. When NOT to Parallelize

| Scenario | Action |
|---|---|
| Work item is small and easily concluded by a single review (copy, version metadata, formatting, no logic change) | Fan-out = 1; extra instances only produce P3 noise |
| Disjoint file sets cannot be strictly proven | Fall back to exclusive serial execution for writers |
| Subsequent step requires the previous step's **decision** to form prompt | Execute sequentially; no parallelization possible |
| Iteration budget exhausted; human escalation in progress | Halt all dispatches and escalate to human |
| Global concurrency slots exhausted | Defer execution; do not bypass limits |
