"""Unified command, native timeline intake and embedded finite-state regressions."""
import copy
from contextlib import closing
import itertools
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import surd
from surd_timeline import run
from loom_qulap.model import solve


class Integrations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name)/"store.sqlite"
        self.ledger = Path(self.tmp.name)/"ledger.sqlite"
        with closing(sqlite3.connect(self.store)) as c:
            c.execute("CREATE TABLE findings(sha256,path,mech,layer,where_,text,detail)")
            for mech,layer,text,detail in [
                ("docx_deleted_text","deleted","removed sentence",{"date":"2020-01-02T12:00:00Z"}),
                ("eml_thread_prior_message","metadata","2020-01-03T12:00:00Z",{}),
                ("eml_header","live","Synthetic subject",{"header":"Subject"}),
                ("eml_header","live","Thu, 02 Jan 2020 05:00:00 -0700",{"header":"Date"}),
                ("docx_comment","comment","undated",{})]:
                c.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?)",("syn","synthetic.docx",mech,layer,"SYN:1",text,json.dumps(detail)))
            c.commit()
        with closing(sqlite3.connect(self.ledger)) as c:
            c.execute("CREATE TABLE transactions(date,amount,sign,type,check_no,counterparty,bates)")
            c.execute("INSERT INTO transactions VALUES(?,?,?,?,?,?,?)",("2020-01-02","90071992547409.93","-","check","42","SYN","SYN:2"))
            c.commit()
        self.model = json.loads((ROOT/'examples/data/qulap-credit.model.json').read_text())

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'loom.py'),*args],text=True,capture_output=True,timeout=15,env=dict(os.environ,SURD_USAGE="0"))

    def test_timeline_exact_money_unknown_date_and_read_only(self):
        before = self.store.read_bytes(),self.ledger.read_bytes()
        r=run('window 2020-01-01 .. 2020-01-04; hidden; mail; ghosts; money ledger; collide days 1',str(self.store),str(self.ledger))
        self.assertTrue(r['complete'])
        self.assertGreater(r['unknown_date_rows'],0)
        m=next(e for e in r['events'] if e['kind']=='money')
        self.assertEqual(m['amount_text'],'90071992547409.93')
        self.assertEqual(m['t'],'2020-01-02')
        self.assertEqual(m['precision'],'day')
        self.assertTrue(any(e['kind']=='ghost' for e in r['events']))
        self.assertTrue(r['collisions'])
        self.assertEqual(before,(self.store.read_bytes(),self.ledger.read_bytes()))

    def test_row_limit_is_partial_and_missing_store_stays_missing(self):
        r=run('hidden',str(self.store),max_rows=1)
        self.assertEqual(r['state'],'PARTIAL')
        absent=Path(self.tmp.name)/'absent.sqlite'
        with self.assertRaises(sqlite3.Error):run('hidden',str(absent))
        self.assertFalse(absent.exists())

    def test_quoted_delimiters_save_is_exclusive_and_validation(self):
        r=run('say "a;b"; say "two words"')
        self.assertEqual(r['messages'],['a;b','two words'])
        dest=Path(self.tmp.name)/'saved.jsonl'
        run('save '+json.dumps(str(dest)))
        with self.assertRaises(FileExistsError):run('save '+json.dumps(str(dest)))
        for src in ['window 2020-02-01 .. 2020-01-01','money other','around 2020-01-01 days -1','collide days -1']:
            with self.assertRaises(ValueError):run(src)

    def test_dialect_routing_both_file_forms_and_legacy_inline(self):
        p=Path(self.tmp.name)/'program.loom';p.write_text('say "legacy timeline"\n')
        for args in [(str(p),),('run',str(p)),('timeline',str(p)),('-e','say legacy timeline')]:
            r=self.cli(*args);self.assertEqual(r.returncode,0,r.stderr+r.stdout);self.assertIn('legacy timeline',r.stdout)
        p.write_text('expect h >> h == id\n')
        self.assertEqual(self.cli('run',str(p)).returncode,0)

    def test_qulap_inside_language_and_cli(self):
        expr='qulap_solve('+json.dumps(self.model)+')["feasible_states"]'
        r=self.cli('eval',expr,'--json');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(r.stdout)['display'],'3')
        r=self.cli('qulap','solve',str(ROOT/'examples/data/qulap-credit.model.json'))
        d=json.loads(r.stdout);self.assertEqual(d['feasible_states'],3);self.assertEqual(d['proof_effect'],'NONE')
        r=self.cli('eval','timeline_run("say synthetic")["state"]','--json')
        self.assertEqual(json.loads(r.stdout)['display'],'COMPLETE')

    def test_incomplete_stays_incomplete_through_language(self):
        r=self.cli('eval','qulap_solve('+json.dumps(self.model)+', {"max_nodes":1})["state"]','--json')
        self.assertEqual(json.loads(r.stdout)['display'],'INCOMPLETE')

    def test_exhaustive_small_integer_oracle(self):
        for cap in range(-2,5):
            p={'schema':'qulap.model.v1','source_tier':'synthetic','variables':[{'id':n,'domain':[-1,0,2],'unit':'integer'} for n in ['x','y']],
               'constraints':[{'id':'cap','expr':f'x + y <= {cap}'}],'metrics':[{'id':'sum','expr':'x + y','unit':'integer'}],'sources':[],'propositions':[]}
            expected=[a+b for a,b in itertools.product([-1,0,2],repeat=2) if a+b<=cap]
            r=solve(p);self.assertTrue(r['complete']);self.assertEqual(r['feasible_states'],len(expected))
            self.assertEqual((r['metrics']['sum']['min'],r['metrics']['sum']['max']),(min(expected),max(expected)))

    def test_embedded_quantum_uses_this_runtime(self):
        r=self.cli('qulap','quantum',str(ROOT/'examples/data/qulap-mechanism.model.json'),'--proposition','owner_funding_required')
        self.assertEqual(r.returncode,0,r.stderr)
        d=json.loads(r.stdout);self.assertEqual(d['state'],'SIMULATED');self.assertEqual(d['backend']['version'],surd.VERSION)
        self.assertEqual(d['marked_states'],4)


if __name__=='__main__':unittest.main()
