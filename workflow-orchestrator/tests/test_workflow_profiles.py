"""Versioned workflow gates: run with python3 -m unittest discover -s ... ."""
import contextlib
import copy
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _workflow_steps as steps
import validate_workflow as validator
import index_workflow


def decision(profile):
    return {
        "rationale": "Inspected affected call paths and acceptance criteria",
        "evidence": ["src/example.py:1"],
        "risk_checks": {key: {"present": False, "evidence": ["src/example.py:1"]}
                        for key in steps.PROFILE_RISK_CHECKS},
        "light_eligibility": {key: True for key in steps.LIGHT_ELIGIBILITY},
    }


def evidence(step, revision, profile, outcome="pass"):
    actor = steps.PROFILE_EVIDENCE_ACTORS[profile].get(step, ("main",))[0]
    result = {"revision": revision, "outcome": outcome,
              "references": ["actual-output.txt"], "actor": actor}
    if step == "review":
        result.update(p0=False, p1=outcome == "fail", requires_adjudication=False)
        if profile == "strict":
            result["reports"] = {r: {"revision": revision, "outcome": outcome, "references": [r + ".txt"]}
                                 for r in ("self", "indep:security", "indep:correctness")}
    return result


def fixture(profile="light"):
    flow = steps.PROFILE_FLOWS[profile]
    initial = {"from": None, "to": "active", "execution_profile": profile,
               "change_revision": 0, "profile_decision": decision(profile),
               "step": "investigate", "evidence": {"investigate": evidence("investigate", 0, profile)}}
    wi = {"id": "profile-test", "execution_profile": profile, "change_revision": 1,
          "profile_decision": decision(profile), "change_class": "code",
          "routing": {"scenario": "feature", "phase": "backend", "method": "direct",
                      "required": [], "used": [], "handoffs": {}, "architecture_required": False}}
    if profile == "strict":
        wi["routing"].update(required=["scenario-standards", "coding-tactics", "qa-suite"],
                             used=["scenario-standards", "coding-tactics", "qa-suite"],
                             handoffs={"scenario-standards": {"status": "complete", "scenario": "feature", "phase": "backend", "scope_boundary": ["src"], "affected_files": ["src/example.py"], "acceptance_criteria": ["works"], "non_goals": []},
                                       "coding-tactics": {"status": "complete", "method": "direct", "checkpoints": ["verify"]},
                                       "qa-suite": {"status": "complete", "project_profile": {}, "test_types": ["unit"], "frameworks": ["unittest"], "verify_commands": ["python3 -m unittest"], "gaps": []}})
    if profile == "light":
        remaining = flow[1:]
        events = [initial, {"from": "active", "to": "done", "is_complete": True,
                           "has_code_change": True, "execution_profile": profile, "change_revision": 1,
                           "step": remaining, "evidence": {s: evidence(s, 1, profile) for s in remaining}}]
    else:
        before = flow[1:flow.index("review")]
        after = flow[flow.index("review"):]
        roster = ["indep:general"] if profile == "standard" else ["self", "indep:security", "indep:correctness"]
        events = [initial, {"from": "active", "to": "reviewing", "is_complete": True,
                           "has_code_change": True, "execution_profile": profile, "change_revision": 1,
                           "reviewers": roster, "step": before,
                           "evidence": {s: evidence(s, 1, profile) for s in before}},
                  {"from": "reviewing", "to": "done", "execution_profile": profile, "change_revision": 1,
                   "step": after, "evidence": {s: evidence(s, 1, profile) for s in after}}]
    return {"schema_version": 2, "work_item": wi, "events": events}


class ProfilesTest(unittest.TestCase):
    def run_check(self, ledger, expected=0):
        for check in (validator.check_ledger, validator.check_coverage):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                actual = check(ledger)
            self.assertEqual(expected, actual, (check.__name__, err.getvalue()))

    def test_all_profiles(self):
        for profile in ("light", "standard", "strict"):
            with self.subTest(profile=profile):
                self.run_check(fixture(profile))

    def test_v1_retains_full_code_gates(self):
        wi = copy.deepcopy(fixture("strict")["work_item"])
        wi["routing"]["tier"] = "L3"
        wi["routing"]["handoffs"]["scenario-standards"]["tier"] = "L3"
        ledger = {"work_item": wi, "events": [
            {"from": None, "to": "active", "step": "investigate"},
            {"from": "active", "to": "reviewing", "is_complete": True, "has_code_change": True,
             "reviewers": ["self", "indep:correctness"], "step": ["implement", "evaluate", "review"]},
            {"from": "reviewing", "to": "done", "step": ["judge", "final-evaluate", "validate"]}]}
        self.run_check(ledger)
        ledger["schema_version"] = 1
        self.run_check(ledger)
        ledger["events"][-1]["step"] = "validate"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, validator.check_coverage(ledger))

    def test_missing_unknown_profiles_and_risks(self):
        mutations = [lambda x: x["work_item"].pop("execution_profile"),
                     lambda x: x["work_item"].update(execution_profile="tiny"),
                     lambda x: x["work_item"]["profile_decision"]["risk_checks"].pop("security"),
                     lambda x: x["work_item"]["profile_decision"]["risk_checks"].update(typo=False),
                     lambda x: x["work_item"]["profile_decision"]["risk_checks"]["security"].update(evidence=[]),
                     lambda x: x["work_item"]["profile_decision"]["risk_checks"]["security"].update(present="unknown"),
                     lambda x: x["work_item"]["profile_decision"]["risk_checks"]["security"].update(present=True)]
        for mutate in mutations:
            ledger = fixture()
            mutate(ledger)
            with self.subTest(mutation=mutate):
                self.run_check(ledger, 1)

    def test_stale_and_fabricated_evidence(self):
        ledger = fixture()
        ledger["events"][-1]["evidence"]["verify"]["revision"] = 0
        self.run_check(ledger, 1)
        ledger = fixture()
        ledger["events"][-1]["step"].append("judge")
        ledger["events"][-1]["evidence"]["judge"] = evidence("judge", 1, "strict")
        self.run_check(ledger, 1)

    def test_invalid_transitions_and_rosters(self):
        for profile, roster in (("standard", ["self", "indep:general"]),
                                ("strict", ["self", "indep:correctness"])):
            ledger = fixture(profile)
            ledger["events"][1]["reviewers"] = roster
            self.run_check(ledger, 1)
        ledger = fixture("standard")
        ledger["events"][1]["to"] = "done"
        self.run_check(ledger, 1)

    def test_p1_blocks(self):
        for profile in ("standard", "strict"):
            ledger = fixture(profile)
            ledger["events"][-1]["evidence"]["review"]["p1"] = True
            self.run_check(ledger, 1)

    def test_skipped_display(self):
        ledger = fixture()
        progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
        self.assertEqual("skipped", next(s for s in progress["steps"] if s["id"] == "judge")["status"])
        self.assertNotIn("judge", progress["completed"])

    def test_terminal_steps_must_follow_profile_order(self):
        for profile in ("standard", "strict"):
            ledger = fixture(profile)
            final = ledger["events"][-1]
            final["step"] = ["validate"] + [s for s in final["step"] if s != "validate"]
            self.run_check(ledger, 1)
        ledger = fixture()
        ledger["events"][-1]["step"] = ["verify", "implement", "diff-check", "validate"]
        self.run_check(ledger, 1)

    def test_v2_progress_requires_explicit_steps_and_evidence(self):
        for field in ("step", "evidence"):
            ledger = fixture()
            del ledger["events"][0][field]
            progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
            self.assertIn("error", progress)
            self.assertEqual([], progress["completed"])
        # Legacy progress still performs its original best-effort state inference.
        legacy = steps.derive_step_progress([{"from": None, "to": "active"},
                                             {"from": "active", "to": "done"}], {}, 1)
        self.assertIn("investigate", legacy["completed"])

    def test_profile_upgrade(self):
        ledger = fixture("strict")
        initial = fixture()["events"][0]
        upgrade = {"from": "active", "to": "active", "reason": "profile-upgrade",
                   "execution_profile": "strict", "change_revision": 0,
                   "profile_upgrade": {"from": "light", "to": "strict", "reason": "Found transaction behavior",
                                       "evidence": ["src/transaction.py:1"], "decision": decision("strict")}}
        ledger["events"][:1] = [initial, upgrade]
        self.run_check(ledger)
        ledger["events"][1]["profile_upgrade"]["evidence"] = []
        self.run_check(ledger, 1)

    def test_fix_requires_fresh_verification_and_review(self):
        for profile in ("standard", "strict"):
            ledger = fixture(profile)
            failure = copy.deepcopy(ledger["events"][-1])
            failure["to"] = "needs_fix"
            failure["step"] = ["review"] + (["judge"] if profile == "strict" else [])
            failure["evidence"] = {"review": evidence("review", 1, profile, "fail")}
            if profile == "strict":
                failure["evidence"]["judge"] = evidence("judge", 1, profile, "fail")
                failure["evidence"]["judge"]["p1"] = True
            fix_steps = ["fix", "verify", "diff-check"]
            fixed = {"from": "needs_fix", "to": "reviewing", "execution_profile": profile,
                     "change_revision": 2, "reviewers": ledger["events"][1]["reviewers"],
                     "step": fix_steps, "evidence": {s: evidence(s, 2, profile) for s in fix_steps}}
            final_steps = ["review", "validate"] if profile == "standard" else ["rejudge", "final-evaluate", "validate"]
            final = {"from": "reviewing", "to": "done", "execution_profile": profile,
                     "change_revision": 2, "step": final_steps,
                     "evidence": {s: evidence(s, 2, profile) for s in final_steps}}
            ledger["events"][-1:] = [failure, fixed, final]
            ledger["work_item"]["change_revision"] = 2
            self.run_check(ledger)
            progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
            current = steps.resolve_execution_path(ledger["events"], ledger["work_item"], 2)
            self.assertEqual(set(current["required_evidence"]), set(progress["completed"]))
            self.assertNotIn("implement", progress["completed"])
            final["evidence"][final_steps[0]]["revision"] = 1
            self.run_check(ledger, 1)

    def test_conflicts_require_upgrade(self):
        ledger = fixture("standard")
        ledger["events"][-1]["evidence"]["review"]["requires_adjudication"] = True
        self.run_check(ledger, 1)

    def test_v2_needs_fix_can_escalate_after_budget_exhaustion(self):
        ledger = fixture("standard")
        failed = ledger["events"][-1]
        failed.update(to="needs_fix", step=["review"], evidence={"review": evidence("review", 1, "standard", "fail")})
        for revision in range(2, 5):
            fix_steps = ["fix", "verify", "diff-check"]
            ledger["events"].append({"from": "needs_fix", "to": "reviewing", "execution_profile": "standard",
                                      "change_revision": revision, "reviewers": ["indep:general"], "step": fix_steps,
                                      "evidence": {s: evidence(s, revision, "standard") for s in fix_steps}})
            outcome = copy.deepcopy(failed)
            outcome["change_revision"] = revision
            outcome["evidence"]["review"] = evidence("review", revision, "standard", "fail")
            ledger["events"].append(outcome)
        ledger["events"].append({"from": "needs_fix", "to": "escalated", "execution_profile": "standard",
                                  "change_revision": 4, "reason": "Review fix budget exhausted; unresolved P1 remains"})
        ledger["work_item"]["change_revision"] = 4
        self.run_check(ledger)
        del ledger["events"][-1]["reason"]
        self.run_check(ledger, 1)

    def test_needs_fix_escalation_does_not_change_v1_transitions(self):
        self.assertNotIn("needs_fix->escalated", validator.TRANSITIONS)
        self.assertNotIn("needs_fix->escalated", steps.resolve_execution_path([], {}, 1)["allowed_transitions"])
        ledger = fixture("strict")
        del ledger["schema_version"]
        ledger["work_item"]["routing"]["tier"] = "L3"
        ledger["work_item"]["routing"]["handoffs"]["scenario-standards"]["tier"] = "L3"
        entry = ledger["events"][1]
        entry["step"] = ["implement", "evaluate", "review"]
        ledger["events"][-1:] = [{"from": "reviewing", "to": "needs_fix", "step": "judge"},
                                  {"from": "needs_fix", "to": "escalated", "reason": "budget exhausted"}]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(1, validator.check_ledger(ledger))
        self.assertIn("illegal transition needs_fix -> escalated", errors.getvalue())

    def test_upgrade_progress_requires_final_profile_actors_and_gates(self):
        for origin in ("light", "standard"):
            original = fixture(origin)
            ledger = fixture("strict")
            ledger["events"] = original["events"] + [
                {"from": "done", "to": "active", "explicit": True,
                 "execution_profile": origin, "change_revision": 1},
                {"from": "active", "to": "active", "reason": "profile-upgrade",
                 "execution_profile": "strict", "change_revision": 1,
                 "profile_upgrade": {"from": origin, "to": "strict", "reason": "New critical impact",
                                     "evidence": ["src/example.py:10"], "decision": decision("strict")}}]
            progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
            self.assertNotIn("error", progress)
            for step in ("implement", "verify", "review", "validate"):
                self.assertNotIn(step, progress["completed"], (origin, step, progress))
            strict_events = fixture("strict")["events"][1:]
            strict_events[0]["evidence"]["implement"]["action"] = "adopt"
            ledger["events"].extend(strict_events)
            progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
            self.assertEqual(set(steps.PROFILE_FLOWS["strict"]), set(progress["completed"]))

    def test_strict_progress_requires_complete_current_review_reports(self):
        ledger = fixture("strict")
        del ledger["events"][-1]["evidence"]["review"]["reports"]["indep:security"]
        progress = steps.derive_step_progress(ledger["events"], ledger["work_item"], 2)
        self.assertNotIn("review", progress["completed"])
        self.assertNotIn("validate", progress["completed"])

    def test_strict_review_report_cannot_be_stale(self):
        ledger = fixture("strict")
        ledger["events"][-1]["evidence"]["review"]["reports"]["indep:security"]["revision"] = 0
        self.run_check(ledger, 1)

    def test_downgrade_and_revision_rollback(self):
        ledger = fixture()
        ledger["events"][0]["execution_profile"] = "strict"
        ledger["events"][0]["profile_decision"] = decision("strict")
        self.run_check(ledger, 1)
        ledger = fixture()
        ledger["work_item"]["change_revision"] = 0
        self.run_check(ledger, 1)

    def test_malformed_version(self):
        for version in (0, 3, True, "2", None):
            ledger = fixture()
            ledger["schema_version"] = version
            self.run_check(ledger, 1)

    def test_specialized_paths_v1_and_v2(self):
        for kind in ("status-only", "standalone-script"):
            ledger = fixture()
            wi = ledger["work_item"]
            wi["change_class"] = kind
            wi["routing"] = {}
            if kind == "status-only":
                wi["changed_files"] = ["docs/usage.md"]
                wi["status_only"] = dict.fromkeys(validator.STATUS_ONLY_BOOLEAN_FIELDS, False)
                wi["status_only"]["reason"] = "text"
                step, flag, tier = "status-record", "status_only", "L1-status-only"
            else:
                wi["kind"] = kind
                wi["standalone_script"] = {"path": "scratch.py", "run_command": "python3 scratch.py",
                                           "exit_code": 0, "outcome": "pass", "result": "Reproduced expected output",
                                           "promotion_checks": dict.fromkeys(validator.STANDALONE_SCRIPT_CHECKS, False)}
                step, flag, tier = "script-record", "standalone_script", "L1-standalone-script"
            wi["routing"]["tier"] = "L3"  # informational and ignored by v2
            end = ledger["events"][-1]
            end.update(has_code_change=False, change_class=kind)
            end[flag] = True
            end["step"] = [step, "validate"]
            end["evidence"] = {s: evidence(s, 1, "light") for s in end["step"]}
            self.run_check(ledger)
            del ledger["schema_version"]
            wi["routing"]["tier"] = tier
            self.run_check(ledger)

    def test_cli_and_index_share_contract(self):
        for profile in ("light", "standard", "strict"):
            ledger = fixture(profile)
            with tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "ledger.json"
                path.write_text(json.dumps(ledger), encoding="utf-8")
                for mode in ("check-ledger", "check-coverage"):
                    result = subprocess.run([sys.executable, validator.__file__, mode, str(path)],
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("profile=" + profile, result.stdout)
                summary = index_workflow.summarize(str(path), "test-project")
                self.assertEqual(profile, summary["execution_profile"])
                self.assertIsNone(summary["progress_error"])
                self.assertEqual("done", summary["state"])
                self.assertEqual("done", next(s for s in summary["steps"] if s["id"] == "validate")["status"])

    def test_duplicate_version_and_tier_cannot_weaken_strict(self):
        ledger = fixture("strict")
        ledger["work_item"]["routing"]["tier"] = "L1"
        self.run_check(ledger)
        ledger["work_item"]["schema_version"] = 2
        self.run_check(ledger, 1)

    def test_legacy_does_not_accept_v2_steps(self):
        ledger = {"work_item": {}, "events": [{"from": None, "to": "active", "step": "verify"},
                  {"from": "active", "to": "done", "is_complete": True, "has_code_change": False}]}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, validator.check_ledger(ledger))
            self.assertEqual(2, validator.check_coverage(ledger))

    def test_strict_adopts_existing_work_after_upgrade(self):
        ledger = fixture("strict")
        first = fixture()["events"][0]
        mutation = {"from": "active", "to": "active", "reason": "retry", "execution_profile": "light",
                    "change_revision": 1, "step": "implement", "evidence": {"implement": evidence("implement", 1, "light")}}
        upgrade = {"from": "active", "to": "active", "reason": "profile-upgrade", "execution_profile": "strict",
                   "change_revision": 1, "profile_upgrade": {"from": "light", "to": "strict", "reason": "New risk discovered",
                                                             "evidence": ["src/example.py:5"], "decision": decision("strict")}}
        ledger["events"][:1] = [first, mutation, upgrade]
        adopted = ledger["events"][-2]["evidence"]["implement"]
        adopted["action"] = "adopt"
        self.run_check(ledger)
        del adopted["action"]
        self.run_check(ledger, 1)


if __name__ == "__main__":
    unittest.main()
