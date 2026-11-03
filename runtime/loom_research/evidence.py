"""Reviewable relation evolution and source-first explanation/planning views."""
from collections import Counter
from copy import deepcopy
import re
from .contracts import fields, require, result, digest, integer, text, PREDICATES
from .learning import clean_node
from . import indexed

DIMENSIONS=('actor','property','loan','account','instrument','money_stage','scope')
ACTION_HINTS={'paid':'paid','payment':'paid','credited':'credited','credit':'credited','funded':'funded',
              'release':'funded','requested':'requested','approved':'related_to','authorized':'related_to'}


def _span(snapshot,raw):
    fields(raw,('node_id','start','end','quote'))
    nodes={n['id']:n for n in snapshot['nodes']};n=nodes.get(raw['node_id'])
    require(n and n['type'] in ('passage','claim','event') and n['effective_status']!='stale','span requires a current passage/claim/event')
    start=integer(raw['start'],0,len(n.get('text','')),'span start');end=integer(raw['end'],start+1,len(n.get('text','')),'span end')
    require(n['text'][start:end]==raw['quote'],'evidence span must match exact current text')
    require(bool(n['source_ids']),'relation evidence requires a source lineage')
    return {**raw,'content_hash':n['content_hash'],'source_ids':n['source_ids'],'locator':n.get('locator')}


def propose(store,packet):
    fields(packet,('batch_id','relations'),('expected_revision',))
    snap=store.snapshot();require(packet.get('expected_revision',snap['revision'])==snap['revision'],'relation plan revision changed')
    nodes={n['id']:n for n in snap['nodes']};existing={e['id']:e for e in snap['edges']};edges=[]
    require(isinstance(packet['relations'],list) and 0<len(packet['relations'])<=200,'propose 1..200 relations')
    for raw in packet['relations']:
        fields(raw,('subject','predicate','object','evidence'),('scope','clocks','rule'))
        require(raw['subject'] in nodes and raw['object'] in nodes,'relation endpoint unavailable')
        require(raw['predicate'] in PREDICATES-{'same_entity'},'use identity review for same_entity')
        require(isinstance(raw['evidence'],list) and 0<len(raw['evidence'])<=10,'relation needs 1..10 exact spans')
        spans=[_span(snap,s) for s in raw['evidence']]
        ident='relation:'+digest([raw['subject'],raw['predicate'],raw['object'],raw.get('scope',''),spans])[:32]
        if ident in existing:continue
        edges.append({'id':ident,'subject':raw['subject'],'predicate':raw['predicate'],'object':raw['object'],
                      'status':'candidate','source_ids':sorted({s for span in spans for s in span['source_ids']}),
                      'scope':raw.get('scope',''),'clocks':raw.get('clocks',{}),
                      'depends_on':[{'kind':'node','id':s['node_id']} for s in spans],
                      'data':{'evidence_spans':spans,'rule':raw.get('rule','operator_proposal'),'review_state':'PENDING',
                              'truth_status':'SOURCE_SPAN_MATCHED_REVIEW_OPEN'}})
    if not edges:return result('IDEMPOTENT',proposed=0)
    receipt=store.apply({'schema':'loom.graph.batch.v1','batch_id':packet['batch_id'],'edges':edges},expected_revision=snap['revision'])
    return result('PROPOSED',proposed=len(edges),edge_ids=[e['id'] for e in edges],commit=receipt)


def suggest(store,after_revision=0,limit=100):
    """Delta-scoped lexical proposals; action hints never auto-accept assertions."""
    integer(after_revision,0,10**9,'after_revision');integer(limit,1,200,'limit')
    snap=store.snapshot();entities=[n for n in snap['nodes'] if n['type']=='entity']
    require(len(entities)<=500,'suggestion entity budget is 500; use scoped explicit proposals')
    with store.connection() as c:
        changed={r[0] for r in c.execute("SELECT id FROM items WHERE kind='node' AND seq>?",(after_revision,))}
    relations=[]
    for n in snap['nodes']:
        if n['id'] not in changed or n['type']!='passage' or not n['source_ids']:continue
        for match in re.finditer(r'[^.!?\n]+(?:[.!?]|$)',n.get('text','')):
            sentence=match.group();mentions=[e for e in entities if re.search(r'(?<!\w)'+re.escape(e['label'])+r'(?!\w)',sentence,re.I)]
            words=set(re.findall(r'\w+',sentence.casefold()));hints=sorted(words & set(ACTION_HINTS))
            if len(mentions)!=2 or not hints:continue
            relations.append({'subject':mentions[0]['id'],'predicate':'proposed_relation','object':mentions[1]['id'],
                              'scope':'lexical co-mention; suggested action '+ACTION_HINTS[hints[0]],
                              'rule':'two-explicit-entity-mentions/action-hint/v1','clocks':n['clocks'],
                              'evidence':[{'node_id':n['id'],'start':match.start(),'end':match.end(),'quote':sentence}]})
            if len(relations)>=limit:break
        if len(relations)>=limit:break
    if not relations:return result('NO_PROPOSALS',after_revision=after_revision)
    return propose(store,{'batch_id':'suggest:'+digest([snap['revision'],relations])[:32],
                          'expected_revision':snap['revision'],'relations':relations})


def review(store,packet):
    fields(packet,('batch_id','edge_id','decision','reviewer','reason','expected_revision'))
    require(packet['decision'] in ('accept','reject'),'decision must be accept or reject')
    text(packet['reviewer'],'reviewer');text(packet['reason'],'review reason',8000)
    snap=store.snapshot(include_stale=True);require(snap['revision']==packet['expected_revision'],'review revision changed; reopen the proposal')
    old=next((e for e in snap['edges'] if e['id']==packet['edge_id']),None)
    require(old and old.get('data',{}).get('review_state')=='PENDING' and old['effective_status']!='stale','only current pending proposals can be reviewed')
    for span in old['data']['evidence_spans']:_span(snap,{k:span[k] for k in ('node_id','start','end','quote')})
    edge={k:deepcopy(v) for k,v in old.items() if k not in ('_deps','content_hash','active','effective_status','stale_reasons','temporal_state')}
    edge['revision']+=1;edge['status']='annotated' if packet['decision']=='accept' else 'retired'
    edge['data'].update(review_state='ACCEPTED_ANNOTATION' if packet['decision']=='accept' else 'REJECTED',
                        reviewer=packet['reviewer'],reason=packet['reason'],truth_status='REVIEWED_INTERPRETATION')
    correction={'id':'review:'+digest(packet)[:32],'type':'correction','label':'Relation review: '+packet['decision'],
                'text':packet['reason'],'space':'research','status':'annotated','source_ids':edge['source_ids'],
                'depends_on':[{'kind':'edge','id':edge['id']}],
                'data':{'reviewer':packet['reviewer'],'decision':packet['decision'],'edge_id':edge['id'],
                        'rule':edge['data']['rule'],'prior_revision':old['revision']}}
    receipt=store.apply({'schema':'loom.graph.batch.v1','batch_id':packet['batch_id'],'nodes':[correction],'edges':[edge]},expected_revision=snap['revision'])
    return result('REVIEWED',decision=packet['decision'],commit=receipt,
                  boundary='A named reviewer supplied this interpretation. Source revisions invalidate its captured dependencies; legal and factual conclusions require their own support.')


def explain(store,query,top=6,candidates=400,semantic=None,where=None,ranges=None,episode=None):
    if episode:
        from .casework import episode as compile_episode
        return compile_episode(store,episode)
    found=indexed.search(store,query,top=top,candidates=candidates,semantic=semantic,where=where,ranges=ranges)
    cards=[];open_bridges=[];lineages=set()
    for n in found['hits']:
        detail=indexed.inspect(store,n['id'],limit=40);data=n['data'];clocks=n['clocks']
        context={k:data.get(k) for k in DIMENSIONS};missing=[k for k in ('event','statement') if not clocks.get(k)]
        if not clocks.get('receipt'):missing.append('actor_specific_receipt')
        if not data.get('money_stage'):missing.append('money_stage')
        if not data.get('scope'):missing.append('scope')
        for key in missing:open_bridges.append({'node_id':n['id'],'dimension':key,'state':'OPEN_'+key.upper()})
        reviewed=[e for e in detail['edges'] if e.get('data',{}).get('review_state')=='ACCEPTED_ANNOTATION']
        pending=[e for e in detail['edges'] if e['status']=='candidate']
        adverse=[e for e in reviewed if e['predicate']=='opposes']
        for s in n['sources']:lineages.add(s['origin'])
        cards.append({'id':n['id'],'title':n['label'],'excerpt':n['text'],'excerpt_is_truncated':len(detail['node'].get('text',''))>len(n['text']),
                      'locator':n.get('locator'),'sources':n['sources'],'content_hash':n['content_hash'],
                      'source_tier':data.get('source_tier','OPERATOR_SUPPLIED'),
                      'clocks':clocks,'context':context,'open_dimensions':missing,'headers':data.get('headers'),
                      'reconstruction_note':data.get('reconstruction_note'),'speech_scope':data.get('speech_scope'),
                      'reviewed_relationships':reviewed,'pending_relationships':pending,'contrary_relationships':adverse,
                      'versions':detail['versions'],'native_context_required':True})
    # These sentences describe the selected records, rather than inventing a causal story.
    orientation=f'{len(cards)} source-linked records were selected for “{query}”. They trace to {len(lineages)} supplied source origins.'
    lines=[orientation]
    for i,card in enumerate(cards,1):
        lines.append(f'[{i}] {card["title"]}. Source: {card["locator"] or card["id"]}.')
    return result('EXPLANATION_DRAFT',question=query,orientation=orientation,cards=cards,open_bridges=open_bridges,
                  narrative='\n'.join(lines),retrieval=found,source_origin_count=len(lineages),
                  reader_note='Read the excerpts with their clocks and scope. Receipt, understanding, authorization and money application are separate questions.',
                  boundary='Extractive draft for review. Source origin counts describe supplied lineage; independent corroboration and reader comprehension require separate assessment.')


def next_steps(store,query,top=6):
    view=explain(store,query,top=top);actions=[]
    choices={
        'event':('Locate the event date','Native instrument and surrounding correspondence','Aligns the act with the relevant decision','Places the event outside the proposed sequence'),
        'statement':('Locate the statement/version date','Original document metadata or complete email','Identifies the contemporaneous wording','Identifies a later recollection or changed version'),
        'actor_specific_receipt':('Resolve who received this version','Complete To/Cc/Bcc, delivery and acknowledgment records','Shows actor-specific access before the decision','Shows later access or a different received version'),
        'money_stage':('Resolve the money stage','Request, approval, bank clearing, application and credit records','Closes the selected movement/application bridge','Shows a request, reversal or different application'),
        'scope':('Resolve the operative scope','Contract clause, amendment, full thread and attachments','Aligns property, obligation and authorization','Shows a different property, obligation or limited assent')}
    for bridge in view['open_bridges']:
        title,custodian,confirm,weaken=choices[bridge['dimension']]
        actions.append({'id':'next:'+digest(bridge)[:20],'node_id':bridge['node_id'],'priority':'HIGH' if bridge['dimension'] in ('actor_specific_receipt','money_stage','scope') else 'MEDIUM',
                        'question':title,'record_to_seek':custodian,'confirming_result':confirm,'weakening_result':weaken,
                        'state':'PROPOSED_RECORD_TEST','cost_state':'OPEN_COST','proof_effect':'NONE'})
    return result('RESEARCH_PROPOSALS',question=query,actions=sorted(actions,key=lambda a:(a['priority']!='HIGH',a['node_id'],a['id'])),
                  basis_revision=view['retrieval']['revision'],automatic_external_actions=False,
                  boundary='Structural gap priorities. Exact value-of-information calculations remain in research_plan with declared finite models, observations and costs.')
