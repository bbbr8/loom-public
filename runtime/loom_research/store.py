"""Transactional event-sourced graph, dependency invalidation and historical views."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from .contracts import (MAX_NODES, MAX_EDGES, PREDICATES, ResearchError, canonical, digest,
                        fields, integer, normalize_batch, require, result, timestamp, boundary, utc, ResearchConflict)

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commits(seq INTEGER PRIMARY KEY,batch_id TEXT UNIQUE NOT NULL,
 input_hash TEXT NOT NULL,recorded_at TEXT NOT NULL,previous_hash TEXT NOT NULL,
 commit_hash TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
 active INTEGER NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,seq INTEGER NOT NULL,
 PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS versions(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
 active INTEGER NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,seq INTEGER NOT NULL,
 PRIMARY KEY(kind,id,revision));
CREATE INDEX IF NOT EXISTS history_by_seq ON versions(seq,kind,id);
CREATE TABLE IF NOT EXISTS dependencies(kind TEXT,id TEXT,dep_kind TEXT,dep_id TEXT,
 dep_revision INTEGER,dep_hash TEXT,PRIMARY KEY(kind,id,dep_kind,dep_id));
CREATE INDEX IF NOT EXISTS reverse_dependencies ON dependencies(dep_kind,dep_id);
CREATE TABLE IF NOT EXISTS adjacency(id TEXT PRIMARY KEY,subject TEXT,predicate TEXT,object TEXT);
CREATE INDEX IF NOT EXISTS outgoing ON adjacency(subject,predicate);
CREATE INDEX IF NOT EXISTS incoming ON adjacency(object,predicate);
CREATE VIRTUAL TABLE IF NOT EXISTS node_text USING fts5(id UNINDEXED,label,text);
"""


class GraphStore:
    def __init__(self, path, create=False):
        self.path = Path(path).expanduser().resolve()
        require(str(self.path) != ":memory:","use an explicit persistent path")
        if create:
            require(not self.path.exists(),"graph already exists; open it without init")
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.path,timeout=5)) as c:
                c.executescript(DDL)
                c.execute("INSERT INTO meta VALUES('schema','loom.graph.store.v1')")
                c.commit()
        require(self.path.is_file(),"graph store unavailable; run loom graph init with an explicit path")
        with self.connection() as c:
            row=c.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            require(row is not None and row[0]=='loom.graph.store.v1',"unrecognized graph store")

    def connection(self):
        # Every connection closes; readers never create a missing store.
        c=sqlite3.connect(self.path.as_uri()+"?mode=rw",uri=True,timeout=5)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return closing(c)

    def search(self,query,**options):
        from . import retrieval
        import os
        require(not any(k.startswith('_') for k in options),'private search options are unavailable')
        cache=self.path.parent/'.loom-index-cache'
        if cache.is_symlink() or os.environ.get('SURD_PORTABLE_TEST')=='1' and os.environ.get('LOOM_INDEX_CACHE')!='1':cache_path=None
        else:cache_path=cache/(self.path.name+'.json.gz')
        return retrieval.search(self.read_projection(),query,_cache_path=cache_path,**options)

    def head(self):
        with self.connection() as c:
            row=c.execute('SELECT seq,commit_hash FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
        return (row['seq'],row['commit_hash']) if row else (0,'0'*64)

    def read_projection(self,**options):
        """Disposable read model. Verification and write planning use raw snapshots."""
        import os
        from .projection_cache import load,save
        if os.environ.get('SURD_PORTABLE_TEST')=='1' and os.environ.get('LOOM_INDEX_CACHE')!='1':
            return self.snapshot(**options)
        head=self.head();key=digest([head,options,'projection-v1'])
        path=self.path.parent/'.loom-index-cache'/(self.path.name+'.projection.gz')
        cached=load(path,key)
        if cached is not None:return cached
        snap=self.snapshot(**options)
        if self.head()==head:save(path,key,snap)
        return snap

    def page(self,limit=200,cursor=None,**options):
        """Revision-bound byte-bounded projection pages with a disposable disk cache."""
        import base64
        integer(limit,1,1000,'page limit');scope=digest([options,limit]);offset=0;requested_hash=None
        if cursor is not None:
            require(isinstance(cursor,str) and len(cursor)<=1000,'invalid page cursor')
            try:raw=json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            except (ValueError,UnicodeError):raise ResearchError('invalid page cursor')
            require(isinstance(raw,dict) and set(raw)=={'graph_hash','scope','offset'},'invalid page cursor')
            require(raw['scope']==scope,'page cursor belongs to another view or page size')
            offset=integer(raw.get('offset'),0,120000,'page offset');requested_hash=raw['graph_hash']
        head=self.head()
        key=digest([head,options,limit,'pages-v2']);cache=self.path.parent/'.loom-index-cache'
        path=cache/(self.path.name+'.pages.sqlite');connection=None;header=None;parts=None
        if not cache.is_symlink() and not path.is_symlink():
            try:
                cache.mkdir(mode=0o700,exist_ok=True);connection=sqlite3.connect(path,timeout=10)
                connection.execute('CREATE TABLE IF NOT EXISTS header(key TEXT PRIMARY KEY,payload TEXT)')
                connection.execute('CREATE TABLE IF NOT EXISTS pages(offset INTEGER PRIMARY KEY,finish INTEGER,payload TEXT)')
                connection.execute('BEGIN IMMEDIATE')
                found=connection.execute('SELECT payload FROM header WHERE key=?',(key,)).fetchone()
                if found:header=json.loads(found[0])
            except (OSError,ValueError,sqlite3.Error):
                if connection:connection.close()
                connection=None;header=None
        try:
            if header is None:
                snap=self.read_projection(**options);header={k:v for k,v in snap.items() if k not in ('nodes','edges')}
                # Oversized diagnostics are available in the native snapshot; pages
                # retain counts and a bounded preview to keep every response bounded.
                for name in ('omitted','unknown_clock_ids'):
                    if len(header.get(name,[]))>1000:
                        header[name+'_count']=len(header[name]);header[name]=header[name][:1000]
                header['total_nodes']=len(snap['nodes']);header['total_edges']=len(snap['edges'])
                values=[('node',n) for n in snap['nodes']]+[('edge',e) for e in snap['edges']]
                parts=[];at=0
                while at<len(values) or not parts:
                    chosen=[];used=0
                    for kind,item in values[at:at+limit]:
                        size=len(canonical(item).encode())
                        if chosen and used+size>2*1024**2:break
                        require(size<=3*1024**2,'item exceeds page byte budget')
                        chosen.append((kind,item));used+=size
                    finish=at+len(chosen)
                    payload={'nodes':[v for k,v in chosen if k=='node'],'edges':[v for k,v in chosen if k=='edge']}
                    parts.append((at,finish,canonical(payload)));at=finish
                    if at>=len(values):break
                # Snapshot revision controls the cache even if a writer won the race.
                key=key if self.head()==head else None
                if connection:
                    if key is None:
                        connection.rollback();connection.close();connection=None
                    else:
                        connection.execute('DELETE FROM header');connection.execute('DELETE FROM pages')
                        connection.executemany('INSERT INTO pages VALUES(?,?,?)',parts)
                        connection.execute('INSERT INTO header VALUES(?,?)',(key,canonical(header)))
            require(requested_hash is None or requested_hash==header['graph_hash'],'page cursor is stale; restart pagination')
            if connection:row=connection.execute('SELECT finish,payload FROM pages WHERE offset=?',(offset,)).fetchone()
            else:row=next(((end,payload) for begin,end,payload in parts if begin==offset),None)
            require(row is not None,'invalid page offset')
            end,payload=row;total=header['total_nodes']+header['total_edges']
            next_cursor=base64.urlsafe_b64encode(canonical({'graph_hash':header['graph_hash'],'scope':scope,'offset':end}).encode()).decode() if end<total else None
            out=dict(header,**json.loads(payload));out['pagination']={'total_nodes':header['total_nodes'],'total_edges':header['total_edges'],
                'returned':end-offset,'offset':offset,'next_cursor':next_cursor,'complete':next_cursor is None}
            return out
        finally:
            if connection:connection.commit();connection.close()

    @staticmethod
    def _rows(c,seq=None):
        require(c.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM items').fetchone()[0]<=64*1024*1024,
                'graph projection exceeds the 64 MiB read budget')
        if seq is None:
            rows=c.execute("SELECT * FROM items").fetchall()
        else:
            rows=c.execute("SELECT v.* FROM versions v JOIN (SELECT kind,id,MAX(seq) AS m FROM versions WHERE seq<=? GROUP BY kind,id) x ON v.kind=x.kind AND v.id=x.id AND v.seq=x.m",(seq,)).fetchall()
        return {(r['kind'],r['id']):dict(r) for r in rows}

    @staticmethod
    def _projection(rows):
        return digest([[k[0],k[1],r['revision'],r['active'],r['content_hash']] for k,r in sorted(rows.items())
                       if json.loads(r['payload']).get('space','evidence')!='research'])

    @staticmethod
    def _custom_predicates(rows):
        return {n['data']['name']:n['data']['shape'] for k,r in rows.items() if k[0]=='node' and r['active']
                for n in [json.loads(r['payload'])] if n['type']=='predicate' and n['status']=='active'
                and isinstance(n.get('data',{}).get('name'),str) and isinstance(n['data'].get('shape'),dict)}

    @staticmethod
    def validate_edge(edge,nodes,custom=None):
        for key in ('subject','object'):
            require(edge[key] in nodes,"edge endpoint unavailable: "+edge[key])
        pred=edge['predicate'];custom=custom or {}
        require(pred in PREDICATES or pred in custom,"unknown predicate; propose and validate it first: "+pred)
        if pred in custom:
            shape=custom[pred]
            require(nodes[edge['subject']]['type'] in shape['subjects'],"predicate subject type mismatch")
            require(nodes[edge['object']]['type'] in shape['objects'],"predicate object type mismatch")
            require(not shape.get('require_sources',True) or bool(edge['source_ids']),"predicate requires source provenance")
        if pred=='same_entity':
            require(nodes[edge['subject']]['type'] in ('entity','instrument') and
                    nodes[edge['subject']]['type']==nodes[edge['object']]['type'],"same_entity requires compatible entity/instrument types")
            require(edge['status'] in ('candidate','hypothesis') or edge['data'].get('reviewed') is True,
                    "accepted identity links require an explicit reviewed annotation")
            a=nodes[edge['subject']]['data'].get('entity_type');b=nodes[edge['object']]['data'].get('entity_type')
            require(a is None or b is None or a==b,'same_entity has incompatible declared entity types')

    def apply(self,packet,_recorded_at=None,_validated_methods=(),expected_revision=None,expected_projection=None):
        if expected_revision is not None:integer(expected_revision,0,10000,'expected_revision')
        if expected_projection is not None:
            require(isinstance(expected_projection,str) and len(expected_projection)==64,'expected_projection must be a graph projection hash')
        batch=normalize_batch(packet);ih=digest(batch)
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM commits WHERE batch_id=?',(batch['batch_id'],)).fetchone()
            if old:
                require(old['input_hash']==ih,"batch id was already used with different content")
                return result('IDEMPOTENT',revision=old['seq'],commit_hash=old['commit_hash'],changed=[])
            previous=c.execute('SELECT * FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
            current_revision=previous['seq'] if previous else 0
            if expected_revision is not None and expected_revision!=current_revision:
                raise ResearchConflict('graph_revision_conflict: expected '+str(expected_revision)+', current '+str(current_revision))
            seq=previous['seq']+1 if previous else 1
            require(seq<=10000,'graph journal reached 10000 commits; archive and start an explicitly versioned store')
            prev=previous['commit_hash'] if previous else '0'*64
            recorded=timestamp(_recorded_at) if _recorded_at else utc()
            require(len(recorded)>10,"journal time requires an exact zoned timestamp")
            require(not previous or recorded>=previous['recorded_at'],"journal recorded time must be monotonic")
            rows=self._rows(c);changes={}
            if expected_projection is not None and self._projection(rows)!=expected_projection:
                raise ResearchConflict('graph_projection_conflict: evidence changed; recompute the plan')
            # Install all prospective nodes before checking edge references/dependencies.
            for kind,plural,removed in [('node','nodes','remove_nodes'),('edge','edges','remove_edges')]:
                for item,active in [(x,1) for x in batch[plural]]+[(x,0) for x in batch[removed]]:
                    key=(kind,item['id']);current=rows.get(key)
                    if not active:
                        payload=json.loads(current['payload']) if current else {'id':item['id'],'space':'evidence'}
                        payload['revision']=item['revision']
                    else:
                        payload=deepcopy(item)
                    if current and kind=='node':
                        prior=json.loads(current['payload'])
                        if prior.get('type')=='benchmark':
                            require(prior.get('data',{}).get('dataset_hash')==payload.get('data',{}).get('dataset_hash'),
                                    'a frozen benchmark dataset is immutable; use a new id')
                    # Idempotent same-revision upserts compare the declared payload, not captured dependencies.
                    declared={k:v for k,v in payload.items() if k!='_deps'}
                    if current and item['revision']==current['revision']:
                        existing={k:v for k,v in json.loads(current['payload']).items() if k!='_deps'}
                        require(active==current['active'] and declared==existing,"changed item must increase revision: "+item['id'])
                        continue
                    require(not current or item['revision']>current['revision'],"stale item revision: "+item['id'])
                    row={'kind':kind,'id':item['id'],'revision':item['revision'],'active':active,
                         'payload':canonical(payload),'content_hash':digest(payload),'seq':seq}
                    rows[key]=row;changes[key]=row
            nodes={k[1]:json.loads(v['payload']) for k,v in rows.items() if k[0]=='node' and v['active']}
            require(len(nodes)<=MAX_NODES and sum(r['active'] for k,r in rows.items() if k[0]=='edge')<=MAX_EDGES,"graph capacity reached")
            custom=self._custom_predicates(rows)
            for key,row in changes.items():
                if key[0]=='node' and row['active']:
                    n=json.loads(row['payload'])
                    if n['type']=='method' and n['status']=='active':
                        require(n['id'] in _validated_methods,'active recipes must be installed through measured research learn gates')
                    if n['type']=='predicate' and n['status']=='active':
                        from .learning import test_predicate
                        require(all(c['passed'] for c in test_predicate(n,nodes)),"predicate activation trials failed")
            for key,row in changes.items():
                if not row['active']:continue
                payload=json.loads(row['payload'])
                if key[0]=='edge':self.validate_edge(payload,nodes,custom)
                for source in payload.get('source_ids',[]):
                    require(source in nodes and nodes[source]['type']=='source',"source_ids must reference active source nodes: "+source)
                wanted={(d['kind'],d['id']) for d in payload.get('depends_on',[])}
                wanted|={('node',s) for s in payload.get('source_ids',[])}
                if key[0]=='edge':wanted|={('node',payload['subject']),('node',payload['object'])}
                require(key not in wanted,"an item cannot depend on itself")
                captures=[]
                for dep in sorted(wanted):
                    require(dep in rows and rows[dep]['active'],"dependency unavailable: "+dep[1])
                    captures.append({'kind':dep[0],'id':dep[1],'revision':rows[dep]['revision']})
                payload['_deps']=captures
                row['payload']=canonical(payload);row['content_hash']=digest(payload)
                rows[key]=row
            # Refuse cycles across explicit and captured dependencies.
            pending=set();done=set()
            def visit(k,depth=0):
                require(depth<=256,"dependency depth exceeds 256")
                if k in done or k not in rows or not rows[k]['active']:return
                require(k not in pending,"dependency cycle")
                pending.add(k)
                for d in json.loads(rows[k]['payload']).get('_deps',[]):visit((d['kind'],d['id']),depth+1)
                pending.remove(k);done.add(k)
            for key in changes:visit(key)
            require(sum(len(r['payload'].encode()) for r in rows.values())<=64*1024*1024,'graph projection byte capacity reached')
            require(c.execute('SELECT COUNT(*) FROM versions').fetchone()[0]+len(changes)<=250000,'graph history capacity reached; archive explicitly')
            history_bytes=c.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM versions').fetchone()[0]
            journal_bytes=c.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM commits').fetchone()[0]
            require(history_bytes+journal_bytes+len(canonical(batch).encode())+sum(len(r['payload'].encode()) for r in changes.values())<=256*1024*1024,'graph history/journal byte capacity reached; archive explicitly')
            for key,row in changes.items():
                args=tuple(row[k] for k in ('kind','id','revision','active','payload','content_hash','seq'))
                c.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?)',args)
                c.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?)',args)
                c.execute('DELETE FROM dependencies WHERE kind=? AND id=?',key)
                payload=json.loads(row['payload'])
                for dep in payload.get('_deps',[]):
                    c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?)',(*key,dep['kind'],dep['id'],dep['revision'],''))
                if key[0]=='node':
                    c.execute('DELETE FROM node_text WHERE id=?',(key[1],))
                    if row['active']:c.execute('INSERT INTO node_text VALUES(?,?,?)',(key[1],payload['label'],payload.get('text','')))
                else:
                    c.execute('DELETE FROM adjacency WHERE id=?',(key[1],))
                    if row['active']:c.execute('INSERT INTO adjacency VALUES(?,?,?,?)',(key[1],payload['subject'],payload['predicate'],payload['object']))
            ch=digest({'seq':seq,'batch_id':batch['batch_id'],'input_hash':ih,'recorded_at':recorded,'previous_hash':prev,'payload':batch})
            c.execute('INSERT INTO commits VALUES(?,?,?,?,?,?,?)',(seq,batch['batch_id'],ih,recorded,prev,ch,canonical(batch)))
            c.commit()
            return result(revision=seq,commit_hash=ch,changed=[{'kind':k[0],'id':k[1]} for k in sorted(changes)],projection_hash=self._projection(rows))

    def snapshot(self,revision=None,recorded_before=None,event_before=None,valid_at=None,actor=None,receipt_before=None,
                 include_stale=False,include_inactive=False,unknown='retain'):
        require(unknown in ('retain','exclude'),"unknown must be retain or exclude")
        require((actor is None)==(receipt_before is None),"actor and receipt_before must be supplied together")
        for v in (recorded_before,event_before,valid_at,receipt_before):
            if v is not None:timestamp(v)
        with self.connection() as c:
            c.execute('BEGIN')
            latest=c.execute('SELECT MAX(seq) FROM commits').fetchone()[0] or 0
            seq=latest if revision is None else integer(revision,0,latest,'graph revision')
            if recorded_before is not None:
                seq=min(seq,c.execute('SELECT COALESCE(MAX(seq),0) FROM commits WHERE recorded_at<=?',(boundary(recorded_before,True),)).fetchone()[0])
            rows=self._rows(c,None if seq==latest else seq)
        projection=self._projection(rows);memo={}
        def stale(key,depth=0):
            if key in memo:return memo[key]
            if key not in rows or not rows[key]['active']:return ['removed:'+key[1]]
            if depth>256:return ['dependency_depth']
            p=json.loads(rows[key]['payload']);reasons=[]
            for d in p.get('_deps',[]):
                dep=(d['kind'],d['id'])
                if dep not in rows or not rows[dep]['active'] or rows[dep]['revision']!=d['revision']:
                    reasons.append('changed:'+d['id'])
                else:reasons+=stale(dep,depth+1)
            if p.get('data',{}).get('basis_projection') not in (None,projection):reasons.append('graph_projection_changed')
            memo[key]=sorted(set(reasons));return memo[key]
        nodes=[];edges=[];omitted=[];unknown_ids=[]
        for key,row in sorted(rows.items()):
            if not row['active'] and not include_inactive:continue
            p=json.loads(row['payload']);p['content_hash']=row['content_hash'];p['active']=bool(row['active'])
            p['stale_reasons']=stale(key) if row['active'] else ['removed']
            p['effective_status']='stale' if p['stale_reasons'] else p.get('status','retired')
            if p['stale_reasons'] and not include_stale and row['active']:
                omitted.append({'kind':key[0],'id':key[1],'reason':'stale'});continue
            clock=p.get('clocks',{});open_clock=False;eligible=True
            if event_before is not None:
                v=clock.get('event');open_clock|=v is None or v[1] is None
                if v and v[1] is not None:eligible&=boundary(v[1],True)<=boundary(event_before,True)
            if valid_at is not None:
                v=clock.get('valid');open_clock|=v is None or any(x is None for x in v)
                if v:eligible&=(v[0] is None or boundary(v[0])<=boundary(valid_at,True)) and (v[1] is None or boundary(valid_at)<=boundary(v[1],True))
            if actor is not None:
                v=clock.get('receipt',{}).get(actor);open_clock|=v is None or v[1] is None
                if v and v[1] is not None:eligible&=boundary(v[1],True)<=boundary(receipt_before,True)
            if open_clock:
                if p.get('space')!='research':unknown_ids.append(key[1])
                p['temporal_state']='OPEN'
                if unknown=='exclude' or actor is not None:eligible=False
            else:p['temporal_state']='WITHIN_FILTER' if any(v is not None for v in (event_before,valid_at,actor)) else 'UNFILTERED'
            if eligible:(nodes if key[0]=='node' else edges).append(p)
            else:omitted.append({'kind':key[0],'id':key[1],'reason':'clock'})
        kept={n['id'] for n in nodes}
        edges=[e for e in edges if e.get('subject') in kept and e.get('object') in kept and set(e.get('source_ids',[]))<=kept]
        # Availability is closed over every captured dependency, not just source_ids.
        if actor is not None or event_before is not None or valid_at is not None:
            items={('node',n['id']):n for n in nodes if n['active']}
            items.update({('edge',e['id']):e for e in edges if e['active']})
            available=set(items)
            while True:
                remove=set()
                for k,p in items.items():
                    if k not in available:continue
                    required={(d['kind'],d['id']) for d in p.get('_deps',[])}
                    required|={('node',s) for s in p.get('source_ids',[])}
                    if k[0]=='edge':required|={('node',p['subject']),('node',p['object'])}
                    if not required<=available:remove.add(k)
                if not remove:break
                for k in remove:omitted.append({'kind':k[0],'id':k[1],'reason':'dependency_outside_temporal_view'})
                available-=remove
            nodes=[n for n in nodes if not n['active'] or ('node',n['id']) in available]
            edges=[e for e in edges if not e['active'] or ('edge',e['id']) in available]
        return result('PARTIAL_TEMPORAL' if unknown_ids else 'COMPLETE',revision=seq,projection_hash=projection,
                      graph_hash=digest({'nodes':nodes,'edges':edges}),nodes=nodes,edges=edges,
                      omitted=omitted,unknown_clock_ids=sorted(set(unknown_ids)),
                      boundary='Versioned supplied observations and annotations; actor receipt is distinct from understanding and authorization.')

    def diff(self,before,after=None):
        a=self.snapshot(revision=before,include_stale=True,include_inactive=True)
        b=self.snapshot(revision=after,include_stale=True,include_inactive=True)
        def flat(s):return {(kind,x['id']):x for kind in ('nodes','edges') for x in s[kind]}
        left,right=flat(a),flat(b)
        return result(before=a['revision'],after=b['revision'],changes=[{'kind':k[0],'id':k[1],
                      'before':left.get(k),'after':right.get(k)} for k in sorted(set(left)|set(right)) if left.get(k)!=right.get(k)])

    def withdraw(self,origin,batch_id):
        s=self.snapshot(include_stale=True)
        removals=[{'id':n['id'],'revision':n['revision']+1} for n in s['nodes'] if n['type']=='source' and n['origin']==origin]
        require(removals,"origin has no active source nodes")
        return self.apply({'schema':'loom.graph.batch.v1','batch_id':batch_id,'remove_nodes':removals},expected_revision=s['revision'])

    def journal(self):
        with self.connection() as c:records=[dict(r) for r in c.execute('SELECT * FROM commits ORDER BY seq')]
        for row in records:row['payload']=json.loads(row['payload'])
        return {'schema':'loom.graph.journal.v1','records':records,'proof_effect':'NONE'}

    @classmethod
    def replay(cls,path,journal):
        fields(journal,('schema','records'),('proof_effect',))
        require(journal['schema']=='loom.graph.journal.v1' and isinstance(journal['records'],list),"invalid journal")
        require(len(journal['records'])<=10000,"journal exceeds replay limit")
        # Validate the complete chain before creating the destination.
        prev='0'*64
        for seq,row in enumerate(journal['records'],1):
            require(row['seq']==seq and row['previous_hash']==prev,"journal sequence/chain mismatch")
            require(row['input_hash']==digest(normalize_batch(row['payload'])),"journal payload mismatch")
            expected=digest({k:row[k] for k in ('seq','batch_id','input_hash','recorded_at','previous_hash','payload')})
            require(expected==row['commit_hash'],"journal hash mismatch")
            require(row['batch_id']==row['payload']['batch_id'],"journal batch identity mismatch")
            prev=expected
        store=cls(path,create=True)
        for row in journal['records']:
            methods=[n['id'] for n in row['payload'].get('nodes',[]) if n['type']=='method' and n['status']=='active']
            actual=store.apply(row['payload'],_recorded_at=row['recorded_at'],_validated_methods=methods)
            require(actual['commit_hash']==row['commit_hash'],"replay hash mismatch")
        return store

    def verify(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='loom-graph-verify-') as td:
            # SQLite's online backup pins a consistent database while live writes
            # may continue. Replay and every materialized comparison use that copy.
            pinned_path=Path(td)/'pinned.sqlite'
            with self.connection() as source,closing(sqlite3.connect(pinned_path)) as target:
                source.backup(target,pages=256)
            pinned=GraphStore(pinned_path)
            replayed=self.replay(Path(td)/'replay.sqlite',pinned.journal())
            actual=pinned.snapshot(include_stale=True,include_inactive=True)
            expected=replayed.snapshot(include_stale=True,include_inactive=True)
            require(actual==expected,"materialized graph differs from journal replay")
            with pinned.connection() as left,replayed.connection() as right:
                for table in ('items','versions','dependencies','adjacency'):
                    a=sorted((dict(r) for r in left.execute('SELECT * FROM '+table)),key=canonical)
                    b=sorted((dict(r) for r in right.execute('SELECT * FROM '+table)),key=canonical)
                    require(a==b,'materialized '+table+' differs from journal replay')
                a=sorted(tuple(r) for r in left.execute('SELECT id,label,text FROM node_text'))
                b=sorted(tuple(r) for r in right.execute('SELECT id,label,text FROM node_text'))
                require(a==b,'text index differs from journal replay')
        return result(ok=True,revision=actual['revision'],graph_hash=actual['graph_hash'],integrity='replayed hash chain and materialized graph match')
