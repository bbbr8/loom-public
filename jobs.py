"""Bounded durable Loom jobs. Single trusted service key; no automatic mutation replay."""
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

MAX_JOBS=512
MAX_PENDING=64
MAX_RESULT=4*1024**2
MAX_STORAGE=256*1024**2

class JobError(ValueError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status

class Jobs:
    def __init__(self,directory,execute,workers=2,pass_owner=False):
        root=Path(directory).resolve()/'.loom-jobs'
        if root.is_symlink():raise JobError('linked_job_store',422)
        self.path=root/'jobs.sqlite'
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.is_symlink():raise JobError('linked_job_store',422)
        # Ownership precedes recovery. A second server must never interrupt the
        # first server's live mutations. flock is released by process death.
        self.owner_guard=threading.Lock();self.owner=None
        lockpath=root/'owner.lock'
        if lockpath.is_symlink():raise JobError('linked_job_store',422)
        fd=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            os.close(fd);raise JobError('job_store_already_owned',503)
        self.owner=fd
        self.execute=execute;self.pass_owner=pass_owner;self.stopping=threading.Event();self.wake=threading.Event()
        self.claim_guard=threading.Lock();self.active_graphs=set();self.running_threads=workers
        try:
            with self.connection() as c:
                c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,idem TEXT UNIQUE,request_hash TEXT,request TEXT,state TEXT,created REAL,updated REAL,result TEXT,error TEXT)')
                c.execute('CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state,created,id)')
                c.execute('CREATE TABLE IF NOT EXISTS job_scopes(id TEXT PRIMARY KEY,graph TEXT,principal TEXT)')
                if 'principal' not in {r[1] for r in c.execute('PRAGMA table_info(job_scopes)')}:c.execute('ALTER TABLE job_scopes ADD COLUMN principal TEXT')
                c.execute('CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY,idem TEXT UNIQUE,request_hash TEXT,archive_sha256 TEXT,archived_at REAL)')
                for row in c.execute('SELECT id,request FROM jobs WHERE id NOT IN (SELECT id FROM job_scopes)').fetchall():
                    graph=json.loads(row['request']).get('graph') if row['request'] else '__legacy_unknown__'
                    c.execute('INSERT INTO job_scopes VALUES(?,?,?)',(row['id'],graph,None))
                # An interrupted mutation has unknown outcome. Never silently execute it twice.
                c.execute("UPDATE jobs SET state='interrupted',error='service_restarted_during_execution',request=NULL,updated=? WHERE state='running'",(time.time(),));c.commit()
        except Exception:
            self.release_owner();raise
        self.path.chmod(0o600)
        self.threads=[threading.Thread(target=self.loop,name='loom-job-'+str(i),daemon=True) for i in range(workers)]
        for t in self.threads:t.start()

    def connection(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        return closing(c)

    def submit(self,request,key,principal=None):
        if self.stopping.is_set():raise JobError('job_queue_stopping',503)
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}',key):raise JobError('idempotency_key_required',400)
        raw=json.dumps(request,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(raw.encode())>256*1024:raise JobError('body_limit_256_kib',413)
        hashed=hashlib.sha256(raw.encode()).hexdigest();now=time.time()
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM jobs WHERE idem=?',(key,)).fetchone()
            tombstone=c.execute('SELECT * FROM tombstones WHERE idem=?',(key,)).fetchone()
            if tombstone:
                if tombstone['request_hash']!=hashed:raise JobError('idempotency_key_content_conflict',409)
                raise JobError('job_archived_inspect_archive_no_replay',409)
            if old:
                if old['request_hash']!=hashed:raise JobError('idempotency_key_content_conflict',409)
                return self.public(old),False
            if c.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=MAX_PENDING:
                raise JobError('job_queue_full_retry_later',429)
            if c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]>=MAX_JOBS:
                raise JobError('job_history_capacity_archive_required',429)
            if c.execute('SELECT COALESCE(SUM(COALESCE(length(request),0)+COALESCE(length(result),0)),0) FROM jobs').fetchone()[0]+len(raw)>MAX_STORAGE:
                raise JobError('job_storage_capacity_archive_required',429)
            ident=uuid.uuid4().hex
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(ident,key,hashed,raw,'queued',now,now,None,None))
            c.execute('INSERT INTO job_scopes VALUES(?,?,?)',(ident,request.get('graph'),principal));c.commit()
            row=c.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
        self.wake.set();return self.public(row),True

    def public(self,row):
        out={'id':row['id'],'state':row['state'],'created_at':row['created'],'updated_at':row['updated'],
             'status_url':'/v1/jobs/'+row['id'],'request_sha256':row['request_hash']}
        if row['state']=='succeeded':out['result']=json.loads(row['result'])
        elif row['error']:out['error']=row['error']
        return out

    def get(self,ident):
        if not re.fullmatch(r'[0-9a-f]{32}',ident):raise JobError('job_not_found',404)
        with self.connection() as c:row=c.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row:raise JobError('job_not_found',404)
        return self.public(row)

    def status(self):
        with self.connection() as c:
            rows=list(c.execute('SELECT state,COUNT(*) AS n,MIN(created) AS oldest FROM jobs GROUP BY state'))
        counts={row['state']:row['n'] for row in rows}
        queued=next((row['oldest'] for row in rows if row['state']=='queued'),None)
        return {'accepting':not self.stopping.is_set(),'workers_alive':sum(t.is_alive() for t in self.threads),
                'states':counts,'pending_limit':MAX_PENDING,'history_limit':MAX_JOBS,
                'oldest_queued_seconds':max(0,time.time()-queued) if queued is not None else 0}

    def scope(self,ident):
        with self.connection() as c:row=c.execute('SELECT graph FROM job_scopes WHERE id=?',(ident,)).fetchone()
        return row[0] if row else '__legacy_unknown__'

    def principal(self,ident):
        with self.connection() as c:row=c.execute('SELECT principal FROM job_scopes WHERE id=?',(ident,)).fetchone()
        return row[0] if row else None

    def archive(self,destination,before):
        """Exclusive-owner operator action; publish archive before deleting payloads.

        Tombstones preserve idempotency. A crash before database commit leaves a
        recoverable extra archive, while retained jobs remain authoritative.
        """
        import math
        if type(before) not in (int,float) or not math.isfinite(before):raise JobError('finite_archive_cutoff_required')
        path=Path(destination).expanduser().absolute()
        if path.exists() or path.is_symlink():raise JobError('archive_destination_exists')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE state IN ('succeeded','failed','interrupted') AND updated<? ORDER BY created,id",(before,))]
            if c.execute('SELECT COUNT(*) FROM tombstones').fetchone()[0]+len(rows)>8192:raise JobError('tombstone_capacity_operator_migration_required')
            scopes=[dict(c.execute('SELECT * FROM job_scopes WHERE id=?',(r['id'],)).fetchone()) for r in rows]
            raw=json.dumps({'schema':'loom.jobs.archive.v1','jobs':rows,'scopes':scopes},sort_keys=True,separators=(',',':')).encode()
            sha=hashlib.sha256(raw).hexdigest()
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise JobError('archive_readback_failed')
            parent=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(parent)
            finally:os.close(parent)
            for row in rows:
                c.execute('INSERT INTO tombstones VALUES(?,?,?,?,?)',(row['id'],row['idem'],row['request_hash'],sha,time.time()))
                c.execute('DELETE FROM jobs WHERE id=?',(row['id'],));c.execute('DELETE FROM job_scopes WHERE id=?',(row['id'],))
            c.commit()
        return {'archived':len(rows),'archive':str(path),'sha256':sha,'idempotency_preserved':True}

    def claim(self):
        # Skip a busy graph while preserving FIFO within each graph. Claim and
        # graph reservation share a lock, so two workers never select it together.
        with self.claim_guard:
            if self.stopping.is_set():return None,None
            with self.connection() as c:
                c.execute('BEGIN IMMEDIATE');row=None;graph=None
                for candidate in c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT ?",(MAX_PENDING,)):
                    value=json.loads(candidate['request']).get('graph')
                    value=value if isinstance(value,str) else None
                    if value is not None and value in self.active_graphs:continue
                    row=candidate;graph=value;break
                if row:
                    c.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),row['id']))
                    if graph is not None:self.active_graphs.add(graph)
                c.commit()
            return row,graph

    def loop(self):
        try:
            self.run_loop()
        except Exception:
            # Persistence failure leaves execution uncertain. Stop admission and
            # let the next exclusive owner reconcile the retained running row.
            self.stopping.set();self.wake.set()
            raise
        finally:
            with self.owner_guard:
                self.running_threads-=1
                release=self.stopping.is_set() and self.running_threads==0
            if release:self.release_owner()

    def run_loop(self):
        while not self.stopping.is_set():
            row,graph=self.claim()
            if not row:
                self.wake.wait(1);self.wake.clear();continue
            request=json.loads(row['request'])
            try:
                owner={'owner_fd':self.owner} if self.pass_owner else {}
                code,value=self.execute(json.dumps(request).encode(),long=True,**owner)
                success=code==200 and value.get('ok') is True
                result=json.dumps(value['result'],allow_nan=False) if success else None
                if result is not None and len(result.encode())>MAX_RESULT:raise JobError('result_limit_use_pagination')
                state='succeeded' if success else 'failed';error=None if success else value.get('error','analysis_failed')
            except Exception:
                result=None;state='failed';error='job_execution_failed'
            with self.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                stored=c.execute('SELECT COALESCE(SUM(COALESCE(length(request),0)+COALESCE(length(result),0)),0) FROM jobs').fetchone()[0]
                if result is not None and stored+len(result.encode())>MAX_STORAGE:
                    result=None;state='failed';error='job_storage_capacity_archive_required'
                c.execute('UPDATE jobs SET state=?,updated=?,result=?,error=?,request=NULL WHERE id=?',(state,time.time(),result,error,row['id']));c.commit()
            with self.claim_guard:
                self.active_graphs.discard(graph)
            self.wake.set()

    def release_owner(self):
        with self.owner_guard:
            if self.owner is not None:
                fcntl.flock(self.owner,fcntl.LOCK_UN);os.close(self.owner);self.owner=None

    def close(self,timeout=2):
        with self.claim_guard:self.stopping.set()
        self.wake.set();deadline=time.monotonic()+timeout
        for t in self.threads:t.join(timeout=max(0,deadline-time.monotonic()))
        done=not any(t.is_alive() for t in self.threads)
        if done:self.release_owner()
        return done
