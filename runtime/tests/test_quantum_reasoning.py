"""Adversarial contracts and exact circuit invariants for quantum reasoning."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from fractions import Fraction as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd_search as search
import surd_quantum as q
from surd_quantum_core import circuit


def fixture():
    docs = json.loads((ROOT/"examples/data/quantum-documents.json").read_text())
    spec = json.loads((ROOT/"examples/data/quantum-contract.json").read_text())
    return docs, spec


class QuantumTests(unittest.TestCase):
    def setUp(self):
        self.docs, self.spec = fixture()

    def report(self):
        return search.quantum(search.SearchIndex(self.docs), "credit scenarios", self.spec)

    def test_exact_grover_and_walsh_normalization(self):
        r = self.report(); c = r["quantum"]
        self.assertEqual((r["feasible_states"], r["marked_states"]), (4, 1))
        self.assertEqual(c["initial_marked_share"]["exact"], "1/8")
        self.assertEqual(c["final_marked_share"]["exact"], "121/128")
        self.assertEqual(c["iterations"], 2)
        for rows in (c["state"], c["interference"]["spectrum"]):
            self.assertEqual(sum(F(x["measurement_share"]["exact"]) for x in rows), 1)

    def test_assumptions_excluded_from_baseline(self):
        r = self.report()
        self.assertEqual(r["posture"]["issues"][0]["assessment"]["status"], "unresolved")
        for cid in self.spec["variables"]:
            self.assertEqual(r["posture"]["reasoning"]["argument"]["claims"][cid]["status"], "unresolved")
        self.assertEqual(r["posture"]["issues"][0]["record_floor"]["status"], "supported")

    def test_inactive_assumption_is_unresolved(self):
        r = self.report()
        self.assertEqual(r["worlds"][0]["claim_statuses"]["due"], "unresolved")

    def test_world_order_and_exclusivity(self):
        r = self.report()
        marked = [w for w in r["worlds"] if w["marked"]]
        self.assertEqual([w["bits"] for w in marked], ["110"])
        self.assertEqual(marked[0]["active_assumptions"], ["due", "elsewhere"])
        self.assertTrue(all(sum(v in w["active_assumptions"] for v in ["here", "elsewhere"]) == 1 for w in r["worlds"] if w["feasible"]))

    def test_record_discrimination_counts(self):
        requests = self.report()["next_records"]
        self.assertEqual([r["id"] for r in requests], ["credit-validity", "posting"])
        self.assertTrue(all((r["target_pairs_separated"], r["target_pairs"]) == (2, 3) for r in requests))

    def test_whole_family_withdrawal(self):
        r = self.report()
        self.assertTrue(all(x["marked_states"] == 0 for x in r["family_withdrawals"]))
        self.assertTrue(all(x["lost_marked"] == ["110"] for x in r["family_withdrawals"]))

    def test_copies_do_not_create_a_new_origin(self):
        self.docs.append(dict(self.docs[0], id="copy", locator="synthetic copy"))
        self.spec["posture"]["argument"]["claims"][0]["support"].append({"document":"copy", "quote":self.docs[0]["text"]})
        self.spec["posture"]["source_families"]["copy"] = "purported-other-origin"
        r = self.report()
        self.assertEqual(len(r["family_withdrawals"]), 2)
        self.assertEqual(r["marked_states"], 1)
        self.assertIn(["copy", "credit"], [x["documents"] for x in r["family_withdrawals"]])

    def test_fact_kind_blocks_issued_to_applied(self):
        self.spec["guards"][0]["expected"] = "credit_applied"
        r = self.report()
        self.assertEqual(r["state"], "guard_blocked")
        self.assertEqual(r["marked_states"], 0)
        self.assertEqual(r["guards"][0]["state"], "blocked")

    def test_missing_metadata_keeps_guard_open(self):
        del self.docs[0]["metadata"]["fact_kind"]
        r = self.report()
        self.assertEqual(r["state"], "guard_open")
        self.assertEqual(r["marked_states"], 0)

    def test_guard_diagnostics_preserve_missing_and_conflicting_sources(self):
        self.docs[1]["metadata"].pop("project")
        self.docs.append(dict(copy.deepcopy(self.docs[0]), id="different", text="A different project record."))
        self.docs[-1]["metadata"]["project"] = "Job B"
        self.spec["posture"]["argument"]["claims"][0]["support"].append(
            {"document":"different", "quote":"A different project record."})
        g = next(g for g in self.report()["guards"] if g["kind"] == "same_scope")
        self.assertEqual(g["state"], "blocked")
        self.assertTrue(any("conflict" in s for s in g["reasons"]))
        self.assertTrue(any("Missing project" in s for s in g["reasons"]))

    def test_money_conflict_stays_blocked_with_missing_other_claim(self):
        self.docs[1]["metadata"].pop("amount")
        self.docs.append(dict(copy.deepcopy(self.docs[0]), id="different", text="A different amount record."))
        self.docs[-1]["metadata"]["amount"] = "100.01"
        self.spec["posture"]["argument"]["claims"][0]["support"].append(
            {"document":"different", "quote":"A different amount record."})
        g = next(g for g in self.report()["guards"] if g["kind"] == "money_equal")
        self.assertEqual(g["state"], "blocked")
        self.assertEqual(g["values"], [["100.00", "100.01"], []])
        self.assertEqual(len(g["reasons"]), 2)

    def test_equivalent_money_annotations_are_exactly_equal(self):
        self.docs[1]["metadata"]["amount"] = "1E2"
        self.assertEqual(self.report()["guards"][-1]["state"], "passed")

    def test_scope_mismatch_blocked(self):
        self.docs[1]["metadata"]["project"] = "Job B"
        self.assertEqual(self.report()["state"], "guard_blocked")

    def test_dates_not_lexically_compared(self):
        self.docs[0]["metadata"]["event_date"] = "2021-01-01"
        self.assertEqual(self.report()["state"], "guard_blocked")

    def test_equal_dates_leave_order_blocked(self):
        self.docs[1]["metadata"]["event_date"] = self.docs[0]["metadata"]["event_date"]
        self.assertEqual(self.report()["state"], "guard_blocked")

    def test_invalid_date_rejected(self):
        self.docs[1]["metadata"]["event_date"] = "later"
        with self.assertRaises(search.SearchError): self.report()

    def test_exact_cent_mismatch(self):
        self.docs[1]["metadata"]["amount"] = "100.01"
        self.assertEqual(self.report()["state"], "guard_blocked")

    def test_float_amount_rejected(self):
        self.docs[1]["metadata"]["amount"] = 100.0
        with self.assertRaises(search.SearchError): self.report()

    def test_nonfinite_amount_rejected(self):
        for bad in ["NaN", "Infinity", "1e-100000", "1e100000"]:
            with self.subTest(value=bad):
                self.docs[1]["metadata"]["amount"] = bad
                with self.assertRaises(search.SearchError): self.report()

    def test_guard_is_optional_and_scope_explicit(self):
        self.spec["guards"] = []
        self.assertFalse(self.report()["verification"]["typed_guards_requested"])

    def test_contested_target_is_never_marked(self):
        self.spec["posture"]["argument"]["claims"][-1]["oppose"] = [{"document":"demand", "quote":self.docs[1]["text"]}]
        r = self.report()
        self.assertEqual(r["marked_states"], 0)
        self.assertTrue(any(w["target_status"] == "contested" for w in r["worlds"]))

    def test_withdrawal_can_gain_marked_scenarios(self):
        self.spec["guards"] = []
        self.spec["posture"]["argument"]["claims"][-1]["oppose"] = [{"document":"demand", "quote":self.docs[1]["text"]}]
        # Opposition gets a separate source; keep the demand premise available.
        self.docs.append({"id":"adverse", "text":"An opposing account.", "locator":"synthetic adverse"})
        self.spec["posture"]["argument"]["claims"][-1]["oppose"] = [{"document":"adverse", "quote":"An opposing account."}]
        self.spec["posture"]["source_families"]["adverse"] = "adverse-origin"
        r = self.report()
        self.assertTrue(any(x["gained_marked"] == ["110"] for x in r["family_withdrawals"]))

    def test_requirement_uses_four_valued_status(self):
        self.spec["requirements"] = [{"claim":"due", "status":"unresolved"}]
        r = self.report()
        self.assertEqual(r["feasible_states"], 2)
        self.assertEqual(r["marked_states"], 0)

    def test_global_assumption_remains_visible(self):
        self.spec["posture"]["argument"]["claims"].append({"id":"global", "text":"Global scope assumption.", "assumption":True})
        self.spec["posture"]["argument"]["rules"][0]["if"].append("global")
        r = self.report()
        self.assertIn("global", r["worlds"][6]["target_assessment"]["conditional_on"])

    def test_posture_fork_baseline_is_compatible(self):
        self.spec["posture"]["issues"][0]["forks"] = [
            {"id":"concede", "label":"credit due", "assume":["due"], "goal":"reconcile"}]
        self.assertEqual(self.report()["marked_states"], 1)

    def test_invalid_contracts(self):
        for key, val in [("variables", []), ("variables", ["issued"]), ("variables", ["due", "due"]),
                         ("target", "missing"), ("iterations", True), ("iterations", 17),
                         ("requirements", [{"claim":"due", "status":"true"}]),
                         ("exclusive_groups", [["due", "missing"]]),
                         ("guards", [{"id":"g", "kind":"magic", "claims":["issued"]}])]:
            with self.subTest(key=key, value=val):
                spec = copy.deepcopy(self.spec); spec[key] = val
                with self.assertRaises(search.SearchError): search.quantum(search.SearchIndex(self.docs), "test", spec)

    def test_malformed_statuses_and_fields_raise_search_errors(self):
        for bad in [[], {}, None, True, 7]:
            for kind in ["requirement", "same_scope", "before"]:
                with self.subTest(value=bad, kind=kind):
                    spec = copy.deepcopy(self.spec)
                    if kind == "requirement":
                        spec["requirements"] = [{"claim":"due", "status":bad}]
                    else:
                        spec["guards"] = [{"id":"g", "kind":kind, "claims":["issued", "demand"], "field":bad}]
                    with self.assertRaises(search.SearchError):
                        search.quantum(search.SearchIndex(self.docs), "test", spec)

    def test_invalid_iterations_and_records_fail_before_scenario_work(self):
        for key, value in [("iterations", []), ("records", [{"id":"bad", "claim":"due"}])]:
            with self.subTest(key=key):
                spec = copy.deepcopy(self.spec); spec[key] = value
                with patch.object(q, "posture", side_effect=AssertionError("late validation")):
                    with self.assertRaises(search.SearchError):
                        q.quantum(search.SearchIndex(self.docs), "test", spec)

    def test_missing_record_outcome_rejected(self):
        del self.spec["records"][0]["if_weakens"]
        with self.assertRaises(search.SearchError): self.report()

    def test_scope_and_exact_quote_enforced(self):
        self.spec["posture"]["argument"]["claims"][0]["support"][0]["quote"] = "a fabricated quote"
        with self.assertRaises(search.SearchError): self.report()

    def test_report_roundtrip_and_tamper(self):
        r = self.report()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"q.json"; search.save(r,path)
            self.assertEqual(search.load(path)["report_hash"], r["report_hash"])
        r["quantum"]["final_marked_share"]["numerator"] += 1
        with self.assertRaises(search.SearchError): q.validate_report(r)

    def test_renderer_includes_source_posture(self):
        s = q.render(self.report())
        for text in ["121/128", "SYNTHETIC credit memo", "If weakens", "Withdraw credit"]:
            self.assertIn(text, s)
        self.assertIn("Oracle parity diagnostic", s)
        self.assertIn("target-marked/unmarked", s)
        self.assertNotIn("target-opposed", s)
        self.assertIn("Guards after withdrawal", s)

    def test_renderer_explains_guard_failure(self):
        self.docs[1]["metadata"]["amount"] = "100.01"
        s = q.render(self.report())
        self.assertIn("Available source annotations conflict", s)
        self.assertIn("issued: 100.00", s)
        self.assertIn("demand: 100.01", s)

    def test_large_report_preview_retains_complete_data_and_leads_with_target(self):
        for cid in ["extra1", "extra2", "extra3"]:
            self.spec["posture"]["argument"]["claims"].append({"id":cid, "text":cid, "assumption":True})
            self.spec["variables"].append(cid)
        self.spec["exclusive_groups"] = []
        self.spec["target"] = "due"
        r = self.report(); s = q.render(r)
        self.assertEqual(len(r["worlds"]), 64)
        self.assertIn("16 of 64 feasible assignments", s)
        rows = [line for line in s.splitlines() if line.startswith("Scenario ") and ": target " in line]
        self.assertEqual(len(rows), 16)
        self.assertTrue(all(": target supported;" in line for line in rows))
        self.assertEqual(rows[0].split(":")[0], "Scenario 100000")

    def test_cli_json_and_native_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"index.json"; search.save(search.SearchIndex(self.docs), path)
            p = subprocess.run([sys.executable,str(ROOT/"surd.py"),"search","quantum",str(path),"credit","--spec",str(ROOT/"examples/data/quantum-contract.json"),"--json"],capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr+p.stdout)
            self.assertEqual(json.loads(p.stdout)["marked_states"],1)
        p = subprocess.run([sys.executable,str(ROOT/"surd.py"),"run","examples/quantum_reasoning.surd"],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn("6/6 expects",p.stdout)

    def test_zero_all_half_oracles_and_bounds(self):
        for marked, expected in [(set(),"0"),(set(range(8)),"1"),({0,1,2,3},"1/2")]:
            c = circuit(3,marked)
            self.assertEqual(c["iterations"],0)
            self.assertEqual(c["final_marked_share"]["exact"],expected)
        for bad in [0,9,True]:
            with self.assertRaises(ValueError): circuit(bad,[])

    def test_fixed_iterations_can_overshoot(self):
        c = circuit(3,{6},3)
        self.assertLess(F(c["final_marked_share"]["exact"]),F(121,128))

    def test_auto_selection_stops_after_peak_lookahead(self):
        c = circuit(3, {6})
        self.assertEqual(c["selection_simulated_steps"], 3)
        fixed = circuit(3, {6}, c["iterations"])
        self.assertEqual(c["state"], fixed["state"])
        self.assertEqual(c["history"], fixed["history"])
        self.assertEqual(fixed["selection_simulated_steps"], 2)
        for marked in [[], range(8), range(4)]:
            self.assertEqual(circuit(3, marked)["selection_simulated_steps"], 1)

    def test_eight_wire_walsh_matches_direct_parity_sums(self):
        marked = {0, 5, 18, 127, 200}
        c = circuit(8, marked)
        for mask, mode in enumerate(c["interference"]["spectrum"]):
            expected = F(sum((-1 if x in marked else 1) * (-1 if bin(x & mask).count("1") % 2 else 1)
                             for x in range(256)), 256)
            self.assertEqual(F(mode["amplitude"]["exact"]), expected)

    def test_invalid_core_oracle_indices_raise_value_errors(self):
        for bad in [None, [{}], [True], [0.0], [-1], [8]]:
            with self.subTest(value=bad):
                with self.assertRaises(ValueError): circuit(3, bad)

    def test_evidence_and_legal_confidence_remain_unassessed(self):
        v = self.report()["verification"]
        self.assertFalse(v["circuit_measurement_is_case_confidence"])
        self.assertFalse(v["quantum_speedup_claimed"])
        self.assertFalse(v["legal_outcome_assessed"])


if __name__ == "__main__":
    unittest.main()
