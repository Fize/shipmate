# Execution profiles (schema v2)

The ledger root owns `schema_version: 2`. `work_item.execution_profile` is its only gate selector.
The root version is passed explicitly into the shared resolver; profile-like fields in v1 never
change its legacy path. Unknown versions, profiles, checks or missing evidence fail validation.

## Admission and fixed risk checks

`profile_decision` contains a non-empty `rationale`, a non-empty `evidence` array of source
references, and `risk_checks` with **exactly** these keys:

```text
security, funds, sensitive_data, migration, concurrency, transactions,
retry_behavior, public_contract, runtime_dependencies, production_behavior,
external_side_effects, irreversible, large_blast_radius, uncertainty
```

Each value is exactly `{"present": false, "evidence": ["source:line or inspected output"]}`.
`present` must be a boolean. A true value requires strict. `production_behavior` means production
release/configuration behavior; ordinary local business behavior changes are evaluated through
other applicable risks. `external_side_effects` concerns changed externally observable operations,
not routine local file edits. `uncertainty` is unresolved critical impact after investigation.
An uninvestigated key cannot be guessed false.

Light additionally requires `light_eligibility` with exactly `clear_acceptance`, `bounded_impact`,
`direct_verification`, `reversible`, all true, supported by the decision's references. Otherwise
choose standard if all critical risks are false. Critical risks select strict regardless of size.
A single permission check can require strict; adding a local regression test can qualify for light.

## Steps and actors

| Profile | Required steps, in order | Actor rules |
|---|---|---|
| light | investigate, implement, verify, diff-check, validate | main |
| standard | investigate, implement, verify, diff-check, review, validate | main except `review`: indep:general |
| strict | investigate, implement, verify, diff-check, evaluate, review, judge, final-evaluate, validate | main investigation/diff-check/collection/validation; implementer implementation/verification; completion-evaluator evaluations; review-judge adjudication |

Strict `reviewers` contains `self`, `indep:security`, `indep:correctness`, and optional distinct
recognized specialist lenses. Its collected review evidence references a report for every roster
member. Standard's roster is exactly `["indep:general"]`: one independent reviewer covers
correctness, verification sufficiency, security and other relevant risks. It receives no peer
reports. Separate evaluator, self-reviewer and judge roles do not apply to standard or light.

For an explicit status/script record use the existing three-step specialized flow and factual
classification checks. Without code changes, omit implement; all other profile obligations apply.

## Evidence and repair

Every event explicitly records its `execution_profile` and non-negative integer `change_revision`.
The first event is `null → active`, revision 0, step investigate, with its `profile_decision`.
Each actual implement/fix mutation advances the revision by one. A mutation and its verification
may be recorded on the same event in execution order. No step is inferred from states in v2.
A control-only event such as a profile upgrade may omit step/evidence.

Every recorded step has a matching `evidence[step]` snapshot:

```json
{"revision": 1, "outcome": "pass", "actor": "main", "references": ["command-output.txt"]}
```

The references must locate real commands, outputs, diffs or reports; the validator checks their
structure and consistency, while the executing/reviewing agents inspect their content. JSON alone
does not prove that a referenced command ran. Verification failures cannot be marked pass.
All evidence for a step matches its event revision; current gates must match the latest revision.
Investigation remains useful history. Fresh verification and diff-check are required after edits.

A review result also includes explicit boolean `p0`, `p1`, `requires_adjudication`. Its outcome is
`fail` only for verified P0/P1 and transitions reviewing → needs_fix. Standard fixes are performed
by main: needs_fix → reviewing records fix, verify, diff-check at the new revision with the same
reviewer roster; reviewing → done records the fresh review and validate. Never remove a blocking
finding unilaterally. `requires_adjudication: true` requires strict before completion.

Strict failures retain judge/fixer ownership: reviewing → needs_fix records the failing review
and judge; needs_fix → reviewing records fixer fix, verification and main diff-check at the new
revision; reviewing → done records rejudge, final-evaluate, validate. The current Mode B decision
verifies the actual diff and closes each original finding. Original implementation/evaluation/
reviews remain historical coverage; they are not current evidence for the edited revision.
Strict current evidence after a fix requires fix, verify, diff-check, rejudge, final-evaluate,
validate. New scope or risk beyond Mode B requires fresh evaluation/review, not wider ad hoc fixes.

## Monotonic upgrades

On an active → active event use reason `profile-upgrade` and:

```json
{"from": "light", "to": "strict", "reason": "Found changed transaction behavior",
 "evidence": ["src/transaction.py:42"], "decision": {"rationale": "...", "evidence": ["..."], "risk_checks": {}}}
```

This is the `profile_upgrade` object; `decision` must contain the complete checks above, not the
abbreviated illustration. Update work_item's final profile/decision to match the event history.
The upgrade does not itself mutate code or consume/reset retry/fix budgets. From reviewing, first
record an explicit rollback to active, then upgrade. Never downgrade or restart the ledger.

Existing valid code may be retained. After promotion to strict, an implementer can record
`implement` at the existing revision with `action: "adopt"` and real inspection references. This
proves inspection/adoption of existing work, not that the implementer originally wrote it.
The remaining strict gates must run for the current revision; prior light/standard verification
can count only when still valid and attached to that revision. Skipped evaluator/judge gates
must be filled, never fabricated. The final work_item.change_revision matches the last event.

## Compatibility and progress

No-version / explicit version 1 ledgers use their existing state, routing, roster and coverage
checks. No automatic migration is performed. Mixed root/work-item version fields are rejected in
v2. `routing.tier`, if retained in v2, cannot choose a path or suppress QA/review.

Both validation commands consult the same resolver and enforce v2 state and evidence checks.
Progress is derived from current passing evidence; profile omissions appear as skipped/not
applicable. Invalid v2 metadata produces a progress error, never a fallback light path.
Progress also checks the final profile's actor and review-report obligations and their ordered
prerequisites. An earlier-profile pass is retained only when it satisfies those current
obligations. After repairs, the current fix/rejudge sequence replaces the superseded historical
gates in the progress checklist. Standard/strict may end needs_fix → escalated with an explicit
reason when the repair budget is exhausted, preserving the unresolved findings in history.
