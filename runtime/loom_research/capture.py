"""Optional bounded research memory for ordinary Loom source searches.

LOOM_RESEARCH_DB binds an existing graph for this process. Capture failures warn
without changing the original analytical result. Sources remain indexed snapshots.
"""
import os
import sys
from .contracts import digest, result, require
from .store import GraphStore
from .engine import records_batch, ingest
from .learning import upsert_node
from .bindings import bound_db


def record_search(index,query,report):
    try:
        path=bound_db()
        if not path:return result('DISABLED')
        store=GraphStore(path);records=[]
        for hit in report.get('hits',[])[:50]:
            doc=index.document(hit['id']);meta=doc.get('metadata',{});loc=doc.get('locator') or 'indexed-document:'+doc['id']
            ident='captured:'+digest([loc,doc['id'],doc['content_hash']])[:24]
            clock={}
            for field,name in [('event_date','event'),('statement_date','statement')]:
                if isinstance(meta.get(field),str):clock[name]=[meta[field],meta[field]]
            if isinstance(meta.get('receipt_by_actor'),dict):
                clock['receipt']={a:([v,v] if isinstance(v,str) else v) for a,v in meta['receipt_by_actor'].items()}
            records.append({'id':ident,'origin':str(meta.get('origin_id') or meta.get('lineage_id') or 'indexed-locator:'+loc),
                            'locator':loc,'label':doc.get('title') or doc['id'],'text':doc['text'],'clocks':clock,
                            'data':{'indexed_content_hash':doc['content_hash'],'indexed_id':doc['id'],
                                    'source_metadata':meta,'source_independence_verified':False,
                                    'origin_declared':bool(meta.get('origin_id') or meta.get('lineage_id'))}})
        receipt=ingest(store,records_batch('capture:'+digest([query,index.manifest()['corpus_hash'],[r['id'] for r in records]]),records))
        snap=store.snapshot();ident='capture-search:'+digest([query,index.manifest()['index_hash'],[r['id'] for r in records]])[:24]
        upsert_node(store,{'id':ident,'type':'search','label':query[:2000],'space':'research','status':'derived',
                          'data':{'query':query,'index_manifest':index.manifest(),'retained_hits':len(records),
                                  'returned_hits':len(report.get('hits',[])),'top_k_is_exhaustive':False,
                                  'basis_projection':snap['projection_hash']},
                          'depends_on':[{'kind':'node','id':r['id']} for r in records]},ident+':'+snap['projection_hash'])
        return result('CAPTURED',retained_hits=len(records),evolution=receipt['evolution'])
    except Exception as exc:
        # This optional side channel never masks a successful or correctly rejected primary query.
        print('Loom research memory warning: '+str(exc),file=sys.stderr)
        return result('ERROR',diagnostic=str(exc))
