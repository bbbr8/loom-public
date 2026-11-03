"""Durability, idempotency and bounded job concurrency contracts."""
import json,sys,tempfile,threading,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jobs import Jobs,JobError

class JobContracts(unittest.TestCase):
    def test_same_identity_executes_once_and_rejects_changed_content(self):
        with tempfile.TemporaryDirectory() as td:
            calls=[]
            def execute(raw,long=False):calls.append(json.loads(raw));return 200,{'ok':True,'result':{'value':7}}
            jobs=Jobs(td,execute)
            try:
                first,_=jobs.submit({'operation':'search'},'logical-use-0001');second,created=jobs.submit({'operation':'search'},'logical-use-0001')
                self.assertEqual(first['id'],second['id']);self.assertFalse(created)
                with self.assertRaises(JobError) as error:jobs.submit({'operation':'graph_init'},'logical-use-0001')
                self.assertEqual(error.exception.status,409)
                deadline=time.monotonic()+5
                while jobs.get(first['id'])['state'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(jobs.get(first['id'])['result'],{'value':7});self.assertEqual(len(calls),1)
                with jobs.connection() as c:self.assertIsNone(c.execute('SELECT request FROM jobs').fetchone()[0])
            finally:jobs.close()

    def test_queued_recovery_and_interrupted_mutation_not_replayed(self):
        with tempfile.TemporaryDirectory() as td:
            jobs=Jobs(td,lambda *a,**k:None,workers=0)
            queued,_=jobs.submit({'operation':'search'},'queued-original-1');unknown,_=jobs.submit({'operation':'graph_ingest'},'unknown-original-1')
            with jobs.connection() as c:c.execute("UPDATE jobs SET state='running' WHERE id=?",(unknown['id'],));c.commit()
            jobs.close();seen=[]
            def execute(raw,long=False):seen.append(json.loads(raw)['operation']);return 200,{'ok':True,'result':{}}
            recovered=Jobs(td,execute)
            try:
                deadline=time.monotonic()+5
                while recovered.get(queued['id'])['state']!='succeeded' and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(recovered.get(unknown['id'])['state'],'interrupted');self.assertEqual(seen,['search'])
            finally:recovered.close()

    def test_same_graph_serialized_and_different_graph_can_run(self):
        with tempfile.TemporaryDirectory() as td:
            guard=threading.Lock();active={};max_same=0
            def execute(raw,long=False):
                nonlocal max_same
                graph=json.loads(raw)['graph']
                with guard:active[graph]=active.get(graph,0)+1;max_same=max(max_same,active[graph])
                time.sleep(.03)
                with guard:active[graph]-=1
                return 200,{'ok':True,'result':{}}
            jobs=Jobs(td,execute)
            try:
                ids=[jobs.submit({'graph':str(i%2)},'graph-test-'+str(i))[0]['id'] for i in range(8)]
                deadline=time.monotonic()+5
                while any(jobs.get(i)['state']!='succeeded' for i in ids) and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(all(jobs.get(i)['state']=='succeeded' for i in ids));self.assertEqual(max_same,1)
            finally:jobs.close()


class JobArchitecture(unittest.TestCase):
    def test_exclusive_owner_rejects_second_service_without_touching_live_job(self):
        with tempfile.TemporaryDirectory() as td:
            jobs=Jobs(td,lambda *a,**k:None,workers=0);other=None
            try:
                job,_=jobs.submit({'operation':'graph_ingest'},'live-owner-test-1')
                with jobs.connection() as c:c.execute("UPDATE jobs SET state='running' WHERE id=?",(job['id'],));c.commit()
                with self.assertRaises(JobError) as error:other=Jobs(td,lambda *a,**k:None,workers=0)
                self.assertEqual(error.exception.status,503);self.assertEqual(jobs.get(job['id'])['state'],'running')
            finally:
                if other:other.close()
                jobs.close()

    def test_busy_graph_does_not_block_an_unrelated_graph(self):
        with tempfile.TemporaryDirectory() as td:
            started=threading.Event();release=threading.Event();other_done=threading.Event();order=[]
            def execute(raw,long=False):
                value=json.loads(raw);order.append(value['name'])
                if value['name']=='first':started.set();release.wait(4)
                if value['name']=='unrelated':other_done.set()
                return 200,{'ok':True,'result':{}}
            jobs=Jobs(td,execute)
            try:
                jobs.submit({'graph':'A','name':'first'},'fair-first-0001');self.assertTrue(started.wait(2))
                jobs.submit({'graph':'A','name':'second'},'fair-second-001')
                jobs.submit({'graph':'B','name':'unrelated'},'fair-unrelated-1')
                self.assertTrue(other_done.wait(1),'unrelated graph starved behind the busy graph')
                self.assertEqual(order,['first','unrelated'])
            finally:release.set();jobs.close()

    def test_shutdown_retains_ownership_until_live_execution_finishes(self):
        with tempfile.TemporaryDirectory() as td:
            started=threading.Event();release=threading.Event()
            def execute(raw,long=False):started.set();release.wait(4);return 200,{'ok':True,'result':{'value':9}}
            jobs=Jobs(td,execute)
            try:
                job,_=jobs.submit({'graph':'A'},'shutdown-job-001');self.assertTrue(started.wait(2))
                self.assertFalse(jobs.close(timeout=.01))
                with self.assertRaises(JobError):Jobs(td,execute,workers=0)
                with self.assertRaises(JobError):jobs.submit({},'after-close-001')
            finally:release.set();jobs.close()
            replacement=Jobs(td,execute,workers=0)
            try:self.assertEqual(replacement.get(job['id'])['result'],{'value':9})
            finally:replacement.close()

    def test_many_graphs_leave_no_unbounded_lock_registry(self):
        with tempfile.TemporaryDirectory() as td:
            jobs=Jobs(td,lambda *a,**k:(200,{'ok':True,'result':{}}))
            try:
                ids=[jobs.submit({'graph':'graph-'+str(i)},'many-graphs-'+str(i))[0]['id'] for i in range(40)]
                deadline=time.monotonic()+4
                while any(jobs.get(i)['state']!='succeeded' for i in ids) and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(all(jobs.get(i)['state']=='succeeded' for i in ids));self.assertEqual(jobs.active_graphs,set())
            finally:jobs.close()

    def test_orphan_worker_retains_ownership_after_queue_process_dies(self):
        import subprocess,os
        with tempfile.TemporaryDirectory() as td:
            script="""import sys,time,json,subprocess
from jobs import Jobs
def execute(raw,long=False,owner_fd=None):
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.7)'],pass_fds=(owner_fd,))
 print('CHILD',flush=True);child.wait()
 return 200,{'ok':True,'result':{}}
j=Jobs(sys.argv[1],execute,pass_owner=True)
x=j.submit({'operation':'graph_ingest'},'orphan-owner-job-1')[0]
print(x['id'],flush=True)
time.sleep(30)
"""
            child=subprocess.Popen([sys.executable,'-u','-c',script,td],cwd=str(Path(__file__).resolve().parents[1]),stdout=subprocess.PIPE,text=True)
            try:
                lines=[child.stdout.readline().strip(),child.stdout.readline().strip()]
                ident=next(x for x in lines if len(x)==32);self.assertIn('CHILD',lines)
                child.kill();child.wait(timeout=3)
                with self.assertRaises(JobError):Jobs(td,lambda *a,**k:None,workers=0)
                deadline=time.monotonic()+3;recovered=None
                while time.monotonic()<deadline:
                    try:recovered=Jobs(td,lambda *a,**k:None,workers=0);break
                    except JobError:time.sleep(.03)
                self.assertIsNotNone(recovered)
                try:self.assertEqual(recovered.get(ident)['state'],'interrupted')
                finally:recovered.close()
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)
                child.stdout.close()

    def test_process_death_releases_owner_and_flags_unknown_mutation(self):
        import subprocess,os
        with tempfile.TemporaryDirectory() as td:
            script="""import sys,time,json
from jobs import Jobs
def execute(raw,long=False):
 print('STARTED',flush=True);time.sleep(20)
j=Jobs(sys.argv[1],execute)
x=j.submit({'operation':'graph_ingest'},'crash-owner-job-1')[0]
print(x['id'],flush=True)
time.sleep(30)
"""
            child=subprocess.Popen([sys.executable,'-u','-c',script,td],cwd=str(Path(__file__).resolve().parents[1]),stdout=subprocess.PIPE,text=True)
            try:
                lines=[child.stdout.readline().strip(),child.stdout.readline().strip()]
                ident=next(x for x in lines if len(x)==32);self.assertIn('STARTED',lines)
                child.kill();child.wait(timeout=3)
                seen=[];recovered=Jobs(td,lambda *a,**k:seen.append(1),workers=0)
                try:self.assertEqual(recovered.get(ident)['state'],'interrupted');self.assertEqual(seen,[])
                finally:recovered.close()
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)
                child.stdout.close()

if __name__=='__main__':unittest.main()
