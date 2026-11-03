import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from qulap_fixtures import build
from loom_qulap.accounting import allocation_model
from loom_qulap.bridges import evidence_matrix
from loom_qulap.cubes import minor_units, query, rollup
from loom_qulap.model import Expression, ModelError, contradiction_core, sensitivity, solve
from loom_qulap.surd_bridge import export_witness_program, load_surd
from loom_qulap.temporal import asof_receipt


class QulapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def packet(self, name="credit.model.json"):
        return json.loads((self.root / name).read_text())

    def test_credit_joint_states(self):
        r = solve(self.packet())
        self.assertEqual(r["feasible_states"], 3)
        self.assertEqual(r["metrics"]["unresolved_credit"]["min"], 0)
        self.assertEqual(r["metrics"]["unresolved_credit"]["max"], 60000)
        self.assertEqual(r["propositions"]["credit_not_delivered"]["status"], "POSSIBLE_IN_MODEL")
        self.assertEqual(r["propositions"]["double_satisfaction"]["status"], "IMPOSSIBLE_IN_MODEL")

    def test_mechanism_does_not_establish_knowledge(self):
        r = solve(self.packet("mechanism.model.json"))
        self.assertEqual(r["metrics"]["owner_related_application"]["min"], 30000)
        self.assertEqual(r["metrics"]["owner_related_application"]["max"], 40000)
        self.assertEqual(r["propositions"]["owner_funding_required"]["status"], "FORCED_IN_MODEL")
        self.assertEqual(r["propositions"]["knowing_use_hypothesis"]["status"], "POSSIBLE_IN_MODEL")

    def test_incomplete_never_proves_forced_or_impossible(self):
        r = solve(self.packet(), max_nodes=3)
        self.assertFalse(r["complete"])
        self.assertEqual(r["state"], "INCOMPLETE")
        self.assertTrue(all(p["status"] == "UNRESOLVED_INCOMPLETE" for p in r["propositions"].values()))

    def test_inconsistent_never_vacuously_proves(self):
        r = solve(self.packet("contradiction.model.json"))
        self.assertEqual(r["state"], "INCONSISTENT_MODEL")
        self.assertEqual(r["propositions"]["disputed"]["status"], "INCONSISTENT_MODEL")
        self.assertIsNone(r["metrics"]["charge"]["min"])

    def test_core_is_deletion_minimal(self):
        r = contradiction_core(self.packet("contradiction.model.json"))
        self.assertEqual(r["state"], "IRREDUCIBLE_CORE")
        self.assertEqual(set(r["constraints"]), {"earliest_copy", "later_copy"})

    def test_knockout_detects_dependency(self):
        r = sensitivity(self.packet("mechanism.model.json"))
        row = next(x for x in r["knockouts"] if x["removed_constraint"] == "lender_capacity")
        self.assertEqual(row["metrics"]["owner_related_application"]["min"], 0)
        self.assertEqual(row["propositions"]["owner_funding_required"], "POSSIBLE_IN_MODEL")

    def test_domains_are_not_invented(self):
        p = self.packet()
        p["variables"][1]["domain"] = [60000]
        r = solve(p)
        self.assertEqual(r["feasible_states"], 1)
        self.assertEqual(r["propositions"]["credit_not_delivered"]["status"], "IMPOSSIBLE_IN_MODEL")

    def test_next_record_reductions_are_per_unit(self):
        r = solve(self.packet("mechanism.model.json"))
        row = next(x for x in r["next_records"] if x["variable"] == "owner_part")
        self.assertEqual(row["metric_reductions"]["owner_related_application"]["guaranteed_width_reduction"], 10000)

    def test_unsourced_assumptions_visible(self):
        r = solve(self.packet("contradiction.model.json"))
        self.assertTrue(r["input_issues"])
        self.assertEqual(r["proof_effect"], "NONE")

    def test_float_rejected(self):
        p = self.packet()
        p["variables"][0]["domain"] = [60000.0]
        with self.assertRaises(ModelError):
            solve(p)

    def test_bool_not_money(self):
        p = self.packet()
        p["variables"][0]["domain"] = [True]
        with self.assertRaises(ModelError):
            solve(p)

    def test_duplicate_variable_rejected(self):
        p = self.packet()
        p["variables"].append(p["variables"][0])
        with self.assertRaises(ModelError):
            solve(p)

    def test_malformed_entries_return_model_error(self):
        p = self.packet()
        p["variables"] = [None]
        with self.assertRaises(ModelError):
            solve(p)

    def test_classical_work_counters_are_explicit(self):
        r = solve(self.packet())
        self.assertGreater(r["classical_constraint_evaluations"], 0)
        self.assertGreaterEqual(r["full_assignments_evaluated"], r["feasible_states"])

    def test_duplicate_domain_rejected(self):
        p = self.packet()
        p["variables"][0]["domain"] = [1, 1]
        with self.assertRaises(ModelError):
            solve(p)

    def test_unknown_source_rejected(self):
        p = self.packet()
        p["variables"][0]["source_ids"] = ["missing"]
        with self.assertRaises(ModelError):
            solve(p)

    def test_cross_stage_requires_bridge(self):
        p = self.packet()
        del p["constraints"][0]["bridge"]
        with self.assertRaises(ModelError):
            solve(p)

    def test_unit_multiplication_rejected(self):
        p = self.packet()
        p["metrics"][0]["expr"] = "issued * issued"
        with self.assertRaises(ModelError):
            solve(p)

    def test_metric_wrong_unit_rejected(self):
        p = self.packet()
        p["metrics"][0]["unit"] = "integer"
        with self.assertRaises(ModelError):
            solve(p)

    def test_code_injection_rejected(self):
        for expr in ("__import__('os').system('pwd')", "x.__class__", "[x for x in y]", "x ** 5000", "x / 0"):
            with self.assertRaises(ModelError):
                Expression(expr, {"x", "y"})

    def test_chained_compare_and_boolean_negation(self):
        e = Expression("0 <= x < 3 and not flag", {"x", "flag"})
        self.assertTrue(e.value({"x": 2, "flag": False}))
        self.assertFalse(e.value({"x": 3, "flag": False}))

    def test_permutation_preserves_world_set(self):
        p = self.packet()
        a = solve(p)
        p["variables"].reverse()
        p["constraints"].reverse()
        b = solve(p)
        self.assertEqual(a["worlds"], b["worlds"])
        self.assertEqual(a["metrics"], b["metrics"])

    def test_world_retention_not_exhaustiveness(self):
        r = solve(self.packet(), retain_worlds=1)
        self.assertTrue(r["complete"])
        self.assertTrue(r["worlds_truncated"])
        with self.assertRaises(ModelError):
            export_witness_program(r)

    def test_surd_export_boolean_encoding(self):
        r = solve(self.packet("mechanism.model.json"))
        source, worlds = export_witness_program(r)
        self.assertIn('field(qstates[0], "actor_knew_use")', source)
        self.assertTrue(all(type(w["actor_knew_use"]) is int for w in worlds))

    def test_surd_loader_resolves_local_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "qulap_testdep.py").write_text("value = 42\n")
            (d / "surd.py").write_text("import qulap_testdep\nVERSION='fixture'\ndef grover_realm(*args): return qulap_testdep.value\n")
            try:
                mod, receipt = load_surd(d / "surd.py")
                self.assertEqual(mod.grover_realm(), 42)
                self.assertIn("qulap_testdep.py", receipt["local_source_sha256"])
            finally:
                sys.modules.pop("qulap_testdep", None)

    def test_surd_missing_dependency_is_explicit_model_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "surd.py"
            p.write_text("import qulap_missing_fixture_dependency\n")
            with self.assertRaisesRegex(ModelError, "dependency unavailable"):
                load_surd(p)

    def test_whole_payment_joint_allocations(self):
        m = allocation_model(self.packet("allocations.json"))
        r = solve(m)
        self.assertEqual(r["feasible_states"], 2)
        self.assertEqual(r["metrics"]["unapplied_paid"]["max"], 0)
        self.assertTrue(all(p["status"] == "POSSIBLE_IN_MODEL" for p in r["propositions"].values()))

    def test_allocations_no_double_use(self):
        p = self.packet("allocations.json")
        p["payments"] = p["payments"][:1]
        r = solve(allocation_model(p))
        self.assertEqual(r["state"], "INCONSISTENT_MODEL")

    def test_allocations_stage_rejected(self):
        p = self.packet("allocations.json")
        p["payments"][0]["stage"] = "requested"
        with self.assertRaises(ModelError):
            allocation_model(p)

    def test_allocations_scope_rejected(self):
        p = self.packet("allocations.json")
        p["payments"][0]["scope"] = "B"
        with self.assertRaises(ModelError):
            allocation_model(p)

    def cube(self, sql="select * from facts order by event_id", **kwargs):
        return query(self.root / "synthetic.sqlite", sql, **kwargs)

    def test_cube_read_hash_unchanged(self):
        r = self.cube()
        self.assertEqual(r["state"], "ROWS")
        self.assertTrue(r["bytes_stable_during_read"])

    def test_cube_write_rejected(self):
        self.assertEqual(self.cube("delete from facts")["state"], "ERROR")
        self.assertEqual(len(self.cube()["rows"]), 4)

    def test_cube_pragma_attach_rejected(self):
        for sql in ("PRAGMA user_version=8", "ATTACH DATABASE ':memory:' AS x", "CREATE TEMP TABLE x(a)"):
            self.assertEqual(self.cube(sql)["state"], "ERROR")

    def test_cube_empty_vs_error_vs_unavailable(self):
        self.assertEqual(self.cube("select * from facts where 0")["state"], "QUERY_EMPTY")
        self.assertEqual(self.cube("select * from missing")["state"], "ERROR")
        self.assertEqual(query(self.root / "missing.sqlite", "select 1")["state"], "UNAVAILABLE")

    def test_cube_duplicate_columns_rejected(self):
        self.assertEqual(self.cube("select event_id, event_id from facts")["state"], "ERROR")

    def test_cube_truncation_visible(self):
        r = self.cube(max_rows=2)
        self.assertEqual(r["state"], "TRUNCATED")
        a = rollup(r, self.packet("cube-contract.json"))
        self.assertTrue(all(g["complete_total_cents"] is None for g in a["groups"]))

    def test_cube_null_not_zero_and_stages_separate(self):
        a = rollup(self.cube(), self.packet("cube-contract.json"))
        paid = next(g for g in a["groups"] if g["axes"]["stage"] == "paid")
        self.assertEqual(paid["known_sum_cents"], 7999)
        self.assertEqual(paid["unknown_count"], 1)
        self.assertIsNone(paid["complete_total_cents"])
        self.assertEqual(len(a["groups"]), 2)

    def test_cube_join_fanout_suppressed(self):
        a = rollup(self.cube("select facts.* from facts cross join (select 1 union all select 2)"), self.packet("cube-contract.json"))
        self.assertEqual(a["duplicate_observations_suppressed"], 4)
        self.assertEqual(sum(g["known_sum_cents"] for g in a["groups"]), 11332)

    def test_cube_conflict_excluded(self):
        r = self.cube()
        r["rows"].append(dict(r["rows"][0], amount_cents=500))
        a = rollup(r, self.packet("cube-contract.json"))
        self.assertEqual(a["state"], "PARTIAL")
        self.assertEqual(len(a["conflicting_events"]), 1)

    def test_cube_float_cents_rejected(self):
        r = self.cube()
        r["rows"][0]["amount_cents"] = 10.01
        a = rollup(r, self.packet("cube-contract.json"))
        self.assertEqual(a["state"], "PARTIAL")
        self.assertEqual(len(a["rejected"]), 1)

    def test_decimal_intake_signed_and_large(self):
        self.assertEqual(minor_units("($1,234.56)"), -123456)
        self.assertEqual(minor_units("-20.02"), -2002)
        self.assertEqual(minor_units("9007199254740993.01"), 900719925474099301)
        self.assertEqual(minor_units("123456789012345678901234567890.01"), 12345678901234567890123456789001)
        self.assertIsNone(minor_units(None))

    def test_decimal_intake_no_rounding_or_float(self):
        for raw in ("1.001", "1,23", "NaN", "1e2", 10.01, "(-20.02)", "1$0", "$$10"):
            with self.assertRaises(ModelError):
                minor_units(raw)

    def test_magnitude_intake_requires_direction(self):
        self.assertEqual(minor_units("20.02", "magnitude", -1), -2002)
        for direction in (None, True, "-1", 0):
            with self.assertRaises(ModelError):
                minor_units("20.02", "magnitude", direction)

    def test_text_contract_preserves_raw_amount(self):
        r = self.cube()
        for row in r["rows"]:
            value = row.pop("amount_cents")
            row["amount_raw"] = None if value is None else ("-" if value < 0 else "") + str(abs(value) // 100) + "." + str(abs(value) % 100).zfill(2)
        c = self.packet("cube-contract.json")
        c["columns"].pop("amount_cents")
        c["columns"]["amount_text"] = "amount_raw"
        c["money"] = {"source_mode": "signed"}
        a = rollup(r, c)
        self.assertEqual(sum(g["known_sum_cents"] for g in a["groups"]), 11332)
        self.assertIn("amount_text", a["groups"][0]["events"][0]["source_observations"][0])

    def test_rico_both_and_open_stay_visible(self):
        r = evidence_matrix(self.packet("rico-bridges.json"))
        a = next(x for x in r["matrix"] if x["actor"] == "Synthetic Actor A" and x["slot"] == "conduct_participation")
        b = next(x for x in r["matrix"] if x["actor"] == "Synthetic Actor B" and x["slot"] == "conduct_participation")
        self.assertEqual(a["state"], "BOTH_ANNOTATED")
        self.assertEqual(a["clock_gaps"], ["E1"])
        self.assertEqual(b["state"], "OPEN")
        self.assertEqual(r["legal_determination"], "NONE")

    def test_rico_copies_do_not_add_lineages(self):
        p = self.packet("rico-bridges.json")
        p["annotations"].append(dict(p["annotations"][0], id="E3"))
        r = evidence_matrix(p)
        a = next(x for x in r["matrix"] if x["state"] == "BOTH_ANNOTATED")
        self.assertEqual(len(a["annotations"]), 3)
        self.assertEqual(len(a["distinct_native_lineages"]), 2)

    def test_rico_unknown_subsection_not_guessed(self):
        p = self.packet("rico-bridges.json")
        p["subsection"] = "1962a"
        with self.assertRaises(ModelError):
            evidence_matrix(p)

    def test_cli_refuses_output_overwrite(self):
        cli = Path(__file__).resolve().parents[1] / "loom.py"
        out = self.root / "kept.json"
        out.write_text("original")
        r = subprocess.run([sys.executable, str(cli), "qulap", "solve", str(self.root / "credit.model.json"), "--out", str(out)], capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(out.read_text(), "original")

    def timed_packet(self):
        return {"schema": "qulap.model.v1", "source_tier": "synthetic",
                "variables": [{"id": "charge", "domain": [10], "prior_domain": [0, 10], "unit": "integer", "source_ids": ["S_later"]}],
                "constraints": [{"id": "later_claim", "expr": "charge == 10", "source_ids": ["S_later"]}],
                "metrics": [{"id": "amount", "expr": "charge", "unit": "integer"}],
                "propositions": [{"id": "positive", "expr": "charge > 0"}],
                "sources": [{"id": "S_later", "locator": "SYN later message", "lineage_id": "SYN_LATER", "receipt_by_actor": {"A": "2020-02-01"}}]}

    def test_asof_removes_hindsight_with_explicit_prior(self):
        p = asof_receipt(self.timed_packet(), "A", "2020-01-01")
        self.assertEqual(p["constraints"], [])
        self.assertEqual(solve(p)["propositions"]["positive"]["status"], "POSSIBLE_IN_MODEL")
        self.assertIn("does not establish awareness", p["receipt_projection"]["boundary"])

    def test_asof_retains_received_record(self):
        p = asof_receipt(self.timed_packet(), "A", "2020-02-01")
        self.assertEqual(solve(p)["propositions"]["positive"]["status"], "FORCED_IN_MODEL")

    def test_asof_missing_prior_abstains(self):
        p = self.timed_packet()
        del p["variables"][0]["prior_domain"]
        with self.assertRaises(ModelError):
            asof_receipt(p, "A", "2020-01-01")

    def test_asof_actor_specific_and_unknown_preserved(self):
        p = asof_receipt(self.timed_packet(), "B", "2021-01-01")
        self.assertEqual(p["receipt_projection"]["excluded_sources"][0]["state"], "UNKNOWN_RECEIPT")

    def test_asof_interval_overlap_not_known_before(self):
        p = self.timed_packet()
        p["sources"][0]["receipt_by_actor"]["A"] = ["2020-01-01", "2020-03-01"]
        p = asof_receipt(p, "A", "2020-02-01")
        self.assertEqual(p["receipt_projection"]["excluded_sources"][0]["state"], "OVERLAPS_CUTOFF")


if __name__ == "__main__":
    unittest.main()
