"""Evaluate a bounded Pareto archive of proposed retrieval recipes.

Development labels select recipes; a disjoint-family audit evaluates the selected
recipe afterwards. This read-only evaluator never activates a method. Model prompt
reflection and proposal generation remain external to the deterministic engine.
"""
import argparse
import hashlib
import math
from pathlib import Path
from .contracts import canonical,digest,fields,integer,read_json,require,result,text
from .learning import PARAMS,metric
from .retrieval import search,policy_hash
from .store import GraphStore


def dominates(left,right):
    require(len(left)==len(right) and len(left)>0,'score vectors need equal nonzero dimensions')
    require(all(type(v) in (int,float) and math.isfinite(v) for v in list(left)+list(right)),
            'scores must be finite numbers')
    return all(a>=b for a,b in zip(left,right)) and any(a>b for a,b in zip(left,right))


def evaluate_frontier(store,packet):
    fields(packet,('development','recipes'),('audit','max_evaluations'))
    development=packet['development'];audit=packet.get('audit',[]);recipes=packet['recipes']
    require(isinstance(development,list) and 1<=len(development)<=200,'supply 1..200 development queries')
    require(isinstance(audit,list) and len(audit)<=200,'supply at most 200 audit queries')
    require(isinstance(recipes,list) and 1<=len(recipes)<=16,'supply 1..16 recipes')
    budget=integer(packet.get('max_evaluations',256),1,4096,'max_evaluations')
    snapshot=store.snapshot();available={n['id'] for n in snapshot['nodes']};ids=set()
    for query in development+audit:
        fields(query,('id','query','family','relevant'),('chain','answerable'))
        text(query['id']);text(query['query']);text(query['family'])
        require(query['id'] not in ids,'duplicate query ID');ids.add(query['id'])
        require(isinstance(query['relevant'],list) and all(isinstance(x,str) for x in query['relevant']) and len(query['relevant'])==len(set(query['relevant'])) and set(query['relevant'])<=available,'invalid relevance labels')
        chain=query.get('chain',[])
        require(isinstance(chain,list) and all(isinstance(x,str) for x in chain) and len(chain)==len(set(chain)) and set(chain)<=available,'invalid chain labels')
        if 'answerable' in query:
            require(type(query['answerable']) is bool,'answerable must be boolean')
            require(query['answerable']==bool(query['relevant']),'answerable must agree with relevance labels')
        if not query['relevant']:require(query.get('answerable') is False,'empty labels require answerable:false')
    require(not ({q['family'] for q in development}&{q['family'] for q in audit}),'development and audit families overlap')
    grouped={};ids=set();cutoffs=set()
    for recipe in recipes:
        fields(recipe,('id','params'));text(recipe['id'])
        require(recipe['id'] not in ids,'duplicate recipe ID');ids.add(recipe['id'])
        require(isinstance(recipe['params'],dict) and set(recipe['params'])<=PARAMS,'unknown recipe parameters')
        top=recipe['params'].get('top',10);integer(top,1,50,'top');cutoffs.add(top)
        key=digest(recipe['params']);grouped.setdefault(key,{'id':recipe['id'],'aliases':[],'params':recipe['params']})['aliases'].append(recipe['id'])
    for recipe in grouped.values():
        recipe['aliases'].sort();recipe['id']=recipe['aliases'][0]
    require(len(cutoffs)==1,'recipe cutoffs must match')
    required=len(grouped)*len(development)+len(audit)
    basis={'benchmark_sha256':digest({'development':development,'audit':audit}),
           'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'proposal_sha256':digest(recipes),'evidence_graph_hash':snapshot['projection_hash'],
           'policy_hash':policy_hash(),'max_evaluations':budget,'required_evaluations':required,
           'scope':'supplied relevance labels on one graph snapshot; no automatic activation',
           'label_independence_verified':False,'activation_allowed':False}
    if required>budget:return result('BUDGET_EXHAUSTED',**basis,frontier=[],selected=None,evaluations=0)
    def evaluate(recipe,query):
        hits=search(snapshot,query['query'],**recipe['params'])
        m=metric(hits['hits'],query['relevant'],query.get('chain',[]),recipe['params'].get('top',10))
        vector=[m[k] for k in ('ndcg','recall','chain_coverage') if m[k] is not None]
        if not query['relevant']:vector=[-len(hits['hits'])]
        return {'id':query['id'],'family':query['family'],'metrics':m,'score_vector':vector,
                'complete':hits['state']!='PARTIAL'}
    evaluated=[];count=0
    for key,recipe in sorted(grouped.items()):
        checks=[evaluate(recipe,q) for q in development];count+=len(checks)
        evaluated.append({**recipe,'params_sha256':key,'development':checks,
                          'complete':all(c['complete'] for c in checks),
                          'score_vector':[v for c in checks for v in c['score_vector']]})
    eligible=[r for r in evaluated if r['complete']]
    frontier=[r for r in eligible if not any(dominates(other['score_vector'],r['score_vector']) for other in eligible)]
    # Aggregate tie-breaking is explicit; audit values never affect this choice.
    def rank(recipe):
        qs=recipe['development'];averages=[]
        for key in ('ndcg','recall','chain_coverage'):
            values=[q['metrics'][key] for q in qs if q['metrics'][key] is not None]
            averages.append(sum(values)/len(values) if values else 0)
        false_hits=sum(len(q['metrics']['hits']) for q,source in zip(qs,development) if not source['relevant'])
        return tuple(-v for v in averages)+(false_hits,recipe['params_sha256'])
    frontier.sort(key=rank);selected=frontier[0] if frontier else None
    audit_checks=[evaluate(selected,q) for q in audit] if selected else [];count+=len(audit_checks)
    unchanged=store.snapshot()['projection_hash']==snapshot['projection_hash']
    complete=unchanged and len(eligible)==len(evaluated) and all(c['complete'] for c in audit_checks)
    return result('EVALUATED' if complete else 'PARTIAL',**basis,
                  recipes=evaluated,frontier=[r['id'] for r in frontier],
                  selected=selected['id'] if selected and complete else None,
                  selected_params_sha256=selected['params_sha256'] if selected and complete else None,
                  selected_before_audit=selected['id'] if selected else None,
                  audit_state='EVALUATED_AFTER_SELECTION' if audit_checks else 'NOT_SUPPLIED',
                  audit=audit_checks,evaluations=count,
                  selection_policy='Pareto on per-query scores, then mean nDCG/recall/chain coverage, false hits, parameter hash; smallest alias identifies each recipe',
                  graph_unchanged=unchanged)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--db',required=True);parser.add_argument('--input',required=True);a=parser.parse_args(argv)
    try:out=evaluate_frontier(GraphStore(a.db),read_json(a.input))
    except (ValueError,KeyError,TypeError,OSError) as exc:parser.exit(2,str(exc)+'\n')
    print(canonical(out));return 0 if out['state']=='EVALUATED' else 3


if __name__=='__main__':raise SystemExit(main())
