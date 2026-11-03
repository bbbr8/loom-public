"""Source-pair diagnostics with exact, stage-qualified quantitative comparisons."""
from collections import defaultdict
from itertools import combinations
from .contracts import integer, rational, require, result

AXES=('measure','unit','stage','scope','instrument','version','clock')


def analyze(snapshot,max_pairs=20000,invariants=None):
    integer(max_pairs,1,100000,'max_pairs');nodes={n['id']:n for n in snapshot['nodes']}
    claims=defaultdict(list);quantities=defaultdict(list);origins={n['id']:n['origin'] for n in nodes.values() if n['type']=='source'}
    source_gaps=[];diagnostics=[];pairs=0;partial=False
    for n in nodes.values():
        c=n.get('data',{}).get('claim')
        if c:
            require(isinstance(c,dict) and all(isinstance(c.get(k),str) and c[k] for k in ('key','scope','clock','stance')),'claim coordinates need key/scope/clock/stance')
            require(c['stance'] in ('support','oppose','unknown'),'claim stance must be support/oppose/unknown')
            claims[(c['key'],c['scope'],c['clock'])].append(n)
        for index,q in enumerate(n.get('data',{}).get('quantities',[])):
            require(isinstance(q,dict) and 'value' in q,'quantity needs exact value')
            missing=[a for a in AXES if not isinstance(q.get(a),str) or not q[a]]
            if missing:
                source_gaps.append({'node':n['id'],'quantity':index,'state':'OPEN_AXES','missing':missing});continue
            value=rational(q['value'])
            quantities[tuple(q[a] for a in AXES)].append((n,q,value,index))
    for coord,group in sorted(claims.items()):
        for a,b in combinations(group,2):
            if pairs>=max_pairs:partial=True;break
            pairs+=1;sa=a['data']['claim']['stance'];sb=b['data']['claim']['stance']
            if {sa,sb}=={'support','oppose'}:
                src=sorted(set(a['source_ids']+b['source_ids']))
                diagnostics.append({'state':'OPPOSED_ANNOTATED_CLAIMS','coordinates':list(coord),'nodes':[a['id'],b['id']],
                                    'source_ids':src,'origins':sorted({origins[s] for s in src}),
                                    'same_origin':len({origins[s] for s in src})<2})
        if partial:break
    if not partial:
        for axes,group in sorted(quantities.items()):
            for a,b in combinations(group,2):
                if pairs>=max_pairs:partial=True;break
                pairs+=1;an,aq,av,ai=a;bn,bq,bv,bi=b
                if av!=bv:
                    diagnostics.append({'state':'ALIGNED_QUANTITY_CONFLICT','axes':dict(zip(AXES,axes)),
                                        'nodes':[an['id'],bn['id']],'values':[str(av),str(bv)],'delta':str(bv-av),
                                        'source_ids':sorted(set(an['source_ids']+bn['source_ids']))})
                elif aq.get('event_id') and aq.get('event_id')==bq.get('event_id'):
                    diagnostics.append({'state':'DUPLICATE_EVENT_OBSERVATION','axes':dict(zip(AXES,axes)),
                                        'event_id':aq['event_id'],'nodes':[an['id'],bn['id']],
                                        'same_origin':{origins[s] for s in an['source_ids']}=={origins[s] for s in bn['source_ids']}})
            if partial:break
    checks=[]
    require(invariants is None or isinstance(invariants,list) and len(invariants)<=100,'at most 100 declared invariants')
    for rule in invariants or []:
        require(isinstance(rule,dict) and isinstance(rule.get('terms'),list) and 1<=len(rule['terms'])<=100,'invariant needs bounded terms')
        total=rational(0);axes=None;dependencies=[]
        for term in rule['terms']:
            require(term.get('node') in nodes,'invariant node unavailable')
            n=nodes[term['node']];index=integer(term.get('quantity',0),0,1000,'quantity index')
            qs=n.get('data',{}).get('quantities',[]);require(index<len(qs),'quantity unavailable')
            q=qs[index];current=tuple(q.get(a) for a in AXES)
            require(all(current),'invariant quantity has open axes')
            require(axes is None or current==axes,'invariant crosses scope/unit/stage/version/clock; compile an explicit bridged QULAP model instead')
            axes=current;total+=rational(term.get('weight',1))*rational(q['value']);dependencies.append(n['id'])
        expected=rational(rule.get('equals',0))
        checks.append({'id':rule.get('id'),'state':'SATISFIED_IN_DECLARED_DATA' if total==expected else 'INVARIANT_VIOLATION',
                       'actual':str(total),'expected':str(expected),'residual':str(total-expected),'axes':dict(zip(AXES,axes)),
                       'nodes':dependencies})
    return result('PARTIAL' if partial else 'COMPLETE_WITHIN_ALIGNED_GROUPS',diagnostics=diagnostics,invariants=checks,
                  open_inputs=source_gaps,pair_checks=pairs,graph_hash=snapshot['graph_hash'],
                  boundary='Exact arithmetic and explicit claim coordinates on supplied annotations; mismatched money stages and instruments remain separate.')
