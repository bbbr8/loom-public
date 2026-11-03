"""Shared CLI and program adapter for persistent Loom research capabilities."""
import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote
from .contracts import read_json, require, result, ResearchError, canonical
from .store import GraphStore
from . import engine, retrieval, identity, analytics, compiler, planner, learning
from .bindings import bound_db,bind,ROOT
from .studio import FUNCTIONS as STUDIO_FUNCTIONS

FUNCTIONS={'graph_init','graph_ingest','graph_snapshot','graph_diff','graph_withdraw','graph_verify','graph_search',
           'graph_paths','graph_overview','graph_resolve','graph_analyze','graph_compile','graph_evolve','graph_import_timeline','graph_ingest_files',
           'research_plan','research_run','research_learn','research_methods','graph_propose_predicate','graph_activate_predicate'}
FUNCTIONS |= STUDIO_FUNCTIONS


def call(name,values):
    if name in STUDIO_FUNCTIONS:
        from .studio import call as studio_call
        return studio_call(name,values)
    require(values,'research function needs an explicit graph path or model packet')
    if name=='research_plan':
        require(len(values) in (1,2),'research_plan(model, options?)')
        return planner.plan(read_json(values[0]),**(values[1] if len(values)==2 else {}))
    path=values[0]
    require(isinstance(path,str),'graph path must be text')
    if name=='graph_init':
        require(len(values)==1,'graph_init(path)')
        return GraphStore(path,create=True).snapshot()
    store=GraphStore(path)
    if name in ('graph_snapshot','graph_evolve','graph_resolve','graph_analyze','graph_overview','research_methods'):
        require(len(values) in (1,2),name+'(path, options?)')
        options=values[1] if len(values)==2 else {};require(isinstance(options,dict),'options must be a map')
        if name=='graph_snapshot':return store.snapshot(**options)
        if name=='graph_evolve':return engine.evolve(store,**options)
        if name=='graph_resolve':return identity.resolve(store.snapshot(),**options)
        if name=='graph_analyze':return analytics.analyze(store.snapshot(),**options)
        if name=='graph_overview':return retrieval.overview(store.snapshot(),**options)
        return learning.methods(store.snapshot(include_stale=True),**options)
    if name=='graph_verify':
        require(len(values)==1,'graph_verify(path)');return store.verify()
    if name=='graph_diff':
        require(len(values) in (2,3),'graph_diff(path, before, after?)');return store.diff(*values[1:])
    if name=='graph_withdraw':
        require(len(values)==3,'graph_withdraw(path, origin, batch_id)')
        r=store.withdraw(values[1],values[2]);return result(withdrawal=r,evolution=engine.evolve(store))
    if name=='graph_search':
        require(len(values) in (2,3),'graph_search(path, query, options?)')
        return store.search(values[1],**(values[2] if len(values)==3 else {}))
    if name=='graph_paths':
        require(len(values) in (2,3),'graph_paths(path, start, options?)')
        return retrieval.paths(store.snapshot(),values[1],**(values[2] if len(values)==3 else {}))
    if name=='graph_import_timeline':
        require(len(values)==3,'graph_import_timeline(path, report, batch_id)')
        return engine.import_timeline(store,read_json(values[1]),values[2])
    if name=='graph_ingest':
        require(len(values) in (2,3),'graph_ingest(path, packet, options?)')
        options=values[2] if len(values)==3 else {};require(isinstance(options,dict),'options must be a map')
        return engine.ingest(store,read_json(values[1]),**options)
    require(len(values)==2,name+'(path, packet)')
    packet=read_json(values[1])
    if name=='graph_ingest_files':return engine.import_files(store,packet)
    if name=='graph_compile':return compiler.compile_graph(store.snapshot(),packet)
    if name=='research_run':return engine.run(store,packet)
    if name=='research_learn':return learning.evaluate(store,packet)
    if name=='graph_propose_predicate':return learning.propose_predicate(store,packet)
    if name=='graph_activate_predicate':return learning.activate_predicate(store,packet)
    raise ResearchError('unknown research function')


def export_jsonld(snapshot):
    def iri(kind,ident):return 'urn:loom:'+kind+':'+quote(ident,safe='')
    graph=[]
    for n in snapshot['nodes']:
        graph.append({'@id':iri('node',n['id']),'@type':['loom:'+n['type'],'prov:Entity'],
                      'label':n['label'],'revision':n['revision'],'status':n['effective_status'],
                      'text':n.get('text',''),'data':n['data'],'clocks':n['clocks'],
                      'prov:wasDerivedFrom':[{'@id':iri('node',s)} for s in n['source_ids']],
                      'contentHash':n['content_hash']})
    for e in snapshot['edges']:
        graph.append({'@id':iri('assertion',e['id']),'@type':'loom:Assertion',
                      'subject':{'@id':iri('node',e['subject'])},'predicate':e['predicate'],
                      'object':{'@id':iri('node',e['object'])},'status':e['effective_status'],
                      'data':e['data'],'clocks':e['clocks'],'revision':e['revision'],
                      'prov:wasDerivedFrom':[{'@id':iri('node',s)} for s in e['source_ids']]})
    return {'@context':{'loom':'urn:loom:vocabulary:','prov':'http://www.w3.org/ns/prov#',
                        **{k:'loom:'+k for k in ('label','revision','status','text','contentHash','subject','predicate','object')},
                        'data':{'@id':'loom:data','@type':'@json'},'clocks':{'@id':'loom:clocks','@type':'@json'}},
            '@graph':graph,'loom:graphHash':snapshot['graph_hash'],'loom:graphRevision':snapshot['revision']}


def cli(family,args):
    parser=argparse.ArgumentParser(prog='loom '+family,description='Persistent, source-bound graph and analytical research')
    subs=parser.add_subparsers(dest='command',required=True)
    if family=='graph':
        for command in ('init','ingest','inspect','asof','diff','withdraw','verify','search','paths','resolve','analyze','compile',
                        'evolve','overview','ingest-files','import-timeline','export','journal','replay','propose','activate'):
            s=subs.add_parser(command);s.add_argument('--db',default=bound_db() or str(ROOT/'state/research.sqlite'));s.add_argument('--out')
            if command in ('ingest','ingest-files','compile','import-timeline','replay','propose','activate'):s.add_argument('input')
            if command in ('search','paths'):s.add_argument('query')
            if command in ('inspect','asof','search','paths','resolve','analyze','evolve','overview'):s.add_argument('--options',default='{}')
            if command=='diff':s.add_argument('--before',type=int,required=True);s.add_argument('--after',type=int)
            if command=='withdraw':s.add_argument('--origin',required=True);s.add_argument('--batch-id',required=True)
            if command=='import-timeline':s.add_argument('--batch-id',required=True)
            if command=='export':s.add_argument('--format',choices=['json','jsonld'],default='jsonld')
    else:
        for command in ('run','plan','compile','learn','methods','bind','unbind','demo'):
            s=subs.add_parser(command);s.add_argument('--out')
            if command not in ('plan','demo'):s.add_argument('--db',default=bound_db() or str(ROOT/'state/research.sqlite'))
            if command not in ('methods','bind','unbind','demo'):s.add_argument('input')
            if command in ('plan','methods'):s.add_argument('--options',default='{}')
            if command=='demo':s.add_argument('--directory',required=True)
    a=parser.parse_args(args)
    try:
        options=read_json(json.loads(a.options)) if hasattr(a,'options') else {}
        if family=='research':
            if a.command=='plan':out=call('research_plan',[read_json(a.input),options])
            elif a.command=='methods':out=call('research_methods',[a.db,options])
            elif a.command in ('bind','unbind'):out=bind(a.db,a.command=='bind')
            elif a.command=='demo':
                directory=Path(a.directory);directory.mkdir(parents=True,exist_ok=False)
                store=GraphStore(directory/'research.sqlite',create=True)
                out=engine.run(store,read_json(str(ROOT/'examples/data/research-run.json')))
                (directory/'result.json').write_text(json.dumps(out,indent=2)+'\n')
                out={**out,'demo_directory':str(directory.resolve())}
            else:out=call({'run':'research_run','compile':'graph_compile','learn':'research_learn'}[a.command],[a.db,read_json(a.input)])
        elif a.command=='init':out=call('graph_init',[a.db])
        elif a.command in ('inspect','asof'):out=call('graph_snapshot',[a.db,options])
        elif a.command=='diff':out=call('graph_diff',[a.db,a.before,a.after])
        elif a.command=='withdraw':out=call('graph_withdraw',[a.db,a.origin,a.batch_id])
        elif a.command=='verify':out=call('graph_verify',[a.db])
        elif a.command in ('search','paths'):out=call('graph_'+a.command,[a.db,a.query,options])
        elif a.command in ('resolve','analyze','evolve','overview'):out=call('graph_'+a.command,[a.db,options])
        elif a.command=='import-timeline':out=call('graph_import_timeline',[a.db,read_json(a.input),a.batch_id])
        elif a.command=='journal':out=GraphStore(a.db).journal()
        elif a.command=='replay':out=GraphStore.replay(a.db,read_json(a.input)).verify()
        elif a.command=='export':
            snap=GraphStore(a.db).snapshot();out=export_jsonld(snap) if a.format=='jsonld' else snap
        else:out=call({'ingest':'graph_ingest','ingest-files':'graph_ingest_files','compile':'graph_compile','propose':'graph_propose_predicate','activate':'graph_activate_predicate'}[a.command],[a.db,read_json(a.input)])
        encoded=json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
        if a.out:
            path=Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x') as f:f.write(encoded)
        else:print(encoded,end='')
        return 3 if out.get('state','').startswith(('PARTIAL','INCOMPLETE','OPEN_')) else 0
    except (ResearchError,ValueError,OSError,TypeError,KeyError,sqlite3.Error,RecursionError) as exc:
        print(json.dumps(result('ERROR',diagnostic=str(exc))))
        return 2
