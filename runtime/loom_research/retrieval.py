"""Source-backed lexical/identifier seeds, reciprocal ranks and bounded graph diffusion."""
from collections import Counter, defaultdict, deque
import math
import re
import time
import threading
from collections import OrderedDict
from .contracts import integer, norm, require, result, digest, text
from surd_search import SearchIndex, has_phrase, DEFAULTS
from surd_search import VERSION as SEARCH_VERSION
from surd_search_core import tokens

# Bounded in-process cache; persistent adapters may supply their own corpus port.
# The key hashes actual rows, not caller-supplied graph metadata.
_INDEX_CACHE=OrderedDict()
_CACHE_LOCK=threading.RLock()
_CACHE_INPUT_BYTES=0

def policy_hash():
    return digest([SEARCH_VERSION,DEFAULTS,'direct-evidence-first-v2','paged-evolution-v2'])

def cached_index(rows,cache_path=None):
    global _CACHE_INPUT_BYTES
    from .contracts import canonical
    config={'fields':{**DEFAULTS['fields'],'identifiers':4}}
    raw=canonical([SEARCH_VERSION,rows,config]);key=digest(raw);size=len(raw.encode())
    with _CACHE_LOCK:
        if key in _INDEX_CACHE:
            index,cost=_INDEX_CACHE.pop(key);_INDEX_CACHE[key]=(index,cost)
            return index,True
    index=None;disk_hit=False
    if cache_path is not None:
        try:index=SearchIndex.load_compiled(cache_path,key);disk_hit=True
        except (OSError,ValueError,KeyError,TypeError,EOFError,RecursionError):pass
    if index is None:
        index=SearchIndex(rows,config)
        if cache_path is not None:
            try:index.save_compiled(cache_path,key)
            except OSError:pass # Caching never changes the source result.
    if size<=24*1024**2:
        with _CACHE_LOCK:
            if key in _INDEX_CACHE:
                old,cost=_INDEX_CACHE.pop(key);_CACHE_INPUT_BYTES-=cost
            while _INDEX_CACHE and (_CACHE_INPUT_BYTES+size>24*1024**2 or len(_INDEX_CACHE)>=2):
                _,(_,cost)=_INDEX_CACHE.popitem(last=False);_CACHE_INPUT_BYTES-=cost
            _INDEX_CACHE[key]=(index,size);_CACHE_INPUT_BYTES+=size
    return index,disk_hit


def terms(value):
    return re.findall(r'[\w]+(?:[-_.][\w]+)*',norm(value))


def overview(snapshot,top=20,max_visits=100000,include_candidates=False):
    integer(top,1,100,'top');integer(max_visits,1,1000000,'max_visits')
    nodes={n['id']:n for n in snapshot['nodes'] if n.get('space')!='research'}
    adjacency=defaultdict(set);degree=Counter()
    for e in snapshot['edges']:
        if e['subject'] not in nodes or e['object'] not in nodes:continue
        if e['status']=='retired' or not include_candidates and e['status'] in ('candidate','hypothesis','proposed'):continue
        a,b=e['subject'],e['object'];adjacency[a].add(b);adjacency[b].add(a);degree[a]+=1;degree[b]+=1
    unseen=set(nodes);groups=[];visits=0;partial=False
    while unseen and visits<max_visits:
        seed=min(unseen);unseen.remove(seed);queue=deque([seed]);component=[]
        while queue and visits<max_visits:
            at=queue.popleft();component.append(at);visits+=1
            for to in sorted(adjacency[at]):
                if to in unseen:unseen.remove(to);queue.append(to)
        if queue:partial=True
        origins={nodes[k]['origin'] for k in component if nodes[k]['type']=='source'}
        groups.append({'id':'component:'+digest(sorted(component))[:16],'node_count':len(component),
                       'source_origin_count':len(origins),'nodes':sorted(component)[:100],
                       'node_preview_truncated':len(component)>100,
                       'topics':[{'id':k,'label':nodes[k]['label'],'degree':degree[k]} for k in sorted(component,key=lambda k:(-degree[k],k)) if nodes[k]['type']=='concept'][:top]})
    partial|=bool(unseen)
    groups.sort(key=lambda g:(-g['node_count'],g['id']))
    return result('PARTIAL' if partial else 'COMPLETE_WITHIN_SNAPSHOT',components=groups,
                  type_counts=dict(Counter(n['type'] for n in nodes.values())),source_origins=len({n['origin'] for n in nodes.values() if n['type']=='source'}),
                  hubs=[{'id':k,'type':nodes[k]['type'],'label':nodes[k]['label'],'degree':degree[k]} for k in sorted(nodes,key=lambda k:(-degree[k],k))[:top]],
                  work=visits,graph_hash=snapshot['graph_hash'],
                  boundary='Exact connected components and degree-based topics within the work budget; structural prominence is distinct from evidence independence or importance.')


def paths(snapshot,start,end=None,max_hops=3,max_paths=20,max_visits=20000,include_candidates=False):
    integer(max_hops,0,6,'max_hops');integer(max_paths,1,100,'max_paths');integer(max_visits,1,100000,'max_visits')
    nodes={n['id']:n for n in snapshot['nodes']};require(start in nodes,'start node unavailable')
    require(end is None or end in nodes,'end node unavailable')
    adjacency=defaultdict(list)
    for e in snapshot['edges']:
        if e['status']=='retired' or not include_candidates and e['status'] in ('candidate','hypothesis','proposed'):continue
        adjacency[e['subject']].append((e['object'],e));adjacency[e['object']].append((e['subject'],e))
    queue=deque([(start,[start],[])]);found=[];visits=0
    while queue and len(found)<max_paths and visits<max_visits:
        at,ns,es=queue.popleft();visits+=1
        if es and (end is None or at==end):
            found.append({'nodes':ns,'edges':[e['id'] for e in es],
                          'source_ids':sorted({s for e in es for s in e['source_ids']}),
                          'predicates':[e['predicate'] for e in es]})
            if end is not None:continue
        if len(es)>=max_hops:continue
        for to,e in sorted(adjacency[at],key=lambda x:(x[0],x[1]['id'])):
            if to not in ns:queue.append((to,ns+[to],es+[e]))
    return result('PARTIAL' if queue else 'COMPLETE_WITHIN_HOPS',paths=found,visits=visits,
                  graph_hash=snapshot['graph_hash'],boundary='Typed source-linked paths within explicit hop/path/work bounds.')


def search(snapshot,query,top=10,hops=2,iterations=24,max_visits=100000,semantic=None,include_candidates=False,spaces=None,where=None,ranges=None,include_structural=False,_index=None,_cache_path=None):
    text(query,'query',8192);integer(top,1,100,'top');integer(hops,0,6,'hops');integer(iterations,1,100,'iterations')
    integer(max_visits,1,1000000,'max_visits')
    require(type(include_structural) is bool,'include_structural must be boolean')
    nodes={n['id']:n for n in snapshot['nodes'] if n.get('space')!='research' and (spaces is None or n.get('space') in spaces)}
    words=terms(query);require(words,'query needs searchable terms')
    rows=[{'id':k,'title':n['label'],'text':n.get('text',''),'locator':n.get('locator',''),
           'metadata':{**n['data'],'type':n['type'],'space':n['space']},
           'fields':{'identifiers':' '.join(x['value'] for x in n.get('identifiers',[]))}} for k,n in nodes.items()
           if include_structural or n['type'] not in ('source','concept')]
    index,cache_hit=cached_index(rows,_cache_path) if _index is None else (_index,True)
    if where or ranges:
        eligible={n['id'] for n in index.documents(where,ranges)};nodes={k:n for k,n in nodes.items() if k in eligible}
    lexical_result=index.search(query,{'k':1000,'where':where or {},'ranges':ranges or {},'explain':False})
    scores={h['id']:h['score'] for h in lexical_result['hits']};exact=[]
    for ident,n in nodes.items():
        if query==ident or any(query==x['value'] for x in n.get('identifiers',[])):
            exact.append(ident);scores[ident]=scores.get(ident,0)+8
    lexical=sorted(scores,key=lambda k:(-scores[k],k));seed=defaultdict(float)
    for rank,k in enumerate(lexical[:100],1):seed[k]+=1/(60+rank)
    semantic_state='NOT_REQUESTED'
    if semantic is not None:
        require(isinstance(semantic,dict) and semantic.get('graph_hash')==snapshot['graph_hash'] and semantic.get('query')==query,'semantic rankings must align to this graph snapshot and exact query')
        ids=semantic.get('ids');require(isinstance(ids,list) and len(ids)<=1000 and len(ids)==len(set(ids)) and all(k in nodes for k in ids),'invalid semantic ranked ids')
        for rank,k in enumerate(ids,1):seed[k]+=1/(60+rank)
        semantic_state='ALIGNED_EXTERNAL_RANKINGS'
    if not seed:return result(hits=[],query=query,graph_hash=snapshot['graph_hash'],semantic_state=semantic_state,coverage='no seeds in this bounded snapshot')
    adjacency=defaultdict(list);edge_byid={};visits=0;partial=False
    for e in snapshot['edges']:
        if e['subject'] not in nodes or e['object'] not in nodes:continue
        if e['status']=='retired' or not include_candidates and e['status'] in ('candidate','hypothesis','proposed'):continue
        adjacency[e['subject']].append((e['object'],e));adjacency[e['object']].append((e['subject'],e));edge_byid[e['id']]=e
    selected=set(seed);trace={k:[] for k in seed};root={k:k for k in seed};queue=deque((k,0) for k in sorted(seed,key=lambda k:(-seed[k],k)))
    while queue and visits<max_visits:
        at,depth=queue.popleft()
        if depth>=hops:continue
        for to,e in sorted(adjacency[at],key=lambda x:(x[0],x[1]['id'])):
            if visits>=max_visits:partial=True;break
            visits+=1
            if to not in selected:
                selected.add(to);trace[to]=trace[at]+[e['id']];root[to]=root[at];queue.append((to,depth+1))
    if queue:partial=True
    z=sum(seed.values());personal={k:seed.get(k,0)/z for k in selected};rank=dict(personal)
    # Personalized diffusion is a retrieval score, never a probability of source truth.
    for _ in range(iterations if hops else 0):
        new={k:.2*personal[k] for k in selected}
        dangling=0.0
        for k in sorted(selected):
            neighbors=[n for n,e in adjacency[k] if n in selected]
            if neighbors:
                for n in neighbors:
                    if visits>=max_visits:partial=True;break
                    visits+=1
                    new[n]+=.8*rank[k]/len(neighbors)
            else:
                dangling+=rank[k]
            if partial:break
        if partial:break
        for n in selected:new[n]+=.8*dangling*personal[n]
        rank=new
    # Reserve exact identifiers, then diversify originating sources within a small relevance penalty.
    hard_clauses=[c for c in lexical_result['clauses'] if c['kind'] in ('require','exclude') or c['phrase']]
    def query_constraints(k):
        if not hard_clauses:return True
        n=nodes[k];fs=[tokens(n['label']),tokens(n.get('text','')),tokens(k),tokens(n.get('locator','')),
                        tokens(' '.join(x['value'] for x in n.get('identifiers',[])))]
        for clause in hard_clauses:
            matches=any(has_phrase(ts,clause['tokens']) if clause['phrase'] else all(t in ts for t in clause['tokens']) for ts in fs)
            if clause['kind']=='exclude' and matches or (clause['kind']=='require' or clause['phrase']) and clause['kind']!='exclude' and not matches:return False
        return True
    def evidence(k):return include_structural or nodes[k]['type'] not in ('source','concept') or k in exact
    # Preserve direct evidence relevance. Graph-only neighbors supplement it;
    # lexical concept hubs never displace a matching source passage.
    def ordering(k):
        return (k not in exact,k not in seed if semantic is not None else k not in scores,
                -seed.get(k,0) if semantic is not None else -scores.get(k,0),-rank.get(k,0),k)
    remaining=sorted((k for k in selected if evidence(k) and query_constraints(k)),key=ordering)
    chosen=[];origins=Counter()
    while remaining and len(chosen)<top:
        k=min(remaining,key=ordering)
        remaining.remove(k);chosen.append(k);origins[nodes[k].get('origin',k)]+=1
    hits=[]
    for k in chosen:
        n=nodes[k];edge_path=trace.get(k,[]);sources=set(n.get('source_ids',[]))
        for eid in edge_path:sources.update(edge_byid[eid]['source_ids'])
        hits.append({'id':k,'type':n['type'],'label':n['label'],'text':n.get('text','')[:2000],
                     'content_hash':n['content_hash'],'locator':n.get('locator'),
                     'source_ids':sorted(sources),'source_origins':sorted({nodes[s].get('origin',s) for s in sources if s in nodes}),
                     'lexical_score':scores.get(k,0),'graph_score':rank.get(k,0),'seed':root.get(k,k),
                     'edge_path':edge_path,'predicates':[edge_byid[e]['predicate'] for e in edge_path]})
    return result('PARTIAL' if partial or snapshot['state']!='COMPLETE' else 'COMPLETE_WITHIN_SNAPSHOT',
                  hits=hits,query=query,graph_hash=snapshot['graph_hash'],projection_hash=snapshot['projection_hash'],
                  work=visits,semantic_state=semantic_state,candidate_edges_included=include_candidates,
                  lexical_index_hash=index.manifest()['index_hash'],
                  index_cache_hit=cache_hit,ranking_policy='direct-evidence-first-v2',
                  related_concepts=[{'id':k,'label':nodes[k]['label']} for k in sorted(selected) if nodes[k]['type']=='concept'][:20],
                  boundary='Lexical/identifier and source-backed graph relevance; top-k and bounded paths are not exhaustive retrieval or factual confidence.')
