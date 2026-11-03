"""Loom browser workbench and authenticated API. Standard library only."""
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from jobs import Jobs, JobError
from access import principal, permits
MAX_BODY = 256 * 1024
API_KEY = os.environ.get('LOOM_API_KEY', os.environ.get('SURD_API_KEY', ''))
job_lock = threading.Lock()
quota_lock = threading.Lock()
recent = []
jobs_recent = []
graph_locks = {}
graph_locks_guard = threading.Lock()

@contextmanager
def graph_guard(graph,timeout=None):
    if not isinstance(graph,str):
        yield True
        return
    with graph_locks_guard:
        entry=graph_locks.setdefault(graph,{'lock':threading.Lock(),'users':0});entry['users']+=1
    acquired=False
    try:
        acquired=entry['lock'].acquire() if timeout is None else entry['lock'].acquire(timeout=timeout)
        yield acquired
    finally:
        if acquired:entry['lock'].release()
        with graph_locks_guard:
            entry['users']-=1
            if not entry['users']:graph_locks.pop(graph,None)

def execute(raw,long=False,owner_fd=None):
    """One isolated worker; graph operations share a lock across both API routes."""
    try:data=json.loads(raw)
    except (ValueError,RecursionError):return 400,{'error':'invalid_json'}
    graph=data.get('graph') if isinstance(data,dict) else None
    with graph_guard(graph,timeout=15 if long else 0) as admitted:
        if graph is not None and not admitted:return 429,{'error':'graph_busy_retry_later','hint':'Use the queued route or retry this read later.'}
        env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8',
             'SURD_USAGE':'0','LOOM_RESEARCH_DB':'','SURD_PORTABLE_TEST':'1','LOOM_INDEX_CACHE':'1'}
        if os.environ.get('LOOM_DATA_DIR'):env['LOOM_DATA_DIR']=os.environ['LOOM_DATA_DIR']
        if long:env['LOOM_WORKER_PROFILE']='job'
        try:
            process=subprocess.run([sys.executable,'-B',str(ROOT/'worker.py')],input=raw,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=125 if long else 12,env=env,cwd=str(ROOT),
                pass_fds=(owner_fd,) if owner_fd is not None else ())
            if process.returncode:
                if process.returncode==3:return 409,{'error':'graph_revision_conflict','hint':'Read the current graph and recompute the mutation.'}
                if process.returncode<0:return 408,{'error':'analysis_resource_limit','hint':'Use a bounded job for expensive operations.'}
                try:detail=json.loads(process.stdout).get('error')
                except ValueError:detail=None
                return 422,{'error':detail or 'invalid_or_resource_limited_analysis'}
            return 200,json.loads(process.stdout)
        except subprocess.TimeoutExpired:return 408,{'error':'analysis_timeout'}
        except (OSError,ValueError,RecursionError):return 400,{'error':'request_failed'}
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/examples.json': ('examples.json', 'application/json')}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(64)
        self.jobs=None;self.jobs_guard=threading.Lock()
        super().__init__(*args,**kwargs)

    def job_queue(self):
        with self.jobs_guard:
            if self.jobs is None:
                directory=os.environ.get('LOOM_JOB_DIR') or os.environ.get('LOOM_DATA_DIR')
                if not directory:raise JobError('job_storage_not_configured',503)
                self.jobs=Jobs(directory,execute,pass_owner=True)
            return self.jobs

    def server_close(self):
        super().server_close()
        if self.jobs:self.jobs.close(timeout=145)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            # Graceful bounded overload, even before allocating a request thread.
            try:
                request.settimeout(.05)
                body=b'{"error":"connection_capacity_retry_later"}'
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nRetry-After: 1\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                request.shutdown(socket.SHUT_WR)
                drained=0
                while drained<MAX_BODY+8192:
                    part=request.recv(min(65536,MAX_BODY+8192-drained))
                    if not part:break
                    drained+=len(part)
            except OSError:pass
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # No request content or traceback logging.

class Handler(BaseHTTPRequestHandler):
    server_version = 'LoomAPI'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, status, value, headers=None):
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if status in (202,429,503):self.send_header('Retry-After','1')
        for name,content in (headers or {}).items():self.send_header(name,content)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path=='/v1/jobs':
            if not self.authorized():return self.reply(401,{'error':'unauthorized'})
            if self.identity()['role']!='admin':return self.reply(403,{'error':'operator_diagnostics_required'})
            try:status=self.server.job_queue().status()
            except JobError as e:return self.reply(e.status,{'error':str(e)})
            return self.reply(200,{'ok':True,'queue':status})
        if self.path.startswith('/v1/jobs/'):
            if not self.authorized():return self.reply(401,{'error':'unauthorized'})
            try:
                queue=self.server.job_queue();ident=self.path[len('/v1/jobs/'):]
                scope=queue.scope(ident);who=self.identity()
                if scope is None and who['role']!='admin' and queue.principal(ident)!=who['id']:return self.reply(403,{'error':'job_owner_access_denied'})
                if scope=='__legacy_unknown__' and who['role']!='admin':return self.reply(403,{'error':'project_access_denied'})
                if not permits(who,{'operation':'graph_snapshot','graph':scope}):return self.reply(403,{'error':'project_access_denied'})
                job=queue.get(ident)
            except JobError as e:return self.reply(e.status,{'error':str(e)})
            return self.reply(200,{'ok':True,'job':job})
        if self.path == '/health':
            self.reply(200, {'ok': True, 'service': 'loom', 'api_version': 3,
                             'runtime_version': '1.19.1',
                             'jobs_configured': bool(os.environ.get('LOOM_JOB_DIR') or os.environ.get('LOOM_DATA_DIR')),
                             'job_workers':2,'job_queue_limit':64,
                             'graph_storage_configured': bool(os.environ.get('LOOM_DATA_DIR')),
                             'storage_mode': os.environ.get('LOOM_STORAGE_MODE', 'operator-managed')})
        elif self.path in STATIC:
            name, content_type = STATIC[self.path]
            body = (ROOT / 'web' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.reply(404, {'error': 'not_found'})

    def do_POST(self):
        if self.path not in ('/v1/analyze','/v1/jobs'):
            return self.reply(404, {'error': 'not_found'})
        if not API_KEY and not os.environ.get('LOOM_ACCESS_FILE'):
            return self.reply(503, {'error': 'service_not_configured'})
        if not self.authorized():
            return self.reply(401, {'error': 'unauthorized'})
        if self.headers.get('Transfer-Encoding'):
            return self.reply(400, {'error': 'transfer_encoding_not_supported'})
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths) != 1:
            return self.reply(411, {'error': 'content_length_required'})
        try:
            length = int(lengths[0])
        except ValueError:
            return self.reply(400, {'error': 'invalid_content_length'})
        if not 0 < length <= MAX_BODY:
            # Consume a bounded oversize prefix before replying. Otherwise a
            # client still sending its last chunk can see a broken pipe instead
            # of the 413 response. Never drain an unbounded declared body.
            if length>0:
                self.connection.settimeout(.25)
                try:self.rfile.read(min(length,MAX_BODY+65536))
                except (TimeoutError,OSError):pass
            return self.reply(413, {'error': 'body_limit_256_kib'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.reply(415, {'error': 'json_required'})
        try:raw=self.rfile.read(length)
        except (TimeoutError,OSError):return self.reply(400,{'error':'incomplete_body'})
        if len(raw)!=length:return self.reply(400,{'error':'incomplete_body'})
        try:
            from worker import decode
            request_packet=decode(raw.decode('utf-8'))
            if not isinstance(request_packet,dict):raise ValueError('object required')
        except (ValueError,UnicodeError,RecursionError) as exc:
            if str(exc)=='duplicate key':return self.reply(422,{'error':'invalid_analysis_request'})
            return self.reply(400,{'error':'invalid_json'})
        if not permits(self.identity(),request_packet):return self.reply(403,{'error':'project_or_role_access_denied'})
        with quota_lock:
            now = time.monotonic()
            bucket=jobs_recent if self.path=='/v1/jobs' else recent
            bucket[:] = [t for t in bucket if now - t < 60]
            if len(bucket) >= (120 if self.path=='/v1/jobs' else 30):
                return self.reply(429, {'error': 'pilot_rate_limit'})
            bucket.append(now)
        if self.path=='/v1/jobs':
            try:
                from worker import decode
                packet=decode(raw.decode('utf-8'))
                if not isinstance(packet,dict):raise ValueError('object required')
                job,created=self.server.job_queue().submit(packet,self.headers.get('Idempotency-Key'),principal=self.identity()['id'])
            except JobError as e:return self.reply(e.status,{'error':str(e)})
            except (ValueError,UnicodeError,RecursionError):return self.reply(400,{'error':'invalid_json'})
            return self.reply(202 if created or job['state'] in ('queued','running') else 200,{'ok':True,'job':job})
        if not job_lock.acquire(blocking=False):
            return self.reply(429, {'error': 'analysis_busy_retry_later'})
        try:
            try:
                json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError):
                return self.reply(400, {'error': 'invalid_json'})
            code,value=execute(raw);self.reply(code,value)
        except subprocess.TimeoutExpired:
            self.reply(408, {'error': 'analysis_timeout'})
        except (TimeoutError, OSError, ValueError, RecursionError):
            self.reply(400, {'error': 'request_failed'})
        finally:
            job_lock.release()

    def authorized(self):
        return self.identity() is not None

    def identity(self):
        return principal(self.headers.get('Authorization',''),API_KEY)

if __name__ == '__main__':
    if len(API_KEY) < 32 and not os.environ.get('LOOM_ACCESS_FILE'):
        raise SystemExit('LOOM_API_KEY must contain at least 32 characters')
    import signal
    service=Server((os.environ.get('LOOM_HOST','127.0.0.1'),int(os.environ.get('PORT','10000'))),Handler)
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:service.serve_forever()
    except KeyboardInterrupt:pass
    finally:service.server_close()
