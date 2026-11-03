"""Synthetic contract checks with independent expected results and rejection controls."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.ontology import check, validate, expand, project, diff, compile_ontology
from loom_research.contracts import ResearchError
ROOT=Path(__file__).resolve().parents[1]


def schema():
    return {'schema':'loom.ontology.v1','id':'org','version':1,'types':{
        'Thing':{'abstract':True,'properties':{'name':{'kind':'string','required':True}}},
        'Person':{'extends':['Thing'],'properties':{'balance':{'kind':'decimal'},'manager':{'kind':'reference','target':'Person'},
          'tags':{'kind':'array','items':{'kind':'string'},'max_items':3},
          'profile':{'kind':'object','properties':{'active':{'kind':'boolean','required':True}}},
          'stage':{'kind':'enum','enum':['requested','paid']},'when':{'kind':'date','nullable':True}}},
        'Company':{'extends':['Thing'],'disjoint':['Person']}},
        'relations':{'reports_to':{'domain':['Person'],'range':['Person'],'functional':True,'acyclic':True,'inverse':'manages'},
                     'manages':{'domain':['Person'],'range':['Person']},
                     'ancestor':{'domain':['Person'],'range':['Person']}},
        'rules':[{'id':'hierarchy','chain':['reports_to','reports_to'],'infer':'ancestor'}]}


def instances():
    return {'schema':'loom.ontology.instances.v1','ontology_id':'org','ontology_version':1,
            'nodes':[{'id':x,'type':'Person','properties':{'name':x}} for x in 'ABC'],
            'edges':[{'id':'ab','subject':'A','predicate':'reports_to','object':'B','source_ids':['s1']},
                     {'id':'bc','subject':'B','predicate':'reports_to','object':'C','source_ids':['s2']}]}


class OntologyTests(unittest.TestCase):
    def test_inheritance_and_canonical_roundtrip(self):
        s=schema();before=deepcopy(s);c=compile_ontology(s)
        self.assertIn('name',c['properties']['Person']);self.assertEqual(c['ancestors']['Person'],['Person','Thing'])
        self.assertEqual(c,compile_ontology(c['ontology']));self.assertEqual(s,before)

    def test_required_and_closed_properties(self):
        p=instances();p['nodes'][0]['properties']={'typo':'A'}
        r=validate(schema(),p);self.assertEqual({e['code'] for e in r['issues']},{'required','unknown_property'})

    def test_nested_exact_decimal_reference_and_date(self):
        p=instances();props=p['nodes'][0]['properties'];props.update(balance='12345678901234567890.01',manager='B',profile={'active':True},tags=['x'],when='2026-10-13',stage='requested')
        self.assertTrue(validate(schema(),p)['ok'])
        for k,v in [('balance',1.2),('manager','missing'),('profile',{}),('tags',[1]),('when','2026-99-99'),('stage','received')]:
            q=deepcopy(p);q['nodes'][0]['properties'][k]=v;self.assertFalse(validate(schema(),q)['ok'],k)

    def test_boolean_is_not_integer(self):
        s=schema();s['types']['Person']['properties']['n']={'kind':'integer'}
        p=instances();p['nodes'][0]['properties']['n']=True;self.assertFalse(validate(s,p)['ok'])

    def test_nullable_and_abstract(self):
        p=instances();p['nodes'][0]['properties']['when']=None;self.assertTrue(validate(schema(),p)['ok'])
        p['nodes'][0]['properties']['name']=None;self.assertFalse(validate(schema(),p)['ok'])
        p=instances();p['nodes'][0]['type']='Thing';self.assertIn('abstract_type',{e['code'] for e in validate(schema(),p)['issues']})

    def test_cycles_unknown_parent_disjoint_and_conflicting_inheritance(self):
        for edit in ('cycle','unknown','disjoint','conflict'):
            s=schema()
            if edit=='cycle':s['types']['Thing']['extends']=['Person']
            if edit=='unknown':s['types']['Person']['extends']=['Absent']
            if edit=='disjoint':s['types']['Person']['extends']=['Company']
            if edit=='conflict':s['types']['Person']['properties']['name']={'kind':'integer'}
            with self.assertRaises(ResearchError,msg=edit):check(s)

    def test_domain_range_dangling_functional_and_cycles(self):
        p=instances();p['nodes'][0]['type']='Company';self.assertIn('domain',{x['code'] for x in validate(schema(),p)['issues']})
        p=instances();p['edges'][0]['object']='absent';self.assertIn('dangling_endpoint',{x['code'] for x in validate(schema(),p)['issues']})
        p=instances();p['edges'].append({'id':'ac','subject':'A','predicate':'reports_to','object':'C'})
        self.assertIn('functional',{x['code'] for x in validate(schema(),p)['issues']})
        p=instances();p['edges'].append({'id':'ca','subject':'C','predicate':'reports_to','object':'A'})
        self.assertIn('cycle',{x['code'] for x in validate(schema(),p)['issues']})

    def test_expand_premises_sources_and_no_mutation(self):
        p=instances();before=deepcopy(p);r=expand(schema(),p)
        self.assertTrue(r['ok'] and r['complete']);self.assertEqual(len(r['derived']),3)
        ancestor=next(e for e in r['derived'] if e['predicate']=='ancestor')
        self.assertEqual((ancestor['subject'],ancestor['object']),('A','C'))
        self.assertEqual(ancestor['source_ids'],['s1','s2']);self.assertEqual(ancestor['native']['premise_ids'],['ab','bc'])
        self.assertEqual(p,before);self.assertTrue(all(e['status']=='derived' for e in r['derived']))
        with self.assertRaises(ResearchError):expand(schema(),r['instances'])
        # Removing a premise recomputes from the supplied input; no stale inferred edge survives.
        p['edges'].pop();self.assertFalse(any(e['predicate']=='ancestor' for e in expand(schema(),p)['derived']))

    def test_symmetric_closure_round_cap_and_conflict(self):
        s=schema();s['relations']['friend']={'domain':['Person'],'range':['Person'],'symmetric':True}
        p=instances();p['edges']=[{'id':'f','subject':'A','predicate':'friend','object':'B'}]
        r=expand(s,p,max_rounds=1);self.assertFalse(r['complete']);self.assertEqual(len(r['derived']),1)
        self.assertTrue(expand(s,p)['complete'])
        with self.assertRaises(ResearchError):expand(schema(),instances(),max_derived=1)
        s=schema();s['rules']=[{'id':'bad','chain':['reports_to','reports_to'],'infer':'reports_to'}]
        self.assertFalse(expand(s,instances())['ok'])

    def test_invalid_schema_shapes_limits_and_duplicate_ids(self):
        for prop in ({'kind':'array'}, {'kind':'decimal','target':'Person'}, {'kind':'reference','target':'Missing'}, {'kind':'enum','enum':[True,True]}):
            s=schema();s['types']['Person']['properties']['bad']=prop
            with self.assertRaises(ResearchError):check(s)
        p=instances();p['nodes'].append(p['nodes'][0])
        with self.assertRaises(ResearchError):validate(schema(),p)
        p=instances();p['ontology_version']=True
        with self.assertRaises(ResearchError):validate(schema(),p)

    def test_diff_versions_and_revalidation(self):
        a=schema();b=deepcopy(a);b['version']=2;b['types']['Person']['properties']['role']={'kind':'string','required':True}
        r=diff(a,b);self.assertTrue(r['requires_instance_revalidation']);self.assertEqual(len(r['changes']),1)
        with self.assertRaises(ResearchError):diff(a,a)
        p=instances();p['ontology_version']=2;self.assertFalse(validate(b,p)['ok'])

    def test_graph_projection_preserves_native_and_omissions(self):
        p=instances();nodes=[{'id':n['id'],'type':'entity','data':{'ontology_type':n['type'],'properties':n['properties']},'effective_status':'annotated','source_ids':['s']} for n in p['nodes']]
        nodes.append({'id':'source','type':'source','data':{}})
        snap={'nodes':nodes,'edges':p['edges'],'graph_hash':'g','projection_hash':'p'};before=deepcopy(snap)
        r=project(schema(),snap);self.assertTrue(r['ok']);self.assertEqual(r['graph_hash'],'g');self.assertEqual(r['instances']['nodes'][0]['native'],nodes[0]);self.assertEqual(len(r['omitted']),1);self.assertEqual(before,snap)
        snap['nodes'][1]['effective_status']='stale';r=project(schema(),snap)
        self.assertEqual(len(r['omitted']),4)

    def test_cli_and_loom_public_functions(self):
        with tempfile.TemporaryDirectory() as td:
            s=Path(td)/'s.json';p=Path(td)/'p.json';s.write_text(json.dumps(schema()));p.write_text(json.dumps(instances()))
            from loom import main
            from contextlib import redirect_stdout
            import io
            out=io.StringIO()
            with redirect_stdout(out):code=main(['ontology','validate',str(s),str(p)])
            self.assertEqual(code,0);self.assertTrue(json.loads(out.getvalue())['ok'])
            from surd import Interp
            expr='ontology_expand('+json.dumps(str(s))+','+json.dumps(str(p))+')["complete"]'
            self.assertTrue(Interp(echo=False).evaluate(expr))
            self.assertTrue(Interp(echo=False).evaluate('ontology_check('+json.dumps(str(s))+')["ok"]'))


if __name__=='__main__':unittest.main()
