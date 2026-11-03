"""Exact cost-aware record-bundle minimax planning, including complementarities."""
from collections import defaultdict
from itertools import combinations
import time
from loom_qulap.model import solve, compile_model
from .contracts import rational, integer, require, result, digest


def plan(model,records=None,objective=None,max_bundle=3,max_bundles=512,max_worlds=10000,
         max_nodes=1000000,seconds=10,budget=None,ranking='gain_per_cost'):
    integer(max_bundle,1,6,'max_bundle');integer(max_bundles,1,10000,'max_bundles')
    integer(max_worlds,1,10000,'max_worlds');integer(max_nodes,1,2000000,'max_nodes')
    require(type(seconds) in (int,float) and 0<seconds<=60,'planning seconds must be in (0,60]')
    require(ranking in ('gain_per_cost','guaranteed_gain'),'unknown planning ranking')
    started=time.monotonic();base=solve(model,max_nodes=max_nodes,seconds=seconds,retain_worlds=max_worlds)
    if not base['complete'] or base['worlds_truncated']:
        return result('INCOMPLETE',solver=base,bundles=[],reason='complete traversal and all feasible witnesses are required for exact bundle planning')
    if not base['feasible_states']:return result('INCONSISTENT_MODEL',solver=base,bundles=[])
    variables=model['variables'];compiled=compile_model(model);metric_specs=compiled['metrics']
    require(metric_specs,'bundle planning requires at least one metric')
    objective=objective or metric_specs[0][0]['id'];require(objective in base['metrics'],'unknown objective metric')
    if records is None:
        records=[{'id':v['id'],'variables':[v['id']],'record':v['next_record'],'cost':'1'} for v in variables if v.get('next_record')]
    require(isinstance(records,list) and 1<=len(records)<=30,'declare 1..30 record candidates')
    names={v['id'] for v in variables};ids=[];costs={}
    for r in records:
        require(isinstance(r,dict) and isinstance(r.get('id'),str) and r['id'],'record needs an id')
        require(isinstance(r.get('variables'),list) and r['variables'] and set(r['variables'])<=names,'record variables must name declared model variables')
        ids.append(r['id']);costs[r['id']]=rational(r.get('cost','1'));require(costs[r['id']]>=0,'record cost must be nonnegative')
    require(len(ids)==len(set(ids)),'duplicate record ids')
    budget=None if budget is None else rational(budget);require(budget is None or budget>=0,'budget must be nonnegative')
    values=[{spec['id']:expr.value(w) for spec,expr in metric_specs} for w in base['worlds']]
    bundles=[];evaluated=0;partial=False;single={};partition_work=0
    for size in range(1,min(max_bundle,len(records))+1):
        for group in combinations(records,size):
            if evaluated>=max_bundles or time.monotonic()-started>=seconds:partial=True;break
            cost=sum((costs[r['id']] for r in group),rational(0))
            if budget is not None and cost>budget:continue
            evaluated+=1;observed=sorted({v for r in group for v in r['variables']});parts=defaultdict(list)
            for i,w in enumerate(base['worlds']):
                partition_work+=1
                if partition_work>2000000 or time.monotonic()-started>=seconds:partial=True;break
                parts[tuple(w[v] for v in observed)].append(i)
            if partial:break
            reductions={}
            for spec,expr in metric_specs:
                metric=spec['id'];before=base['metrics'][metric]['max']-base['metrics'][metric]['min']
                worst=max(max(values[i][metric] for i in indexes)-min(values[i][metric] for i in indexes) for indexes in parts.values())
                reductions[metric]={'unit':spec.get('unit','integer'),'current_width':before,
                                    'worst_remaining_width':worst,'guaranteed_reduction':before-worst}
            gain=reductions[objective]['guaranteed_reduction'];ratio=None if cost==0 else str(rational(gain)/cost)
            if size==1:single[group[0]['id']]=gain
            bundles.append({'records':[r['id'] for r in group],'variables':observed,'cost':str(cost),
                            'objective':objective,'guaranteed_gain':gain,'gain_per_cost':ratio,
                            'synergy':gain-sum(single.get(r['id'],0) for r in group),
                            'metric_reductions':reductions,'feasible_outcome_partitions':len(parts),
                            'outcome_preview':([dict(zip(observed,values_)) for values_,indexes in list(sorted(parts.items()))[:16]]
                                               if size==1 else [])})
        if partial:break
    def order(b):
        cost=rational(b['cost']);gain=rational(b['guaranteed_gain'])
        score=(1 if cost==0 and gain>0 else 0,gain/cost if cost else rational(0)) if ranking=='gain_per_cost' else (0,gain)
        return (-score[0],-score[1],-gain,cost,tuple(b['records']))
    bundles.sort(key=order)
    # Report the exact two-objective frontier of evaluated bundles, not a mixed-unit aggregate.
    frontier=[]
    for b in bundles:
        if not any(x['guaranteed_gain']>=b['guaranteed_gain'] and rational(x['cost'])<=rational(b['cost']) and
                   (x['guaranteed_gain']>b['guaranteed_gain'] or rational(x['cost'])<rational(b['cost'])) for x in bundles):
            frontier.append(b['records'])
    return result('PARTIAL' if partial else 'COMPLETE_WITHIN_BUNDLE_LIMIT',model_hash=digest(model),objective=objective,
                  objective_unit=base['metrics'][objective].get('unit'),bundles=bundles,pareto_frontier=frontier,
                  evaluated_bundles=evaluated,partition_work=partition_work,max_bundle=max_bundle,
                  exhaustive_within_declared_bundle_limit=not partial,solver_summary={k:base[k] for k in ('complete','candidate_states','feasible_states','visited_nodes')},
                  boundary='Exact worst-case reduction over declared feasible worlds and candidate record bundles; no outcome probabilities or unverified greedy optimality assumption.')
