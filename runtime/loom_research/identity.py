"""Explainable multi-field identity candidates and reversible reviewed aliases."""
from collections import defaultdict
from difflib import SequenceMatcher
import re
from itertools import combinations
from .contracts import norm, require, result, integer
from .normalization import normalize


def field_form(field,value,data):
    kind={'birth_date':'date','registration_id':'identifier'}.get(field,field)
    try:return normalize(kind,value,data.get('normalization_profile'))
    except ValueError:return {'canonical':None,'state':'OPEN_NORMALIZATION_INPUT','raw':value}


def keyset(node, unique=False):
    return {(k['kind'],k['namespace'],k['value']) for k in node.get('identifiers',[]) if not unique or k['unique']}


def compare(a,b):
    if a['type']!=b['type']:
        return None
    ad,bd=a.get('data',{}),b.get('data',{})
    if ad.get('entity_type')!=bd.get('entity_type'):
        return None
    if ad.get('scope') and bd.get('scope') and ad['scope']!=bd['scope']:
        return None
    shared=keyset(a)&keyset(b);unique=keyset(a,True)&keyset(b,True)
    ac={(k['kind'],k['namespace']):k['value'] for k in a.get('identifiers',[]) if k['unique']}
    bc={(k['kind'],k['namespace']):k['value'] for k in b.get('identifiers',[]) if k['unique']}
    conflicts=[{'kind':k[0],'namespace':k[1],'left':ac[k],'right':bc[k]} for k in sorted(set(ac)&set(bc)) if ac[k]!=bc[k]]
    features=[]
    for field in ('email','phone','address','birth_date','registration_id'):
        av,bv=ad.get(field),bd.get(field)
        if av and bv:
            left,right=field_form(field,av,ad),field_form(field,bv,bd)
            eligible={'NORMALIZED','ADDRESS_TEXT_REVIEW_OPEN'}
            same=left['state'] in eligible and right['state'] in eligible and left['canonical']==right['canonical']
            features.append({'field':field,'match':same,'left':av,'right':bv,
                             'left_normalization':left,'right_normalization':right})
    name_similarity=SequenceMatcher(None,norm(a['label']),norm(b['label'])).ratio()
    exact_fields=[f['field'] for f in features if f['match']]
    if conflicts:
        state='IDENTIFIER_CONFLICT'
    elif unique:
        state='EXACT_DECLARED_KEY'
    elif len(exact_fields)>=2 and name_similarity>=.65:
        state='MULTIFIELD_CANDIDATE'
    elif shared or exact_fields or name_similarity>=.8:
        state='INSUFFICIENT_IDENTITY'
    else:
        return None
    return {'left':a['id'],'right':b['id'],'state':state,'shared_keys':[list(x) for x in sorted(shared)],
            'conflicts':conflicts,'features':features,'name_similarity':round(name_similarity,6),
            'matching_field_count':len(exact_fields),'calibrated_probability':None,
            'action':'review the indicated identifiers and fields; raw node IDs remain separate'}


def resolve(snapshot,max_pairs=20000):
    integer(max_pairs,1,100000,'max_pairs')
    nodes=[n for n in snapshot['nodes'] if n.get('type') in ('entity','instrument') and n['active']]
    # Blocking uses exact keys, token prefixes and non-name fields, rather than all-pairs expansion.
    blocks=defaultdict(set);byid={n['id']:n for n in nodes}
    for n in nodes:
        blocks[('name',n.get('data',{}).get('entity_type'),norm(n['label'])[:5])].add(n['id'])
        for k in keyset(n):blocks[('key',*k)].add(n['id'])
        for f in ('email','phone','address','birth_date','registration_id'):
            if n.get('data',{}).get(f):
                view=field_form(f,n['data'][f],n['data'])
                if view['canonical'] is not None and view['state'] in ('NORMALIZED','ADDRESS_TEXT_REVIEW_OPEN'):
                    from .contracts import canonical
                    blocks[(f,canonical(view['canonical']))].add(n['id'])
    tested=set();pairs=[];partial=False
    for block in sorted(blocks,key=str):
        for a,b in combinations(sorted(blocks[block]),2):
            if (a,b) in tested:continue
            if len(tested)>=max_pairs:partial=True;break
            tested.add((a,b));v=compare(byid[a],byid[b])
            if v:pairs.append(v)
        if partial:break
    parent={n['id']:n['id'] for n in nodes}
    def find(k):
        while parent[k]!=k:
            parent[k]=parent[parent[k]];k=parent[k]
        return k
    for edge in snapshot['edges']:
        if edge['predicate']=='same_entity' and edge['data'].get('reviewed') is True and edge['status'] not in ('candidate','hypothesis'):
            a,b=edge['subject'],edge['object']
            if a in parent and b in parent:
                x,y=sorted((find(a),find(b)));parent[y]=x
    return result('PARTIAL' if partial else 'COMPLETE_WITHIN_BLOCKS',pairs=pairs,
                  pair_checks=len(tested),clusters={k:find(k) for k in sorted(parent)},
                  boundary='Blocked candidate matching, descriptive string similarity and explicitly reviewed alias edges; no calibrated identity probabilities or destructive merge.')
