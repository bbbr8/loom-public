"""Alternative proof paths, shared dependencies and bounded exactness."""
import copy
import itertools
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import surd_search as s
import surd_reason as r
from reasoning_oracle import reference


def shared_contract():
    return {"claims": [
        {"id": x, "text": x, "assumption": True} for x in "abc"
    ] + [{"id": x, "text": x} for x in ("p", "q", "goal")], "rules": [
        {"id": "ap", "if": ["a"], "then": "p"},
        {"id": "bp", "if": ["b"], "then": "p"},
        {"id": "bq", "if": ["b"], "then": "q"},
        {"id": "cq", "if": ["c"], "then": "q"},
        {"id": "join", "if": ["p", "q"], "then": "goal"}
    ], "goal": "goal"}


def branching_contract(pairs):
    """2**pairs incomparable proof unions, within public graph-size limits."""
    claims, rules = [], []
    for i in range(pairs):
        for option in ("a", "b"):
            aid = option+str(i)
            claims.append({"id": aid, "text": aid, "assumption": True})
            rules.append({"id": aid, "if": [aid], "then": "p"+str(i)})
        claims.append({"id": "p"+str(i), "text": "choice"})
    for group, ids in [("left", range(6)), ("right", range(6, pairs))]:
        claims.append({"id": group, "text": group})
        rules.append({"id": group, "if": ["p"+str(i) for i in ids], "then": group})
    claims.append({"id": "goal", "text": "goal"})
    rules.append({"id": "join", "if": ["left", "right"], "then": "goal"})
    return {"claims": claims, "rules": rules}


class AssumptionFrontierTests(unittest.TestCase):
    def run_contract(self, spec=None):
        return s.reason(s.SearchIndex([{"id": "doc", "text": "Fixture record."}]),
                        "Fixture", shared_contract() if spec is None else spec)

    def test_shared_assumption_beats_greedy_choice(self):
        a = self.run_contract()["assessment"]
        self.assertEqual(a["conditional_on"], ["b"])
        self.assertEqual(a["assumption_sets"], [["b"], ["a", "c"]])
        self.assertTrue(a["assumption_analysis"]["complete"])
        self.assertEqual(a["assumptions_in_every_path"], [])
        self.assertIn("not minimum models", a["assumption_analysis"]["scope"])

    def test_rule_and_claim_traces_retain_alternatives(self):
        result = self.run_contract()
        self.assertEqual(result["argument"]["claims"]["p"]["assumption_sets"]["support"], [["a"], ["b"]])
        trace = next(x for x in result["argument"]["rules"] if x["id"] == "join")
        self.assertEqual(trace["assumption_sets"], [["b"], ["a", "c"]])
        self.assertEqual(result["argument"]["claims"]["goal"]["support"][0]["assumption_sets"], trace["assumption_sets"])

    def test_larger_local_path_can_make_smaller_global_union(self):
        spec = shared_contract()
        spec["rules"][1]["if"] = ["b", "c"]
        spec["rules"][2]["if"] = ["b", "c"]
        spec["rules"] = [x for x in spec["rules"] if x["id"] != "cq"]
        self.assertEqual(self.run_contract(spec)["assessment"]["assumption_sets"], [["b", "c"]])

    def test_unconditional_path_dominates_conditional_paths(self):
        spec = shared_contract()
        spec["claims"][-1]["support"] = [{"document": "doc", "quote": "Fixture record."}]
        result = self.run_contract(spec)
        self.assertEqual(result["assessment"]["status"], "supported")
        self.assertEqual(result["assessment"]["assumption_sets"], [[]])
        self.assertEqual(result["assessment"]["conditional_on"], [])

    def test_common_assumptions_reported_across_all_paths(self):
        spec = shared_contract()
        spec["claims"].append({"id": "d", "text": "d", "assumption": True})
        spec["rules"][-1]["if"].append("d")
        self.assertEqual(self.run_contract(spec)["assessment"]["assumptions_in_every_path"], ["d"])

    def test_opposition_frontier(self):
        spec = shared_contract(); spec["rules"][-1]["stance"] = "oppose"
        a = self.run_contract(spec)["assessment"]
        self.assertEqual(a["status"], "opposed")
        self.assertEqual(a["assumption_sets"], [["b"], ["a", "c"]])

    def test_defeater_clearance_uses_alternative_opposition_paths(self):
        spec = shared_contract()
        for rule in spec["rules"]:
            if rule["then"] == "q": rule["stance"] = "oppose"
        spec["rules"][-1].update({"if": ["p"], "unless": ["q"]})
        self.assertEqual(self.run_contract(spec)["assessment"]["assumption_sets"], [["b"], ["a", "c"]])

    def test_contested_goal_and_blocked_rules_do_not_claim_paths(self):
        spec = shared_contract()
        spec["claims"][-1]["oppose"] = [{"document": "doc", "quote": "Fixture record."}]
        result = self.run_contract(spec)
        self.assertEqual(result["assessment"]["status"], "contested")
        self.assertEqual(result["assessment"]["assumption_sets"], [])
        spec["claims"][3]["oppose"] = [{"document": "doc", "quote": "Fixture record."}]
        trace = next(x for x in self.run_contract(spec)["argument"]["rules"] if x["id"] == "join")
        self.assertEqual(trace["status"], "blocked")
        self.assertEqual(trace["assumption_sets"], [])

    def test_removal_exposes_lost_alternative_even_if_best_unchanged(self):
        result = self.run_contract()
        row = next(x for x in result["sensitivity"]["assumptions"] if x["assumption"] == "a")
        self.assertFalse(row["changes_goal"])
        self.assertTrue(row["changes_assumption_paths"])
        self.assertEqual(row["assumption_sets_after"], [["b"]])
        row = next(x for x in result["sensitivity"]["assumptions"] if x["assumption"] == "b")
        self.assertEqual(row["assumption_sets_after"], [["a", "c"]])

    def test_no_goal_or_unresolved_goal_has_no_paths(self):
        spec = shared_contract(); spec.pop("goal")
        self.assertEqual(self.run_contract(spec)["assessment"]["assumption_sets"], [])
        spec["goal"] = "goal"; spec["rules"] = []
        self.assertEqual(self.run_contract(spec)["assessment"]["assumption_sets"], [])

    def test_frontiers_do_not_claim_truth_after_other_assumptions_removed(self):
        spec = shared_contract()
        spec["claims"].append({"id":"switch", "text":"switch", "support":[{"document":"doc", "quote":"Fixture record."}]})
        spec["rules"].extend([
            {"id":"contest_switch", "if":["a"], "then":"switch", "stance":"oppose"},
            {"id":"oppose_goal", "if":["switch"], "then":"goal", "stance":"oppose"}])
        result = self.run_contract(spec)
        self.assertEqual(result["assessment"]["assumption_sets"], [["b"], ["a","c"]])
        # Removing a preserves the b support path but unblocks new opposition.
        removed = next(x for x in result["sensitivity"]["assumptions"] if x["assumption"] == "a")
        self.assertEqual(removed["goal_after"], "contested")

    def test_set_work_and_candidate_limits_fail_explicitly(self):
        for name, value, message in [("MAX_FRONTIER_SETS", 1, "set limit"),
                                     ("MAX_FRONTIER_WORK", 1, "work limit"),
                                     ("MAX_FRONTIER_CANDIDATES", 1, "candidate limit")]:
            with self.subTest(name=name), patch.object(r, name, value):
                with self.assertRaisesRegex(s.SearchError, message): self.run_contract()

    def test_shared_budget_includes_sensitivity_reruns(self):
        result = self.run_contract()
        claims, rules, order, deps, goal = r.prepare(s.SearchIndex([{"id":"doc", "text":"Fixture record."}]), shared_contract(), {}, {})
        budget = r.FrontierBudget(); r.audit(claims, rules, order, goal, budget=budget)
        self.assertGreater(result["assessment"]["assumption_analysis"]["work_used"], budget.work)
        with patch.object(r, "MAX_FRONTIER_WORK", budget.work):
            with self.assertRaisesRegex(s.SearchError, "work limit"): self.run_contract()

    def test_default_set_limit_accepts_boundary_and_rejects_explosion(self):
        result = self.run_contract(branching_contract(10))
        self.assertEqual(len(result["argument"]["claims"]["goal"]["assumption_sets"]["support"]), 1024)
        with self.assertRaisesRegex(s.SearchError, "set limit"):
            self.run_contract(branching_contract(11))

    def test_repeat_hash_persistence_and_legacy_policy(self):
        first = self.run_contract()
        self.assertEqual(first["report_hash"], self.run_contract()["report_hash"])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/"reason.json"); s.save(first, path)
            self.assertEqual(s.load(path), first)
        legacy = copy.deepcopy(first); legacy["policy"] = r.LEGACY_POLICY
        legacy["assessment"].pop("assumption_sets")
        legacy["report_hash"] = r.fingerprint(legacy)
        self.assertEqual(r.validate_report(legacy)["policy"], r.LEGACY_POLICY)
        self.assertIn("Alternative assumption paths: b OR a + c", r.render(first))

    def test_all_shared_path_shapes_against_exhaustive_oracle(self):
        choices = [("a",), ("b",), ("c",), ("a","b"), ("a","c"), ("b","c")]
        for left, right in itertools.product(itertools.combinations(choices, 2), repeat=2):
            spec = shared_contract(); spec["rules"] = []
            for target, paths in [("p", left), ("q", right)]:
                for i, path in enumerate(paths):
                    spec["rules"].append({"id": target+str(i), "if": list(path), "then": target})
            spec["rules"].append({"id":"join", "if":["p","q"], "then":"goal"})
            result = self.run_contract(spec); expected = reference(spec)
            self.assertEqual(result["assessment"]["assumption_sets"], expected[3])

    def test_input_order_does_not_change_frontiers(self):
        spec = shared_contract(); rng = random.Random(17)
        for _ in range(12):
            rng.shuffle(spec["claims"]); rng.shuffle(spec["rules"])
            self.assertEqual(self.run_contract(spec)["assessment"]["assumption_sets"], [["b"], ["a", "c"]])


if __name__ == "__main__":
    unittest.main()
