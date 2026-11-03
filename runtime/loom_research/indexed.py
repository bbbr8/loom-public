"""Incremental FTS candidate retrieval with captured-dependency eligibility.

The index is derived from journaled items. Triggers update only changed rows.
All retrieval runs inside one SQLite read transaction; hard clauses apply after
fusion too. Candidate budgets are disclosed, never described as exhaustive.
"""
import json
import time
from collections import defaultdict
from .contracts import require, integer, text, result, digest, canonical
from surd_search import SearchIndex, parse_query, has_phrase
from surd_search_core import tokens

MIGRATIONS = {
    1: ["CREATE VIRTUAL TABLE studio_text USING fts5(id UNINDEXED,label,text,identifiers,tokenize='porter unicode61')",
        "INSERT INTO studio_text SELECT id,json_extract(payload,'$.label'),json_extract(payload,'$.text'),COALESCE((SELECT group_concat(json_extract(value,'$.value'),' ') FROM json_each(items.payload,'$.identifiers')),'') FROM items WHERE kind='node' AND active=1"],
    2: ["CREATE TRIGGER studio_item_insert AFTER INSERT ON items WHEN NEW.kind='node' BEGIN DELETE FROM studio_text WHERE id=NEW.id; INSERT INTO studio_text SELECT NEW.id,json_extract(NEW.payload,'$.label'),json_extract(NEW.payload,'$.text'),COALESCE((SELECT group_concat(json_extract(value,'$.value'),' ') FROM json_each(NEW.payload,'$.identifiers')),'') WHERE NEW.active=1; END",
        "CREATE TRIGGER studio_item_update AFTER UPDATE ON items WHEN NEW.kind='node' BEGIN DELETE FROM studio_text WHERE id=OLD.id; INSERT INTO studio_text SELECT NEW.id,json_extract(NEW.payload,'$.label'),json_extract(NEW.payload,'$.text'),COALESCE((SELECT group_concat(json_extract(value,'$.value'),' ') FROM json_each(NEW.payload,'$.identifiers')),'') WHERE NEW.active=1; END",
        "CREATE TRIGGER studio_item_delete AFTER DELETE ON items WHEN OLD.kind='node' BEGIN DELETE FROM studio_text WHERE id=OLD.id; END"]}

# Additive side index; the original source ranking and immutable migrations stay intact.
_FORMS="COALESCE((SELECT group_concat(json_extract(value,'$.value.canonical'),' ') FROM json_each({payload},'$.data.normalization.fields') WHERE json_type(value,'$.value.canonical') IN ('text','integer') AND json_extract(value,'$.value.state') IN ('NORMALIZED','OPEN_CURRENCY','DATE_ONLY','ADDRESS_TEXT_REVIEW_OPEN')), '')"
MIGRATIONS[3]=[
    "CREATE VIRTUAL TABLE studio_normalized USING fts5(id UNINDEXED,forms,tokenize='unicode61')",
    "INSERT INTO studio_normalized SELECT id,"+_FORMS.format(payload='payload')+" FROM items WHERE kind='node' AND active=1 AND json_type(payload,'$.data.normalization')='object'"]
for _event in ('INSERT','UPDATE','DELETE'):
    _old='OLD' if _event in ('UPDATE','DELETE') else 'NEW'
    _sql=f"CREATE TRIGGER studio_normalized_{_event.lower()} AFTER {_event} ON items WHEN {_old}.kind='node' BEGIN DELETE FROM studio_normalized WHERE id={_old}.id; "
    if _event!='DELETE':
        _sql+="INSERT INTO studio_normalized SELECT NEW.id,"+_FORMS.format(payload='NEW.payload')+" WHERE NEW.active=1 AND json_type(NEW.payload,'$.data.normalization')='object'; "
    MIGRATIONS[3].append(_sql+'END')


def migrate(store):
    """Two immutable additive migrations; native journal and bootstrap v1 stay intact."""
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS studio_migrations(version INTEGER PRIMARY KEY,sha256 TEXT NOT NULL)')
        applied=dict(c.execute('SELECT version,sha256 FROM studio_migrations'))
        require(set(applied)<=set(MIGRATIONS),'unsupported studio migration version')
        for version,statements in MIGRATIONS.items():
            fingerprint=digest(statements)
            if version in applied:
                require(applied[version]==fingerprint,'studio migration fingerprint mismatch')
            else:
                for sql in statements:c.execute(sql)
                c.execute('INSERT INTO studio_migrations VALUES(?,?)',(version,fingerprint))
        # Validate live definitions too; a retained ledger alone cannot verify a trigger.
        for statements in MIGRATIONS.values():
            for sql in statements:
                if sql.startswith('CREATE '):
                    name=sql.split()[3] if sql.startswith('CREATE VIRTUAL') else sql.split()[2]
                    row=c.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
                    require(row is not None and ' '.join(row[0].split())==' '.join(sql.split()),'studio schema definition mismatch: '+name)
        c.commit()
    return result('READY',migrations=[{'version':v,'sha256':digest(s)} for v,s in MIGRATIONS.items()])


def _ready(store):
    with store.connection() as c:
        row=c.execute("SELECT name FROM sqlite_master WHERE name='studio_migrations'").fetchone()
        versions=dict(c.execute('SELECT version,sha256 FROM studio_migrations')) if row else {}
    if versions!={v:digest(s) for v,s in MIGRATIONS.items()}:migrate(store)


class Reader:
    """Candidate-local dependency closure, with an explicit total item budget."""
    def __init__(self,c,budget=16000):self.c=c;self.budget=budget;self.rows={};self.memo={};self.reasons={};self.work=0

    def row(self,kind,ident):
        key=(kind,ident)
        if key not in self.rows:
            require(self.work<self.budget,'candidate dependency budget reached; narrow the query')
            self.work+=1
            row=self.c.execute('SELECT * FROM items WHERE kind=? AND id=?',key).fetchone()
            self.rows[key]=dict(row) if row else None
        return self.rows[key]

    def get(self,kind,ident,trail=()):
        key=(kind,ident)
        if key in self.memo:return self.memo[key]
        row=self.row(kind,ident)
        if not row or not row['active']:
            self.reasons[key]=['removed_or_missing'];return None
        if key in trail or len(trail)>256:return None
        p=json.loads(row['payload']);reasons=[]
        for dep in p.get('_deps',[]):
            other=self.row(dep['kind'],dep['id'])
            if not other or not other['active'] or other['revision']!=dep['revision']:
                reasons.append('changed:'+dep['id'])
            elif self.get(dep['kind'],dep['id'],trail+(key,)) is None:reasons.append('stale:'+dep['id'])
        # Projection-wide methods require the full native projection evaluator.
        if p.get('data',{}).get('basis_projection') is not None:reasons.append('requires_full_projection_view')
        self.reasons[key]=reasons
        self.memo[key]=None if reasons else {**p,'content_hash':row['content_hash'],'active':True,
                                           'effective_status':p['status'],'stale_reasons':[]}
        return self.memo[key]


def _row(n):
    return {'id':n['id'],'title':n['label'],'text':n.get('text',''),'locator':n.get('locator',''),
            'metadata':{**n.get('data',{}),'type':n['type'],'space':n['space']},
            'fields':{'identifiers':' '.join(x['value'] for x in n.get('identifiers',[]))}}


def hard_match(n,clauses):
    fs=[tokens(v) for v in (n['id'],n['label'],n.get('text',''),n.get('locator',''),
                           ' '.join(x['value'] for x in n.get('identifiers',[])))]
    for clause in clauses:
        hit=any(has_phrase(ts,clause['tokens']) if clause['phrase'] else all(t in ts for t in clause['tokens']) for ts in fs)
        if clause['kind']=='exclude' and hit:return False
        if (clause['kind']=='require' or clause['phrase']) and clause['kind']!='exclude' and not hit:return False
    return True


def search(store,query,top=10,candidates=400,semantic=None,where=None,ranges=None):
    text(query,'query',8192);integer(top,1,100,'top');integer(candidates,top,1000,'candidates')
    clauses=parse_query(query);positive=[t for cl in clauses if cl['kind']!='exclude' for t in cl['tokens']]
    require(positive,'indexed search needs positive terms')
    match=' OR '.join('"'+t.replace('"','""')+'"' for t in sorted(set(positive)))
    _ready(store);started=time.perf_counter()
    with store.connection() as c:
        c.execute('BEGIN');head=c.execute('SELECT seq,commit_hash FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
        revision=head[0] if head else 0;commit=head[1] if head else '0'*64
        # LIMIT bounds candidate materialization. SQL execution has its own deadline.
        deadline=time.monotonic()+8;c.set_progress_handler(lambda:int(time.monotonic()>deadline),5000)
        # Structural and research rows must never consume source candidate slots.
        # Keep immutable index migrations intact and filter their current items
        # before LIMIT; dependency and hard-clause checks still follow fusion.
        ranked=list(c.execute("SELECT studio_text.id,bm25(studio_text,0,3,1,4) AS score FROM studio_text JOIN items i ON i.kind='node' AND i.id=studio_text.id WHERE studio_text MATCH ? AND i.active=1 AND json_extract(i.payload,'$.space')!='research' AND json_extract(i.payload,'$.type') NOT IN ('source','concept') ORDER BY score,studio_text.id LIMIT ?",(match,candidates+1)))
        lexical=[r['id'] for r in ranked[:candidates]];limited=len(ranked)>candidates
        exact=[r[0] for r in c.execute("SELECT id FROM items WHERE kind='node' AND active=1 AND (id=? OR EXISTS(SELECT 1 FROM json_each(payload,'$.identifiers') WHERE json_extract(value,'$.value')=?)) LIMIT 101",(query,query))]
        require(len(exact)<=100,'exact identifier fanout requires a narrower scope')
        lists=[exact,lexical];semantic_state='NOT_CONFIGURED';fingerprint=None
        if semantic is not None:
            require(isinstance(semantic,dict) and semantic.get('query')==query and semantic.get('commit_hash')==commit,
                    'semantic result must match current commit and exact query')
            ids=semantic.get('ids');require(isinstance(ids,list) and len(ids)<=1000 and len(ids)==len(set(ids)) and all(isinstance(x,str) for x in ids),'invalid semantic ids')
            fingerprint=text(semantic.get('model_fingerprint'),'model fingerprint',256)
            lists.append(ids);semantic_state='ALIGNED_SUPPLIED_RANKING'
        reader=Reader(c);nodes={};scores=defaultdict(float)
        for lane,ids in enumerate(lists):
            for rank,ident in enumerate(ids,1):
                n=reader.get('node',ident)
                if not n or n['type'] in ('source','concept') or n['space']=='research' or not hard_match(n,clauses):continue
                nodes[ident]=n;scores[ident]+=(2 if lane==0 else 1)/(60+rank)
        if where or ranges:
            allowed={n['id'] for n in SearchIndex([_row(n) for n in nodes.values()]).documents(where or {},ranges or {})}
            nodes={k:v for k,v in nodes.items() if k in allowed}
        ordered=sorted(nodes,key=lambda k:(k not in exact,-scores[k],k))[:top]
        hits=[]
        for k in ordered:
            n=nodes[k];sources=[reader.get('node',s) for s in n['source_ids']]
            hits.append({**n,'text':n.get('text','')[:4000],'retrieval_score':scores[k],
                         'sources':[{'id':s['id'],'origin':s['origin'],'locator':s['locator'],'data':s['data']} for s in sources if s],
                         'lanes':[name for name,ids in zip(('exact','fts5','semantic'),lists) if k in ids]})
        return result('BOUNDED_CANDIDATES',hits=hits,query=query,revision=revision,commit_hash=commit,
                      semantic_state=semantic_state,model_fingerprint=fingerprint,candidate_limit=candidates,
                      candidate_limit_reached=limited,items_read=reader.work,seconds=time.perf_counter()-started,
                      boundary='Current captured-dependency view; ranking measures relevance. Projection-wide methods use graph_search. Temporal views use graph_snapshot.')


def inspect(store,ident,limit=60):
    text(ident,'record id');integer(limit,1,200,'limit')
    with store.connection() as c:
        c.execute('BEGIN');reader=Reader(c);row=reader.row('node',ident);require(row,'record unavailable')
        n=reader.get('node',ident);raw=json.loads(row['payload'])
        edges=[]
        for r in c.execute('SELECT id FROM adjacency WHERE subject=? OR object=? ORDER BY id LIMIT ?',(ident,ident,limit+1)):
            edge=reader.get('edge',r[0])
            if edge:edges.append(edge)
        linked={v for e in edges[:limit] for v in (e['subject'],e['object'])}- {ident}
        sources=[reader.get('node',s) for s in raw.get('source_ids',[])]
        versions=[{'revision':r['revision'],'active':bool(r['active']),'content_hash':r['content_hash'],'commit':r['seq']} for r in c.execute('SELECT * FROM versions WHERE kind=? AND id=? ORDER BY revision',('node',ident))]
        if not n:raw.update(stale_reasons=reader.reasons.get(('node',ident),[]),content_hash=row['content_hash'])
        return result('AVAILABLE' if n else 'UNAVAILABLE_IN_CANDIDATE_VIEW',node=n or raw,
                      sources=[s for s in sources if s],edges=edges[:limit],
                      neighbors=[n for k in sorted(linked) for n in [reader.get('node',k)] if n],
                      relationship_limit_reached=len(edges)>limit,versions=versions,
                      boundary='Reviewed annotations, candidates and source extracts retain their distinct states.')


def normalized_search(store,query,top=10,candidates=400,where=None,ranges=None,profile=None):
    """Opt-in normalized-form retrieval returning original records and match lineage."""
    from .normalization import normalize
    text(query,'query',8192);integer(top,1,100,'top');integer(candidates,top,1000,'candidates')
    clauses=parse_query(query);terms=[t for cl in clauses if cl['kind']!='exclude' for t in cl['tokens']]
    require(terms,'normalized search requires positive terms')
    if len(clauses)==1 and not clauses[0]['phrase'] and clauses[0]['kind']!='require':
        for kind in ('number','money','date'):
            view=normalize(kind,query,profile)
            if view['canonical'] is not None and view['state'] in ('NORMALIZED','OPEN_CURRENCY','DATE_ONLY'):
                terms.extend(tokens(str(view['canonical'])))
    match=' OR '.join('"'+t.replace('"','""')+'"' for t in sorted(set(terms)))
    _ready(store)
    with store.connection() as c:
        c.execute('BEGIN');head=c.execute('SELECT seq,commit_hash FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
        deadline=time.monotonic()+8;c.set_progress_handler(lambda:int(time.monotonic()>deadline),5000)
        ranked=list(c.execute('SELECT id,bm25(studio_normalized) AS score FROM studio_normalized WHERE studio_normalized MATCH ? ORDER BY score,id LIMIT ?',(match,candidates+1)))
        reader=Reader(c);found={}
        for row in ranked[:candidates]:
            annotation=reader.get('node',row['id'])
            if not annotation:continue
            original=reader.get('node',annotation['data']['source_node']) if annotation['data'].get('source_node') else annotation
            if not original or original['space']=='research' or not hard_match(original,clauses):continue
            if original['id'] not in found:found[original['id']]={**original,'normalization_matches':[]}
            found[original['id']]['normalization_matches'].append({'view_id':annotation['id'],'profile_hash':annotation['data']['normalization']['profile_hash']})
        if where or ranges:
            allowed={n['id'] for n in SearchIndex([_row(n) for n in found.values()]).documents(where or {},ranges or {})}
            found={k:v for k,v in found.items() if k in allowed}
        hits=[]
        for n in list(found.values())[:top]:
            sources=[reader.get('node',i) for i in n['source_ids']]
            hits.append({**n,'text':n.get('text','')[:4000],'lanes':['normalized'],
                         'sources':[{'id':s['id'],'origin':s['origin'],'locator':s['locator'],'data':s['data']} for s in sources if s]})
        return result('NORMALIZED_CANDIDATES',query=query,hits=hits,revision=head[0] if head else 0,
                      commit_hash=head[1] if head else '0'*64,candidate_limit_reached=len(ranked)>candidates,
                      items_read=reader.work,boundary='Opt-in format-equivalence retrieval. Source text, hard clauses, scope filters and identity review remain controlling.')
