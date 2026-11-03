"""Regression contracts from the real-data and capacity stress evaluation."""
import copy,json,os,subprocess,sys,tempfile,unittest,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from surd_search import SearchIndex,SearchError
from loom_research import retrieval,engine
from loom_research.store import GraphStore
from loom_research.contracts import ResearchError

class Scalability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'graph.sqlite';self.store=GraphStore(self.path,create=True)
    def tearDown(self):self.tmp.cleanup()
    def put(self,ident,nodes=None,edges=None):return self.store.apply({'schema':'loom.graph.batch.v1','batch_id':ident,'nodes':nodes or [],'edges':edges or []})

    def test_morphology_preserves_literal_constraints_and_identifiers(self):
        ix=SearchIndex([{'id':'original','text':'activated cells were not authorized'},
                        {'id':'transfer','text':'authorizing payments'}, {'id':'authorized','text':'unrelated'}])
        self.assertEqual(ix.search('activation cell')['hits'][0]['id'],'original')
        self.assertEqual(ix.search('authorized')['hits'][0]['id'],'authorized')
        self.assertEqual([h['id'] for h in ix.search('"not authorized"')['hits']],['original'])
        self.assertEqual(ix.search('activation +cell')['hits'],[]) # + remains literal
        self.assertEqual(ix.search('activation -not')['hits'],[])
        self.assertEqual(ix.search('zzzznomatch')['hits'],[])
        self.assertEqual(SearchIndex([{'id':'CB-151','text':'x'}]).search('CB-152')['hits'],[])

    def test_graph_concepts_never_displace_direct_evidence(self):
        for topic in ('enzyme activation','refund disposition'):
            nodes=[{'id':'paper'+topic[0],'type':'passage','label':'Native record','text':topic},
                   {'id':'hub'+topic[0],'type':'concept','label':topic,'text':topic*30,'status':'derived'}]
            self.put(topic,nodes)
            r=retrieval.search(self.store.snapshot(),topic,top=1,hops=0)
            self.assertEqual(r['hits'][0]['id'],'paper'+topic[0])
            self.assertNotEqual(retrieval.search(self.store.snapshot(),topic,top=10,hops=0,include_structural=True)['hits'],[])

    def test_cache_uses_actual_rows_and_revision(self):
        self.put('one',[{'id':'A','type':'passage','text':'blue invoice'}]);snap=self.store.snapshot()
        retrieval._INDEX_CACHE.clear();retrieval._CACHE_INPUT_BYTES=0
        self.assertFalse(retrieval.search(snap,'blue')['index_cache_hit'])
        self.assertTrue(retrieval.search(snap,'blue')['index_cache_hit'])
        altered=copy.deepcopy(snap);altered['nodes'][0]['text']='green payment'
        self.assertFalse(retrieval.search(altered,'blue')['hits'])
        self.put('two',[{'id':'A','type':'passage','revision':2,'text':'green payment'}])
        self.assertFalse(self.store.search('blue')['hits'])

    def test_compiled_cache_roundtrip_and_corruption(self):
        ix=SearchIndex([{'id':'A','text':'activated cells','locator':'native:a'}]);path=Path(self.tmp.name)/'cache.gz'
        self.assertTrue(ix.save_compiled(path,'basis'))
        copy_ix=SearchIndex.load_compiled(path,'basis');self.assertEqual(ix.search('activation')['hits'],copy_ix.search('activation')['hits'])
        with self.assertRaises(SearchError):SearchIndex.load_compiled(path,'other-basis')
        path.write_bytes(b'invalid');rows=[{'id':'A','text':'activated cells'}]
        self.assertTrue(retrieval.cached_index(rows,path)[0].search('activation')['hits'])

    def test_pages_are_complete_ordered_byte_bounded_and_revision_bound(self):
        self.put('many',[{'id':'n'+str(i),'type':'passage','text':'x'*1000} for i in range(11)])
        cursor=None;ids=[]
        while True:
            p=self.store.page(limit=3,cursor=cursor);ids += [n['id'] for n in p['nodes']]
            self.assertLess(len(json.dumps(p).encode()),4*1024**2)
            cursor=p['pagination']['next_cursor']
            if cursor is None:break
        self.assertEqual(len(ids),11);self.assertEqual(len(set(ids)),11)
        first=self.store.page(limit=3);self.put('new',[{'id':'new','type':'entity'}])
        with self.assertRaises(ResearchError):self.store.page(limit=3,cursor=first['pagination']['next_cursor'])
        with self.assertRaises(ResearchError):self.store.page(limit=4,cursor=first['pagination']['next_cursor'])
        with self.assertRaises(ResearchError):self.store.page(cursor='%%%invalid')

    def test_evolution_resumes_without_permanent_truncation(self):
        records=[{'id':'d'+str(i),'origin':'o'+str(i),'locator':'SYN:'+str(i),'text':'common scientific vocabulary topic'+str(i)} for i in range(20)]
        self.store.apply(engine.records_batch('records',records));r=engine.evolve(self.store,max_concepts=2,max_links=3)
        self.assertFalse(r['evolution_complete']);self.assertGreater(sum(r['remaining'].values()),0)
        basis=r['continuation']
        for i in range(50):
            r=engine.evolve(self.store,max_concepts=2,max_links=3,continuation=basis)
            if r['evolution_complete']:break
        self.assertTrue(r['evolution_complete'],r);self.assertEqual(r['remaining'],{'nodes':0,'edges':0})
        self.assertEqual(engine.evolve(self.store)['state'],'IDEMPOTENT')
        self.put('new-source',[{'id':'new-source','type':'source','origin':'new','locator':'SYN:new'}])
        with self.assertRaises(ResearchError):engine.evolve(self.store,continuation=basis)
        self.assertTrue(self.store.verify()['ok'])

    def test_dense_budget_never_exceeds_reported_limit(self):
        self.put('dense',[{'id':'n'+str(i),'type':'passage','text':'common'} for i in range(10)],
                 [{'id':'e'+str(i),'subject':'n0','object':'n'+str(i),'predicate':'related_to','status':'candidate'} for i in range(1,10)])
        r=retrieval.search(self.store.snapshot(),'common',include_candidates=True,max_visits=2)
        self.assertEqual(r['state'],'PARTIAL');self.assertLessEqual(r['work'],2)

    def test_legacy_recipe_is_revalidated_without_overwriting_old_receipt(self):
        from loom_research import learning
        target=Path(self.tmp.name)/'legacy.sqlite'
        shutil.copy2(ROOT/'examples/data/recipe-upgrade-original.sqlite',target)
        legacy=GraphStore(target);packet=json.loads((ROOT/'examples/data/recipe-upgrade-original.json').read_text())
        methods=learning.methods(legacy.snapshot())['methods']
        self.assertEqual(methods[0]['validation_state'],'REVALIDATION_REQUIRED')
        r=engine.run(legacy,{'schema':'loom.research.run.v1','id':'upgrade','scope':packet['scope'],'analyze':False,'resolve':False})
        self.assertIsNone(r['method_used'])
        before={n['id'] for n in legacy.snapshot()['nodes'] if n['id'].startswith('evaluation:')}
        result=learning.evaluate(legacy,packet);self.assertTrue(result['activated'])
        after={n['id'] for n in legacy.snapshot()['nodes'] if n['id'].startswith('evaluation:')}
        self.assertTrue(before<after)
        self.assertTrue(any(m['validation_state']=='CURRENT_POLICY' for m in learning.methods(legacy.snapshot())['methods']))
        self.assertTrue(legacy.verify()['ok'])

if __name__=='__main__':unittest.main()
