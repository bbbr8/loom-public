"""Measured retrieval recipes and versioned relation schemas, with executable gates."""
from copy import deepcopy
import math
from .contracts import TYPES, PREDICATES, digest, fields, normalize_edge, normalize_node, require, result, text
from .retrieval import search,policy_hash
from .store import GraphStore

PARAMS={'top','hops','iterations','max_visits','include_candidates','spaces','where','ranges','include_structural'}


def clean_node(node):
    return {k:deepcopy(v) for k,v in node.items() if k not in ('_deps','content_hash','active','stale_reasons','effective_status','temporal_state')}


def upsert_node(store,node,batch_id,validated_method=False,expected_projection=None):
    snapshot=store.snapshot(include_stale=True,include_inactive=True)
    if expected_projection is not None and snapshot['projection_hash']!=expected_projection:
        from .contracts import ResearchConflict
        raise ResearchConflict('graph_projection_conflict: evidence changed; rerun evaluation')
    previous=next((n for n in snapshot['nodes'] if n['id']==node['id']),None)
    if previous and previous['active']:
        node['revision']=previous['revision']
        if normalize_node(node)==normalize_node(clean_node(previous)):
            return result('IDEMPOTENT',revision=store.snapshot()['revision'],changed=[])
    node['revision']=previous['revision']+1 if previous else 1
    return store.apply({'schema':'loom.graph.batch.v1','batch_id':batch_id,'nodes':[node]},
                       _validated_methods=[node['id']] if validated_method else [],expected_revision=snapshot['revision'],
                       expected_projection=expected_projection)


def shape_valid(shape):
    fields(shape,('subjects','objects'),('require_sources',))
    for key in ('subjects','objects'):
        require(isinstance(shape[key],list) and shape[key] and set(shape[key])<=TYPES,'shape kinds must be declared graph node types')
    require(type(shape.get('require_sources',True)) is bool,'require_sources must be boolean')
    return shape


def test_predicate(node,nodes):
    shape=shape_valid(node['data']['shape']);name=text(node['data']['name'],'predicate name')
    trials=node['data'].get('activation',{}).get('trials',[])
    require(isinstance(trials,list) and len(trials)>=3 and len(trials)<=12,'activation needs 3..12 executable schema trials')
    require({'original','transfer','adversarial'}<=set(t.get('context') for t in trials),'original/transfer/adversarial trials required')
    require(len({digest(t.get('edge')) for t in trials})==len(trials),'trials must have distinct edge inputs')
    require(sum(t.get('expected_valid') is False for t in trials)>=1 and sum(t.get('expected_valid') is True for t in trials)>=2,'activation needs positive transfers and a negative control')
    results=[]
    for t in trials:
        fields(t,('context','edge','expected_valid'),('context_id',))
        require(type(t['expected_valid']) is bool,'expected_valid must be boolean')
        try:
            edge=normalize_edge(t['edge']);require(edge['predicate']==name,'trial predicate mismatch')
            require(all(s in nodes and nodes[s]['type']=='source' for s in edge['source_ids']),'trial source unavailable')
            GraphStore.validate_edge(edge,nodes,{name:shape});actual=True;diagnostic=None
        except (ValueError,TypeError,KeyError) as exc:actual=False;diagnostic=str(exc)
        results.append({'context':t['context'],'input_hash':digest(t['edge']),'expected_valid':t['expected_valid'],
                        'actual_valid':actual,'passed':actual==t['expected_valid'],'diagnostic':diagnostic})
    return results


def propose_predicate(store,packet):
    fields(packet,('id','name','shape','description'),())
    require(packet['name'] not in PREDICATES,'built-in predicates are immutable')
    shape_valid(packet['shape'])
    node={'id':'predicate:'+text(packet['id']),'type':'predicate','label':text(packet['description']),
          'status':'proposed','space':'research','data':{'name':text(packet['name']),'shape':packet['shape']}}
    commit=upsert_node(store,node,'predicate-propose:'+digest(packet))
    n=next(n for n in store.snapshot()['nodes'] if n['id']==node['id'])
    return result('PROPOSED',node_id=node['id'],candidate_hash=n['content_hash'],commit=commit)


def activate_predicate(store,packet):
    fields(packet,('id','candidate_hash','trials'))
    snapshot=store.snapshot(include_stale=True);nodes={n['id']:n for n in snapshot['nodes']}
    ident='predicate:'+packet['id'];require(ident in nodes and nodes[ident]['type']=='predicate','predicate candidate unavailable')
    n=clean_node(nodes[ident]);require(nodes[ident]['content_hash']==packet['candidate_hash'],'predicate candidate changed; rerun trials against current version')
    n['data']['activation']={'trials':packet['trials']};checks=test_predicate(n,nodes)
    passed=all(c['passed'] for c in checks)
    n['status']='active' if passed else 'held';n['data']['checks']=checks
    n['data']['validation_scope']='Executable structural schema tests; semantic validity remains a supplied relation interpretation.'
    commit=upsert_node(store,n,'predicate-activate:'+digest(packet),expected_projection=snapshot['projection_hash'])
    return result('ACTIVE' if passed else 'HELD',checks=checks,commit=commit,predicate=n['data']['name'])


def metric(hits,relevant,chain,top):
    ids=[h['id'] for h in hits[:top]];ideal=sum(1/math.log2(i+2) for i in range(min(len(relevant),top)))
    dcg=sum(1/math.log2(i+2) for i,k in enumerate(ids) if k in relevant)
    return {'ndcg':dcg/ideal if ideal else None,'recall':len(set(ids)&set(relevant))/len(relevant) if relevant else None,
            'chain_coverage':len(set(ids)&set(chain))/len(chain) if chain else None,'hits':ids}


def evaluate(store,packet):
    fields(packet,('id','scope','queries','candidate'),('baseline','label_author','method_id','activate'))
    require(isinstance(packet['queries'],list) and 3<=len(packet['queries'])<=200,'evaluation needs 3..200 frozen queries')
    require(isinstance(packet['candidate'],dict) and set(packet['candidate'])<=PARAMS,'unknown recipe parameters')
    baseline=packet.get('baseline',{'hops':0});require(isinstance(baseline,dict) and set(baseline)<=PARAMS,'invalid baseline recipe')
    require(baseline.get('top',10)==packet['candidate'].get('top',10),'baseline/candidate need the same evaluation cutoff')
    snap=store.snapshot();available={n['id'] for n in snap['nodes']};definition={k:packet[k] for k in ('scope','queries')}
    bench_id='benchmark:'+text(packet['id']);dataset_hash=digest(definition)
    existing=next((n for n in store.snapshot(include_stale=True)['nodes'] if n['id']==bench_id),None)
    require(existing is None or existing['data'].get('dataset_hash')==dataset_hash,'frozen benchmark id already has different labels; use a new benchmark id')
    if not existing:
        upsert_node(store,{'id':bench_id,'type':'benchmark','label':'Frozen research benchmark '+packet['id'],'space':'research',
                          'status':'observed','data':{'definition':definition,'dataset_hash':dataset_hash,'label_author':packet.get('label_author','unknown')}},'benchmark:'+digest([bench_id,dataset_hash]),expected_projection=snap['projection_hash'])
    checks=[];contexts=set();queryids=[]
    for q in packet['queries']:
        fields(q,('id','query','relevant','context'),('chain','answerable'))
        require(q['context'] in ('original','transfer','adversarial'),'unknown evaluation context')
        contexts.add(q['context']);queryids.append(q['id'])
        require(isinstance(q['relevant'],list) and set(q['relevant'])<=available,'relevance labels reference unavailable nodes')
        chain=q.get('chain',[]);require(isinstance(chain,list) and set(chain)<=available,'chain labels reference unavailable nodes')
        if not q['relevant']:require(q.get('answerable') is False,'empty relevance labels need explicit answerable:false')
        a=search(snap,q['query'],**baseline);b=search(snap,q['query'],**packet['candidate'])
        am=metric(a['hits'],q['relevant'],chain,baseline.get('top',10));bm=metric(b['hits'],q['relevant'],chain,packet['candidate'].get('top',10))
        complete=a['state']!='PARTIAL' and b['state']!='PARTIAL'
        passed=complete and all(bm[k] is None or am[k] is not None and bm[k]+1e-12>=am[k] for k in ('ndcg','recall','chain_coverage'))
        if q.get('answerable') is False:passed=complete and len(b['hits'])<=len(a['hits'])
        checks.append({'id':q['id'],'context':q['context'],'baseline':am,'candidate':bm,'passed':passed,'complete':complete})
    require(len(queryids)==len(set(queryids)),'duplicate evaluation query IDs')
    passed=contexts=={'original','transfer','adversarial'} and all(c['passed'] for c in checks)
    evaluation=result('PASSED' if passed else 'HELD',checks=checks,dataset_hash=dataset_hash,
                      policy_hash=policy_hash(),
                      evidence_graph_hash=snap['projection_hash'],basis_projection=snap['projection_hash'],
                      label_author=packet.get('label_author','unknown'),label_independence_verified=False)
    method_id=packet.get('method_id',packet['id']);rid='evaluation:'+digest([bench_id,dataset_hash,baseline,packet['candidate'],packet.get('label_author'),snap['projection_hash'],policy_hash()])[:24]
    upsert_node(store,{'id':rid,'type':'analysis','label':'Retrieval evaluation '+packet['id'],'space':'research','status':'derived',
                      'data':{'evaluation':evaluation,'basis_projection':snap['projection_hash']},'depends_on':[{'kind':'node','id':bench_id}]},rid,expected_projection=snap['projection_hash'])
    activated=False
    if packet.get('activate',False):
        ident='method:'+text(method_id);old=next((n for n in store.snapshot(include_stale=True)['nodes'] if n['id']==ident),None)
        target=ident if passed else 'method-candidate:'+digest([ident,rid])[:24]
        upsert_node(store,{'id':target,'type':'method','label':'Research recipe '+method_id,'status':'active' if passed else 'held','space':'research',
                          'data':{'scope':packet['scope'],'params':packet['candidate'],'evaluation':rid,'dataset_hash':dataset_hash,'policy_hash':policy_hash(),
                                  'basis_projection':snap['projection_hash']},'depends_on':[{'kind':'node','id':rid}]},'recipe:'+digest([target,rid]),validated_method=passed,expected_projection=snap['projection_hash'])
        activated=passed
    return {**evaluation,'method_id':method_id,'activated':activated,'evaluation_id':rid}


def methods(snapshot,scope=None):
    return result(methods=[{'id':n['id'],'status':n['status'],'effective_status':n['effective_status'],**n['data'],
                           'validation_state':'CURRENT_POLICY' if n['data'].get('policy_hash')==policy_hash() else 'REVALIDATION_REQUIRED'}
                           for n in snapshot['nodes'] if n['type']=='method' and (scope is None or n['data'].get('scope')==scope)])
