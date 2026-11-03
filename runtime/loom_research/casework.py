"""Source-span case adapters, episode compilation and scoped lifecycle tests.

All mappings are named annotations over captured graph revisions. Native sources
remain immutable. No inferred receipt/approval, automatic finding promotion or
unqualified financial aggregation is performed.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from .contracts import (fields, require, result, digest, text, integer, rational,
                        clocks, canonical)
from .learning import clean_node
from .evidence import _span
from . import indexed

CONTEXT = ('actor', 'property', 'loan', 'account', 'instrument', 'scope', 'obligation')
KINDS = {'statement', 'transmission', 'finding', 'money', 'role', 'approval', 'receipt',
         'understanding', 'discovery', 'coverage', 'document_version'}
STAGES = ('requested', 'authorized', 'funded', 'received', 'paid', 'applied',
          'credited', 'refunded', 'claimed_loss')
KNOWLEDGE = {'visible_recipient', 'transmission_recorded', 'receipt_confirmed',
             'understanding_reported', 'acknowledged', 'scoped_approval', 'later_discovery'}
ALIGN = ('property', 'loan', 'scope', 'obligation', 'version', 'basis_clock', 'money_stage')


def map_records(store, packet):
    """Adapt explicitly supplied source-qualified findings/statements atomically."""
    fields(packet, ('batch_id', 'reviewer', 'mappings', 'expected_revision'))
    reviewer=text(packet['reviewer'], 'reviewer');snap=store.snapshot()
    require(snap['revision']==packet['expected_revision'], 'case mapping revision changed')
    raw=packet['mappings'];require(isinstance(raw,list) and 0<len(raw)<=200, 'map 1..200 annotations')
    existing={n['id']:n for n in snap['nodes']};nodes=[];edges=[]
    for m in raw:
        fields(m, ('id','episode','kind','evidence','context'),
               ('summary','clocks','knowledge','finding','claim','money','role','related_ids'))
        require(m['kind'] in KINDS, 'unknown case annotation kind')
        text(m['episode'],'episode');text(m['id'],'mapping id')
        require(isinstance(m['evidence'],list) and 1<=len(m['evidence'])<=20, 'mapping requires exact spans')
        spans=[_span(snap,s) for s in m['evidence']]
        context=m['context'];fields(context, (), CONTEXT+('version','basis_clock','money_stage'))
        for k,v in context.items():text(v,k)
        clock=clocks(m.get('clocks',{}))
        knowledge=m.get('knowledge',[])
        require(isinstance(knowledge,list) and len(knowledge)<=30,'bounded knowledge list required')
        for k in knowledge:
            fields(k,('actor','state'),('version','scope','at'))
            text(k['actor'],'knowledge actor');require(k['state'] in KNOWLEDGE,'unknown knowledge state')
            if 'at' in k:clocks({'event':k['at']})
            if k['state']=='scoped_approval':require(k.get('scope') and k.get('version'),'approval needs scope and version')
        finding=m.get('finding')
        if finding:
            fields(finding,('external_id','status','statement'),('reviewer','reviewed_at','register'))
            for k in ('external_id','status','statement'):text(finding[k],k,10000)
        if m.get('role'):
            fields(m['role'],('entity','capacity'),('authority_scope',))
            for k,v in m['role'].items():text(v,k)
        claim=m.get('claim')
        if claim:
            fields(claim,('key','stance'),())
            text(claim['key'],'claim key');require(claim['stance'] in ('support','oppose','unknown'),'invalid stance')
        money=m.get('money')
        if money:
            fields(money,('event_id','stage','value','unit'),('reversal_of',))
            require(money['stage'] in STAGES,'unknown money stage');rational(money['value'])
            text(money['event_id'],'money event');text(money['unit'],'money unit')
            require(all(context.get(k) for k in ('property','loan','instrument','scope','obligation')),
                    'money mappings require project/loan/instrument/scope/obligation')
            require(context.get('money_stage',money['stage'])==money['stage'],'money stage conflict')
            context={**context,'money_stage':money['stage']}
        ident='case:'+m['id'];old=existing.get(ident)
        data={'case_schema':'loom.case.v1','episode':m['episode'],'kind':m['kind'],
              'context':deepcopy(context),'reviewer':reviewer,'evidence_spans':spans,
              'knowledge':knowledge,'finding':finding,'claim':claim,'money':money,
              'role':m.get('role'),'summary':m.get('summary'),
              'truth_status':'NAMED_SOURCE_ANNOTATION','source_tier':'source_bound_annotation',
              **{k:v for k,v in context.items() if k in CONTEXT or k=='money_stage'}}
        if m.get('summary'):text(m['summary'],'summary',10000)
        node={'id':ident,'type':'claim' if m['kind']=='finding' else 'event',
              'label':(m.get('summary') or m['kind']+' · '+m['episode'])[:1900],
              'text':'\n'.join(s['quote'] for s in spans),'status':'annotated','space':'research',
              'source_ids':sorted({i for s in spans for i in s['source_ids']}),
              'clocks':clock,'data':data,
              'depends_on':[{'kind':'node','id':s['node_id']} for s in spans]}
        if old:
            # Content idempotency is independent of invocation/batch IDs.
            previous=clean_node(old);previous.pop('revision',None)
            from .contracts import normalize_node
            new=normalize_node(node);new.pop('revision',None)
            if canonical(previous)==canonical(new):continue
            node['revision']=old['revision']+1
        nodes.append(node)
        related=m.get('related_ids',[]);require(isinstance(related,list) and len(related)<=20,'bounded related IDs required')
        for target in related:
            require(target in existing,'related mapping must already exist')
            edges.append({'id':'case-link:'+digest([ident,target])[:32], 'subject':ident,
                          'predicate':'related_to','object':target,'status':'annotated',
                          'revision':node.get('revision',1),'source_ids':node['source_ids'],
                          'depends_on':node['depends_on'],
                          'data':{'meaning':'reviewer-declared episode connection','reviewer':reviewer}})
    if not nodes:return result('IDEMPOTENT',mapped=0)
    receipt=store.apply({'schema':'loom.graph.batch.v1','batch_id':packet['batch_id'],
                         'nodes':nodes,'edges':edges},expected_revision=snap['revision'])
    return result('CASE_MAPPED',mapped=len(nodes),commit=receipt)


def _selected(store, episode):
    text(episode,'episode');snap=store.snapshot(include_stale=True)
    all_nodes={n['id']:n for n in snap['nodes']}
    group=[n for n in snap['nodes'] if n['data'].get('case_schema')=='loom.case.v1'
           and n['data'].get('episode')==episode]
    require(bool(group),'episode unavailable; map source spans first')
    return snap,all_nodes,group


def compare(store, packet):
    """Compare reviewer-declared propositions on fully explicit coordinates."""
    fields(packet,('left','right'))
    nodes={n['id']:n for n in store.snapshot(include_stale=True)['nodes']}
    require(packet['left'] in nodes and packet['right'] in nodes,'comparison endpoint unavailable')
    a,b=(nodes[packet[k]] for k in ('left','right'))
    return _compare(a,b)


def _compare(a,b):
    require(a['id']!=b['id'],'comparison needs distinct records')
    require(all(n['data'].get('case_schema')=='loom.case.v1' for n in (a,b)),'case annotations required')
    if any(n['effective_status']=='stale' for n in (a,b)):
        return result('OPEN_SOURCE_REVISION',nodes=[a['id'],b['id']])
    ac,bc=a['data']['context'],b['data']['context']
    missing=[k for k in ALIGN if not ac.get(k) or not bc.get(k)]
    different=[k for k in ALIGN if ac.get(k) and bc.get(k) and ac[k]!=bc[k]]
    ca,cb=a['data'].get('claim'),b['data'].get('claim')
    if not ca or not cb or ca['key']!=cb['key']:missing.append('shared_proposition')
    state='OPEN_COLLISION' if missing else 'OPEN_COORDINATE_COMPARISON' if different else (
        'SUPPORTED_COLLISION' if {ca['stance'],cb['stance']}=={'support','oppose'} else 'NO_MATERIAL_COLLISION')
    return result(state,nodes=[a['id'],b['id']],open_axes=missing,differing_axes=different,
                  source_ids=sorted(set(a['source_ids']+b['source_ids'])),
                  meaning='Comparison of named annotations; differing coordinates explain the comparison scope.')


def lifecycle(store, episode):
    snap,_,group=_selected(store,episode);groups=defaultdict(list)
    for n in group:
        m=n['data'].get('money')
        if m and n['effective_status']!='stale':
            c=n['data']['context'];key=tuple(c.get(k) for k in ('property','loan','scope','obligation'))+(m['unit'],)
            groups[key].append(n)
    lanes=[]
    for key,items in sorted(groups.items()):
        stages=defaultdict(list);events=defaultdict(list)
        for n in items:
            m=n['data']['money'];stages[m['stage']].append(n['id']);events[m['event_id']].append(n)
        duplicates=[];conflicts=[]
        for event,observations in events.items():
            signatures={(n['data']['money']['stage'],str(rational(n['data']['money']['value'])),
                         n['data']['context']['instrument']) for n in observations}
            if len(observations)>1:
                (duplicates if len(signatures)==1 else conflicts).append({'event_id':event,'nodes':[n['id'] for n in observations]})
        lanes.append({'coordinates':dict(zip(('property','loan','scope','obligation','unit'),key)),
                      'stages':dict(stages),'open_stages':[s for s in STAGES if s not in stages],
                      'duplicate_observations':duplicates,'event_conflicts':conflicts,
                      'observations':[{'id':n['id'],**n['data']['money'],'instrument':n['data']['context']['instrument'],
                                       'clocks':n['clocks'],'source_ids':n['source_ids']} for n in items],
                      'aggregation':'STAGE_SEPARATE; explicit aligned model required for totals'})
    return result('LIFECYCLE_VIEW',episode=episode,lanes=lanes,revision=snap['revision'])


def episode(store, episode, max_pairs=1000):
    integer(max_pairs,1,10000,'max_pairs');snap,nodes,group=_selected(store,episode)
    current=[n for n in group if n['effective_status']!='stale'];sentences=[];timeline=[];knowledge=[];coverage=[]
    for n in current:
        d=n['data'];c=d['context'];actor=c.get('actor','The retained record')
        # Summaries are explicitly reviewer-supplied; exact source text stays alongside.
        quote=n['text'];sentence=d.get('summary') or f'{actor}: “{quote}”'
        sentences.append({'text':sentence,'annotation_id':n['id'],'source_ids':n['source_ids'],
                          'basis':'NAMED_ANNOTATION' if d.get('summary') else 'EXACT_EXTRACT',
                          'quotes':d['evidence_spans'],'context':c,'clocks':n['clocks'],
                          'finding':d.get('finding')})
        for k,v in n['clocks'].items():
            if k=='receipt':
                for actor,at in v.items():timeline.append({'id':n['id'],'clock':'receipt','actor':actor,'at':at})
            else:timeline.append({'id':n['id'],'clock':k,'at':v})
        if not n['clocks']:timeline.append({'id':n['id'],'clock':'unknown','at':None})
        knowledge.extend({'annotation_id':n['id'],**k} for k in d.get('knowledge',[]))
        if d['kind']=='coverage':coverage.append({'id':n['id'],'text':n['text'],'context':c})
    collisions=[];checked=0;truncated=False
    claims=defaultdict(list)
    for n in current:
        if n['data'].get('claim'):claims[n['data']['claim']['key']].append(n)
    from itertools import combinations
    for items in claims.values():
        for a,b in combinations(items,2):
            if checked>=max_pairs:truncated=True;break
            collisions.append(_compare(a,b));checked+=1
        if truncated:break
    origins={s for n in current for s in n['source_ids']};actions=[]
    for n in current:
        d=n['data'];missing=[k for k in CONTEXT if not d['context'].get(k)]
        # Empty mapping fields and searched record gaps have distinct states.
        if missing:actions.append({'id':n['id'],'state':'MAPPING_COVERAGE_OPEN','dimensions':missing,
                                   'next':'Check existing reviewed stores and native context before new retrieval.'})
    timeline.sort(key=lambda t:(not t['at'] or t['at'][0] is None,(t['at'] or [''])[0] or '',t['id'],t['clock']))
    order={t['id']:i for i,t in reversed(list(enumerate(timeline)))}
    sentences.sort(key=lambda s:order.get(s['annotation_id'],len(order)))
    narrative='\n\n'.join(f'{s["text"]} [{i}]' for i,s in enumerate(sentences,1))
    return result('CASE_EPISODE_DRAFT',episode=episode,revision=snap['revision'],graph_hash=snap['graph_hash'],
                  narrative=narrative,sentences=sentences,timeline=timeline,knowledge=knowledge,
                  comparisons=collisions,pair_checks=checked,pair_budget_exhausted=truncated,
                  lifecycle=lifecycle(store,episode),mapping_actions=actions,coverage=coverage,
                  source_ids=sorted(origins),stale_annotations=[n['id'] for n in group if n['effective_status']=='stale'],
                  reader_questions=['Who said or did what?','Which version and scope?','What was received or approved?',
                                    'Which money stage?','Which contrary account and open bridge?'],
                  comprehension_state='OPEN_INDEPENDENT_READER_EVALUATION')


def versions(store, packet):
    """Raw native/embedded workbook cell differences under a declared scope."""
    fields(packet,('left_hash','right_hash','scope'))
    text(packet['scope'],'comparison scope')
    snap=store.snapshot();maps=[]
    for sha in (packet['left_hash'],packet['right_hash']):
        require(isinstance(sha,str) and len(sha)==64,'attachment hash required')
        rows=[n for n in snap['nodes']
              if (n['data'].get('attachment_sha256') or n['data'].get('native_sha256'))==sha
              and n['data'].get('cells')]
        require(rows,'version workbook cells unavailable')
        maps.append({(n['data']['sheet'],cell):(value,n['id']) for n in rows for cell,value in n['data']['cells'].items()})
    changes=[]
    for cell in sorted(set(maps[0])|set(maps[1])):
        a,b=(m.get(cell) for m in maps)
        if a and b and a[0]==b[0]:continue
        changes.append({'sheet':cell[0],'cell':cell[1],'left':a[0] if a else None,'right':b[0] if b else None,
                        'nodes':[x[1] for x in (a,b) if x]})
    return result('VERSION_CELL_COMPARISON',scope=packet['scope'],changes=changes,
                  interpretation='Raw coordinate differences; scope, formula evaluation and obligation effects require review.')


def recover(store, packet):
    """Attach operator-reviewed OCR to its precise native page/hash and revision."""
    fields(packet,('batch_id','source_node','native_sha256','page','text','method','reviewer','expected_revision'))
    snap=store.snapshot();require(snap['revision']==packet['expected_revision'],'OCR revision changed')
    n=next((n for n in snap['nodes'] if n['id']==packet['source_node']),None)
    sources={s['id']:s for s in snap['nodes'] if s['type']=='source'}
    hashes=({n['data']['native_sha256']} if n and n['data'].get('native_sha256') else
            {sources[s]['data'].get('native_sha256_supplied') for s in n['source_ids']} if n else set())
    require(n and n['effective_status']!='stale' and hashes=={packet['native_sha256']},'OCR native fingerprint mismatch')
    require(n['data'].get('page')==packet['page'],'OCR page mismatch')
    text(packet['text'],'OCR text',100000);text(packet['method'],'method');text(packet['reviewer'],'reviewer')
    ident='ocr:'+digest([n['id'],packet['text'],packet['method']])[:32]
    node={'id':ident,'type':'passage','label':'Reviewed OCR · '+n['label'],'text':packet['text'],
          'source_ids':n['source_ids'],'status':'annotated','locator':n.get('locator') or sources[n['source_ids'][0]]['locator'],
          'depends_on':[{'kind':'node','id':n['id']}],
          'data':{'source_tier':'reviewed_ocr','native_sha256':packet['native_sha256'],'page':packet['page'],
                  'method':packet['method'],'reviewer':packet['reviewer'],'original_node':n['id']}}
    if any(x['id']==ident for x in snap['nodes']):return result('IDEMPOTENT',id=ident)
    receipt=store.apply({'schema':'loom.graph.batch.v1','batch_id':packet['batch_id'],'nodes':[node]},expected_revision=snap['revision'])
    return result('OCR_REVIEW_ATTACHED',id=ident,commit=receipt)


def evaluate(store, packet):
    """Frozen candidate-pool retrieval labels; no model or index writes."""
    fields(packet,('dataset_id','split','questions'),('definition_hash',))
    require(packet['split'] in ('development','audit'),'split must be development or audit')
    qs=packet['questions'];require(isinstance(qs,list) and 0<len(qs)<=200,'evaluate 1..200 questions')
    frozen=digest(qs)
    if packet.get('definition_hash'):require(packet['definition_hash']==frozen,'frozen labels changed')
    out=[]
    for q in qs:
        fields(q,('id','query','relevant_ids'),('forbidden_ids','where'))
        require(isinstance(q['relevant_ids'],list) and q['relevant_ids'],'explicit relevance labels required')
        found=indexed.search(store,q['query'],top=5,where=q.get('where'))
        ids=[h['id'] for h in found['hits']];rank=next((i for i,v in enumerate(ids,1) if v in q['relevant_ids']),None)
        out.append({'id':q['id'],'rank':rank,'hits':ids,'forbidden_hits':sorted(set(ids)&set(q.get('forbidden_ids',[])))})
    return result('CASE_EVALUATION',dataset_id=packet['dataset_id'],split=packet['split'],definition_hash=frozen,
                  questions=out,metrics={'n':len(out),'top1':sum(x['rank']==1 for x in out),
                                        'top5':sum(x['rank'] is not None for x in out),
                                        'mrr':sum(1/x['rank'] if x['rank'] else 0 for x in out)/len(out),
                                        'forbidden_hits':sum(len(x['forbidden_hits']) for x in out)},
                  boundary='Operator-labeled retrieval evaluation; independent comprehension and full-corpus coverage remain separate.')
