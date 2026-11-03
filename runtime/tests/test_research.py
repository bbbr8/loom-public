"""Adversarial graph, analytical, planning, learning and public integration tests."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.contracts import ResearchError, digest, normalize_node
from loom_research.store import GraphStore
from loom_research import engine, identity, retrieval, analytics, compiler, planner, learning
from research_fixtures import batch,credit_graph,run_packet,ROOT


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'graph.sqlite';self.store=GraphStore(self.path,create=True)

    def base(self):
        self.store.apply(batch());return self.store.snapshot()

    def test_missing_store_and_init_preserve_existing(self):
        absent=Path(self.tmp.name)/'absent'
        with self.assertRaises(ResearchError):GraphStore(absent)
        self.assertFalse(absent.exists())
        before=self.path.read_bytes()
        with self.assertRaises(ResearchError):GraphStore(self.path,create=True)
        self.assertEqual(before,self.path.read_bytes())

    def test_batch_idempotence_and_transactional_rejection(self):
        self.base();count=self.store.snapshot()['revision']
        self.assertEqual(self.store.apply(batch())['state'],'IDEMPOTENT')
        bad=batch();bad['nodes'][0]['label']='different'
        with self.assertRaises(ResearchError):self.store.apply(bad)
        bad={'schema':'loom.graph.batch.v1','batch_id':'bad','nodes':[{'id':'new','type':'entity'}],
             'edges':[{'id':'bad','subject':'new','object':'absent','predicate':'related_to','status':'candidate'}]}
        with self.assertRaises(ResearchError):self.store.apply(bad)
        self.assertEqual(self.store.snapshot()['revision'],count)
        self.assertNotIn('new',{n['id'] for n in self.store.snapshot()['nodes']})

    def test_revision_stales_transitive_dependents_and_preserves_past(self):
        original=self.base();source=deepcopy(batch()['nodes'][0]);source['revision']=2;source['label']='Corrected source'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'corrected','nodes':[source]})
        snap=self.store.snapshot();self.assertNotIn('A',{n['id'] for n in snap['nodes']});self.assertFalse(snap['edges'])
        full=self.store.snapshot(include_stale=True);a=next(n for n in full['nodes'] if n['id']=='A');self.assertEqual(a['effective_status'],'stale')
        self.assertEqual(original,self.store.snapshot(revision=1))
        self.assertTrue(self.store.diff(1)['changes'])
        self.assertTrue(self.store.verify()['ok'])

    def test_stale_revision_and_tombstone_block_resurrection(self):
        self.base();self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'remove','remove_nodes':[{'id':'A','revision':2}]})
        with self.assertRaises(ResearchError):self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'reappear','nodes':[batch()['nodes'][2]]})
        self.assertEqual(next(n for n in self.store.snapshot(include_inactive=True)['nodes'] if n['id']=='A')['active'],False)

    def test_origin_withdrawal_removes_copies_not_unrelated_sources(self):
        self.base();s=deepcopy(batch()['nodes'][0]);s['id']='S1-copy'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'copy','nodes':[s]})
        self.store.withdraw('ORIGIN1','withdraw')
        snap=self.store.snapshot();ids={n['id'] for n in snap['nodes']}
        self.assertFalse({'S1','S1-copy','A'}&ids);self.assertIn('S2',ids);self.assertIn('B',ids)

    def test_journal_replay_is_exact_and_rejects_corruption(self):
        self.base();j=self.store.journal();replay=GraphStore.replay(Path(self.tmp.name)/'replay',j)
        self.assertEqual(self.store.snapshot(),replay.snapshot())
        j['records'][0]['payload']['nodes'][0]['locator']='tampered'
        with self.assertRaises(ResearchError):GraphStore.replay(Path(self.tmp.name)/'bad-replay',j)
        self.assertFalse((Path(self.tmp.name)/'bad-replay').exists())

    def test_materialized_history_and_indexes_are_verified(self):
        self.base()
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE node_text SET text='tampered' WHERE id='A'");c.commit()
        with self.assertRaises(ResearchError):self.store.verify()

    def test_cycles_rejected_atomically(self):
        packet={'schema':'loom.graph.batch.v1','batch_id':'cycle','nodes':[
            {'id':'a','type':'hypothesis','depends_on':[{'kind':'node','id':'b'}]},
            {'id':'b','type':'hypothesis','depends_on':[{'kind':'node','id':'a'}]}]}
        with self.assertRaises(ResearchError):self.store.apply(packet)
        self.assertEqual(self.store.snapshot()['nodes'],[])

    def test_actor_specific_receipt_and_unknown_dates(self):
        self.base();snap=self.store.snapshot(actor='Actor A',receipt_before='2020-01-10')
        ids={n['id'] for n in snap['nodes']};self.assertIn('A',ids);self.assertNotIn('B',ids);self.assertFalse(snap['edges'])
        snap=self.store.snapshot(actor='Actor B',receipt_before='2020-01-10');self.assertFalse(snap['nodes']);self.assertEqual(snap['state'],'PARTIAL_TEMPORAL')
        early=self.store.snapshot(event_before='2020-01-02');self.assertIn('A',{n['id'] for n in early['nodes']});self.assertNotIn('B',{n['id'] for n in early['nodes']})
        with self.assertRaises(ResearchError):self.store.snapshot(actor='Actor A')

    def test_clock_intervals_do_not_turn_overlap_into_receipt(self):
        b=batch();b['nodes'][2]['clocks']['receipt']['Actor A']=['2020-01-01','2020-01-20']
        self.store.apply(b);snap=self.store.snapshot(actor='Actor A',receipt_before='2020-01-10')
        self.assertNotIn('A',{n['id'] for n in snap['nodes']})

    def test_identity_names_alone_and_conflicting_keys_preserve_separation(self):
        self.base();items=[]
        for ident,key in [('e1','account1'),('e2','account2')]:
            items.append({'id':ident,'type':'entity','label':'Alex Smith','source_ids':['S1'],
                          'data':{'entity_type':'person'},'identifiers':[{'kind':'id','namespace':'SYN','value':key,'unique':True}]})
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'entities','nodes':items})
        r=identity.resolve(self.store.snapshot());self.assertEqual(r['pairs'][0]['state'],'IDENTIFIER_CONFLICT')
        self.assertNotEqual(r['clusters']['e1'],r['clusters']['e2'])
        for n in items:n['identifiers']=[]
        pair=identity.compare(items[0],items[1]);self.assertEqual(pair['state'],'INSUFFICIENT_IDENTITY')

    def test_reviewed_alias_can_be_retracted_without_destroying_ids(self):
        self.base();nodes=[{'id':i,'type':'entity','label':'Entity '+i,'source_ids':['S1'],'data':{'entity_type':'company'}} for i in ['e1','e2']]
        edge={'id':'same','subject':'e1','predicate':'same_entity','object':'e2','source_ids':['S1'],'data':{'reviewed':True}}
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'alias','nodes':nodes,'edges':[edge]})
        r=identity.resolve(self.store.snapshot());self.assertEqual(r['clusters']['e1'],r['clusters']['e2'])
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'split','remove_edges':[{'id':'same','revision':2}]})
        r=identity.resolve(self.store.snapshot());self.assertNotEqual(r['clusters']['e1'],r['clusters']['e2'])
        self.assertIn('e1',{n['id'] for n in self.store.snapshot()['nodes']})

    def test_graph_paths_bridge_vocabulary_and_retain_sources(self):
        snap=self.base();r=retrieval.search(snap,'blue invoice',hops=1,top=10)
        hit=next(h for h in r['hits'] if h['id']=='B');self.assertEqual(hit['edge_path'],['AB']);self.assertEqual(set(hit['source_ids']),{'S1','S2'})
        p=retrieval.paths(snap,'A',end='B');self.assertEqual(p['paths'][0]['predicates'],['refers_to'])
        self.assertEqual(retrieval.search(snap,'blue invoice',hops=0)['hits'][0]['id'],'A')

    def test_phrase_negation_filters_and_semantic_snapshot_alignment(self):
        snap=self.base();r=retrieval.search(snap,'"blue invoice" -cancelled',hops=2)
        self.assertNotIn('B',[h['id'] for h in r['hits']])
        with self.assertRaises(ResearchError):retrieval.search(snap,'blue',semantic={'graph_hash':'wrong','query':'blue','ids':['B']})
        r=retrieval.search(snap,'blue',semantic={'graph_hash':snap['graph_hash'],'query':'blue','ids':['B']})
        self.assertEqual(r['semantic_state'],'ALIGNED_EXTERNAL_RANKINGS')
        r=retrieval.search(snap,'blue',where={'type':'passage'});self.assertTrue(all(h['type']=='passage' for h in r['hits']))

    def test_automatic_graph_evolution_is_idempotent_and_correction_safe(self):
        r=engine.ingest(self.store,batch());self.assertIn(r['evolution']['state'],('COMPLETE','PARTIAL'))
        s=self.store.snapshot();self.assertTrue(any(n['type']=='concept' for n in s['nodes']))
        self.assertEqual(engine.evolve(self.store)['state'],'IDEMPOTENT')
        records=[{'id':'X','origin':'SX','locator':'SYN:X','text':'blue invoice vocabulary'}]
        engine.ingest(self.store,engine.records_batch('r1',records))
        records[0].update(revision=2,text='green replacement vocabulary')
        engine.ingest(self.store,engine.records_batch('r2',records))
        latest=self.store.snapshot();self.assertTrue(any(n['id']=='X' and 'green' in n['text'] for n in latest['nodes']))
        self.assertTrue(self.store.verify()['ok'])

    def test_exact_quantity_checks_require_all_axes_and_preserve_large_values(self):
        self.base();axis={'measure':'amount','unit':'USD','stage':'paid','scope':'SYN','instrument':'42','version':'v1','clock':'2020-01-01'}
        nodes=[]
        for i,v in enumerate(['9007199254740993.01','9007199254740993.02']):
            nodes.append({'id':'q'+str(i),'type':'event','source_ids':['S1' if i==0 else 'S2'],
                          'data':{'quantities':[{**axis,'value':v}]}})
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'amounts','nodes':nodes})
        r=analytics.analyze(self.store.snapshot());d=next(d for d in r['diagnostics'] if d['state']=='ALIGNED_QUANTITY_CONFLICT')
        self.assertEqual(d['delta'],'1/100')
        r=analytics.analyze(self.store.snapshot(),invariants=[{'id':'delta','terms':[{'node':'q1'},{'node':'q0','weight':'-1'}],'equals':'1/100'}])
        self.assertEqual(r['invariants'][0]['state'],'SATISFIED_IN_DECLARED_DATA')
        nodes[1]['revision']=2;nodes[1]['data']['quantities'][0]['stage']='requested'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'stages','nodes':[nodes[1]]})
        self.assertFalse(analytics.analyze(self.store.snapshot())['diagnostics'])
        with self.assertRaises(ResearchError):analytics.analyze(self.store.snapshot(),invariants=[{'terms':[{'node':'q1'},{'node':'q0'}]}])

    def test_opposition_needs_aligned_scope_and_clock(self):
        self.base();nodes=[]
        for i,stance in enumerate(['support','oppose']):nodes.append({'id':'claim'+str(i),'type':'claim','source_ids':['S1' if i==0 else 'S2'],'data':{'claim':{'key':'delivered','scope':'item42','clock':'phase1','stance':stance}}})
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'claims','nodes':nodes})
        self.assertEqual(analytics.analyze(self.store.snapshot())['diagnostics'][0]['state'],'OPPOSED_ANNOTATED_CLAIMS')
        nodes[1]['revision']=2;nodes[1]['data']['claim']['scope']='different-item'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'scope','nodes':[nodes[1]]})
        self.assertFalse(analytics.analyze(self.store.snapshot())['diagnostics'])

    def test_graph_to_qulap_compiler_and_joint_bundle_synergy(self):
        b,spec=credit_graph();self.store.apply(b);c=compiler.compile_graph(self.store.snapshot(),spec)
        self.assertEqual(c['state'],'COMPLETE');r=planner.plan(c['model'],max_bundle=2)
        self.assertEqual(r['bundles'][0]['guaranteed_gain'],60000);self.assertEqual(r['bundles'][0]['synergy'],60000)
        self.assertEqual(set(r['bundles'][0]['records']),{'applied','refunded'})
        self.assertTrue(c['bindings']);self.assertEqual(len(c['model']['sources']),2)

    def test_model_compiler_leaves_unreviewed_or_withdrawn_inputs_open(self):
        b,spec=credit_graph();b['nodes'][2]['data']['reviewed']=False;self.store.apply(b)
        self.assertEqual(compiler.compile_graph(self.store.snapshot(),spec)['state'],'OPEN_INPUT')
        spec['allow_hypotheses']=True;c=compiler.compile_graph(self.store.snapshot(),spec);self.assertTrue(c['assumptions'])
        self.store.withdraw('SYN_C1','remove-credit')
        self.assertEqual(compiler.compile_graph(self.store.snapshot(),spec)['state'],'OPEN_INPUT')

    def test_bundle_budget_and_partial_search_remain_explicit(self):
        b,spec=credit_graph();self.store.apply(b);m=compiler.compile_graph(self.store.snapshot(),spec)['model']
        r=planner.plan(m,budget='1');self.assertTrue(all(len(x['records'])==1 for x in r['bundles']))
        self.assertEqual(planner.plan(m,max_nodes=1)['state'],'INCOMPLETE')
        self.assertEqual(planner.plan(m,max_worlds=1)['state'],'INCOMPLETE')
        self.assertEqual(planner.plan(m,max_bundles=1)['state'],'PARTIAL')
        with self.assertRaises(ResearchError):planner.plan(m,records=[{'id':'x','variables':['applied'],'cost':0.1}])

    def test_research_run_persists_results_and_stales_after_new_evidence(self):
        r=engine.run(self.store,run_packet());self.assertEqual(r['state'],'COMPLETE')
        self.assertEqual(r['planning']['bundles'][0]['guaranteed_gain'],60000)
        self.assertTrue(engine.run(self.store,run_packet())['cached'])
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'new-evidence','nodes':[{'id':'new','type':'hypothesis','status':'hypothesis'}]})
        old=next(n for n in self.store.snapshot(include_stale=True)['nodes'] if n['id']=='run:synthetic-credit-research')
        self.assertEqual(old['effective_status'],'stale');self.assertFalse(engine.run(self.store,run_packet())['cached'])
        self.assertTrue(self.store.verify()['ok'])

    def evaluation(self):
        return {'id':'frozen','scope':'test','method_id':'test-method','candidate':{'hops':0},'baseline':{'hops':0},'activate':True,
                'queries':[{'id':'q1','query':'blue','relevant':['A'],'context':'original'},
                           {'id':'q2','query':'invoice','relevant':['A'],'context':'transfer'},
                           {'id':'q3','query':'zzzzunknown','relevant':[],'answerable':False,'context':'adversarial'}]}

    def test_measured_recipe_activation_and_frozen_label_guard(self):
        self.base();e=self.evaluation();r=learning.evaluate(self.store,e);self.assertTrue(r['activated'])
        self.assertTrue(learning.evaluate(self.store,e)['activated'])
        e['queries'][0]['relevant']=['B']
        with self.assertRaises(ResearchError):learning.evaluate(self.store,e)
        self.assertTrue(self.store.verify()['ok'])

    def test_failed_recipe_is_held_and_preserves_prior_method(self):
        self.base();r=learning.evaluate(self.store,self.evaluation());e=self.evaluation();e['id']='new-frozen';e['candidate']={'hops':2,'max_visits':1}
        r=learning.evaluate(self.store,e);self.assertFalse(r['activated']);self.assertEqual(r['state'],'HELD')
        methods=learning.methods(self.store.snapshot())['methods'];self.assertTrue(any(m['id']=='method:test-method' and m['status']=='active' for m in methods))

    def test_schema_proposal_activation_trials_and_unknown_predicate(self):
        self.base();proposed=learning.propose_predicate(self.store,{'id':'relation','name':'explains','description':'Passage explains passage','shape':{'subjects':['passage'],'objects':['passage']}})
        def t(ident,a,b,expected,context):return {'context':context,'edge':{'id':ident,'subject':a,'object':b,'predicate':'explains','source_ids':['S1']},'expected_valid':expected}
        packet={'id':'relation','candidate_hash':proposed['candidate_hash'],'trials':[t('t1','A','B',True,'original'),t('t2','B','A',True,'transfer'),t('t3','S1','B',False,'adversarial')]}
        r=learning.activate_predicate(self.store,packet);self.assertEqual(r['state'],'ACTIVE')
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'custom-edge','edges':[{'id':'explain','subject':'A','predicate':'explains','object':'B','source_ids':['S1']}]})
        self.assertTrue(self.store.verify()['ok'])
        with self.assertRaises(ResearchError):self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'unvalidated','nodes':[{'id':'bad-predicate','type':'predicate','status':'active','space':'research','data':{'name':'unsafe','shape':{'subjects':['passage'],'objects':['passage']}}}]})

    def test_timeline_intake_keeps_date_roles_and_precision(self):
        timeline={'schema':'loom.timeline.v1','state':'PARTIAL','events':[{'t':'2020-01-01','kind':'money','mech':'ledger','text':'SYN $100','path':'synthetic.sqlite','where':'SYN:1','precision':'day','date_role':'ledger_transaction_date'}]}
        engine.import_timeline(self.store,timeline,'timeline')
        n=next(n for n in self.store.snapshot()['nodes'] if n['type']=='event');self.assertEqual(n['data']['date_role'],'ledger_transaction_date')
        self.assertEqual(n['clocks']['event'],['2020-01-01','2020-01-01']);self.assertEqual(n['data']['upstream_coverage'],'PARTIAL')

    def test_language_and_cli_public_routes(self):
        payload=Path(self.tmp.name)/'run.json';payload.write_text(json.dumps(run_packet()))
        command=[sys.executable,str(ROOT/'loom.py'),'research','run',str(payload),'--db',str(self.path)]
        p=subprocess.run(command,capture_output=True,text=True,timeout=15);self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        expr='graph_verify('+json.dumps(str(self.path))+')["ok"]'
        p=subprocess.run([sys.executable,str(ROOT/'loom.py'),'eval',expr,'--json'],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertEqual(json.loads(p.stdout)['display'],'true')

    def test_day_cutoffs_include_zoned_times_and_fractional_order(self):
        b=batch();b['nodes'][0]['clocks']['receipt']['Actor A']=['2020-01-02T12:00:00.125Z','2020-01-02T12:00:00.125Z']
        b['nodes'][2]['clocks']['receipt']['Actor A']=['2020-01-02T12:00:00.125Z','2020-01-02T12:00:00.125Z']
        self.store.apply(b)
        self.assertIn('A',{n['id'] for n in self.store.snapshot(actor='Actor A',receipt_before='2020-01-02')['nodes']})
        self.assertNotIn('A',{n['id'] for n in self.store.snapshot(actor='Actor A',receipt_before='2020-01-02T12:00:00.100Z')['nodes']})

    def test_explicit_file_intake_hashes_bytes_and_revises_only_derived_store(self):
        f=Path(self.tmp.name)/'source.txt';f.write_text('blue source file')
        manifest={'schema':'loom.graph.files.v1','id':'files','files':[str(f)]}
        r=engine.import_files(self.store,manifest);source=next(n for n in self.store.snapshot()['nodes'] if n['type']=='source')
        import hashlib
        self.assertTrue(source['data']['native_bytes_verified'])
        self.assertEqual(source['data']['native_sha256_computed'],hashlib.sha256(f.read_bytes()).hexdigest())
        self.assertEqual(engine.import_files(self.store,manifest)['commit']['state'],'IDEMPOTENT')
        f.write_text('green changed source file');engine.import_files(self.store,manifest)
        self.assertTrue(any(n.get('text')=='green changed source file' and n['revision']==2 for n in self.store.snapshot()['nodes']))
        self.assertEqual(f.read_text(),'green changed source file');self.assertTrue(self.store.verify()['ok'])

    def test_ordinary_search_capture_and_failure_do_not_change_primary_result(self):
        from surd_search import SearchIndex
        from loom_research.capture import record_search
        ix=SearchIndex([{'id':'d1','text':'blue invoice','locator':'SYN:d1'}]);report=ix.search('blue')
        with patch.dict(os.environ,{'LOOM_RESEARCH_DB':str(self.path)}):
            self.assertEqual(record_search(ix,'blue',report)['state'],'CAPTURED')
            self.assertEqual(record_search(ix,'blue',report)['state'],'CAPTURED')
        self.assertTrue(any(n['type']=='passage' for n in self.store.snapshot()['nodes']))
        self.assertEqual(report['hits'][0]['id'],'d1');self.assertTrue(self.store.verify()['ok'])
        with patch.dict(os.environ,{'LOOM_RESEARCH_DB':''}):self.assertEqual(record_search(ix,'blue',report)['state'],'DISABLED')

    def test_default_binding_is_explicit_and_reversible(self):
        from loom_research import bindings
        with patch.object(bindings,'CONFIG',Path(self.tmp.name)/'binding.json'),patch.dict(os.environ,{},clear=True):
            self.assertIsNone(bindings.bound_db());bindings.bind(str(self.path));self.assertEqual(bindings.bound_db(),str(self.path.resolve()))
            bindings.bind(str(self.path),False);self.assertIsNone(bindings.bound_db())

    def test_source_provenance_paths_and_overview_are_available(self):
        engine.ingest(self.store,batch());snap=self.store.snapshot()
        p=retrieval.paths(snap,'A',end='S1');self.assertTrue(p['paths'])
        r=retrieval.overview(snap);self.assertTrue(r['components']);self.assertEqual(r['source_origins'],2)
        self.assertEqual(r['type_counts']['source'],2)

    def test_temporal_views_close_over_explicit_and_transitive_dependencies(self):
        for clock,options in [('receipt',{'actor':'Actor A','receipt_before':'2020-01-10'}),('event',{'event_before':'2020-01-10'})]:
            with self.subTest(clock=clock):
                path=Path(self.tmp.name)/(clock+'.sqlite');s=GraphStore(path,create=True)
                c=lambda d:{'receipt':{'Actor A':[d,d]}} if clock=='receipt' else {'event':[d,d]}
                s.apply({'schema':'loom.graph.batch.v1','batch_id':clock,'nodes':[
                    {'id':'future','type':'source','origin':'SYN','locator':'SYN','clocks':c('2020-02-01')},
                    {'id':'middle','type':'hypothesis','clocks':c('2020-01-01'),'depends_on':[{'kind':'node','id':'future'}]},
                    {'id':'derived','type':'hypothesis','clocks':c('2020-01-01'),'depends_on':[{'kind':'node','id':'middle'}]}]})
                view=s.snapshot(**options);self.assertEqual(view['nodes'],[])
                self.assertTrue(any(x['reason']=='dependency_outside_temporal_view' for x in view['omitted']))

    def test_json_inputs_cannot_bypass_recipe_or_benchmark_gates(self):
        with self.assertRaises(ResearchError):self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'fake-method','nodes':[
            {'id':'method:fake','type':'method','status':'active','space':'research','data':{'scope':'test','params':{'hops':6}}}]})
        self.base();learning.evaluate(self.store,self.evaluation())
        n=next(n for n in self.store.snapshot()['nodes'] if n['type']=='benchmark');n=learning.clean_node(n)
        n['revision']+=1;n['data']['definition']['queries'][0]['relevant']=['B']
        with self.assertRaises(ResearchError):self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'tamper-labels','nodes':[n]})


if __name__=='__main__':unittest.main()
