"""Optional offline Sentence Transformers adapter; no downloads or hosted calls.

Run indexing on a configured compute worker. Vectors are disposable derived
data, keyed by content and complete model fingerprints. Commit drift rejects use.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from .contracts import require, integer, text, result, canonical


def fingerprint(directory):
    root=Path(directory).expanduser().resolve();require(root.is_dir(),'installed local model directory required')
    h=hashlib.sha256();count=0
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.name.startswith('.'):continue
        h.update(str(p.relative_to(root)).encode());count+=1
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    require(count>0,'local model directory is empty')
    return h.hexdigest()


def encoder(directory):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(str(Path(directory).expanduser().resolve()),local_files_only=True,trust_remote_code=False,device='cpu')


def sync(store,index_path,model_dir,batch_size=32):
    integer(batch_size,1,64,'batch_size')
    from .indexed import Reader
    path=Path(index_path).expanduser().absolute();require(not path.is_symlink(),'linked vector store refused')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    model_hash=fingerprint(model_dir);model=encoder(model_dir)
    with store.connection() as c, closing(sqlite3.connect(path,timeout=10)) as v:
        c.execute('BEGIN');head=c.execute('SELECT seq,commit_hash FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
        commit=head[1] if head else '0'*64
        v.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        v.execute('CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,vector TEXT NOT NULL)')
        v.execute('BEGIN IMMEDIATE')
        old=dict(v.execute('SELECT key,value FROM meta'))
        if old.get('model_fingerprint')!=model_hash:v.execute('DELETE FROM vectors')
        previous=dict(v.execute('SELECT id,content_hash FROM vectors'));reader=Reader(c,budget=40000)
        active=set();changed=[]
        for row in c.execute("SELECT id FROM items WHERE kind='node' AND active=1 ORDER BY id"):
            n=reader.get('node',row[0])
            if not n or n['type'] in ('source','concept') or n['space']=='research':continue
            active.add(n['id'])
            if previous.get(n['id'])!=n['content_hash']:changed.append(n)
        for start in range(0,len(changed),batch_size):
            group=changed[start:start+batch_size]
            vectors=model.encode([(n['label']+'\n'+n.get('text',''))[:12000] for n in group],normalize_embeddings=True,
                                 batch_size=batch_size,show_progress_bar=False)
            for n,vector in zip(group,vectors):
                v.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?)',(n['id'],n['content_hash'],canonical(vector.tolist())))
        for ident in set(previous)-active:v.execute('DELETE FROM vectors WHERE id=?',(ident,))
        for key,value in {'model_fingerprint':model_hash,'commit_hash':commit,'text_policy':'label+text:first12000:v1'}.items():
            v.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value))
        v.commit();path.chmod(0o600)
    return result('INDEXED',commit_hash=commit,model_fingerprint=model_hash,indexed=len(active),
                  embedded=len(changed),removed=len(set(previous)-active),boundary='Offline embeddings; interpretation and truth remain source-review tasks.')


def rank(store,query,index_path,model_dir,top=100,reranker_dir=None):
    import numpy as np
    integer(top,1,1000,'top');text(query,'query',8192)
    path=Path(index_path).expanduser().resolve();require(path.is_file(),'semantic index unavailable; run studio index')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        c.execute('BEGIN');meta=dict(c.execute('SELECT key,value FROM meta'))
        require(meta.get('commit_hash')==store.head()[1],'semantic index is stale; sync before searching')
        model_hash=fingerprint(model_dir);require(meta.get('model_fingerprint')==model_hash,'semantic model fingerprint changed; sync first')
        rows=list(c.execute('SELECT id,vector FROM vectors ORDER BY id'))
    require(len(rows)<=20000,'semantic index exceeds graph capacity')
    model=encoder(model_dir);q=model.encode([query],normalize_embeddings=True,show_progress_bar=False)[0]
    matrix=np.asarray([json.loads(r[1]) for r in rows],dtype=np.float32)
    require(not len(rows) or matrix.ndim==2 and matrix.shape[1]==len(q) and np.isfinite(matrix).all(),'invalid semantic dimensions or values')
    scores=matrix@q if len(rows) else []
    ids=[rows[i][0] for i in sorted(range(len(rows)),key=lambda i:(-float(scores[i]),rows[i][0]))[:top]]
    rerank_hash=None
    if reranker_dir and ids:
        from sentence_transformers import CrossEncoder
        from .indexed import Reader
        rerank_hash=fingerprint(reranker_dir)
        reranker=CrossEncoder(str(Path(reranker_dir).expanduser().resolve()),local_files_only=True,trust_remote_code=False,device='cpu',max_length=512)
        with store.connection() as c:
            c.execute('BEGIN');reader=Reader(c);nodes=[reader.get('node',k) for k in ids]
        require(all(nodes),'candidate became unavailable; repeat against current index')
        values=np.asarray(reranker.predict([(query,(n['label']+'\n'+n.get('text',''))[:12000]) for n in nodes],batch_size=16,show_progress_bar=False))
        require(values.ndim==1 and np.isfinite(values).all(),'reranker must produce one finite relevance score per pair')
        ids=[ids[i] for i in sorted(range(len(ids)),key=lambda i:(-float(values[i]),ids[i]))]
    require(meta['commit_hash']==store.head()[1],'graph changed during semantic ranking; repeat')
    return {'query':query,'commit_hash':meta['commit_hash'],'ids':ids,'model_fingerprint':model_hash,
            'reranker_fingerprint':rerank_hash,'truncation_policy':'first12000chars; reranker max512tokens'}
