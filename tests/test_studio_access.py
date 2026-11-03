"""Cross-project access and archive replay controls; no real credentials."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from access import principal,permits
from jobs import Jobs,JobError


class Access(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.token='reader-'+'r'*40
        self.config=self.root/'access.json'
        self.data={'schema':'loom.access.v1','principals':[{'id':'reader','sha256':hashlib.sha256(self.token.encode()).hexdigest(),'role':'reader','graphs':['allowed']}]}
        self.config.write_text(json.dumps(self.data));self.env=patch.dict(os.environ,{'LOOM_ACCESS_FILE':str(self.config),'LOOM_DATA_DIR':str(self.root)})
        self.env.start()

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def test_reader_scope_and_mutations(self):
        who=principal('Bearer '+self.token,'')
        self.assertTrue(permits(who,{'operation':'graph_explain','graph':'allowed'}))
        self.assertFalse(permits(who,{'operation':'graph_explain','graph':'other'}))
        self.assertFalse(permits(who,{'operation':'graph_ingest','graph':'allowed'}))
        self.assertFalse(permits(who,{'operation':'graph_suggest_relations','graph':'allowed'}))

    def test_malformed_access_fails_closed(self):
        self.data['principals'][0]['graphs']='*';self.config.write_text(json.dumps(self.data))
        self.assertIsNone(principal('Bearer '+self.token,''))

    def test_duplicate_token_fails_closed(self):
        self.data['principals']*=2;self.config.write_text(json.dumps(self.data))
        self.assertIsNone(principal('Bearer '+self.token,''))

    def test_editor_cannot_cross_project(self):
        who={'role':'editor','graphs':['allowed']}
        self.assertTrue(permits(who,{'operation':'graph_review_relation','graph':'allowed'}))
        self.assertFalse(permits(who,{'operation':'graph_review_relation','graph':'other'}))

    def test_http_readonly_and_queue_scope_enforced(self):
        spec=importlib.util.spec_from_file_location('studio_test_server',ROOT/'server.py');server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
        server.API_KEY='owner-'+'x'*40
        http=server.Server(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def request(path,body=None,token=None,key=None):
            headers={'Authorization':'Bearer '+(token or self.token),'Content-Type':'application/json'}
            if key:headers['Idempotency-Key']=key
            raw=json.dumps(body).encode() if body is not None else None
            try:r=urlopen(Request('http://127.0.0.1:'+str(http.server_port)+path,data=raw,headers=headers),timeout=15)
            except HTTPError as e:r=e
            return r.status,json.load(r)
        try:
            self.assertEqual(request('/v1/analyze',{'operation':'graph_init','graph':'allowed'})[0],403)
            self.assertEqual(request('/v1/analyze',{'operation':'graph_snapshot','graph':'other'})[0],403)
            self.assertEqual(request('/v1/analyze',{'operation':'graph_init','graph':'allowed'},server.API_KEY)[0],200)
            code,data=request('/v1/analyze',{'operation':'graph_explain','graph':'allowed','query':'unknown'})
            self.assertEqual(code,200);self.assertEqual(data['result']['cards'],[])
            code,data=request('/v1/jobs',{'operation':'graph_snapshot','graph':'other'},server.API_KEY,'other-job-key')
            self.assertEqual(code,202)
            self.assertEqual(request(data['job']['status_url'])[0],403)
            self.assertEqual(request('/v1/jobs')[0],403)
        finally:http.shutdown();http.server_close();thread.join()

    def test_archive_retains_tombstones_and_never_replays(self):
        calls=[]
        def execute(raw,**kwargs):calls.append(raw);return 200,{'ok':True,'result':{'state':'DONE'}}
        queue=Jobs(self.root,execute,workers=1)
        request={'operation':'graph_ingest','graph':'allowed','packet':{}}
        try:
            job,_=queue.submit(request,'stable-job-key')
            for _ in range(100):
                if queue.get(job['id'])['state']=='succeeded':break
                time.sleep(.01)
            result=queue.archive(self.root/'archive.json',time.time()+1)
            self.assertEqual(result['archived'],1)
            self.assertEqual(hashlib.sha256((self.root/'archive.json').read_bytes()).hexdigest(),result['sha256'])
            with self.assertRaisesRegex(JobError,'archived'):queue.submit(request,'stable-job-key')
            with self.assertRaisesRegex(JobError,'conflict'):queue.submit({'graph':'different'},'stable-job-key')
            self.assertEqual(len(calls),1)
        finally:queue.close()
        queue=Jobs(self.root,execute,workers=1)
        try:
            with self.assertRaisesRegex(JobError,'archived'):queue.submit(request,'stable-job-key')
            self.assertEqual(len(calls),1)
        finally:queue.close()

    def test_archive_refuses_existing_destination(self):
        path=self.root/'archive.json';path.write_text('preserve')
        queue=Jobs(self.root,lambda raw,**k:(200,{'ok':True}),workers=1)
        try:
            with self.assertRaises(JobError):queue.archive(path,time.time())
            self.assertEqual(path.read_text(),'preserve')
        finally:queue.close()


if __name__=='__main__':unittest.main()
