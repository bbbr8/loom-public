"""Synthetic adversarial posture contracts, persistence and entrypoint checks."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd
import surd_search as search
import surd_posture as p


def fixture():
    docs = [
        {"id": "ledger", "text": "The builder recorded a credit of $100.", "locator": "synthetic ledger row 1", "metadata": {"author_side": "opponent"}},
        {"id": "copy", "text": "The builder recorded a credit of $100.", "locator": "synthetic copy row 1"},
        {"id": "demand", "text": "The demand lists no credit.", "locator": "synthetic demand p1", "metadata": {"author_side": "opponent"}},
        {"id": "response", "text": "The credit was applied elsewhere.", "locator": "synthetic response p2"},
        {"id": "adverse", "text": "The supplier reversed the credit.", "locator": "synthetic supplier row 2", "metadata": {"author_side": "third_party"}},
    ]
    claims = [
        {"id": "issued", "text": "The builder recorded a credit.", "support": [{"document": d, "quote": docs[0]["text"]} for d in ["ledger", "copy"]]},
        {"id": "demand", "text": "The demand lists no credit.", "support": [{"document": "demand", "quote": docs[2]["text"]}]},
        {"id": "difference", "text": "The ledger and demand differ in their displayed credit treatment."},
        {"id": "application", "text": "The credit was actually applied to this owner's obligation."},
        {"id": "knowledge", "text": "The builder knew of the discrepancy when making the demand."},
        {"id": "concede", "text": "Assume they concede the credit was due.", "assumption": True},
        {"id": "hold", "text": "Assume they hold that no credit was due.", "assumption": True},
        {"id": "consequence_c", "text": "Under the declared concession, the demand requires reconciliation."},
        {"id": "consequence_h", "text": "Under the declared refusal, their ledger requires explanation."},
    ]
    rules = [
        {"id": "compare", "if": ["issued", "demand"], "then": "difference"},
        {"id": "concession", "if": ["concede", "difference"], "then": "consequence_c"},
        {"id": "refusal", "if": ["hold", "issued"], "then": "consequence_h"},
    ]
    spec = {"argument": {"claims": claims, "rules": rules, "facets": [{"id": "challenge", "query": "reversed credit", "role": "challenge"}]},
            "issues": [{"id": "credit", "title": "Credit treatment", "goal": "difference", "floor": "issued", "materiality": "high",
                        "coordinates": {"actor": "Builder", "stage": "issued versus applied", "knowledge_as_of": None},
                        "bridges": [{"role": "actual_application", "claim": "application"}, {"role": "actor_knowledge", "claim": "knowledge"}],
                        "opponent": {"text": "The credit was applied elsewhere.", "citations": [{"document": "response", "quote": docs[3]["text"]}]},
                        "forks": [{"id": "concede", "label": "credit was due", "assume": ["concede"], "goal": "consequence_c"},
                                  {"id": "hold", "label": "no credit was due", "assume": ["hold"], "goal": "consequence_h"}]}],
            "source_families": {"ledger": "ledger-origin", "copy": "ledger-origin", "demand": "demand-origin", "response": "response-origin"},
            "records": [{"id": "subledger", "claims": ["application", "knowledge"], "record": "Complete job subledger and posting audit", "custodian": "Builder bookkeeper", "identifier": "synthetic job A, period 1",
                         "if_confirms": "A posted credit to job A narrows or resolves the application gap.", "if_weakens": "A posting to another job leaves this owner's credit treatment open."}]}
    return search.SearchIndex(docs), spec


class PostureTests(unittest.TestCase):
    def setUp(self):
        self.index, self.spec = fixture()

    def report(self):
        return p.posture(self.index, "How does the credit treatment compare?", self.spec)

    def test_record_floor_survives_open_knowledge_and_application(self):
        r = self.report(); issue = r["issues"][0]
        self.assertEqual(issue["assessment"]["status"], "supported")
        self.assertEqual(issue["record_floor"]["status"], "supported")
        self.assertTrue(all(b["assessment"]["status"] == "unresolved" for b in issue["bridges"]))
        self.assertIsNone(issue["coordinates"]["knowledge_as_of"])
        self.assertFalse(r["verification"]["legal_elements_assessed"])

    def test_branches_never_become_baseline_facts(self):
        r = self.report()
        for cid in ["concede", "hold", "consequence_c", "consequence_h"]:
            self.assertEqual(r["reasoning"]["argument"]["claims"][cid]["status"], "unresolved")
        self.assertEqual(r["branch_assumptions_excluded_from_baseline"], ["concede", "hold"])

    def test_each_fork_is_conditioned_only_on_its_own_assumption(self):
        issue = self.report()["issues"][0]
        self.assertTrue(issue["two_branches_supported"])
        for f in issue["forks"]:
            self.assertEqual(f["assessment"]["conditional_on"], f["assume"])
            self.assertEqual(f["assessment"]["status"], "conditional")

    def test_unconditional_fact_cannot_fake_two_consequential_forks(self):
        for f in self.spec["issues"][0]["forks"]: f["goal"] = "issued"
        issue = self.report()["issues"][0]
        self.assertFalse(issue["two_branches_supported"])
        self.assertTrue(all(not f["supported_under_stated_branch"] for f in issue["forks"]))

    def test_global_assumption_remains_an_additional_fork_gap(self):
        self.spec["argument"]["claims"].append({"id": "scope", "text": "The scopes match.", "assumption": True})
        self.spec["argument"]["rules"][1]["if"].append("scope")
        f = self.report()["issues"][0]["forks"][0]
        self.assertFalse(f["supported_under_stated_branch"])
        self.assertIn("scope", f["assessment"]["conditional_on"])

    def test_same_origin_copies_withdraw_together(self):
        issue = self.report()["issues"][0]
        f = next(f for f in issue["family_withdrawals"] if "ledger" in f["documents"])
        self.assertEqual(f["documents"], ["copy", "ledger"])
        self.assertEqual(f["goal_after"]["status"], "unresolved")
        self.assertEqual(f["floor_after"]["status"], "unresolved")

    def test_identical_text_merges_even_if_declared_as_independent(self):
        self.spec["source_families"]["copy"] = "claimed-other-origin"
        r = self.report()
        g = next(g for g in r["source_families"] if "ledger" in g["documents"])
        self.assertEqual(g["documents"], ["copy", "ledger"])
        self.assertFalse(g["independence_verified"])

    def test_unknown_origins_do_not_manufacture_independence(self):
        self.spec["source_families"] = {}
        groups = self.report()["source_families"]
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["unknown_lineage"])

    def test_actual_opposition_blocks_broad_claim_without_resetting_floor(self):
        self.spec["argument"]["claims"][2]["oppose"] = [{"document": "adverse", "quote": "The supplier reversed the credit."}]
        self.spec["source_families"]["adverse"] = "supplier"
        issue = self.report()["issues"][0]
        self.assertEqual(issue["assessment"]["status"], "contested")
        self.assertEqual(issue["record_floor"]["status"], "supported")
        self.assertEqual(issue["adverse_evidence"][0]["citations"][0]["document"], "adverse")
        self.assertFalse(issue["two_branches_supported"])

    def test_hypothetical_opponent_account_is_labeled(self):
        self.spec["issues"][0]["opponent"].pop("citations")
        self.spec["source_families"].pop("response")
        r = self.report()
        self.assertEqual(r["issues"][0]["opponent"]["status"], "hypothetical")
        self.assertTrue(any(x.get("kind") == "opponent_account_missing" for x in r["open_checks"]))

    def test_unreviewed_challenge_hits_remain_leads(self):
        r = self.report()
        self.assertEqual(r["issues"][0]["adverse_evidence"], [])
        self.assertTrue(any(x["kind"] == "challenge_unreviewed" for x in r["reasoning"]["next_steps"]))

    def test_next_record_keeps_both_outcomes_and_actual_issue_links(self):
        r = self.report()["next_records"][0]
        self.assertEqual(r["issues"], ["credit"])
        self.assertEqual([x["status"] for x in r["claim_states"]], ["unresolved", "unresolved"])
        self.assertTrue(r["if_confirms"] and r["if_weakens"])

    def test_explanation_order_is_distinct_from_research_priority(self):
        self.spec["issues"][0]["materiality"] = "low"
        self.spec["issues"].append({"id": "knowledge", "title": "Knowledge", "goal": "knowledge", "materiality": "high"})
        r = self.report()
        self.assertEqual(r["lead_order"], ["credit", "knowledge"])
        self.assertEqual(r["research_order"], ["knowledge", "credit"])

    def test_adverse_authorship_cannot_upgrade_support_source_ranking(self):
        self.spec["argument"]["claims"][4]["support"] = [{"document": "response", "quote": "The credit was applied elsewhere."}]
        self.spec["argument"]["claims"][4]["oppose"] = [{"document": "adverse", "quote": "The supplier reversed the credit."}]
        self.spec["issues"][0]["goal"] = "knowledge"
        self.spec["source_families"]["adverse"] = "supplier"
        self.assertEqual(self.report()["issues"][0]["opponent_or_third_party_sources"], [])

    def test_fabricated_and_stale_and_out_of_scope_quotes_fail(self):
        for change in [lambda s: s["argument"]["claims"][0]["support"][0].update(quote="invented"),
                       lambda s: s["argument"]["claims"][0]["support"][0].update(content_hash="stale"),
                       lambda s: s["argument"].update(where={"version": "absent"})]:
            spec = copy.deepcopy(self.spec); change(spec)
            with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)

    def test_circular_rules_and_unknown_refs_fail(self):
        spec = copy.deepcopy(self.spec); spec["argument"]["rules"].append({"id": "cycle", "if": ["difference"], "then": "issued"})
        with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)
        self.spec["issues"][0]["goal"] = "invented"
        with self.assertRaises(search.SearchError): self.report()

    def test_invalid_branch_claims_and_shared_branch_assumptions_fail(self):
        for active in [["issued"], ["concede", "concede"], [[]], []]:
            spec = copy.deepcopy(self.spec); spec["issues"][0]["forks"][0]["assume"] = active
            with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)
        self.spec["issues"][0]["forks"][1]["assume"] = ["concede"]
        with self.assertRaises(search.SearchError): self.report()

    def test_malformed_materiality_raises_search_error(self):
        for bad in [[], {}, None, True, 7]:
            with self.subTest(value=bad):
                self.spec["issues"][0]["materiality"] = bad
                with self.assertRaises(search.SearchError): self.report()

    def test_invalid_record_specs_and_unrelated_tasks_fail(self):
        for change in [lambda s: s["records"][0].pop("if_weakens"),
                       lambda s: s["records"][0].update(claims=[[]]),
                       lambda s: s["source_families"].update(invented="origin"),
                       lambda s: s["issues"][0].update(materiality="certain"),
                       lambda s: s.update(issues=[])]:
            spec = copy.deepcopy(self.spec); change(spec)
            with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)

    def test_duplicate_issue_bridge_record_and_fork_ids_fail(self):
        for path in ["issues", "records"]:
            spec = copy.deepcopy(self.spec); spec[path].append(copy.deepcopy(spec[path][0]))
            with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)
        for path in ["bridges", "forks"]:
            spec = copy.deepcopy(self.spec); spec["issues"][0][path][1] = copy.deepcopy(spec["issues"][0][path][0])
            with self.assertRaises(search.SearchError): p.posture(self.index, "credit", spec)

    def test_input_snapshots_are_unchanged(self):
        before = copy.deepcopy(self.spec); manifest = self.index.manifest()
        self.report()
        self.assertEqual(self.spec, before)
        self.assertEqual(self.index.manifest(), manifest)

    def test_report_hash_is_stable_and_detects_nested_tampering(self):
        a, b = self.report(), self.report()
        self.assertEqual(a["report_hash"], b["report_hash"])
        self.assertEqual(p.validate_report(a), a)
        a["issues"][0]["record_floor"]["status"] = "unresolved"
        with self.assertRaises(search.SearchError): p.validate_report(a)
        b["reasoning"]["assessment"]["status"] = "supported"
        with self.assertRaises(search.SearchError): p.validate_report(b)

    def test_save_load_and_cli_brief_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); ix = tmp/"index.json"; spec = tmp/"spec.json"; report = tmp/"report.json"
            search.save(self.index, ix); spec.write_text(json.dumps(self.spec))
            env = dict(os.environ, SURD_PORTALS="0", PYTHONDONTWRITEBYTECODE="1")
            for extra in [[], ["--json"]]:
                run = subprocess.run([sys.executable, str(ROOT/"surd.py"), "search", "posture", str(ix), "credit", "--spec", str(spec), "-o", str(report)]+extra,
                                     env=env, capture_output=True, text=True, timeout=8)
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                if extra: self.assertEqual(json.loads(run.stdout)["schema"], p.SCHEMA)
                else: self.assertIn("Record floor [supported]", run.stdout)
            self.assertEqual(search.load(report)["schema"], p.SCHEMA)

    def test_native_language_builtin_and_bad_arity(self):
        row = {"id":"a", "text":"A credit was issued."}
        spec = {"argument":{"claims":[{"id":"c","text":"A credit was issued.","support":[{"document":"a","quote":row["text"]}]}]},"issues":[{"id":"c","title":"Credit", "goal":"c"}]}
        src = 'let ix = search_index('+json.dumps([row])+')\nlet r = search_posture(ix, "credit", '+json.dumps(spec)+')\nexpect r["issues"][0]["assessment"]["status"] == "supported"'
        it = surd.Interp(echo=False).run(src)
        self.assertTrue(it.expects[0]["ok"])
        with self.assertRaises(surd.SurdError): surd.Interp(echo=False).run('search_posture(1, "x")')

    def test_readable_report_retains_opponent_quotes_open_bridges_and_both_outcomes(self):
        brief = p.render(self.report())
        for phrase in ["synthetic response p2", "actor_knowledge [unresolved]", "If concede", "If hold", "If confirms:", "If weakens:", "Withdraw family"]:
            self.assertIn(phrase, brief)


if __name__ == "__main__": unittest.main()
