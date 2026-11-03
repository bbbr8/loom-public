"""Compile reviewed typed graph inputs into replayable QULAP finite models."""
from copy import deepcopy
from loom_qulap.model import compile_model
from .contracts import digest, fields, require, result, text


def compile_graph(snapshot,spec):
    fields(spec,('id','nodes'),('question','source_tier','allow_hypotheses'))
    require(isinstance(spec['nodes'],list) and 1<=len(spec['nodes'])<=400,'select 1..400 model input nodes')
    require(type(spec.get('allow_hypotheses',False)) is bool,'allow_hypotheses must be boolean')
    nodes={n['id']:n for n in snapshot['nodes']};selected=[];gaps=[];assumptions=[];source_ids=set();bindings=[]
    model={'schema':'qulap.model.v1','source_tier':spec.get('source_tier','derived'),'question':spec.get('question',spec['id']),
           'variables':[],'constraints':[],'metrics':[],'propositions':[],'sources':[],'coverage_gaps':[]}
    kindmap={'variable':('variables','variable'),'constraint':('constraints','constraint'),
             'metric':('metrics','metric'),'proposition':('propositions','proposition')}
    for ident in spec['nodes']:
        text(ident,'input node id')
        n=nodes.get(ident)
        if n is None or n['effective_status']=='stale':gaps.append({'node':ident,'reason':'unavailable_or_stale'});continue
        if n['type'] not in kindmap:gaps.append({'node':ident,'reason':'unsupported_model_input_type'});continue
        if n['data'].get('reviewed') is not True:
            if spec.get('allow_hypotheses') is not True:gaps.append({'node':ident,'reason':'explicit_review_annotation_required'});continue
            assumptions.append(ident)
        plural,field=kindmap[n['type']];entry=deepcopy(n['data'].get(field))
        require(isinstance(entry,dict),'model input needs data.'+field)
        # Graph provenance is authoritative for this compilation; supplied source refs must align.
        supplied=set(entry.get('source_ids',[]))
        require(not supplied-set(n['source_ids']),'model statement cites sources absent from its graph provenance')
        entry['source_ids']=sorted(set(n['source_ids'])|supplied)
        model[plural].append(entry);source_ids.update(entry['source_ids']);selected.append(n)
        bindings.append({'id':ident,'kind':'node','revision':n['revision'],'content_hash':n['content_hash']})
    for sid in sorted(source_ids):
        s=nodes.get(sid)
        if s is None or s['type']!='source' or s['effective_status']=='stale':gaps.append({'node':sid,'reason':'source_unavailable'});continue
        # QULAP source ids use identifier syntax; preserve graph ids through a deterministic mapping.
        qid='S_'+digest(sid)[:24]
        model['sources'].append({'id':qid,'locator':s['locator'],'lineage_id':s['origin'],
                                 'graph_id':sid,'graph_revision':s['revision'],'graph_content_hash':s['content_hash'],
                                 'receipt_by_actor':deepcopy(s.get('clocks',{}).get('receipt',{}))})
        bindings.append({'id':sid,'kind':'node','revision':s['revision'],'content_hash':s['content_hash']})
    mapping={s['graph_id']:s['id'] for s in model['sources']}
    for plural in ('variables','constraints','metrics','propositions'):
        for entry in model[plural]:
            entry['source_ids']=[mapping[s] for s in entry['source_ids'] if s in mapping]
            if entry.get('bridge'):
                require(not set(entry['bridge'].get('source_ids',[]))-set(mapping),'bridge source is outside the selected source set')
                entry['bridge']['source_ids']=[mapping[s] for s in entry['bridge'].get('source_ids',[])]
    model['coverage_gaps']=['Explicit finite domains; alternatives outside these domains remain outside the model.']
    if assumptions:model['coverage_gaps'].append('Unreviewed hypothetical input nodes: '+', '.join(assumptions))
    if gaps:return result('OPEN_INPUT',id=spec['id'],gaps=gaps,bindings=bindings,assumptions=assumptions,model=None)
    compile_model(model)  # checks expressions, units, lanes, bridges and domains without searching
    return result(id=spec['id'],model=model,model_hash=digest(model),bindings=bindings,
                  graph_hash=snapshot['graph_hash'],basis_projection=snapshot['projection_hash'],assumptions=assumptions,
                  boundary='Compiled finite domains and declared source interpretations; reviewed flags are supplied annotations.')
