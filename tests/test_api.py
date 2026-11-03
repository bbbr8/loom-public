import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
os.environ['LOOM_API_KEY'] = 'test-only-' + 'x' * 40
spec = importlib.util.spec_from_file_location('api_server', ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'LOOM_DATA_DIR': cls.data.name})
        cls.env.start()
        cls.http = server.Server(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.env.stop()
        cls.data.cleanup()

    def setUp(self):
        with server.quota_lock:
            server.recent.clear()
            server.jobs_recent.clear()

    def call(self, body=None, auth=True, path='/v1/analyze', raw=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + server.API_KEY
        data = raw if raw is not None else json.dumps(body).encode()
        try:
            response = urlopen(Request(self.base + path, data=data, headers=headers), timeout=10)
        except HTTPError as e:
            response = e
        return response.status, json.load(response), response.headers

    def test_conditional_ingest_returns_conflict_and_preserves_graph(self):
        graph='conditional-ingest'
        packet={'schema':'loom.graph.batch.v1','batch_id':'first','nodes':[{'id':'A','type':'passage','text':'first'}]}
        request={'operation':'graph_ingest','graph':graph,'packet':packet,'options':{'evolution':False,'expected_revision':0}}
        self.assertEqual(self.call(request)[0],200)
        request['packet']={'schema':'loom.graph.batch.v1','batch_id':'stale','nodes':[{'id':'A','type':'passage','text':'changed','revision':2}]}
        code,value,_=self.call(request)
        self.assertEqual(code,409);self.assertEqual(value['error'],'graph_revision_conflict')
        code,snapshot,_=self.call({'operation':'graph_snapshot','graph':graph})
        self.assertEqual(snapshot['result']['revision'],1);self.assertEqual(snapshot['result']['nodes'][0]['text'],'first')
        request['options']['expected_revision']=1;self.assertEqual(self.call(request)[0],200)
        # Logical retry of the already committed batch returns its saved identity.
        self.assertEqual(self.call(request)[0],200)

    def test_health(self):
        with urlopen(self.base + '/health') as response:
            self.assertTrue(json.load(response)['ok'])

    def test_queue_status_is_authenticated_and_bounded(self):
        for auth,expected in [(False,401),(True,200)]:
            headers={'Authorization':'Bearer '+server.API_KEY} if auth else {}
            try:response=urlopen(Request(self.base+'/v1/jobs',headers=headers),timeout=3)
            except HTTPError as error:response=error
            with response:
                value=json.load(response);self.assertEqual(response.status,expected)
            if auth:
                self.assertEqual(value['queue']['workers_alive'],2);self.assertTrue(value['queue']['accepting'])
                self.assertNotIn('request',json.dumps(value));self.assertNotIn('result',json.dumps(value))

    def test_synchronous_busy_graph_returns_promptly(self):
        with server.graph_guard('busy-graph'):
            started=time.monotonic()
            code,value,_=self.call({'operation':'graph_init','graph':'busy-graph'})
            self.assertEqual(code,429);self.assertEqual(value['error'],'graph_busy_retry_later')
            self.assertLess(time.monotonic()-started,1)
        self.assertNotIn('busy-graph',server.graph_locks)

    def test_auth(self):
        self.assertEqual(self.call({}, auth=False)[0], 401)

    def test_search_and_no_store(self):
        code, value, headers = self.call({'documents':[{'id':'d','text':'Credit applied','locator':'synthetic:d'}], 'question':'credit'})
        self.assertEqual(code, 200)
        self.assertEqual(value['result']['hits'][0]['id'], 'd')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_body_limit(self):
        self.assertEqual(self.call(raw=b'x' * (server.MAX_BODY + 1))[0], 413)

    def test_oversize_response_survives_repeated_and_transfer_payloads(self):
        for size in [server.MAX_BODY+1]*15+[server.MAX_BODY+128]*5:
            self.assertEqual(self.call(raw=b'x'*size)[0],413)

    def test_oversize_declared_body_without_data_has_bounded_wait(self):
        import socket
        started=time.monotonic()
        with socket.create_connection(('127.0.0.1',self.http.server_port),timeout=3) as sock:
            sock.sendall(('POST /v1/analyze HTTP/1.0\r\nAuthorization: Bearer '+server.API_KEY+'\r\nContent-Type: application/json\r\nContent-Length: '+str(server.MAX_BODY*100)+'\r\n\r\n').encode())
            response=sock.recv(4096)
        self.assertIn(b'413',response);self.assertLess(time.monotonic()-started,2)

    def test_invalid_json(self):
        self.assertEqual(self.call(raw=b'{')[0], 400)

    def test_no_file_specs_or_programs(self):
        for body in [
            {'documents':[], 'question':'q', 'operation':'quantum', 'spec':'/etc/passwd'},
            {'documents':[], 'question':'q', 'operation':'exec', 'program':'secret'},
        ]:
            code, value, _ = self.call(body)
            self.assertEqual(code, 422)
            self.assertNotIn('/etc/passwd', json.dumps(value))
            self.assertNotIn('secret', json.dumps(value))

    def test_busy(self):
        with server.job_lock:
            self.assertEqual(self.call({})[0], 429)

    def test_isolated_requests(self):
        self.call({'documents':[{'id':'private','text':'unique_private_marker'}], 'question':'unique_private_marker'})
        code, value, _ = self.call({'documents':[], 'question':'unique_private_marker'})
        self.assertEqual(code, 200)
        self.assertEqual(value['result']['hits'], [])

    def test_complete_runtime_hashes(self):
        import hashlib
        manifest = json.loads((ROOT / 'SOURCE_MANIFEST.json').read_text())
        for name, expected in manifest['files'].items():
            self.assertEqual(hashlib.sha256((ROOT / 'runtime' / name).read_bytes()).hexdigest(), expected)

    def test_browser_assets_and_policy(self):
        for path, content_type in [('/', 'text/html'), ('/app.js', 'text/javascript'), ('/app.css', 'text/css'), ('/examples.json', 'application/json')]:
            with urlopen(self.base + path) as response:
                self.assertIn(content_type, response.headers['Content-Type'])
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                self.assertNotIn(server.API_KEY.encode(), response.read())

    def test_no_static_path_traversal(self):
        for path in ['/server.py', '/runtime/surd.py', '/.env', '/../server.py']:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + path)
            self.assertEqual(error.exception.code, 404)

    def example(self, operation):
        rows = json.loads((ROOT / 'web/examples.json').read_text())
        return next(x['request'] for x in rows if x['request']['operation'] == operation)

    def test_research_persists_and_reuses_complete_result(self):
        packet = self.example('research_run');packet['graph'] = 'retained-research'
        code, value, _ = self.call(packet)
        self.assertEqual(code, 200)
        self.assertEqual(value['result']['state'], 'COMPLETE')
        self.assertEqual(value['result']['planning']['bundles'][0]['guaranteed_gain'], 60000)
        code, cached, _ = self.call(packet)
        self.assertEqual(code, 200, cached)
        self.assertTrue(cached['result']['cached'])
        code, graph, _ = self.call({'operation':'graph_snapshot','graph':packet['graph']})
        self.assertTrue(graph['result']['nodes'])
        code, checked, _ = self.call({'operation':'graph_verify','graph':packet['graph']})
        self.assertTrue(checked['result']['ok'])

    def test_inline_exact_model_and_complementary_records(self):
        for op in ['research_plan', 'qulap_solve', 'qulap_core', 'qulap_sensitivity']:
            request = self.example('qulap_solve');request['operation'] = op
            if op == 'research_plan': request['options'] = {'max_bundle':2}
            code, value, _ = self.call(request)
            self.assertEqual(code, 200, (op, value))
            if op == 'research_plan':
                self.assertEqual(value['result']['bundles'][0]['synergy'], 60000)

    def test_graph_ids_cannot_read_host_files(self):
        for name in ['../secret','/etc/passwd','demo.sqlite','a/b','a\\b','']:
            code, value, _ = self.call({'operation':'graph_snapshot','graph':name})
            self.assertEqual(code, 422)
            self.assertNotIn('passwd', json.dumps(value))

    def test_packets_cannot_reference_files(self):
        for op in ['graph_ingest','research_run','research_learn','research_plan','qulap_solve']:
            code, value, _ = self.call({'operation':op,'graph':'path-test','packet':'/etc/passwd'})
            self.assertEqual(code, 422)
        code, _, _ = self.call({'operation':'research_run','graph':'path-test','packet':{'schema':'loom.research.run.v1','id':'x','model':'/etc/passwd'}})
        self.assertEqual(code, 422)

    def test_graph_storage_requires_operator_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.call({'operation':'graph_init','graph':'test'})[0], 422)

    def test_duplicate_json_keys_rejected(self):
        self.assertEqual(self.call(raw=b'{"operation":"search","operation":"graph_init"}')[0], 422)

    def test_graph_namespaces_and_source_withdrawal(self):
        for graph in ['namespace-a','namespace-b']:
            code, _, _ = self.call({'operation':'graph_init','graph':graph})
            self.assertEqual(code, 200)
            self.assertEqual(self.call({'operation':'graph_init','graph':graph})[0], 200)
        packet={'schema':'loom.graph.batch.v1','batch_id':'one','nodes':[{'id':'S1','type':'source','origin':'SYN','locator':'synthetic:1'},{'id':'P1','type':'passage','text':'synthetic blue credit','source_ids':['S1']}]}
        self.assertEqual(self.call({'operation':'graph_ingest','graph':'namespace-a','packet':packet})[0], 200)
        code, found, _ = self.call({'operation':'graph_search','graph':'namespace-a','query':'blue credit'})
        self.assertTrue(found['result']['hits'])
        code, empty, _ = self.call({'operation':'graph_snapshot','graph':'namespace-b'})
        self.assertEqual(empty['result']['nodes'], [])
        self.assertEqual(self.call({'operation':'graph_withdraw','graph':'namespace-a','origin':'SYN','batch_id':'withdraw'})[0], 200)
        code, empty, _ = self.call({'operation':'graph_snapshot','graph':'namespace-a'})
        self.assertFalse(any(n['id']=='P1' for n in empty['result']['nodes']))

    def test_symlink_graph_rejected(self):
        target = Path(self.data.name) / 'unrelated.txt';target.write_text('private')
        link = Path(self.data.name) / 'linked.sqlite';link.symlink_to(target)
        try:
            self.assertEqual(self.call({'operation':'graph_init','graph':'linked'})[0], 422)
            self.assertEqual(target.read_text(), 'private')
        finally:link.unlink()

    def test_rate_limit(self):
        with server.quota_lock:server.recent[:] = [server.time.monotonic()] * 30
        self.assertEqual(self.call({})[0], 429)

    def test_worker_gets_no_service_key(self):
        completed = subprocess.CompletedProcess([], 0, b'{"ok":true,"result":{}}')
        with patch.object(server.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(self.call({'documents':[], 'question':'q'})[0], 200)
            env = run.call_args.kwargs['env']
            self.assertNotIn('LOOM_API_KEY', env)
            self.assertNotIn('SURD_API_KEY', env)
            self.assertEqual(env['SURD_USAGE'], '0')

    def job_call(self,packet,key='api-logical-request-0001'):
        req=Request(self.base+'/v1/jobs',data=json.dumps(packet).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+server.API_KEY,'Idempotency-Key':key})
        try:response=urlopen(req,timeout=10)
        except HTTPError as e:response=e
        return response.status,json.load(response)

    def job_status(self,job,auth=True):
        req=Request(self.base+job['status_url'],headers={'Authorization':'Bearer '+server.API_KEY} if auth else {})
        try:response=urlopen(req,timeout=10)
        except HTTPError as e:response=e
        return response.status,json.load(response)

    def test_job_api_retains_identity_and_authenticates_results(self):
        packet={'documents':[{'id':'job-source','text':'retained exact source'}],'question':'retained'}
        code,value=self.job_call(packet);self.assertEqual(code,202);job=value['job']
        again,duplicate=self.job_call(packet);self.assertIn(again,(200,202));self.assertEqual(job['id'],duplicate['job']['id'])
        self.assertEqual(self.job_status(job,False)[0],401)
        self.assertEqual(self.job_call({'documents':[],'question':'different'})[0],409)
        deadline=time.monotonic()+10
        while job['state'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.02);job=self.job_status(job)[1]['job']
        self.assertEqual(job['state'],'succeeded',job);self.assertEqual(job['result']['hits'][0]['id'],'job-source')

    def test_twelve_client_burst_has_structured_admission(self):
        packet={'documents':[{'id':'d'+str(i),'text':'common '*100} for i in range(100)],'question':'common'}
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:self.call(packet)[0],range(45)))
        self.assertTrue(all(code in (200,429,503) for code in results),results)
        self.assertIn(200,results)

    def test_paginated_graph_and_cursor_revision(self):
        sys.path.insert(0,str(ROOT/'runtime'))
        from loom_research.store import GraphStore
        s=GraphStore(Path(self.data.name)/'paged.sqlite',create=True)
        s.apply({'schema':'loom.graph.batch.v1','batch_id':'page-fixture','nodes':[{'id':str(i),'type':'entity'} for i in range(25)]})
        code,value,_=self.call({'operation':'graph_snapshot','graph':'paged','options':{'limit':10}})
        self.assertEqual(code,200);page=value['result'];self.assertEqual(page['pagination']['total_nodes'],25);self.assertEqual(len(page['nodes']),10)
        cursor=page['pagination']['next_cursor'];code,second,_=self.call({'operation':'graph_snapshot','graph':'paged','options':{'limit':10,'cursor':cursor}})
        self.assertEqual(code,200);self.assertFalse(set(n['id'] for n in page['nodes'])&set(n['id'] for n in second['result']['nodes']))
        s.apply({'schema':'loom.graph.batch.v1','batch_id':'page-change','nodes':[{'id':'new','type':'entity'}]})
        self.assertEqual(self.call({'operation':'graph_snapshot','graph':'paged','options':{'limit':10,'cursor':cursor}})[0],422)

if __name__ == '__main__':
    unittest.main()
