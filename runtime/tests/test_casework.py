"""Source, clock, stage and contradiction controls for the case layer."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from email.message import EmailMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.store import GraphStore
from loom_research import casework, engine, intake, studio, evidence
from loom_research.contracts import digest


class CaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=GraphStore(self.root/'case.sqlite',create=True)
        self.body='Builder requested a revised budget. Reader acknowledged the attachment. Later account disputed the payment.'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'raw',
            'nodes':[{'id':'s','type':'source','origin':'native:test','locator':'test:1'},
                     {'id':'p','type':'passage','label':'budget source','text':self.body,'source_ids':['s']} ]})

    def mapping(self, ident='a', **kwargs):
        return {'id':ident,'episode':'budget','kind':'statement','context':{'actor':'Builder'},
                'evidence':[{'node_id':'p','start':0,'end':len(self.body),'quote':self.body}],**kwargs}

    def put(self, *m):
        return casework.map_records(self.store,{'batch_id':'m'+str(self.store.head()[0]),'reviewer':'Test reviewer',
            'mappings':list(m),'expected_revision':self.store.head()[0]})

    def test_mapping_idempotency(self):
        self.put(self.mapping());self.assertEqual(self.put(self.mapping())['state'],'IDEMPOTENT')

    def test_false_quote_rejected(self):
        m=self.mapping();m['evidence'][0]['quote']='invented'
        with self.assertRaises(ValueError):self.put(m)

    def test_revision_conflict(self):
        with self.assertRaises(ValueError):casework.map_records(self.store,{'batch_id':'bad','reviewer':'r','mappings':[self.mapping()],'expected_revision':0})

    def test_native_revision_invalidates_annotation(self):
        self.put(self.mapping());snap=self.store.snapshot();p=next(n for n in snap['nodes'] if n['id']=='p')
        from loom_research.learning import clean_node
        p=clean_node(p);p['revision']+=1;p['text']='corrected native extract'
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'revise','nodes':[p]})
        e=casework.episode(self.store,'budget');self.assertEqual(e['stale_annotations'],['case:a']);self.assertEqual(e['sentences'],[])

    def test_visible_recipient_keeps_receipt_open(self):
        self.put(self.mapping(knowledge=[{'actor':'Reader','state':'visible_recipient'}],clocks={'statement':['2018-02-07']*2}))
        e=casework.episode(self.store,'budget');self.assertEqual(e['knowledge'][0]['state'],'visible_recipient')
        self.assertFalse(any(t['clock']=='receipt' for t in e['timeline']))

    def test_scoped_approval_requires_version(self):
        with self.assertRaises(ValueError):self.put(self.mapping(knowledge=[{'actor':'Reader','state':'scoped_approval','scope':'slab'}]))

    def test_quoted_statement_and_wrapper_clocks_separate(self):
        self.put(self.mapping('quoted',clocks={'statement':['2019-07-08']*2}),self.mapping('wrapper',kind='discovery',clocks={'statement':['2026-03-03']*2}))
        e=casework.episode(self.store,'budget');self.assertEqual([t['at'][0] for t in e['timeline']],['2019-07-08','2026-03-03'])

    def test_unknown_date_retained(self):
        self.put(self.mapping());self.assertIsNone(casework.episode(self.store,'budget')['timeline'][0]['at'])

    def test_existing_finding_status_preserved(self):
        f={'external_id':'F1','status':'SUPPORTED_WITH_OPEN_BRIDGES','statement':'Scoped retained finding'}
        self.put(self.mapping(kind='finding',finding=f))
        self.assertEqual(casework.episode(self.store,'budget')['sentences'][0]['finding'],f)

    def aligned(self,ident,stance,**context):
        c={k:'same' for k in casework.ALIGN};c.update(context)
        return self.mapping(ident,context=c,claim={'key':'included-in-this-version','stance':stance})

    def test_aligned_opposed_statements(self):
        self.put(self.aligned('a','support'),self.aligned('b','oppose'))
        self.assertEqual(casework.compare(self.store,{'left':'case:a','right':'case:b'})['state'],'SUPPORTED_COLLISION')

    def test_wrong_loan_resolves_scope_comparison(self):
        self.put(self.aligned('a','support'),self.aligned('b','oppose',loan='other'))
        r=casework.compare(self.store,{'left':'case:a','right':'case:b'})
        self.assertEqual(r['state'],'OPEN_COORDINATE_COMPARISON');self.assertEqual(r['differing_axes'],['loan'])

    def test_missing_axes_stay_open(self):
        self.put(self.mapping('a',claim={'key':'same','stance':'support'}),self.mapping('b',claim={'key':'same','stance':'oppose'}))
        self.assertEqual(casework.compare(self.store,{'left':'case:a','right':'case:b'})['state'],'OPEN_COLLISION')

    def money(self,ident,stage='paid',event='check1',loan='673',value='12.50'):
        return self.mapping(ident,kind='money',context={'property':'lot2','loan':loan,'scope':'slab','obligation':'invoice1','instrument':'check1'},
                            money={'event_id':event,'stage':stage,'value':value,'unit':'USD'})

    def test_money_stages_and_wrong_loan_separate(self):
        self.put(self.money('a'),self.money('b',stage='requested',event='draw1'),self.money('c',loan='393'))
        lanes=casework.lifecycle(self.store,'budget')['lanes'];self.assertEqual(len(lanes),2)
        lane=next(l for l in lanes if l['coordinates']['loan']=='673')
        self.assertEqual(set(lane['stages']),{'paid','requested'});self.assertIn('credited',lane['open_stages'])

    def test_duplicate_observations_visible(self):
        self.put(self.money('a'),self.money('b'))
        self.assertEqual(len(casework.lifecycle(self.store,'budget')['lanes'][0]['duplicate_observations']),1)

    def test_event_value_conflict_visible(self):
        self.put(self.money('a'),self.money('b',value='13'))
        self.assertEqual(len(casework.lifecycle(self.store,'budget')['lanes'][0]['event_conflicts']),1)

    def test_float_money_rejected(self):
        with self.assertRaises(ValueError):self.put(self.money('a',value=12.5))

    def test_reversal_reference_preserved(self):
        m=self.money('a');m['money']['reversal_of']='prior-event';self.put(m)
        self.assertEqual(casework.lifecycle(self.store,'budget')['lanes'][0]['observations'][0]['reversal_of'],'prior-event')

    def test_explanation_sentences_have_exact_spans(self):
        self.put(self.mapping());e=evidence.explain(self.store,'budget',episode='budget')
        self.assertEqual(e['sentences'][0]['quotes'][0]['quote'],self.body)
        self.assertEqual(e['comprehension_state'],'OPEN_INDEPENDENT_READER_EVALUATION')

    def test_mapping_gap_distinct_from_missing_record(self):
        self.put(self.mapping());self.assertEqual(casework.episode(self.store,'budget')['mapping_actions'][0]['state'],'MAPPING_COVERAGE_OPEN')

    def test_analytical_summary_preserves_source_search_slots(self):
        self.put(self.mapping(summary='revised budget '*30))
        from loom_research import indexed
        self.assertEqual(indexed.search(self.store,'revised budget',top=1)['hits'][0]['id'],'p')

    def test_email_metadata_upgrade_revises_existing_body(self):
        p=self.root/'email.eml';p.write_text('From: a@example.test\nTo: b@example.test\nSubject: budget\n\nUpdated budget.')
        packet={'batch_id':'old','files':[{'path':str(p)}]};intake.ingest(self.store,packet)
        packet['batch_id']='upgrade';packet['files'][0]['data']={'mapped_scope':'new metadata'}
        self.assertEqual(intake.ingest(self.store,packet)['imported'],1)
        packet['batch_id']='same';self.assertEqual(intake.ingest(self.store,packet)['state'],'IDEMPOTENT')

    def test_pair_budget_is_explicit(self):
        self.put(*(self.aligned(str(i),'support') for i in range(3)))
        self.assertTrue(casework.episode(self.store,'budget',max_pairs=1)['pair_budget_exhausted'])

    def test_frozen_eval_rejects_changed_labels(self):
        with self.assertRaises(ValueError):casework.evaluate(self.store,{'dataset_id':'x','split':'audit','questions':[{'id':'1','query':'budget','relevant_ids':['p']}],'definition_hash':'wrong'})

    def test_eval_uses_actual_candidates(self):
        r=casework.evaluate(self.store,{'dataset_id':'x','split':'development','questions':[{'id':'1','query':'budget','relevant_ids':['p']}]})
        self.assertEqual(r['metrics']['top1'],1)

    def test_language_dispatch(self):
        import surd
        self.assertIn('graph_case_map',surd.INTEGRATION_FUNCS)
        self.put(self.mapping());self.assertEqual(studio.call('graph_case_episode',[str(self.store.path),'budget'])['state'],'CASE_EPISODE_DRAFT')

    def test_ocr_hash_and_page_gate(self):
        with self.assertRaises(ValueError):casework.recover(self.store,{'batch_id':'ocr','source_node':'p','native_sha256':'x','page':1,'text':'text','method':'OCR','reviewer':'r','expected_revision':1})

    def test_ocr_current_native_and_idempotency(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'page','nodes':[
            {'id':'page','type':'passage','text':'','source_ids':['s'],'data':{'page':1,'native_sha256':'a'*64}}]})
        packet={'batch_id':'ocr','source_node':'page','native_sha256':'a'*64,'page':1,'text':'Recovered page text',
                'method':'Test OCR','reviewer':'r','expected_revision':self.store.head()[0]}
        r=casework.recover(self.store,packet);self.assertEqual(r['state'],'OCR_REVIEW_ATTACHED')
        packet['expected_revision']=self.store.head()[0]
        self.assertEqual(casework.recover(self.store,packet)['state'],'IDEMPOTENT')

    def test_same_attachment_version_has_zero_changes(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'cells','nodes':[
            {'id':'cells','type':'passage','text':'A1: budget','source_ids':['s'],
             'data':{'attachment_sha256':'b'*64,'sheet':'Sheet1','cells':{'A1':'budget'}}}]})
        self.assertEqual(casework.versions(self.store,{'left_hash':'b'*64,'right_hash':'b'*64,'scope':'same exact attachment'})['changes'],[])

    def test_standalone_native_workbook_versions(self):
        self.store.apply(engine.records_batch('native-workbooks',[
            {'id':'left-native','origin':'synthetic:left.xlsx','locator':'Sheet1!A1','text':'A1: 25',
             'data':{'native_sha256':'a'*64,'sheet':'Sheet1','cells':{'A1':'25'}}},
            {'id':'right-native','origin':'synthetic:right.xlsx','locator':'Sheet1!A1','text':'A1: 20',
             'data':{'native_sha256':'b'*64,'sheet':'Sheet1','cells':{'A1':'20'}}}]))
        changes=casework.versions(self.store,{'left_hash':'a'*64,'right_hash':'b'*64,'scope':'same synthetic cell'})['changes']
        self.assertEqual([(c['left'],c['right']) for c in changes],[('25','20')])
        self.assertEqual(changes[0]['nodes'],['left-native','right-native'])

    def test_embedded_workbook_hash_takes_precedence_over_email_hash(self):
        self.store.apply(engine.records_batch('embedded-workbook',[
            {'id':'embedded','origin':'synthetic:email.eml','locator':'attachment Sheet1!A1','text':'A1: 25',
             'data':{'native_sha256':'a'*64,'attachment_sha256':'b'*64,'sheet':'Sheet1','cells':{'A1':'25'}}}]))
        with self.assertRaises(ValueError):casework.versions(self.store,{'left_hash':'a'*64,'right_hash':'a'*64,'scope':'email is a different byte object'})
        self.assertEqual(casework.versions(self.store,{'left_hash':'b'*64,'right_hash':'b'*64,'scope':'exact workbook'})['changes'],[])

    def test_version_cell_changes_preserve_sources(self):
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'cells','nodes':[
            {'id':ident,'type':'passage','text':value,'source_ids':['s'],
             'data':{'attachment_sha256':sha*64,'sheet':'Sheet1','cells':{'A1':value}}}
            for ident,sha,value in [('old','a','100'),('new','b','120')]]})
        r=casework.versions(self.store,{'left_hash':'a'*64,'right_hash':'b'*64,'scope':'test budget'})
        self.assertEqual(r['changes'][0]['nodes'],['old','new']);self.assertEqual(r['changes'][0]['right'],'120')



if __name__=='__main__':unittest.main()
