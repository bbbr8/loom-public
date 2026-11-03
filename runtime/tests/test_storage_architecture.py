"""Independent concurrency, derived-plan and disposable read-model contracts."""
import json,os,shutil,sqlite3,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.store import GraphStore
from loom_research.contracts import ResearchError
from loom_research import engine,retrieval


def packet(name,text='old',revision=1):
    return {'schema':'loom.graph.batch.v1','batch_id':name,'nodes':[{'id':'A','type':'passage','text':text,'revision':revision}]}


class StorageArchitecture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'graph.sqlite'
        self.store=GraphStore(self.path,create=True);self.store.apply(packet('initial'))
    def tearDown(self):self.tmp.cleanup()

    def test_snapshot_pins_revision_and_rows_during_concurrent_commit(self):
        with self.store.connection() as c:c.execute('PRAGMA journal_mode=WAL')
        started=threading.Event();committed=threading.Event();errors=[]
        class Paused(GraphStore):
            @staticmethod
            def _rows(c,seq=None):
                started.set()
                if not committed.wait(3):raise AssertionError('writer did not commit under WAL')
                return GraphStore._rows(c,seq)
        def write():
            try:
                if not started.wait(3):raise AssertionError('reader did not start')
                self.store.apply(packet('correction','new',2))
            except Exception as e:errors.append(e)
            finally:committed.set()
        thread=threading.Thread(target=write);thread.start()
        try:snap=Paused(self.path).snapshot()
        finally:thread.join(4)
        self.assertFalse(errors);self.assertEqual(snap['revision'],1);self.assertEqual(snap['nodes'][0]['text'],'old')
        self.assertEqual(self.store.snapshot()['nodes'][0]['text'],'new')

    def test_conditional_write_rejects_changed_revision_atomically(self):
        before=self.store.journal()
        with self.assertRaises(ResearchError):self.store.apply(packet('bad','new',2),expected_revision=0)
        self.assertEqual(before,self.store.journal())
        self.store.apply(packet('good','new',2),expected_revision=1)
        # A committed logical operation stays idempotent even after its precondition changes.
        self.assertEqual(self.store.apply(packet('good','new',2),expected_revision=1)['state'],'IDEMPOTENT')

    def test_conditional_write_transfer_allows_research_only_changes(self):
        basis=self.store.snapshot()['projection_hash']
        self.store.apply({'schema':'loom.graph.batch.v1','batch_id':'annotation','nodes':[{'id':'note','type':'analysis','space':'research'}]})
        self.store.apply(packet('valid','new',2),expected_projection=basis)
        with self.assertRaises(ResearchError):self.store.apply(packet('stale','third',3),expected_projection=basis)

    def test_evolution_refuses_stale_plan_before_first_commit(self):
        self.store.apply(packet('words','alpha bravo charlie delta',2))
        class Racing(GraphStore):
            def apply(inner,batch,**kwargs):
                if batch['batch_id'].startswith('evolve:'):
                    self.store.apply(packet('raced','changed evidence',3))
                return super(Racing,inner).apply(batch,**kwargs)
        report=engine.evolve(Racing(self.path),max_batches=3)
        self.assertTrue(report['replan_required']);self.assertEqual(report['pages_committed'],0)
        self.assertFalse(any(n['data'].get('auto_evolved') for n in self.store.snapshot()['nodes']))

    def test_evolution_stops_when_evidence_changes_between_pages(self):
        self.store.apply(packet('words','alpha bravo charlie delta echo foxtrot golf',2))
        class Racing(GraphStore):
            def apply(inner,batch,**kwargs):
                out=super(Racing,inner).apply(batch,**kwargs)
                if batch['batch_id'].startswith('evolve:'):
                    self.store.apply(packet('raced','replacement evidence',3))
                return out
        report=engine.evolve(Racing(self.path),max_concepts=1,max_links=1,max_batches=3)
        self.assertEqual(report['pages_committed'],1);self.assertTrue(report['replan_required'])
        self.assertIsNone(report['continuation']);self.assertIn('evidence_changed_restart_required',report['limits'])

    def test_projection_cache_matches_snapshot_invalidates_and_recovers_corruption(self):
        with patch.dict(os.environ,{'LOOM_INDEX_CACHE':'1'}):
            first=self.store.read_projection();self.assertEqual(first,self.store.snapshot())
            with patch.object(self.store,'snapshot',side_effect=AssertionError('cache miss')):
                self.assertEqual(first,self.store.read_projection())
            cache=self.path.parent/'.loom-index-cache'/(self.path.name+'.projection.gz')
            for corrupt in (b'corrupt',b'[]\n',b'{}\n'):
                cache.write_bytes(corrupt)
                self.assertEqual(first,self.store.read_projection())
            self.store.apply(packet('update','new',2))
            self.assertEqual(self.store.read_projection()['nodes'][0]['text'],'new')
            self.assertEqual(self.store.read_projection(),self.store.snapshot())

    def test_cached_temporal_views_remain_distinct(self):
        self.store.apply(packet('update','new',2))
        with patch.dict(os.environ,{'LOOM_INDEX_CACHE':'1'}):
            a=self.store.read_projection(revision=1);b=self.store.read_projection()
            self.assertEqual(a['nodes'][0]['text'],'old');self.assertEqual(b['nodes'][0]['text'],'new')
            self.assertEqual(self.store.read_projection(revision=1),a)

    def test_page_cache_rejects_same_revision_replacement_graph(self):
        first=self.store.page(limit=1)
        other=Path(self.tmp.name)/'other.sqlite';replacement=GraphStore(other,create=True)
        replacement.apply(packet('different-history','replacement'))
        shutil.copyfile(other,self.path)
        second=self.store.page(limit=1)
        self.assertEqual(first['revision'],second['revision']);self.assertEqual(second['nodes'][0]['text'],'replacement')
        self.assertNotEqual(first['graph_hash'],second['graph_hash'])

    def test_cache_symlink_never_overwrites_target(self):
        target=Path(self.tmp.name)/'protected';target.write_bytes(b'original')
        folder=self.path.parent/'.loom-index-cache';folder.mkdir()
        (folder/(self.path.name+'.projection.gz')).symlink_to(target)
        with patch.dict(os.environ,{'LOOM_INDEX_CACHE':'1'}):self.assertEqual(self.store.read_projection(),self.store.snapshot())
        self.assertEqual(target.read_bytes(),b'original')

    def test_verify_detects_raw_materialized_corruption_despite_valid_cache(self):
        with patch.dict(os.environ,{'LOOM_INDEX_CACHE':'1'}):self.store.read_projection()
        with self.store.connection() as c:c.execute("UPDATE node_text SET text='tampered'");c.commit()
        with self.assertRaises(ResearchError):self.store.verify()

    def test_verify_returns_a_pinned_revision_during_later_write(self):
        original=GraphStore.replay.__func__
        def replay(cls,path,journal):
            self.store.apply(packet('after-backup','new',2))
            return original(cls,path,journal)
        with patch.object(GraphStore,'replay',classmethod(replay)):
            report=self.store.verify()
        self.assertEqual(report['revision'],1);self.assertEqual(self.store.snapshot()['revision'],2)

    def test_cache_preserves_retrieval_rankings_and_literal_constraints(self):
        self.store.apply(packet('words','enzymes activated blue marker CB-151',2))
        with patch.dict(os.environ,{'LOOM_INDEX_CACHE':'1'}):
            for query in ('enzyme activation','"blue marker"','CB-152'):
                expected=retrieval.search(self.store.snapshot(),query,hops=0)
                actual=self.store.search(query,hops=0)
                self.assertEqual(expected['hits'],actual['hits']);self.assertEqual(expected['graph_hash'],actual['graph_hash'])

if __name__=='__main__':unittest.main()
