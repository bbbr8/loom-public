"""Dynamic, versioned ontology formatting and bounded validation (stdlib only).

Ontologies are caller-declared contracts, never evidence or an OWL reasoner.
All transformations are pure; graph projections preserve their native payload.
"""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import re
from .contracts import (ResearchError, canonical, clocks, digest, fields, integer,
                        read_json, refs, require, text, timestamp)

SCHEMA = 'loom.ontology.v1'
DATA = 'loom.ontology.instances.v1'
MAX_TYPES, MAX_ITEMS, MAX_DEPTH = 256, 4096, 16
KINDS = {'string', 'integer', 'boolean', 'decimal', 'date', 'reference', 'object', 'array', 'enum'}


def bounded(value):
    require(len(canonical(value).encode()) <= 16*1024**2, 'ontology packet exceeds 16 MiB')
    return deepcopy(value)


def names(value, label):
    require(isinstance(value, list) and len(value) <= MAX_TYPES, label+' must be a bounded list')
    require(all(isinstance(x, str) and x.strip() for x in value), label+' needs text names')
    require(len(set(value)) == len(value), label+' contains duplicates')
    return sorted(value)


def shape(raw, depth=0):
    require(depth <= MAX_DEPTH, 'property nesting exceeds 16')
    fields(raw, ('kind',), ('required', 'nullable', 'enum', 'items', 'properties', 'target', 'min_items', 'max_items'))
    out = deepcopy(raw)
    require(out['kind'] in KINDS, 'unknown property kind')
    for flag in ('required', 'nullable'):
        require(type(out.get(flag, False)) is bool, flag+' must be boolean')
        out[flag] = out.get(flag, False)
    k = out['kind']
    allowed = {'enum'} if k == 'enum' else {'items', 'min_items', 'max_items'} if k == 'array' else {'properties'} if k == 'object' else {'target'} if k == 'reference' else set()
    require(not set(raw)-{'kind','required','nullable'}-allowed, 'property options do not match kind')
    if k == 'enum':
        require(isinstance(out.get('enum'), list) and 1 <= len(out['enum']) <= 256, 'enum needs 1..256 JSON values')
        require(len({canonical(v) for v in out['enum']}) == len(out['enum']), 'duplicate enum value')
    if k == 'array':
        out['items'] = shape(out.get('items'), depth+1)
        out['min_items'] = integer(out.get('min_items', 0), 0, MAX_ITEMS)
        out['max_items'] = integer(out.get('max_items', MAX_ITEMS), out['min_items'], MAX_ITEMS)
    if k == 'object': out['properties'] = properties(out.get('properties'), depth+1)
    if k == 'reference': text(out.get('target'), 'reference target')
    return out


def properties(raw, depth=0):
    require(isinstance(raw, dict) and len(raw) <= 256, 'properties must be a map of at most 256 shapes')
    return {text(k, 'property name'): shape(v, depth) for k,v in sorted(raw.items())}


def compile_ontology(packet):
    raw = bounded(read_json(packet))
    fields(raw, ('schema','id','version','types'), ('relations','rules','description'))
    require(raw['schema'] == SCHEMA, 'expected '+SCHEMA)
    text(raw['id'], 'ontology id'); integer(raw['version'], 1, 10**9, 'ontology version')
    require(isinstance(raw['types'], dict) and 1 <= len(raw['types']) <= MAX_TYPES, 'ontology needs 1..256 types')
    out = {'schema':SCHEMA,'id':raw['id'],'version':raw['version'],'types':{},'relations':{},'rules':[]}
    if 'description' in raw: out['description'] = text(raw['description'], 'description')
    for name,t in sorted(raw['types'].items()):
        text(name, 'type name'); fields(t, (), ('extends','properties','disjoint','abstract','label'))
        out['types'][name] = {'extends':names(t.get('extends', []), 'extends'),
                              'disjoint':names(t.get('disjoint', []), 'disjoint'),
                              'properties':properties(t.get('properties', {})), 'abstract':t.get('abstract',False)}
        require(type(out['types'][name]['abstract']) is bool, 'abstract must be boolean')
        if 'label' in t: out['types'][name]['label'] = text(t['label'], 'type label')
    resolved, ancestors = {}, {}
    def visit(name, stack):
        require(name in out['types'], 'unknown type '+name)
        require(name not in stack, 'cyclic inheritance: '+name)
        require(len(stack) <= MAX_DEPTH, 'inheritance exceeds 16')
        if name in resolved: return
        t = out['types'][name]; props = {}; supers = {name}
        for parent in t['extends']:
            visit(parent, stack+[name]); supers.update(ancestors[parent])
            for k,v in resolved[parent].items():
                require(k not in props or props[k] == v, 'ambiguous inherited property '+name+'.'+k)
                props[k] = v
        for k,v in t['properties'].items():
            require(k not in props or props[k] == v, 'incompatible property override '+name+'.'+k)
            props[k] = v
        for target in t['disjoint']: require(target in out['types'], 'unknown disjoint type '+target)
        resolved[name], ancestors[name] = props, supers
    for name in out['types']: visit(name, [])
    def targets(s):
        if s['kind'] == 'reference': require(s['target'] in resolved, 'unknown reference target')
        if s['kind'] == 'object':
            for sub in s['properties'].values(): targets(sub)
        if s['kind'] == 'array': targets(s['items'])
    for name in resolved:
        for s in resolved[name].values(): targets(s)
        for ancestor in ancestors[name]:
            require(not set(out['types'][ancestor]['disjoint']) & ancestors[name], 'type inherits disjoint types: '+name)
    rels = raw.get('relations', {})
    require(isinstance(rels,dict) and len(rels) <= MAX_TYPES, 'relations must be a bounded map')
    for name,r in sorted(rels.items()):
        text(name,'relation name');fields(r, ('domain','range'), ('functional','acyclic','inverse','symmetric'))
        q = {'domain':names(r['domain'],'domain'),'range':names(r['range'],'range')}
        require(q['domain'] and q['range'], 'relation domain/range cannot be empty')
        require(set(q['domain']+q['range']) <= set(resolved), 'unknown relation endpoint type')
        for flag in ('functional','acyclic','symmetric'):
            require(type(r.get(flag,False)) is bool, flag+' must be boolean'); q[flag] = r.get(flag,False)
        if 'inverse' in r: q['inverse'] = text(r['inverse'],'inverse')
        out['relations'][name] = q
    for name,r in out['relations'].items():
        if 'inverse' in r:
            require(r['inverse'] in out['relations'], 'unknown inverse relation')
            inv = out['relations'][r['inverse']]
            require(inv['domain'] == r['range'] and inv['range'] == r['domain'], 'inverse domain/range must reverse')
        if r['symmetric']: require(r['domain'] == r['range'], 'symmetric domain/range must match')
    rules = raw.get('rules', [])
    require(isinstance(rules,list) and len(rules) <= 64, 'at most 64 chain rules')
    seen = set()
    for rule in rules:
        fields(rule, ('id','chain','infer'))
        text(rule['id'],'rule id');require(rule['id'] not in seen, 'duplicate rule id');seen.add(rule['id'])
        require(isinstance(rule['chain'],list) and len(rule['chain']) == 2, 'rules have exactly two relation premises')
        require(all(isinstance(x,str) and x in out['relations'] for x in rule['chain']+[rule['infer']]), 'unknown rule relation')
        out['rules'].append(deepcopy(rule))
    out['rules'].sort(key=lambda r:r['id'])
    return {'ontology':out,'ontology_hash':digest(out),'properties':resolved,'ancestors':{k:sorted(v) for k,v in ancestors.items()}}


def check(packet):
    c = compile_ontology(packet)
    return {'schema':'loom.ontology.check.v1','ok':True, **c}


def validate(ontology, packet):
    c = compile_ontology(ontology); o = c['ontology']; p = bounded(read_json(packet))
    fields(p, ('schema','ontology_id','ontology_version','nodes'), ('edges',))
    require(p['schema'] == DATA, 'expected '+DATA)
    require(p['ontology_id'] == o['id'] and type(p['ontology_version']) is int and p['ontology_version'] == o['version'], 'ontology id/version mismatch')
    require(isinstance(p['nodes'],list) and len(p['nodes']) <= MAX_ITEMS, 'at most 4096 instance nodes')
    require(isinstance(p.get('edges',[]),list) and len(p.get('edges',[])) <= MAX_ITEMS, 'at most 4096 instance edges')
    issues = []; nodes = {}; edges = []
    def error(path, code, message): issues.append({'path':path,'code':code,'message':message})
    def matches(node, target): return target in c['ancestors'].get(node.get('type'), [])
    for i,n in enumerate(p['nodes']):
        fields(n, ('id','type','properties'), ('source_ids','clocks','status','native'))
        text(n['id'],'node id');text(n['type'],'node type')
        require(n['id'] not in nodes, 'duplicate node id'); nodes[n['id']] = n
        if 'source_ids' in n: refs(n['source_ids'])
        if 'clocks' in n: clocks(n['clocks'])
        if n['type'] not in o['types']: error('nodes/'+str(i), 'unknown_type', n['type'])
        elif o['types'][n['type']]['abstract']: error('nodes/'+str(i), 'abstract_type', n['type'])
    def value(v,s,path,depth=0):
        require(depth <= MAX_DEPTH, 'instance nesting exceeds 16')
        if v is None:
            if not s['nullable']: error(path,'null','null is not allowed')
            return
        k = s['kind']
        valid = True
        if k == 'string': valid = isinstance(v,str)
        elif k == 'integer': valid = type(v) is int
        elif k == 'boolean': valid = type(v) is bool
        elif k == 'decimal': valid = isinstance(v,str) and re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?',v) is not None
        elif k == 'date':
            try: valid = isinstance(v,str) and timestamp(v) is not None
            except ResearchError: valid = False
        elif k == 'enum': valid = canonical(v) in {canonical(x) for x in s['enum']}
        elif k == 'reference':
            valid = isinstance(v,str) and v in nodes and matches(nodes[v],s['target'])
        elif k == 'object':
            valid = isinstance(v,dict)
            if valid: obj(v,s['properties'],path,depth+1)
        elif k == 'array':
            valid = isinstance(v,list) and s['min_items'] <= len(v) <= s['max_items']
            if valid:
                for i,x in enumerate(v): value(x,s['items'],path+'/'+str(i),depth+1)
        if not valid: error(path,'value_type','expected '+k)
    def obj(v,props,path,depth=0):
        if not isinstance(v,dict): error(path,'object','properties must be an object'); return
        for key in sorted(set(v)-set(props)): error(path+'/'+key,'unknown_property',key)
        for key,s in props.items():
            if key not in v:
                if s['required']: error(path+'/'+key,'required','required property missing')
            else: value(v[key],s,path+'/'+key,depth)
    for i,n in enumerate(p['nodes']):
        if n['type'] in o['types']: obj(n['properties'],c['properties'][n['type']],'nodes/'+str(i)+'/properties')
    ids = set(); outgoing = {}; adjacency = {}
    for i,e in enumerate(p.get('edges',[])):
        fields(e, ('id','subject','predicate','object'), ('source_ids','clocks','status','native'))
        for key in ('id','subject','predicate','object'): text(e[key],key)
        require(e['id'] not in ids, 'duplicate edge id');ids.add(e['id'])
        if 'source_ids' in e: refs(e['source_ids'])
        if 'clocks' in e: clocks(e['clocks'])
        path = 'edges/'+str(i); r = o['relations'].get(e['predicate'])
        if r is None: error(path,'unknown_relation',e['predicate']);continue
        a,b = nodes.get(e['subject']),nodes.get(e['object'])
        if a is None or b is None: error(path,'dangling_endpoint','edge endpoint unavailable');continue
        if not any(matches(a,t) for t in r['domain']): error(path,'domain','subject type violates domain')
        if not any(matches(b,t) for t in r['range']): error(path,'range','object type violates range')
        key = (e['predicate'],e['subject']); outgoing.setdefault(key,set()).add(e['object'])
        adjacency.setdefault(e['predicate'],{}).setdefault(e['subject'],set()).add(e['object'])
        edges.append(e)
    for (pred,sub),targets in sorted(outgoing.items()):
        if o['relations'][pred]['functional'] and len(targets)>1: error('relations/'+pred+'/'+sub,'functional','multiple distinct targets')
    for pred,adj in adjacency.items():
        if not o['relations'][pred]['acyclic']: continue
        # Kahn's algorithm avoids Python recursion on large instance graphs.
        degree = {n:0 for a,bs in adj.items() for n in [a,*bs]}
        for bs in adj.values():
            for b in bs: degree[b] += 1
        todo = [n for n,d in degree.items() if d == 0]; count = 0
        while todo:
            n = todo.pop();count += 1
            for b in adj.get(n,()):
                degree[b] -= 1
                if degree[b] == 0: todo.append(b)
        if count != len(degree): error('relations/'+pred,'cycle','acyclic relation contains a cycle')
    normalized = deepcopy(p); normalized['nodes'].sort(key=lambda n:n['id']); normalized['edges'] = sorted(p.get('edges',[]),key=lambda e:e['id'])
    return {'schema':'loom.ontology.validation.v1','ok':not issues,'ontology_hash':c['ontology_hash'],
            'instance_hash':digest(normalized),'issues':issues,'instances':normalized}


def expand(ontology, packet, max_rounds=8, max_derived=4096):
    integer(max_rounds,1,32);integer(max_derived,1,MAX_ITEMS)
    report = validate(ontology,packet);require(report['ok'], 'cannot expand invalid instances')
    require(all(e.get('status') != 'derived' for e in report['instances']['edges']), 'expand needs base assertions; recompute from original input, not a previous expansion')
    o = compile_ontology(ontology)['ontology']; p = report['instances']
    known = {(e['subject'],e['predicate'],e['object']):e for e in p['edges']}; derived = []
    complete = False; attempts = 0
    for round_no in range(1,max_rounds+1):
        proposals = {}
        def offer(a,pred,b,premises,rule):
            nonlocal attempts
            attempts += 1
            require(attempts <= 200000, 'rule candidate budget exceeded; narrow the ontology')
            key = (a,pred,b)
            if key in known or key in proposals: return
            require(len(derived)+len(proposals) < max_derived, 'derived relation limit exceeded; narrow the ontology')
            sources = sorted({s for e in premises for s in e.get('source_ids',[])})
            proposals[key] = {'id':'derived:'+digest([key,rule,[e['id'] for e in premises]])[:24],
                              'subject':a,'predicate':pred,'object':b,'status':'derived','source_ids':sources,
                              'native':{'rule':rule,'premise_ids':[e['id'] for e in premises],'round':round_no}}
        for key,e in sorted(known.items()):
            a,pred,b = key; r = o['relations'][pred]
            if r['symmetric']: offer(b,pred,a,[e],'symmetric:'+pred)
            if 'inverse' in r: offer(b,r['inverse'],a,[e],'inverse:'+pred)
        by = {}
        for (a,pred,b),e in known.items(): by.setdefault((pred,a),[]).append(e)
        for rule in o['rules']:
            left,right = rule['chain']
            for (a,pred,b),e in sorted(known.items()):
                if pred == left:
                    for f in by.get((right,b),[]): offer(a,rule['infer'],f['object'],[e,f],rule['id'])
        if not proposals: complete = True;break
        known.update(proposals);derived.extend(proposals.values())
    expanded = deepcopy(p);expanded['edges'] += derived
    checked = validate(ontology,expanded)
    return {'schema':'loom.ontology.expansion.v1','ok':checked['ok'],'complete':complete,'rounds':round_no,
            'derived':derived,'instances':checked['instances'],'issues':checked['issues'],
            'ontology_hash':report['ontology_hash'],'input_hash':report['instance_hash'],
            'boundary':'Caller-declared relation rules; derived assertions retain premises and are not promoted to observed facts.'}


def project(ontology, snapshot):
    """Pure adapter: selected graph types live in data.ontology_type/properties."""
    o = compile_ontology(ontology)['ontology']; s = read_json(snapshot)
    require(isinstance(s.get('nodes'),list) and isinstance(s.get('edges'),list), 'expected graph snapshot')
    nodes = []; edges = []; omitted = []
    for n in s['nodes']:
        t = n.get('data',{}).get('ontology_type')
        if not t or n.get('effective_status') == 'stale': omitted.append({'kind':'node','id':n['id'],'reason':'untyped_or_stale'});continue
        nodes.append({'id':n['id'],'type':t,'properties':deepcopy(n['data'].get('properties',{})),
                      'source_ids':n.get('source_ids',[]),'clocks':n.get('clocks',{}),
                      'status':n.get('effective_status',n.get('status','annotated')),'native':deepcopy(n)})
    ids = {n['id'] for n in nodes}
    for e in s['edges']:
        if e.get('effective_status') == 'stale' or e['subject'] not in ids or e['object'] not in ids or e['predicate'] not in o['relations']:
            omitted.append({'kind':'edge','id':e['id'],'reason':'outside_projection_or_stale'});continue
        edges.append({k:deepcopy(e[k]) for k in ('id','subject','predicate','object','source_ids','clocks','status') if k in e})
        edges[-1]['native'] = deepcopy(e)
    p = {'schema':DATA,'ontology_id':o['id'],'ontology_version':o['version'],'nodes':nodes,'edges':edges}
    return {**validate(o,p),'omitted':omitted,'graph_hash':s.get('graph_hash'), 'projection_hash':s.get('projection_hash')}


def diff(before, after):
    a,b = compile_ontology(before),compile_ontology(after)
    require(a['ontology']['id'] == b['ontology']['id'], 'cannot compare unrelated ontologies')
    require(b['ontology']['version'] > a['ontology']['version'], 'after version must increase')
    changes = []
    for section in ('types','relations'):
        x,y = a['ontology'][section],b['ontology'][section]
        for key in sorted(set(x)|set(y)):
            if x.get(key) != y.get(key): changes.append({'section':section,'id':key,'before':x.get(key),'after':y.get(key)})
    if a['ontology']['rules'] != b['ontology']['rules']: changes.append({'section':'rules','before':a['ontology']['rules'],'after':b['ontology']['rules']})
    return {'schema':'loom.ontology.diff.v1','before_hash':a['ontology_hash'],'after_hash':b['ontology_hash'],
            'changes':changes,'requires_instance_revalidation':bool(changes),
            'boundary':'Structural comparison only; no automatic migration or compatibility guarantee.'}


FUNCTIONS = {'ontology_check':check,'ontology_validate':validate,'ontology_expand':expand,'ontology_project':project,'ontology_diff':diff}


def call(name, values):
    fn = FUNCTIONS[name]
    expected = 1 if name == 'ontology_check' else 2
    require(len(values) == expected, name+' expects '+str(expected)+' arguments')
    return fn(*values)


def cli(args):
    parser = argparse.ArgumentParser(prog='loom ontology',description=__doc__)
    subs = parser.add_subparsers(dest='command',required=True)
    for command in ('check','format','validate','expand','project','diff'):
        s = subs.add_parser(command);s.add_argument('ontology');s.add_argument('--out')
        if command in ('validate','expand','project','diff'): s.add_argument('input')
    a = parser.parse_args(args)
    try:
        if a.command == 'format': result = compile_ontology(a.ontology)['ontology']
        elif a.command == 'check': result = check(a.ontology)
        else: result = FUNCTIONS['ontology_'+a.command](a.ontology,a.input)
        encoded = json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
        if a.out: Path(a.out).write_text(encoded)
        else: print(encoded,end='')
        return 0 if result.get('ok',True) else 1
    except (ResearchError,OSError,TypeError,KeyError) as e:
        print(json.dumps({'ok':False,'error':str(e)})); return 2
