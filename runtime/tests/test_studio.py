"""Independent behavior checks for source review, candidate retrieval and recovery."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.store import GraphStore
from loom_research.engine import records_batch
from loom_research import indexed,evidence,intake,studio


class Studio(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=GraphStore(self.root/'g.sqlite',create=True)
        self.store.apply(records_batch('seed',[
            {'id':'p','origin':'native:letter','locator':'page 1','text':'If Alice paid Builder, a credit would be requested.',
             'clocks':{'statement':['2018-01-01','2018-01-01']},'data':{'loan':'project-A','money_stage':'requested'}},
            {'id':'q','origin':'native:bank','locator':'page 2','text':'The bank records funding for project 393.',
             'identifiers':[{'kind':'loan','namespace':'bank','value':'393'}],'data':{'loan':'393','money_stage':'funded'}}]))

    def tearDown(self):self.temp.cleanup()

    def proposal(self,predicate='supports'):
        return {'batch_id':'proposal','relations':[{'subject':'p','predicate':predicate,'object':'q','scope':'supplied interpretation',
                'evidence':[{'node_id':'p','start':0,'end':48,'quote':'If Alice paid Builder, a credit would be requested.'}]}]}

    def test_indexed_read_avoids_full_projection(self):
        with patch.object(self.store,'snapshot',side_effect=AssertionError('full snapshot forbidden')):
            hit=indexed.search(self.store,'funded',top=1)
        self.assertEqual(hit['hits'][0]['id'],'q');self.assertLessEqual(hit['items_read'],4)

    def test_research_views_do_not_consume_source_candidate_slots(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'many-views','nodes':[
            {'id':'view:'+str(i),'type':'analysis','space':'research','label':'funded','text':'funded',
             'source_ids':['src:q']} for i in range(30)]})
        result=indexed.search(self.store,'funded',top=1,candidates=10)
        self.assertEqual([n['id'] for n in result['hits']],['q'])
        self.assertFalse(result['candidate_limit_reached'])
        self.assertLessEqual(result['items_read'],2)

    def test_structural_concepts_do_not_consume_source_candidate_slots(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'many-concepts','nodes':[
            {'id':'concept:'+str(i),'type':'concept','label':'requested','text':'requested',
             'source_ids':['src:p']} for i in range(30)]})
        result=indexed.search(self.store,'requested',top=1,candidates=10)
        self.assertEqual([n['id'] for n in result['hits']],['p'])
        self.assertFalse(result['candidate_limit_reached'])

    def test_actual_source_candidate_limit_remains_disclosed(self):
        self.store.apply(records_batch('source-fanout',[
            {'id':'other:'+str(i),'origin':'synthetic:other:'+str(i),'locator':'page 1','text':'funded'} for i in range(15)]))
        result=indexed.search(self.store,'funded',top=1,candidates=10)
        self.assertTrue(result['candidate_limit_reached'])
        self.assertEqual(len(result['hits']),1)

    def test_migrations_repeat_and_verify_live_trigger(self):
        self.assertEqual(indexed.migrate(self.store),indexed.migrate(self.store))
        with self.store.connection() as c:c.execute('DROP TRIGGER studio_item_update');c.commit()
        with self.assertRaises(ValueError):indexed.migrate(self.store)

    def test_incremental_index_updated_in_same_transaction(self):
        indexed.migrate(self.store)
        snap=self.store.snapshot();p=next(n for n in snap['nodes'] if n['id']=='q')
        from loom_research.learning import clean_node
        p=clean_node(p);p['revision']+=1;p['text']='An entirely different repayment.'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'change','nodes':[p]})
        self.assertFalse(indexed.search(self.store,'funded')['hits'])
        self.assertEqual(indexed.search(self.store,'repayment')['hits'][0]['id'],'q')

    def test_failed_batch_does_not_change_index(self):
        indexed.migrate(self.store)
        with self.assertRaises(ValueError):self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'bad','nodes':[{'id':'q','type':'passage','text':'invented'}]})
        self.assertFalse(indexed.search(self.store,'invented')['hits'])

    def test_withdrawal_invalidates_candidate_dependency(self):
        indexed.migrate(self.store);self.store.withdraw('native:bank','remove')
        self.assertFalse(indexed.search(self.store,'funding')['hits'])
        detail=indexed.inspect(self.store,'q')
        self.assertEqual(detail['node']['stale_reasons'],['changed:src:q'])

    def test_fusion_keeps_exact_identifier_and_hard_clauses(self):
        packet={'query':'393','commit_hash':self.store.head()[1],'ids':['p','q'],'model_fingerprint':'test-model'}
        self.assertEqual(indexed.search(self.store,'393',semantic=packet)['hits'][0]['id'],'q')
        packet['query']='"bank records" -requested'
        self.assertEqual([n['id'] for n in indexed.search(self.store,packet['query'],semantic=packet)['hits']],['q'])

    def test_stale_semantic_input_rejected(self):
        with self.assertRaises(ValueError):indexed.search(self.store,'bank',semantic={'query':'bank','commit_hash':'old','ids':['q'],'model_fingerprint':'x'})

    def test_filters_survive_semantic_fusion(self):
        packet={'query':'bank','commit_hash':self.store.head()[1],'ids':['p','q'],'model_fingerprint':'test-model'}
        self.assertEqual([n['id'] for n in indexed.search(self.store,'bank',semantic=packet,where={'loan':'project-A'})['hits']],['p'])

    def test_explanation_preserves_condition_and_unknown_receipt(self):
        draft=evidence.explain(self.store,'Alice')
        self.assertTrue(draft['cards'][0]['excerpt'].startswith('If Alice'))
        self.assertIn('actor_specific_receipt',draft['cards'][0]['open_dimensions'])
        self.assertNotIn('receipt',draft['cards'][0]['clocks'])
        self.assertEqual(draft['cards'][0]['context']['money_stage'],'requested')

    def test_next_records_include_weakening_outcomes(self):
        out=evidence.next_steps(self.store,'Alice')
        self.assertFalse(out['automatic_external_actions'])
        self.assertTrue(all(a['confirming_result'] and a['weakening_result'] for a in out['actions']))

    def test_bad_evidence_span_rejected(self):
        packet=self.proposal();packet['relations'][0]['evidence'][0]['quote']='Alice authorized the payment.'
        with self.assertRaises(ValueError):evidence.propose(self.store,packet)

    def test_review_is_named_and_preserves_history(self):
        p=self.proposal();span=p['relations'][0]['evidence'][0];span['end']=len(span['quote'])
        proposal=evidence.propose(self.store,p);ident=proposal['edge_ids'][0]
        review={'batch_id':'review','edge_id':ident,'decision':'accept','reviewer':'test reviewer','reason':'Interpretation checked against full context.','expected_revision':self.store.head()[0]}
        evidence.review(self.store,review)
        view=indexed.inspect(self.store,'p');edge=next(e for e in view['edges'] if e['id']==ident)
        self.assertEqual(edge['data']['review_state'],'ACCEPTED_ANNOTATION')
        self.assertEqual(edge['revision'],2);self.assertTrue(self.store.verify()['ok'])
        with self.assertRaises(ValueError):evidence.review(self.store,review)

    def test_suggest_is_delta_scoped_and_always_candidate(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'entities','nodes':[{'id':'a','type':'entity','label':'Alice'},{'id':'b','type':'entity','label':'Builder'}]})
        before=self.store.head()[0]
        self.assertEqual(evidence.suggest(self.store,after_revision=before)['state'],'NO_PROPOSALS')
        result=evidence.suggest(self.store)
        self.assertGreater(result['proposed'],0)
        self.assertTrue(all(e['status']=='candidate' and e['predicate']=='proposed_relation' for e in self.store.snapshot()['edges']))

    def test_email_import_preserves_headers_body_and_hash(self):
        p=self.root/'forward.eml';p.write_bytes(b'From: Forwarder <f@example.test>\nTo: Reader <r@example.test>\nDate: Tue, 13 Oct 2026 10:00:00 -0600\nSubject: Forward\nMessage-ID: <outer@example.test>\nContent-Type: text/plain; charset=utf-8\n\nForwarded message from 2018:\nIf approved, Builder would request a draw.\n')
        packet={'batch_id':'email','files':[{'path':str(p),'source_tier':'native_email'}]}
        out=intake.ingest(self.store,packet);self.assertEqual(out['imported'],1)
        self.assertEqual(intake.ingest(self.store,packet)['state'],'IDEMPOTENT')
        draft=evidence.explain(self.store,'approved')
        self.assertIn('If approved',draft['cards'][0]['excerpt'])
        self.assertIn('2026',draft['cards'][0]['clocks']['statement'][0]);self.assertNotIn('receipt',draft['cards'][0]['clocks'])

    def test_csv_preserves_columns_and_duplicate_headers_rejected(self):
        p=self.root/'rows.csv';p.write_text('loan,stage,amount\n673,requested,12.50\n393,paid,12.50\n')
        records,_=intake.prepare({'batch_id':'csv','files':[{'path':str(p)}]})
        self.assertEqual(records[0]['data']['cells']['stage'],'requested')
        self.assertEqual(records[1]['data']['cells']['loan'],'393')
        p.write_text('amount,amount\n1,2\n')
        with self.assertRaises(ValueError):intake.prepare({'batch_id':'bad-csv','files':[{'path':str(p)}]})

    def test_document_versions_are_linked(self):
        p=self.root/'v.txt';p.write_text('first account')
        intake.ingest(self.store,{'batch_id':'first','files':[{'path':str(p)}]})
        p.write_text('revised account')
        intake.ingest(self.store,{'batch_id':'second','files':[{'path':str(p)}]})
        self.assertEqual(sum(e['predicate']=='version_of' for e in self.store.snapshot()['edges']),1)

    def test_selected_pdf_passes_seekable_handle_and_closes_it(self):
        from types import SimpleNamespace
        seen=[]
        class Reader:
            def __init__(self,stream):
                seen.append(stream)
                if not hasattr(stream,'seek'):raise AssertionError('reader must receive a seekable handle')
                self.pages=[SimpleNamespace(extract_text=lambda:'Source text')]
        p=self.root/'selected.pdf';p.write_bytes(b'fake PDF bytes; decoder replaced by test port')
        with patch.dict(sys.modules,{'pypdf':SimpleNamespace(PdfReader=Reader)}):
            parts,warnings=intake.sections(p,None,{'pages':[1]})
        self.assertTrue(seen[0].closed);self.assertEqual(parts[0][0],'PDF page 1')

    def test_restore_fresh_path_verified_existing_destination_preserved(self):
        dest=self.root/'restored.sqlite';out=studio.restore(str(self.store.path),str(dest))
        self.assertEqual(out['state'],'RESTORED');self.assertTrue(GraphStore(dest).verify()['ok'])
        with self.assertRaises(ValueError):studio.restore(str(self.store.path),str(dest))

    def test_language_dispatch_reaches_studio(self):
        import surd
        self.assertIn('graph_explain',surd.INTEGRATION_FUNCS)
        from surd_integrations import call
        self.assertEqual(call('graph_hybrid_search',[str(self.store.path),'funding'])['hits'][0]['id'],'q')


if __name__=='__main__':unittest.main()
