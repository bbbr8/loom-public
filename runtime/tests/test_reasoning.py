"""Frozen synthetic reasoning contracts; no case facts or automatic entailment labels."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd_search as s

DOCS = [
    {"id": "test", "text": "The test passed for version 2. Version 3 is untested.",
     "locator": "tests.txt:1", "metadata": {"kind": "test_log", "version": 2}},
    {"id": "review", "text": "The review approved version 2.", "locator": "review.txt:1",
     "metadata": {"kind": "review", "version": 2}},
    {"id": "counter", "text": "The test failed for version 2.", "locator": "counter.txt:1",
     "metadata": {"kind": "test_log", "version": 2}},
    {"id": "scope", "text": "The target is version 2. The exception does not apply.",
     "locator": "scope.txt:1", "metadata": {"kind": "specification", "version": 2}},
    {"id": "later", "text": "The test passed for version 3.", "locator": "later.txt:1",
     "metadata": {"kind": "test_log", "version": 3}},
]


def citation(doc, quote):
    return {"document": doc, "quote": quote}


def contract():
    return {"claims": [
        {"id": "passes", "text": "The test passes for version 2.",
         "support": [citation("test", "The test passed for version 2.")]},
        {"id": "in_scope", "text": "Version 2 is the target.",
         "support": [citation("scope", "The target is version 2.")]},
        {"id": "ready", "text": "The target is ready under the declared test rule."}],
        "rules": [{"id": "readiness", "if": ["passes", "in_scope"], "then": "ready"}],
        "goal": "ready"}


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.ix = s.SearchIndex(DOCS)

    def reason(self, spec=None):
        return s.reason(self.ix, "Is version 2 ready?", contract() if spec is None else spec)

    def test_grounded_chain_and_quotations(self):
        r = self.reason()
        self.assertEqual(r["assessment"]["status"], "supported")
        self.assertEqual(r["argument"]["claims"]["ready"]["support"][0]["rule"], "readiness")
        evidence = r["argument"]["claims"]["passes"]["support"][0]
        self.assertEqual(DOCS[0]["text"][evidence["start"]:evidence["end"]], evidence["quote"])
        self.assertIn("caller-declared", r["assessment"]["basis"])
        self.assertFalse(r["assessment"]["entailment_verified"])

    def test_missing_premise_is_open_not_false(self):
        spec = contract(); spec["claims"][1].pop("support")
        r = self.reason(spec)
        self.assertEqual(r["assessment"]["status"], "unresolved")
        self.assertEqual(r["argument"]["claims"]["in_scope"]["status"], "unresolved")
        self.assertEqual(r["next_steps"][0]["claim"], "in_scope")

    def test_conflicted_premise_cannot_authorize_a_conclusion(self):
        spec = contract(); spec["claims"][0]["oppose"] = [citation("counter", "The test failed for version 2.")]
        r = self.reason(spec)
        self.assertEqual(r["argument"]["claims"]["passes"]["status"], "contested")
        self.assertEqual(r["assessment"]["status"], "unresolved")
        self.assertEqual(r["argument"]["rules"][0]["status"], "blocked")

    def test_goal_opposition_is_retained(self):
        spec = contract(); spec["claims"][2]["oppose"] = [citation("counter", "The test failed for version 2.")]
        r = self.reason(spec)
        self.assertEqual(r["assessment"]["status"], "contested")
        removal = next(x for x in r["sensitivity"]["documents"] if x["document"] == "counter")
        self.assertEqual(removal["goal_after"], "supported")

    def test_unknown_defeater_is_not_assumed_absent(self):
        spec = contract(); spec["claims"].append({"id": "exception", "text": "An exception applies."})
        spec["rules"][0]["unless"] = ["exception"]
        self.assertEqual(self.reason(spec)["assessment"]["status"], "unresolved")
        spec["claims"][-1]["oppose"] = [citation("scope", "The exception does not apply.")]
        self.assertEqual(self.reason(spec)["assessment"]["status"], "supported")
        spec["claims"][-1]["support"] = [citation("review", "The review approved version 2.")]
        self.assertEqual(self.reason(spec)["assessment"]["status"], "unresolved")

    def test_assumptions_remain_conditional_and_can_be_removed(self):
        spec = contract(); spec["claims"][1] = {"id": "in_scope", "text": "Version 2 is the target.", "assumption": True}
        r = self.reason(spec)
        self.assertEqual(r["assessment"]["status"], "conditional")
        self.assertEqual(r["assessment"]["conditional_on"], ["in_scope"])
        self.assertEqual(r["sensitivity"]["assumptions"][0]["goal_after"], "unresolved")

    def test_alternative_support_survives_document_removal(self):
        spec = contract(); spec["claims"][0]["support"].append(citation("review", "The review approved version 2."))
        r = self.reason(spec)
        removed = next(x for x in r["sensitivity"]["documents"] if x["document"] == "test")
        self.assertEqual(removed["goal_after"], "supported")
        self.assertFalse(removed["changes_goal"])

    def test_conditional_alternative_does_not_hide_unconditional_path(self):
        spec = contract(); spec["claims"].append({"id": "assumed", "text": "An assumed alternative.", "assumption": True})
        spec["rules"].append({"id": "conditional_path", "if": ["assumed"], "then": "ready"})
        r = self.reason(spec)
        self.assertEqual(r["assessment"]["status"], "supported")
        self.assertEqual(r["assessment"]["conditional_on"], [])

    def test_unsupported_quote_unknown_source_and_stale_snapshot_fail(self):
        for entry in [citation("test", "The test failed"), citation("missing", "x"),
                      dict(citation("test", "The test passed for version 2."), content_hash="stale")]:
            with self.subTest(entry=entry), self.assertRaises(s.SearchError):
                spec = contract(); spec["claims"][0]["support"] = [entry]; self.reason(spec)

    def test_ambiguous_quote_requires_explicit_offset(self):
        ix = s.SearchIndex([{"id": "repeat", "text": "yes yes"}]); spec = {"claims": [{"id": "a", "text": "A", "support": [citation("repeat", "yes")]}], "goal": "a"}
        with self.assertRaises(s.SearchError): s.reason(ix, "yes", spec)
        spec["claims"][0]["support"][0]["start"] = 4
        self.assertEqual(s.reason(ix, "yes", spec)["assessment"]["status"], "supported")

    def test_citations_cannot_escape_metadata_scope(self):
        spec = contract(); spec["where"] = {"version": 3}
        with self.assertRaises(s.SearchError): self.reason(spec)

    def test_cycles_self_support_unknown_refs_and_empty_rules_fail(self):
        for rules in [[{"id": "self", "if": ["ready"], "then": "ready"}],
                      [{"id": "r1", "if": ["passes"], "then": "ready"}, {"id": "r2", "if": ["ready"], "then": "passes"}],
                      [{"id": "r", "if": ["missing"], "then": "ready"}],
                      [{"id": "r", "if": [], "then": "ready"}]]:
            with self.subTest(rules=rules), self.assertRaises(s.SearchError):
                spec = contract(); spec["rules"] = rules; self.reason(spec)

    def test_negative_conclusion_requires_an_explicit_rule(self):
        spec = contract(); spec["rules"][0]["stance"] = "oppose"
        self.assertEqual(self.reason(spec)["assessment"]["status"], "opposed")

    def test_query_brief_names_unknown_tokens_and_partial_coverage(self):
        r = s.reason(self.ix, "test impossible_paraphrase")
        self.assertEqual(r["assessment"]["status"], "not_assessed")
        self.assertIn("impossible_paraphrase", r["retrieval"]["unmatched_terms"])
        self.assertEqual(r["argument"]["claims"], {})
        self.assertIn("coverage", r["retrieval"]["results"]["hits"][0])

    def test_facets_are_explicit_scope_preserving_and_not_evidence(self):
        r = self.reason(dict(contract(), where={"version": 2}, facets=[{"id": "challenge", "query": '"test failed"', "role": "challenge"}]))
        self.assertEqual(r["assessment"]["status"], "supported")
        self.assertEqual(r["facets"][0]["results"]["hits"][0]["id"], "counter")
        self.assertEqual(r["facets"][0]["results"]["scope"]["where"], {"version": 2})
        self.assertTrue(any(x["kind"] == "challenge_unreviewed" for x in r["next_steps"]))

    def test_diagnostics_separate_question_framing_without_dropping_negation(self):
        r = s.reason(s.SearchIndex([DOCS[0]]), "Does this test establish not disproved without assumptions?")
        self.assertIn("does", r["retrieval"]["unmatched_terms"])
        self.assertNotIn("does", r["retrieval"]["unmatched_content_terms"])
        self.assertIn("without", r["retrieval"]["unmatched_content_terms"])
        terms = [t for c in r["retrieval"]["results"]["clauses"] for t in c["tokens"]]
        self.assertIn("does", terms)
        self.assertIn("not", terms)

    def test_duplicate_citations_do_not_strengthen_argument(self):
        spec = contract(); spec["claims"][0]["support"] *= 3
        self.assertEqual(len(self.reason(spec)["argument"]["claims"]["passes"]["support"]), 1)

    def test_challenge_in_a_support_source_still_requires_review(self):
        spec = dict(contract(), facets=[{"id": "check", "query": '"Version 3 is untested"', "role": "challenge"}])
        steps = self.reason(spec)["next_steps"]
        self.assertTrue(any(x["kind"] == "challenge_unreviewed" and "test" in x["documents"] for x in steps))

    def test_saved_report_stable_hash_and_tamper_detection(self):
        a, b = self.reason(), self.reason()
        self.assertEqual(a["report_hash"], b["report_hash"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"reason.json"; s.save(a, p)
            self.assertEqual(s.load(p)["report_hash"], a["report_hash"])
            a["assessment"]["status"] = "opposed"; s.save(a, p)
            with self.assertRaises(s.SearchError): s.load(p)

    def test_strict_options_and_bounds(self):
        for spec in [{"unknown": True}, {"k": True}, {"claims": [] , "goal": "missing"},
                     {"claims": [{"id": "x", "text": "X", "assumption": "yes"}]},
                     {"facets": [{"id": "x", "query": "test", "role": "proof"}]}]:
            with self.subTest(spec=spec), self.assertRaises(s.SearchError): self.reason(spec)

    def test_source_metadata_is_never_excluded_from_fingerprint(self):
        ix = s.SearchIndex([dict(DOCS[0], metadata={"elapsed_ms": 12, "report_hash": "source", "schema": "surd.search.results.v1"})])
        spec = {"claims": [contract()["claims"][0]], "goal": "passes"}
        r = s.reason(ix, "test", spec)
        from surd_reason import validate_report
        r["argument"]["claims"]["passes"]["support"][0]["metadata"]["elapsed_ms"] = 13
        with self.assertRaises(s.SearchError): validate_report(r)

    def test_cli_and_language_return_the_same_assessment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); ix, spec = tmp/"index.json", tmp/"contract.json"
            s.save(self.ix, ix); s.save(contract(), spec)
            p = subprocess.run([sys.executable, str(ROOT/"surd.py"), "search", "reason", str(ix), "Is version 2 ready?", "--spec", str(spec), "--json"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            self.assertEqual(json.loads(p.stdout)["assessment"]["status"], "supported")
            program = tmp/"reason.surd"
            # Use an in-program faithful fixture to exercise nested maps and source quotation validation.
            program.write_text('let ix = search_index(' + json.dumps(DOCS) + ')\nlet r = search_reason(ix, "Is version 2 ready?", ' + json.dumps(contract()) + ')\nexpect r["assessment"]["status"] == "supported"\n')
            p = subprocess.run([sys.executable, str(ROOT/"surd.py"), "run", str(program)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
