"""Collection contracts: exact identity, callbacks, bounded work and integration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd
import surd_collections as kernel
import surd_build


class CollectionTests(unittest.TestCase):
    def evaluate(self, expression):
        return surd.Interp(echo=False).evaluate(expression)

    def run_source(self, source, **kwargs):
        it = surd.Interp(echo=False, **kwargs).run(source)
        self.assertTrue(all(x["ok"] for x in it.expects), it.expects)
        return it

    def assert_code(self, source, code):
        with self.assertRaises(surd.SurdError) as caught:
            self.evaluate(source)
        self.assertEqual(caught.exception.code, code)

    def test_all_empty_results_and_initial_scan_snapshot(self):
        self.run_source('''expect group_by(fn(x)=>x, []) == []
expect unique_by(fn(x)=>x, []) == []
expect partition(fn(x)=>x, []) == {"matched":[],"rest":[]}
expect scan(fn(a,x)=>a+x, [], 7) == [7]
expect flat_map(fn(x)=>[x], []) == []
expect chunk([], 3) == []
expect flatten([]) == []''')

    def test_stable_grouping_typed_keys_and_exact_equivalence(self):
        self.run_source('''let gs = group_by(fn(x)=>x, [1,"1",true,float(1),$1, 1,none,none])
expect len(gs) == 6
expect len(gs[0]["items"]) == 2
expect len(gs[5]["items"]) == 2
expect unique_by(fn(x)=>x, [1/3,2/6,cos(pi/4),sqrt(2)/2]) == [1/3,cos(pi/4)]
expect len(group_by(fn(x)=>x, [pi/2,pi/2,@2026-01-01,@2026-01-01,$0.005,$0.005])) == 3
expect len(group_by(fn(x)=>x, [9007199254740992,9007199254740993])) == 2''')

    def test_composite_keys_and_map_order(self):
        self.run_source('''expect len(group_by(fn(x)=>x, [[1,"a"],[1,"a"],["1","a"],["a",1]])) == 3
expect len(group_by(fn(x)=>x, [{"a":1,"b":[2]},{"b":[2],"a":1},{"a":true,"b":[2]}])) == 2''')

    def test_input_record_identity_and_source_locators_survive(self):
        it = self.run_source('''let rows = [{"id":"a","locator":"fixture:1","kind":"test"},{"id":"b","locator":"fixture:2","kind":"test"}]
let gs = group_by(fn(row)=>row["kind"], rows)
let us = unique_by(fn(row)=>row["kind"], rows)
expect gs[0]["items"][1]["locator"] == "fixture:2"''')
        self.assertIs(it.globals['gs'][0]['items'][0], it.globals['rows'][0])
        self.assertIs(it.globals['us'][0], it.globals['rows'][0])
        self.assertIsNot(it.globals['gs'][0]['items'], it.globals['rows'])

    def test_callback_once_per_item_and_list_first_unary_forms(self):
        self.run_source('''let seen = []
fn key(vv) { push(seen,vv); return vv % 2 }
let gs = group_by([1,2,3],key)
expect seen == [1,2,3]
expect unique_by([1,1,2],fn(vv)=>vv) == [1,2]
expect partition([1,2,3],fn(vv)=>vv>1)["rest"] == [1]
expect flat_map([1,2],fn(vv)=>[vv]) == [1,2]''')

    def test_mutating_callback_cannot_extend_input_iteration(self):
        self.run_source('''let xs = [1,2]
fn kk(vv) { push(xs,9); return vv }
expect len(group_by(kk,xs)) == 2
expect xs == [1,2,9,9]''')

    def test_composite_group_keys_are_snapshots(self):
        self.run_source('''let kk = []
fn key(vv) { push(kk,vv); return kk }
let gs = group_by(key,[1,2])
expect gs[0]["key"] == [1]
expect gs[1]["key"] == [1,2]''')

    def test_partition_retains_order_and_truthiness(self):
        self.run_source('''expect partition(fn(x)=>x>0,[2,-1,0,3,-2]) == {"matched":[2,3],"rest":[-1,0,-2]}
expect partition(fn(x)=>x,[0,1,0,2])["matched"] == [1,2]''')

    def test_scan_exact_rational_and_money_prefixes(self):
        self.run_source('''expect scan(fn(a,x)=>a+x,[1/3,1/3,1/3],0) == [0,1/3,2/3,1]
expect scan(fn(a,x)=>a+x,[$0.005,$0.005,-$0.01],$0) == [$0,$0.005,$0.01,$0]
expect exactness(scan(fn(a,x)=>a+x,[1/3,1/3],0))["status"] == "exact"''')

    def test_scan_mutable_accumulators_are_independent_prefixes(self):
        it = self.run_source('''let acc = {"items":[]}
fn step(vv,xx) { push(vv["items"],xx); return vv }
let prefixes = scan(step,[1,2],acc)
expect prefixes == [{"items":[]},{"items":[1]},{"items":[1,2]}]
push(acc["items"],3)
expect prefixes[2]["items"] == [1,2]''')
        self.assertIsNot(it.globals['prefixes'][0], it.globals['prefixes'][1])

    def test_flat_mapping_and_one_level_flattening(self):
        self.run_source('''expect flat_map(fn(x)=>[x,x+1],[1,3]) == [1,2,3,4]
expect flat_map(fn(x)=>[],[1,2]) == []
expect flatten([none,[1,[2]],"text",{"a":1}]) == [none,1,[2],"text",{"a":1}]
expect flat_map(fn(x)=>[[x]],[1,2]) == [[1],[2]]''')

    def test_chunk_roundtrip_and_positive_exact_size(self):
        self.run_source('''expect chunk([1,2,3,4,5],2) == [[1,2],[3,4],[5]]
expect chunk([1,2],100) == [[1,2]]
expect flatten(chunk([1,2,3,4,5],2)) == [1,2,3,4,5]''')
        for size in ('0','-1','1/2','true','float(2)','"2"'):
            self.assert_code('chunk([1],'+size+')', 'S-E009')

    def test_shape_callback_and_empty_arity_errors(self):
        for expression in ('group_by(1,[])','unique_by(1,[])','partition(1,[])','scan(1,[],0)','flat_map(1,[])','flatten("abc")','chunk({},2)','flat_map(fn(x)=>x,[1])'):
            self.assert_code(expression,'S-E009')
        for expression in ('group_by(fn(a,b)=>a,[])','scan(fn(a)=>a,[],0)','partition(fn()=>true,[])','chunk([])','flatten([],1)'):
            self.assert_code(expression,'S-E004')

    def test_unsupported_group_keys_fail(self):
        for expression in ('group_by(fn(x)=>fn(y)=>y,[1])','unique_by(fn(x)=>h,[1])','group_by(fn(x)=>{"bad":h},[1])'):
            self.assert_code(expression,'S-E009')
        it=surd.Interp(echo=False)
        for value in (float('nan'),float('inf')):
            it.globals['bad']=value
            with self.assertRaises(surd.SurdError) as caught: it.evaluate('group_by(fn(x)=>x,[bad])')
            self.assertEqual(caught.exception.code,'S-E009')

    def test_limits_cover_input_output_structural_work_and_depth(self):
        with patch.object(kernel,'MAX_ITEMS',2):
            for expression in ('flatten([1,2,3])','flat_map(fn(x)=>[x,x],[1,2])','scan(fn(a,x)=>a+x,[1,2],0)'):
                self.assert_code(expression,'S-E010')
            self.assertEqual(len(self.evaluate('chunk([1,2],1)')),2)
        with patch.object(kernel,'MAX_VISITS',2):
            self.assert_code('group_by(fn(x)=>x,[1,2])','S-E010')
        with patch.object(kernel,'MAX_DEPTH',1):
            self.assert_code('group_by(fn(x)=>x,[[[1]]])','S-E010')

    def test_cyclic_keys_and_scan_accumulators_fail(self):
        it=surd.Interp(echo=False); cycle=[];cycle.append(cycle);it.globals['cyclic']=cycle
        for expression in ('group_by(fn(x)=>x,[cyclic])','scan(fn(a,x)=>a,[],cyclic)'):
            with self.assertRaises(surd.SurdError) as caught: it.evaluate(expression)
            self.assertEqual(caught.exception.code,'S-E009')

    def test_budget_covers_callbacks_and_callback_free_traversals(self):
        it=surd.Interp(echo=False,max_steps=15);it.globals['xs']=[surd.R2(1)]*100
        for expression in ('flatten(xs)','chunk(xs,1)','group_by(fn(x)=>x,xs)','scan(fn(a,x)=>a+x,xs,0)'):
            with self.assertRaises(surd.SurdError) as caught: it.evaluate(expression)
            self.assertEqual(caught.exception.code,'S-E017')

    def test_callbacks_can_build_circuits_without_false_dynamic_flags(self):
        it=self.run_source('''wire q
fn turn(vv) { q <- x; return [vv] }
expect flat_map(turn,[1,2,3]) == [1,2,3]
expect prob(q,"1") == 1''')
        self.assertFalse(it.used_if)
        self.assertIn('OPENQASM',surd.to_qasm(it))

    def test_measurement_derived_collections_keep_export_guard(self):
        prep='wires q: 1\nbits cc: 1\nq <- h\ncc <- measure q\n'
        for expression in ('group_by(fn(x)=>x,[cc[0]])','unique_by(fn(x)=>x,[cc[0]])','partition(fn(x)=>x,[cc[0]])','scan(fn(a,x)=>a+x,[cc[0]],0)','flat_map(fn(x)=>[x],[cc[0]])','chunk([cc[0]],1)','flatten([[cc[0]]])'):
            with self.subTest(expression=expression):
                it=self.run_source(prep+'let result = '+expression)
                self.assertTrue(it.used_if)
                with self.assertRaises(surd.SurdError) as caught: surd.to_qasm(it)
                self.assertEqual(caught.exception.code,'S-E013')

    def test_realm_records_and_search_hits_work_with_locators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'records.json';path.write_text(json.dumps([{'id':'a','kind':'test','locator':'fixture:1'},{'id':'b','kind':'test','locator':'fixture:2'}]))
            self.run_source('realm rr = load('+json.dumps(str(path))+')\nexpect len(group_by(fn(rec)=>rec["kind"],rr)[0]["items"]) == 2')
        self.run_source('''let ix = search_index([{"id":"a","text":"gates","locator":"fixture:1","kind":"test"},{"id":"b","text":"gates","locator":"fixture:2","kind":"test"}])
let gs = group_by(fn(hit)=>hit["metadata"]["kind"],search(ix,"gates")["hits"])
expect gs[0]["items"][0]["locator"] == "fixture:1"''')

    def test_kernel_boundary_and_executable_docs(self):
        graph=surd_build.architecture(ROOT)
        self.assertEqual(graph['dependencies']['surd_collections'],[])
        with self.assertRaises(surd_build.BuildError):
            surd_build._architecture({'surd_collections':'import surd_search_core','surd_search_core':''})
        docs=surd.describe_builtins()
        rows=docs if isinstance(docs,list) else docs['builtins']
        found=[r for r in rows if r['name'] in kernel.FUNCTIONS]
        self.assertEqual(len(found),7)
        self.assertTrue(all(r['verified'] for r in found))

    def test_cli_example_runs(self):
        p=subprocess.run([sys.executable,str(ROOT/'surd.py'),'run',str(ROOT/'examples/collections.surd')],capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)


if __name__ == '__main__': unittest.main()
