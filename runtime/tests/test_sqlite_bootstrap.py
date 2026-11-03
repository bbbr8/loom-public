"""SQLite bootstrap, native compatibility, idempotency and publication contracts."""
from contextlib import closing
import hashlib,json,os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import loom_sqlite as sql
from loom_research.store import GraphStore
from loom_research.contracts import ResearchError

class SQLiteBootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'research.sqlite'
    def initialize(self,demo=False):return sql.initialize(str(self.path),demo)

    def test_empty_initialization_and_repeat_preserve_bytes(self):
        self.assertEqual(self.initialize()['initialization'],'CREATED');before=self.path.read_bytes()
        self.assertEqual(self.initialize()['initialization'],'IDEMPOTENT');self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(sql.status(str(self.path),check=True)['quick_check'],['ok'])
        self.assertEqual(GraphStore(self.path).snapshot()['nodes'],[])
        if os.name=='posix':self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_demo_is_explicit_queryable_and_native_replayable(self):
        r=self.initialize(True);self.assertTrue(r['demo_seeded']);self.assertEqual(r['revision'],2)
        store=GraphStore(self.path);self.assertEqual(len(store.snapshot()['nodes']),17);self.assertEqual(len(store.snapshot()['edges']),15)
        self.assertEqual(store.search('release approval',hops=0,top=1)['hits'][0]['id'],'demo:release')
        self.assertTrue(store.verify()['ok']);before=self.path.read_bytes()
        self.assertEqual(self.initialize(True)['initialization'],'IDEMPOTENT');self.assertEqual(before,self.path.read_bytes())
        with sql.read_connection(self.path) as c:
            rows=list(c.execute("SELECT json_extract(data_json,'$.amount_cents') FROM loom_node_rows WHERE node_type='passage' AND json_extract(data_json,'$.money_stage')='applied'"))
            self.assertEqual([row[0] for row in rows],[12500]);self.assertIsInstance(rows[0][0],int)

    def test_native_graph_upgrade_preserves_journal_and_projection(self):
        store=GraphStore(self.path,create=True);store.apply({'schema':'loom.graph.batch.v1','batch_id':'one','nodes':[{'id':'A','type':'passage','text':'original'}]})
        before=store.journal(),store.snapshot()
        self.assertEqual(sql.status(str(self.path))['state'],'NATIVE_GRAPH')
        self.assertEqual(self.initialize()['initialization'],'UPGRADED_NATIVE_GRAPH')
        self.assertEqual(before,(store.journal(),store.snapshot()));self.assertTrue(store.verify()['ok'])

    def test_demo_refuses_populated_non_demo_graph_without_changing_bytes(self):
        store=GraphStore(self.path,create=True);store.apply({'schema':'loom.graph.batch.v1','batch_id':'one','nodes':[{'id':'A','type':'passage'}]});before=self.path.read_bytes()
        with self.assertRaises(ResearchError):self.initialize(True)
        self.assertEqual(before,self.path.read_bytes())

    def test_existing_non_loom_database_is_preserved(self):
        with closing(sqlite3.connect(self.path)) as c:c.execute('CREATE TABLE unrelated(value)');c.commit()
        before=self.path.read_bytes()
        with self.assertRaises(ResearchError):self.initialize()
        self.assertEqual(before,self.path.read_bytes())

    def test_missing_status_and_backup_create_no_database(self):
        with self.assertRaises(ResearchError):sql.status(str(self.path))
        with self.assertRaises(ResearchError):sql.backup(str(self.path),str(Path(self.tmp.name)/'out.sqlite'))
        self.assertFalse(self.path.exists());self.assertFalse((Path(self.tmp.name)/'out.sqlite').exists())

    def test_backup_is_exclusive_and_pins_native_graph(self):
        self.initialize(True);dest=Path(self.tmp.name)/'copy.sqlite';source=self.path.read_bytes()
        r=sql.backup(str(self.path),str(dest));self.assertEqual(r['sha256'],hashlib.sha256(dest.read_bytes()).hexdigest())
        self.assertEqual(GraphStore(self.path).snapshot(),GraphStore(dest).snapshot());self.assertTrue(GraphStore(dest).verify()['ok'])
        self.assertEqual(source,self.path.read_bytes());before=dest.read_bytes()
        with self.assertRaises(ResearchError):sql.backup(str(self.path),str(dest))
        self.assertEqual(before,dest.read_bytes())
        with self.assertRaises(ResearchError):sql.backup(str(self.path),str(self.path))

    def test_raw_schema_matches_shipped_sql_and_opens_with_native_runtime(self):
        self.assertEqual(sql.schema_sql(),(ROOT/'sqlite-bootstrap.sql').read_text())
        with closing(sqlite3.connect(self.path)) as c:c.executescript(sql.schema_sql())
        self.assertEqual(sql.status(str(self.path))['bootstrap_schema'],1);self.assertTrue(GraphStore(self.path).verify()['ok'])

    def test_backup_report_stays_pinned_after_source_changes(self):
        self.initialize(True);destination=Path(self.tmp.name)/'copy.sqlite';original=sql.publish
        def publish(temp,target):
            GraphStore(self.path).apply({'schema':'loom.graph.batch.v1','batch_id':'later-write','nodes':[{'id':'new','type':'passage','text':'later'}]})
            original(temp,target)
        with patch.object(sql,'publish',side_effect=publish):report=sql.backup(str(self.path),str(destination))
        self.assertEqual(report['revision'],2);self.assertEqual(GraphStore(destination).head()[0],2)
        self.assertEqual(GraphStore(self.path).head()[0],3);self.assertTrue(GraphStore(destination).verify()['ok'])

    def test_additive_upgrade_refuses_view_collision_without_mutation(self):
        GraphStore(self.path,create=True)
        with closing(sqlite3.connect(self.path)) as c:c.execute('CREATE VIEW loom_node_rows AS SELECT 1 AS bogus');c.commit()
        before=self.path.read_bytes()
        with self.assertRaises(ResearchError):self.initialize()
        self.assertEqual(before,self.path.read_bytes())

    def test_migration_fingerprint_and_view_drift_are_rejected(self):
        self.initialize()
        with closing(sqlite3.connect(self.path)) as c:c.execute("UPDATE loom_bootstrap_migrations SET sha256='bad'");c.commit()
        before=self.path.read_bytes()
        with self.assertRaises(ResearchError):self.initialize()
        self.assertEqual(before,self.path.read_bytes())
        with closing(sqlite3.connect(self.path)) as c:
            c.execute('UPDATE loom_bootstrap_migrations SET sha256=?',(sql.MIGRATION_SHA256,));c.execute('DROP VIEW loom_node_rows');c.execute('CREATE VIEW loom_node_rows AS SELECT 1 AS bogus');c.commit()
        with self.assertRaises(ResearchError):sql.status(str(self.path))

    def test_linked_database_and_sidecars_are_rejected(self):
        target=Path(self.tmp.name)/'original.sqlite';target.write_bytes(b'original');self.path.symlink_to(target)
        with self.assertRaises(ResearchError):self.initialize()
        self.path.unlink();Path(str(self.path)+'-wal').symlink_to(target)
        with self.assertRaises(ResearchError):self.initialize()
        self.assertEqual(target.read_bytes(),b'original')

    def test_failed_new_initialization_publishes_nothing(self):
        with patch.object(sql,'seed_demo',side_effect=ResearchError('injected failure')):
            with self.assertRaises(ResearchError):self.initialize(True)
        self.assertFalse(self.path.exists());self.assertFalse(list(self.path.parent.glob('.loom-bootstrap-*')))

    def test_atomic_publication_refuses_destination_won_by_another_creator(self):
        original=sql.os.link
        def race(source,target):
            Path(target).write_bytes(b'other creator');return original(source,target)
        with patch.object(sql.os,'link',side_effect=race):
            with self.assertRaises(FileExistsError):self.initialize()
        self.assertEqual(self.path.read_bytes(),b'other creator');self.assertFalse(list(self.path.parent.glob('.loom-bootstrap-*')))

    def test_unknown_options_and_false_booleans_rejected(self):
        for values in [[str(self.path),{'unknown':True}],[str(self.path),{'demo':1}]]:
            with self.assertRaises(ResearchError):sql.call('sqlite_bootstrap',values)
        self.assertFalse(self.path.exists())
        with self.assertRaises(ResearchError):sql.initialize(':memory:')

    def test_native_program_functions_and_cli_round_trip(self):
        import surd
        program='let p = '+json.dumps(str(self.path))+'\nexpect sqlite_bootstrap(p,{"demo":true})["state"] == "READY"\nexpect sqlite_status(p)["revision"] == 2\nexpect sqlite_backup(p,'+json.dumps(str(Path(self.tmp.name)/'backup.sqlite'))+')["revision"] == 2'
        surd.Interp(echo=False).run(program)
        p=subprocess.run([sys.executable,str(ROOT/'loom.py'),'sqlite','inspect','--db',str(self.path),'--check'],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertEqual(json.loads(p.stdout)['quick_check'],['ok'])

    def test_sql_views_keep_stored_and_effective_states_distinct(self):
        self.initialize(True);store=GraphStore(self.path);snap=store.snapshot();source=next(n for n in snap['nodes'] if n['id']=='src:demo:release')
        from loom_research.learning import clean_node
        source=clean_node(source);source['revision']=2;source['label']='Corrected synthetic source'
        store.apply({'schema':'loom.graph.batch.v1','batch_id':'corrected','nodes':[source]})
        self.assertNotIn('demo:release',{n['id'] for n in store.snapshot()['nodes']})
        with sql.read_connection(self.path) as c:
            row=c.execute("SELECT materialized_active,stored_status FROM loom_node_rows WHERE id='demo:release'").fetchone()
            self.assertEqual(row[0],1);self.assertEqual(row[1],'annotated')

if __name__=='__main__':unittest.main()
