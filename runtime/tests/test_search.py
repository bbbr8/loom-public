"""Retrieval/evaluation behavior and counterexamples from the Surd research pass."""
import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd
import surd_search as s

DOCS = [
    {"id": "DEMO_00151", "title": "Bank draw", "text": "Payment was not authorized. Amount $1,200.50.", "locator": "bank.pdf:3", "metadata": {"lane": "bank", "date": "2020-01-01", "tier": "native"}},
    {"id": "DEMO_00152", "title": "Owner authorization", "text": "Payment was authorized. Bank transfer approved.", "locator": "owner.pdf:2", "metadata": {"lane": "owner", "date": "2020-02-01", "tier": "native"}},
    {"id": "summary", "title": "Later annotation", "text": "Bank payment draw authorization discussed repeatedly payment payment.", "locator": "notes.md:1", "metadata": {"lane": "bank", "tier": "annotation"}},
    {"id": "empty", "text": "", "locator": "blank.txt:1"},
]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.ix = s.SearchIndex(DOCS)

    def ids(self, query, opts=None):
        return [h["id"] for h in self.ix.search(query, opts)["hits"]]

    def test_describe_examples_work_through_a_symlink_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp)/"surd"
            launcher.symlink_to(ROOT/"surd.py")
            p = subprocess.run([sys.executable, str(launcher), "describe", "--json"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            builtins = json.loads(p.stdout)["builtins"]
            failures = [x for x in builtins if x["scope"] == "portable" and x["verified"] is not True]
            self.assertEqual(failures, [])
            self.assertEqual({x["name"] for x in builtins if x["verified"] is None}, {"docs", "anchor_check", "stages"})

    def test_exact_identifier_and_stale_identifier(self):
        self.assertEqual(self.ids("demo_00151"), ["DEMO_00151"])
        self.assertEqual(self.ids("DOC_99999"), [])

    def test_ordinary_topic_word_is_not_an_identifier_in_a_longer_query(self):
        ix = s.SearchIndex([{"id": "bell", "text": "bell pair"},
                            {"id": "noisy_bell", "text": "depolarizing noise bell pair"}])
        r = ix.search("depolarizing noise bell pair")
        self.assertEqual(r["hits"][0]["id"], "noisy_bell")
        self.assertEqual(next(h for h in r["hits"] if h["id"] == "bell")["explanation"]["matched_identifiers"], [])
        self.assertEqual(ix.search("bell")["hits"][0]["explanation"]["matched_identifiers"], ["bell"])

    def test_phrase_preserves_negation(self):
        self.assertEqual(self.ids('"not authorized"'), ["DEMO_00151"])
        self.assertEqual(self.ids('"was authorized"'), ["DEMO_00152"])
        self.assertIn("DEMO_00151", self.ids("authorized"))  # no invented semantic polarity
        self.assertIn("DEMO_00152", self.ids("authorized"))

    def test_required_excluded_terms(self):
        self.assertEqual(self.ids("payment +authorized -not"), ["DEMO_00152"])
        self.assertEqual(self.ids('payment -"not authorized"', {"where": {"tier": "native"}}), ["DEMO_00152"])

    def test_unknown_scope_never_passes_filter(self):
        self.assertEqual(self.ids("payment", {"where": {"tier": "native", "lane": "bank"}}), ["DEMO_00151"])
        self.assertEqual(self.ids("bank", {"ranges": {"date": {"gte": "2020-02-01"}}}), ["DEMO_00152"])
        self.assertEqual(self.ids("payment", {"where": {"missing": "value"}}), [])

    def test_amount_and_unicode(self):
        self.assertEqual(self.ids("1,200.50"), ["DEMO_00151"])
        ix = s.SearchIndex([{"id": "x", "text": "ＣＡＦÉ résumé"}])
        self.assertEqual(len(ix.search("café résumé")["hits"]), 1)

    def test_no_positive_query_and_empty_index(self):
        self.assertEqual(self.ix.search("-payment")["status"], "empty_query")
        self.assertEqual(s.SearchIndex([]).search("x")["status"], "no_documents")

    def test_source_snapshot_and_explanation(self):
        hit = self.ix.search("authorized")["hits"][0]
        d = next(d for d in DOCS if d["id"] == hit["id"])
        snip = hit["snippet"]
        self.assertEqual(snip["text"], d["text"][snip["start"]:snip["end"]])
        exp = hit["explanation"]
        self.assertAlmostEqual(hit["score"], exp["lexical_score"] + exp["bonus"])
        self.assertEqual(hit["locator"], d["locator"])
        self.assertEqual(len(hit["content_hash"]), 64)

    def test_aliases_are_explicit_and_snapshot_immutable(self):
        rows, cfg = copy.deepcopy(DOCS), {"aliases": {"approved": ["authorized"]}}
        ix = s.SearchIndex(rows, cfg)
        before = ix.manifest()
        rows[0]["text"] = "changed"
        cfg["aliases"]["approved"].append("payment")
        self.assertEqual(ix.manifest(), before)
        self.assertEqual(ix.search("approved")["expansions"], {"approved": ["authorized"]})

    def test_ties_and_hashes_do_not_depend_on_input_order(self):
        rows = [{"id": "z", "text": "same"}, {"id": "a", "text": "same"}]
        a, b = s.SearchIndex(rows), s.SearchIndex(list(reversed(rows)))
        self.assertEqual(a.manifest(), b.manifest())
        self.assertEqual([h["id"] for h in a.search("same")["hits"]], ["a", "z"])

    def test_invalid_inputs_fail(self):
        for rows in ([{"text": "missing id"}], [DOCS[0], DOCS[0]], [{"id": "x", "text": None}], [{"id": "x", "text": "x", "metadata": {"n": float("nan")}}]):
            with self.subTest(rows=rows), self.assertRaises(s.SearchError):
                s.SearchIndex(rows)
        for opts in ({"k": 0}, {"k": True}, {"where": []}, {"ranges": {"date": {"gt": 0}}}, {"mode": "magic"}, {"typo": True}):
            with self.subTest(opts=opts), self.assertRaises(s.SearchError):
                self.ix.search("x", opts)
        with self.assertRaises(s.SearchError):
            self.ix.search('"unfinished')

    def test_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"index.json"
            receipt = s.save(self.ix, p)
            self.assertEqual(s.SearchIndex.load(p).manifest(), self.ix.manifest())
            self.assertEqual(receipt["bytes"], p.stat().st_size)
            raw = s.read_json(p); raw["documents"][0]["text"] = "tampered"; s.save(raw, p)
            with self.assertRaises(s.SearchError):
                s.SearchIndex.load(p)

    def test_csv_jsonl_and_readonly_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/"a.csv").write_text('id,text,locator\nx,"one, two",source:1\n')
            (p/"a.jsonl").write_text('{"id":"x","text":"one"}\n')
            self.assertEqual(s.load_rows(p/"a.csv")[0]["text"], "one, two")
            self.assertEqual(s.load_rows(p/"a.jsonl")[0]["id"], "x")
            db = p/"a.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("create table docs(id text,text text)")
                con.execute("insert into docs values('x','one')")
            self.assertEqual(s.load_rows(db, {"query": "select * from docs"})[0]["text"], "one")
            with self.assertRaises(s.SearchError):
                s.load_rows(db, {"query": "delete from docs returning *"})
            with self.assertRaises(s.SearchError):
                s.load_rows(db)

    def test_fusion_fences_sources_and_duplicates(self):
        a = self.ix.search("payment", {"mode": "bm25"})
        b = self.ix.search("payment", {"mode": "hybrid"})
        result = s.fuse([a, b])
        ident = result["hits"][0]["id"]
        expected = sum(1/(60+next(i for i, h in enumerate(r["hits"], 1) if h["id"] == ident)) for r in (a, b))
        self.assertAlmostEqual(result["hits"][0]["score"], expected)
        for field, replacement in [("corpus_hash", "other"), ("query", "other"), ("scope", {"where": {"lane": "owner"}})]:
            bad = copy.deepcopy(b); bad[field] = replacement
            with self.assertRaises(s.SearchError): s.fuse([a, bad])
        bad = copy.deepcopy(b); bad["hits"].append(bad["hits"][0])
        with self.assertRaises(s.SearchError): s.fuse([a, bad])
        bad = copy.deepcopy(b); bad["hits"][0]["content_hash"] = "other"
        with self.assertRaises(s.SearchError): s.fuse([a, bad])


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.ix = s.SearchIndex(DOCS)
        self.queries = [{"id": "q1", "query": "DEMO_00151", "relevant": {"DEMO_00151": 3, "summary": 0}},
                        {"id": "q2", "query": "missingzz", "relevant": {}, "answerable": False},
                        {"id": "q3", "query": "bank"},
                        {"id": "q4", "query": "bank", "relevant": {"summary": 0}}]

    def test_metrics_against_hand_calculation(self):
        m = s.metrics(["x", "a", "b"], {"a": 3, "b": 1}, [1, 2, 3])
        self.assertEqual(m["precision@3"], 2/3)
        self.assertEqual(m["recall@3"], 1)
        self.assertEqual(m["mrr@3"], .5)
        self.assertAlmostEqual(m["map@3"], (.5+2/3)/2)
        self.assertAlmostEqual(m["ndcg@3"], (3/math.log2(3)+1/math.log2(4))/(3+1/math.log2(3)))
        self.assertEqual(m["precision@1"], 0)

    def test_coverage_and_no_answer_separate_from_relevance(self):
        r = s.evaluate(self.ix, self.queries)
        self.assertEqual(r["coverage"], {"queries": 4, "corpus_documents": 4, "judged": 1, "known_no_answer": 1, "unjudged": 1, "no_positive_judgments": 1})
        self.assertEqual(r["aggregate"]["mrr@10"], 1)
        self.assertEqual(r["aggregate"]["no_answer_accuracy"], 1)
        self.assertEqual(r["metric_query_counts"]["mrr@10"], 1)
        self.assertTrue(r["queries"][2]["unjudged_hits"])
        s.validate_run(r)

    def test_stable_run_hash_not_wall_clock(self):
        a, b = s.evaluate(self.ix, self.queries), s.evaluate(self.ix, self.queries)
        self.assertEqual(a["manifest"]["run_hash"], b["manifest"]["run_hash"])

    def test_missing_judgments_are_not_perfect(self):
        r = s.evaluate(self.ix, [{"id": "q", "query": "bank"}])
        self.assertEqual(r["aggregate"], {})
        with self.assertRaises(s.SearchError): s.compare(r, r)

    def test_split_and_leakage(self):
        qs = self.queries + [{"id": "dev", "query": "bank", "split": "dev"}]
        r = s.evaluate(self.ix, qs)
        self.assertEqual(r["manifest"]["omitted_queries"], 1)
        with self.assertRaises(s.SearchError): s.evaluate(self.ix, qs, {"training_query_ids": ["q1"]})

    def test_bad_judgments_and_scope(self):
        for q in [{"id": "q", "query": "bank", "relevant": {"no_such_id": 1}},
                  {"id": "q", "query": "bank", "relevant": {"DEMO_00151": -1}},
                  {"id": "q", "query": "bank", "relevant": {"DEMO_00151": 1}, "where": {"lane": "owner"}},
                  {"id": "q", "query": "bank", "answerable": False},
                  {"id": "q", "query": "bank", "answerable": False, "relevant": {"DEMO_00151": 1}}]:
            with self.subTest(q=q), self.assertRaises(s.SearchError): s.evaluate(self.ix, [q])

    def test_external_rankings_and_regression_exit(self):
        q = self.queries[:1]
        good = s.evaluate(self.ix, q, {"rankings": {"q1": ["DEMO_00151", "summary"]}})
        bad = s.evaluate(self.ix, q, {"rankings": {"q1": ["summary", "DEMO_00151"]}})
        cmp = s.compare(good, bad)
        self.assertFalse(cmp["passed"])
        self.assertEqual(cmp["losses"], 1)
        self.assertLess(cmp["delta"], 0)
        self.assertTrue(s.compare(bad, good)["passed"])
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d)/"a.json", Path(d)/"b.json"
            s.save(good, a); s.save(bad, b)
            p = subprocess.run([sys.executable, str(ROOT/"surd.py"), "search", "compare", str(a), str(b)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertFalse(json.loads(p.stdout)["passed"])

    def test_external_inputs_do_not_hide_missing_or_invalid_ids(self):
        for ranking in ({}, {"q1": ["missing"]}, {"q1": ["DEMO_00151", "DEMO_00151"]}):
            with self.assertRaises(s.SearchError): s.evaluate(self.ix, self.queries[:1], {"rankings": ranking})

    def test_comparison_mismatch_and_tamper(self):
        run = s.evaluate(self.ix, self.queries)
        tampered = copy.deepcopy(run); tampered["queries"][0]["metrics"]["ndcg@10"] = 0
        with self.assertRaises(s.SearchError): s.compare(run, tampered)
        tampered = copy.deepcopy(run); tampered["manifest"]["run_hash"] = "bad"
        with self.assertRaises(s.SearchError): s.validate_run(tampered)
        tampered = copy.deepcopy(run); tampered["queries"][0]["results"]["hits"][0]["locator"] = "different.pdf:999"
        with self.assertRaises(s.SearchError): s.validate_run(tampered)
        different = s.evaluate(self.ix, self.queries[:1])
        with self.assertRaises(s.SearchError): s.compare(run, different)


class LanguageCorrectnessTests(unittest.TestCase):
    def test_escaped_phrases_and_arbitrary_nested_indexing(self):
        it = surd.Interp(echo=False).run('let ix=search_index([{ "id":"a", "text":"not approved" }])\nlet found=search(ix,"\\\"not approved\\\"")\nexpect found["hits"][0]["snippet"]["text"]=="not approved"')
        self.assertTrue(all(e["ok"] for e in it.expects))

    def test_overdraft_must_be_repaid_before_later_class_deposit_is_available(self):
        it = surd.Interp(echo=False).run('let ev=[{"date":@2020-01-01,"amount":-$300,"cls":""},{"date":@2020-01-02,"amount":$150,"cls":"A"},{"date":@2020-01-03,"amount":-$160,"cls":""}]\nlet b=set_bounds(ev,fn(e)=>e["amount"] < $0,"A")\nexpect b["floor"]==$0\nexpect b["ceiling"]==$0')
        self.assertTrue(all(e["ok"] for e in it.expects))

    def test_pell_signs_large_and_small(self):
        p, q = 1, 1
        for i in range(220):
            x = surd.R2(-p, q)
            expected = 1 if p*p < 2*q*q else -1
            self.assertEqual(x.sign(), expected, (p, q))
            self.assertEqual((-x).sign(), -expected)
            p, q = p+2*q, p+q
        surd.Interp(echo=False).run("expect sqrt(2) > 72722761475561/51422757785981")

    def test_general_cyclotomic_sign_near_zero(self):
        it = surd.Interp(echo=False).run("let c = 2*cos(pi/8)")
        c = it.globals["c"]
        with localcontext() as ctx:
            ctx.prec = 180
            v = (Decimal(2)+Decimal(2).sqrt()).sqrt()
            den = 10**100
            num = int(v*den)
        self.assertEqual((c-surd.R2(Fraction(num, den))).sign(), 1)
        self.assertEqual((c-surd.R2(Fraction(num+1, den))).sign(), -1)
        self.assertEqual((c-c).sign(), 0)

    def test_encode_overlap_rejected_before_state_mutation(self):
        it = surd.Interp(echo=False).run('space A {zero,one}\nspace B {left,right}\nlet j=joint(A,B,[["zero","left",1],["zero","right",1],["one","left",1],["one","right",1]])\nwire w')
        before = list(it.state.v)
        with self.assertRaises(surd.SurdError) as e: it.run("encode(j,w,w)")
        self.assertEqual(e.exception.code, "S-E006")
        self.assertEqual(it.state.v, before)

    def test_mixed_separable_state_not_called_entangled(self):
        it = surd.Interp(echo=False).run("wires q:2\nq[0] <- bitflip(1/2)\nexpect linear_entropy(q[0]) == 1/2")
        with self.assertRaises(surd.SurdError) as e: it.run("let e = entanglement(q[0])")
        self.assertEqual(e.exception.code, "S-E009")
        self.assertIn("linear_entropy", e.exception.hint)
        surd.Interp(echo=False, force_mixed=True).run("wires q:2\nq <- h & id >> cx\nexpect entanglement(q[0]) == 1/2")

    def test_meaning_arity_is_structured_error(self):
        for src in ['space P {yes,no}\nlet f=fidelity(sense(P,"yes"))', 'space P {yes,no}\nlet f=purity(sense(P,"yes"),sense(P,"no"))']:
            with self.assertRaises(surd.SurdError) as e: surd.Interp(echo=False).run(src)
            self.assertEqual(e.exception.code, "S-E004")

    def test_language_search_and_saved_run_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            runpath = json.dumps(str(Path(d)/"run.json"))
            src = 'let ix=search_index([{ "id":"a", "text":"exact gates", "locator":"gates.md:1" }])\nexpect type(ix)=="search index"\nlet r=search(ix,"gates")\nexpect r["hits"][0]["id"]=="a"\nlet qs=[{"id":"q","query":"gates","relevant":{"a":1}}]\nlet ev=search_evaluate(ix,qs)\nexpect ev["aggregate"]["ndcg@10"]==1\n'
            src += f'search_save(ev,{runpath})\nlet old=search_load({runpath})\nlet cmp=search_compare(old,ev)\nexpect cmp["passed"]==true'
            it = surd.Interp(echo=False).run(src)
            self.assertTrue(all(e["ok"] for e in it.expects))

    def test_search_errors_have_native_position(self):
        with self.assertRaises(surd.SurdError) as e: surd.Interp(echo=False).run('let ix=search_index([{ "text":"missing id" }])')
        self.assertEqual(e.exception.code, "S-E016")
        self.assertIsNotNone(e.exception.pos)

    def test_unknown_command_fails(self):
        p = subprocess.run([sys.executable, str(ROOT/"surd.py"), "search", "madeup"], capture_output=True)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
