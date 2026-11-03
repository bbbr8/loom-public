"""Research orchestration and bounded automatic source/concept/link evolution."""
from collections import Counter, defaultdict
from copy import deepcopy
from itertools import combinations
import hashlib
from pathlib import Path
from .contracts import (canonical, digest, fields, normalize_node, normalize_edge, read_json,
                        require, result, text, integer, MAX_BATCH, ResearchConflict)
from .store import GraphStore
from .identity import resolve, keyset
from .retrieval import search, terms
from .analytics import analyze
from .compiler import compile_graph
from .planner import plan
from .learning import clean_node
from loom_qulap.model import solve

STOP={'that','this','with','from','were','have','been','will','their','which','there','about','would','could','should','synthetic','source','record','said','into','than','also','after','before'}


def records_batch(batch_id,records):
    require(isinstance(records,list) and len(records)<=1000,'research intake accepts at most 1000 explicit records')
    nodes=[]
    for r in records:
        fields(r,('id','origin','locator','text'),('revision','label','type','clocks','identifiers','data','native_sha256'))
        ident=text(r['id']);require(not ident.startswith(('src:','auto:')),'record id uses a reserved generated prefix')
        source='src:'+ident;revision=r.get('revision',1);label=r.get('label',ident)
        data=deepcopy(r.get('data',{}));require(isinstance(data,dict),'record data must be an object')
        source_data={'extracted_snapshot_hash':digest({'text':r['text'],'data':data}),
                     'native_sha256_supplied':r.get('native_sha256'),'native_bytes_verified':False}
        nodes.append({'id':source,'type':'source','label':'Source: '+label[:1900],
                      'origin':r['origin'],'locator':r['locator'],'revision':revision,'status':'observed',
                      'clocks':r.get('clocks',{}),'data':source_data})
        nodes.append({'id':ident,'type':r.get('type','passage'),'label':label,'text':r['text'],
                      'origin':r['origin'],'locator':r['locator'],'revision':revision,'source_ids':[source],
                      'clocks':r.get('clocks',{}),'identifiers':r.get('identifiers',[]),'data':data})
    return {'schema':'loom.graph.batch.v1','batch_id':batch_id,'nodes':nodes}


def _clean_item(item,kind):
    out=clean_node(item)
    return normalize_node(out) if kind=='node' else normalize_edge(out)


def _evidence_basis(snapshot):
    return digest([[n['id'],n['content_hash']] for n in snapshot['nodes']
                   if n['active'] and not n.get('data',{}).get('auto_evolved') and n['space']!='research']+
                  [[e['id'],e['content_hash']] for e in snapshot['edges']
                   if e['active'] and not e.get('data',{}).get('auto_evolved') and e['space']!='research'])


def evolve(store,max_links=2000,max_concepts=1000,max_pairs=20000,continuation=None,max_batches=1,seconds=10):
    """Apply resumable pages of deterministic source/lexical/identity candidates.

    max_links/max_concepts are page sizes, rather than permanent truncation.
    Source corrections invalidate a continuation. Capacity and pair exhaustion
    remain explicit; each committed page is independently journaled.
    """
    import time
    from .contracts import MAX_NODES,MAX_EDGES
    integer(max_links,1,4096,'max_links');integer(max_concepts,1,2000,'max_concepts')
    integer(max_pairs,1,100000,'max_pairs');integer(max_batches,1,64,'max_batches')
    require(type(seconds) in (int,float) and 0<seconds<=120,'evolution seconds must be in (0,120]')
    started=time.monotonic();snap=store.snapshot(include_stale=True,include_inactive=True)
    active=[n for n in snap['nodes'] if n['active'] and n['effective_status']!='stale' and n['space']!='research'
            and n['type'] in ('passage','claim','entity','instrument','event') and not n.get('data',{}).get('auto_evolved')]
    core=[n for n in snap['nodes'] if n['active'] and not n.get('data',{}).get('auto_evolved') and n['space']!='research']
    core_edges=[e for e in snap['edges'] if e['active'] and not e.get('data',{}).get('auto_evolved') and e['space']!='research']
    basis=_evidence_basis(snap)
    require(continuation is None or continuation==basis,'evolution continuation is stale; restart against current evidence')
    byid={n['id']:n for n in active};sources={n['id']:n for n in core if n['type']=='source'}
    old={('node',n['id']):n for n in snap['nodes']};old.update({('edge',e['id']):e for e in snap['edges']})
    wanted={};concepts=defaultdict(list);pair_checks=0;links=[];limits=[]
    for n in active:
        for sid in n['source_ids']:
            links.append({'id':'auto:source:'+digest([n['id'],sid])[:24],'subject':n['id'],'predicate':'derived_from','object':sid,
                          'status':'derived','source_ids':[sid],'clocks':n['clocks'],
                          'data':{'auto_evolved':True,'interpretation':'supplied source provenance'}})
        counts=Counter(t for t in terms(n['label']+' '+n.get('text','')) if len(t)>=4 and t not in STOP)
        for token,count in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:4]:concepts[token].append(n)
    node_capacity=MAX_NODES-sum(n['active'] and not n.get('data',{}).get('auto_evolved') for n in snap['nodes'])
    edge_capacity=MAX_EDGES-len(core_edges)
    # Source-reference shards preserve every member without truncating at 1000 origins.
    for token,group in sorted(concepts.items(),key=lambda x:(-len(x[1]),x[0])):
        group=sorted(group,key=lambda n:n['id'])
        for shard,start in enumerate(range(0,len(group),250)):
            if len(wanted)>=node_capacity:limits.append('graph_node_capacity');break
            members=group[start:start+250];src=sorted({sid for n in members for sid in n['source_ids'] if sid in sources})
            # A record can cite many sources; retain a bounded lexical node with
            # provenance on each member edge rather than manufacturing independence.
            cid='auto:concept:'+digest([token,shard])[:24]
            wanted['node',cid]={'id':cid,'type':'concept','label':token,'status':'derived','source_ids':src[:1000],
                                'data':{'auto_evolved':True,'meaning':'lexical concept candidate','record_count':len(members),
                                        'source_refs_truncated':len(src)>1000,'shard':shard}}
            if len(src)>1000:limits.append('concept_source_reference_capacity')
            for n in members:
                links.append({'id':'auto:mentions:'+digest([n['id'],cid])[:24],'subject':n['id'],'predicate':'mentions','object':cid,
                              'status':'derived' if n['source_ids'] else 'candidate','source_ids':n['source_ids'],'clocks':n['clocks'],
                              'data':{'auto_evolved':True,'interpretation':'token mention, not a semantic assertion'}})
        if len(wanted)>=node_capacity:break
    if len(wanted)<sum((len(group)+249)//250 for group in concepts.values()):limits.append('graph_node_capacity')
    blocks=defaultdict(list)
    for n in active:
        for key in keyset(n):blocks[key].append(n)
    tested=set()
    for key,group in sorted(blocks.items()):
        for a,b in combinations(sorted(group,key=lambda n:n['id']),2):
            pair=(a['id'],b['id'])
            if pair in tested:continue
            if pair_checks>=max_pairs:limits.append('identifier_pair_budget');break
            tested.add(pair);pair_checks+=1
            pair_sources=sorted(set(a['source_ids']+b['source_ids']))
            if len(pair_sources)>1000:limits.append('identity_source_reference_capacity')
            links.append({'id':'auto:identifier:'+digest(pair)[:24],'subject':pair[0],'predicate':'same_identifier','object':pair[1],
                          'status':'candidate','source_ids':pair_sources[:1000],
                          'data':{'auto_evolved':True,'identifier':list(key),'identity_established':False,'source_refs_truncated':len(pair_sources)>1000}})
        if pair_checks>=max_pairs:break
    identities=resolve({'nodes':active,'edges':snap['edges']},max_pairs=min(max_pairs,100000))
    if identities['state']=='PARTIAL':limits.append('identity_pair_budget')
    for pair in identities['pairs']:
        if pair['state'] not in ('EXACT_DECLARED_KEY','MULTIFIELD_CANDIDATE'):continue
        a,b=byid[pair['left']],byid[pair['right']]
        pair_sources=sorted(set(a['source_ids']+b['source_ids']))
        if len(pair_sources)>1000:limits.append('identity_source_reference_capacity')
        links.append({'id':'auto:identity:'+digest([a['id'],b['id']])[:24],'subject':a['id'],'predicate':'same_entity','object':b['id'],
                      'status':'candidate','source_ids':pair_sources[:1000],
                      'data':{'auto_evolved':True,'comparison':pair,'reviewed':False,'source_refs_truncated':len(pair_sources)>1000}})
    if len(links)>edge_capacity:limits.append('graph_edge_capacity')
    # Provenance precedes lexical and identity links at a hard graph limit.
    links.sort(key=lambda e:(e['predicate']!='derived_from',e['id']))
    for edge in links[:edge_capacity]:wanted['edge',edge['id']]=edge
    total_wanted_nodes=sum(k[0]=='node' for k in wanted);total_wanted_edges=sum(k[0]=='edge' for k in wanted)
    commits=[];remaining_nodes=remaining_edges=0
    for page in range(max_batches):
        if page:
            snap=store.snapshot(include_stale=True,include_inactive=True)
            if _evidence_basis(snap)!=basis:
                limits.append('evidence_changed_restart_required');break
            old={('node',n['id']):n for n in snap['nodes']};old.update({('edge',e['id']):e for e in snap['edges']})
        changes={'nodes':[],'edges':[],'remove_nodes':[],'remove_edges':[]};changed_nodes=set()
        pending_nodes=[];pending_edges=[]
        for key,item in sorted(wanted.items(),key=lambda kv:(kv[0][0]!='node',kv[0][1])):
            previous=old.get(key);item=deepcopy(item);item['revision']=previous['revision'] if previous else 1
            norm=_clean_item(item,key[0]);endpoint_changed=key[0]=='edge' and (item['subject'] in changed_nodes or item['object'] in changed_nodes or bool(set(item['source_ids'])&changed_nodes))
            if previous and previous['active'] and previous['effective_status']!='stale' and not endpoint_changed and norm==_clean_item(previous,key[0]):continue
            if previous:item['revision']=previous['revision']+1
            if key[0]=='node':pending_nodes.append(item);changed_nodes.add(key[1])
            else:pending_edges.append(item)
        obsolete_nodes=[];obsolete_edges=[]
        # Pair/capacity-limited plans preserve earlier candidate links and declare coverage.
        if not limits:
            for key,item in sorted(old.items()):
                if item.get('active') and item.get('data',{}).get('auto_evolved') and key not in wanted:
                    (obsolete_nodes if key[0]=='node' else obsolete_edges).append({'id':key[1],'revision':item['revision']+1})
        changes['remove_nodes']=obsolete_nodes[:max_concepts];changes['remove_edges']=obsolete_edges[:max_links]
        # Remove obsolete pages first, to free capacity before installing replacements.
        if not obsolete_nodes and not obsolete_edges:
            changes['nodes']=pending_nodes[:max_concepts]
            ready={k[1] for k,n in old.items() if k[0]=='node' and n.get('active')}|{n['id'] for n in changes['nodes']}
            changes['edges']=[e for e in pending_edges if e['subject'] in ready and e['object'] in ready][:max_links]
        remaining_nodes=len(pending_nodes)+len(obsolete_nodes)-len(changes['nodes'])-len(changes['remove_nodes'])
        remaining_edges=len(pending_edges)+len(obsolete_edges)-len(changes['edges'])-len(changes['remove_edges'])
        if not any(changes.values()):break
        try:commit=store.apply({'schema':'loom.graph.batch.v1','batch_id':'evolve:'+digest([basis,snap['projection_hash'],changes]),**changes},expected_revision=snap['revision'])
        except ResearchConflict:
            limits.append('graph_changed_restart_required')
            remaining_nodes+=len(changes['nodes']);remaining_edges+=len(changes['edges']);break
        except ResearchError as exc:
            if 'capacity' not in str(exc):raise
            limits.append(str(exc));remaining_nodes+=len(changes['nodes']);remaining_edges+=len(changes['edges']);break
        commits.append(commit)
        if not remaining_nodes and not remaining_edges or time.monotonic()-started>=seconds:break
    complete=not remaining_nodes and not remaining_edges and not limits
    return result(('COMPLETE' if commits else 'IDEMPOTENT') if complete else 'PARTIAL',
                  revision=commits[-1]['revision'] if commits else snap['revision'],
                  commit=commits[-1] if commits else None,commits=commits,concepts=total_wanted_nodes,links=total_wanted_edges,
                  pair_checks=pair_checks,evolution_complete=complete,continuation=None if complete or any('restart_required' in x for x in limits) else basis,
                  replan_required=any('restart_required' in x for x in limits),
                  remaining={'nodes':remaining_nodes,'edges':remaining_edges},limits=sorted(set(limits)),
                  pages_committed=len(commits),seconds=time.monotonic()-started,
                  boundary='Resumable lexical/source-identifier/identity candidates; explicit graph, pair and time budgets. Raw facts and reviewed identities retain their own state.')


def ingest(store,packet,evolution=True,expected_revision=None):
    commit=store.apply(packet,expected_revision=expected_revision)
    return result(commit=commit,evolution=evolve(store) if evolution else None)


def import_timeline(store,report,batch_id):
    require(report.get('schema')=='loom.timeline.v1','expected a Loom timeline result')
    records=[]
    for e in report['events']:
        t=e['t'];ident='timeline:'+digest([e.get('path'),e.get('where'),e.get('mech'),t,e.get('text')])[:24]
        records.append({'id':ident,'origin':e.get('source_sha256') or e['path'],'locator':e.get('where') or e['path'],
                        'text':e['text'],'type':'event','label':e['kind']+': '+e['mech'],
                        'clocks':{'event':[t,t]},'data':{'timeline_event':e,'date_role':e.get('date_role'),
                                                       'upstream_coverage':report.get('state')}})
    require(len(records)<=1000,'timeline intake exceeds graph batch; narrow its window/row limit')
    return ingest(store,records_batch(batch_id,records))


def import_files(store,manifest):
    fields(manifest,('schema','id','files'),())
    require(manifest['schema']=='loom.graph.files.v1','expected loom.graph.files.v1')
    require(isinstance(manifest['files'],list) and 1<=len(manifest['files'])<=100,'select 1..100 explicit UTF-8 files')
    existing={n['id']:n for n in store.snapshot(include_stale=True,include_inactive=True)['nodes']}
    records=[];hashes={};total=0
    for item in manifest['files']:
        if isinstance(item,str):item={'path':item}
        fields(item,('path',),('id','origin','clocks','data'))
        path=Path(item['path']).expanduser();require(path.is_absolute() and path.is_file(),'file intake needs explicit existing absolute paths')
        require(path.stat().st_size<=1024*1024,'file exceeds 1 MiB; extract a bounded source passage first')
        raw=path.read_bytes();total+=len(raw);require(total<=16*1024*1024,'file intake exceeds 16 MiB')
        content=raw.decode('utf-8');require(len(content)<=100000,'source text exceeds 100000 characters; narrow the extract')
        ident=item.get('id','file:'+digest(str(path.resolve()))[:24]);sha=hashlib.sha256(raw).hexdigest();hashes[ident]=sha
        prior=existing.get(ident);revision=prior['revision'] if prior else 1
        data={**item.get('data',{}),'file_sha256':sha,'file_bytes':len(raw),'extraction':'UTF-8 byte decoding'}
        r={'id':ident,'origin':item.get('origin','file:'+str(path.resolve())),'locator':str(path.resolve()),
           'text':content,'label':path.name,'revision':revision,'clocks':item.get('clocks',{}),'data':data}
        if prior:
            hypothetical=records_batch('compare',[r])['nodes'][1]
            if not prior['active'] or normalize_node(hypothetical)!=normalize_node(clean_node(prior)):
                r['revision']=revision+1
        records.append(r)
    require(len({r['id'] for r in records})==len(records),'duplicate file ids')
    packet=records_batch('files:'+digest([manifest['id'],records]),records)
    for n in packet['nodes']:
        if n['type']=='source':
            rid=n['id'][4:];n['data']['native_sha256_computed']=hashes[rid];n['data']['native_bytes_verified']=True
    return {**ingest(store,packet),'file_sha256':hashes,'source_scope':'Bytes actually read from explicit local UTF-8 files; no recursive crawl or source authenticity conclusion.'}


def run(store,packet):
    fields(packet,('schema','id'),('records','batch','questions','scope','model','plan','invariants','resolve','analyze','snapshot'))
    require(packet['schema']=='loom.research.run.v1','expected loom.research.run.v1')
    from .retrieval import policy_hash as current_policy_hash
    policy_hash=current_policy_hash()
    ident=text(packet['id']);input_hash=digest(packet)
    if 'records' in packet:
        require('batch' not in packet,'supply records or batch, not both')
        intake=ingest(store,records_batch('run-intake:'+digest([ident,packet['records']]),packet['records']))
    elif 'batch' in packet:intake=ingest(store,packet['batch'])
    else:intake={'evolution':evolve(store)}
    snapshot_opts=packet.get('snapshot',{});require(isinstance(snapshot_opts,dict),'snapshot options must be an object')
    read_revision=store.head()[0]
    snap=store.snapshot(**snapshot_opts)
    method=next((n for n in store.snapshot()['nodes'] if n['type']=='method' and n['status']=='active'
                 and n['data'].get('policy_hash')==policy_hash and n['data'].get('scope')==packet.get('scope')),None)
    previous=next((n for n in store.snapshot(include_stale=True)['nodes'] if n['id']=='run:'+ident),None)
    method_hash=method['content_hash'] if method else None
    if previous and previous['data'].get('policy_hash')==policy_hash and previous['data'].get('input_hash')==input_hash and previous['data'].get('basis_projection')==snap['projection_hash'] and previous['data'].get('method_hash')==method_hash:
        return {**previous['data']['report'],'cached':True}
    questions=packet.get('questions',[]);require(isinstance(questions,list) and len(questions)<=16,'at most 16 research questions per run')
    searches=[];saved=[];dependencies=[];gaps=[]
    for index,q in enumerate(questions):
        if isinstance(q,str):q={'id':str(index+1),'query':q}
        fields(q,('id','query'),('options',));opts=deepcopy(method['data']['params']) if method else {}
        opts.update(q.get('options',{}));search_result=search(snap,q['query'],**opts);searches.append({'id':q['id'],**search_result})
        qid='search:'+digest([ident,q['id']])[:24]
        previous_search=next((n for n in store.snapshot(include_stale=True)['nodes'] if n['id']==qid),None)
        used=[h['id'] for h in search_result['hits']]
        saved.append({'id':qid,'type':'search','revision':previous_search['revision']+1 if previous_search else 1,
                      'label':q['query'][:2000],'space':'research','status':'derived',
                      'data':{'result':search_result,'basis_projection':snap['projection_hash'],'method':method['id'] if method else None},
                      'depends_on':[{'kind':'node','id':k} for k in used]})
        dependencies.append({'kind':'node','id':qid})
        if not used:gaps.append({'question':q['id'],'state':'OPEN_RETRIEVAL','next_action':'expand identifiers/terminology or inspect missing source coverage'})
    diagnostics=analyze(snap,invariants=packet.get('invariants')) if packet.get('analyze',True) else None
    identity=resolve(snap) if packet.get('resolve',True) else None
    compiled=compiled_model=solved=planning=None
    if 'model' in packet:
        spec=packet['model']
        if spec.get('schema')=='qulap.model.v1':compiled_model=spec
        else:
            compiled=compile_graph(snap,spec);compiled_model=compiled.get('model')
            if compiled['state']=='OPEN_INPUT':gaps+=compiled['gaps']
        if compiled_model is not None:
            solved=solve(compiled_model)
            if 'plan' in packet:planning=plan(compiled_model,**packet['plan'])
    state='COMPLETE'
    if snap['state']!='COMPLETE' or gaps or diagnostics and diagnostics['state']=='PARTIAL' or identity and identity['state']=='PARTIAL':state='PARTIAL'
    if intake.get('evolution',{}).get('evolution_complete') is False:state='PARTIAL'
    if any(s['state']=='PARTIAL' for s in searches) or planning and planning['state'] in ('PARTIAL','INCOMPLETE') or solved and not solved['complete']:state='PARTIAL'
    report=result(state,run_id=ident,input_hash=input_hash,graph_revision=snap['revision'],basis_projection=snap['projection_hash'],
                  intake=intake,searches=searches,analytics=diagnostics,identity=identity,compiled=compiled,
                  solver=solved,planning=planning,open_questions=gaps,method_used=method['id'] if method else None)
    saved.append({'id':'run:'+ident,'type':'analysis','revision':previous['revision']+1 if previous else 1,
                  'label':'Research: '+ident,'space':'research','status':'derived',
                  'data':{'input_hash':input_hash,'basis_projection':snap['projection_hash'],'method_hash':method_hash,'policy_hash':policy_hash,'report':report},'depends_on':dependencies})
    commit=store.apply({'schema':'loom.graph.batch.v1','batch_id':'run-save:'+digest([ident,input_hash,snap['projection_hash'],method_hash,policy_hash]),'nodes':saved},expected_revision=read_revision)
    return {**report,'commit':commit,'cached':False}
