"""Source-oriented research CLI and shared language/API dispatch."""
import argparse
import json
from pathlib import Path
import sqlite3
from .contracts import require, result, read_json, ResearchError
from .store import GraphStore
from . import indexed, evidence, intake

FUNCTIONS={'graph_hybrid_search','graph_explain','graph_inspect_record','graph_propose_relations',
           'graph_review_relation','graph_suggest_relations','graph_import_documents','research_next'}
CASE_FUNCTIONS={'graph_case_map','graph_case_episode','graph_case_lifecycle','graph_case_compare',
                'graph_case_versions','graph_case_recover','graph_case_evaluate'}
FUNCTIONS |= CASE_FUNCTIONS
FUNCTIONS |= {'graph_normalize','graph_normalization'}
FUNCTIONS |= {'graph_normalized_search'}


def call(name,values):
    require(len(values)>=1 and isinstance(values[0],str),'explicit graph path required')
    store=GraphStore(values[0])
    if name in ('graph_normalize','graph_normalization'):
        from . import normalization
        require(len(values)==2,name+'(path, packet/node_id)')
        return normalization.derive(store,read_json(values[1])) if name=='graph_normalize' else normalization.inspect(store,values[1])
    if name in CASE_FUNCTIONS:
        from . import casework
        require(len(values)==2,name+'(path, packet/episode)')
        function={'graph_case_map':casework.map_records,'graph_case_episode':casework.episode,
                  'graph_case_lifecycle':casework.lifecycle,'graph_case_compare':casework.compare,
                  'graph_case_versions':casework.versions,'graph_case_recover':casework.recover,
                  'graph_case_evaluate':casework.evaluate}[name]
        return function(store,values[1] if name in ('graph_case_episode','graph_case_lifecycle') else read_json(values[1]))
    if name in ('graph_hybrid_search','graph_normalized_search','graph_explain','graph_inspect_record','research_next'):
        require(len(values) in (2,3),name+'(path, query/id, options?)')
        options=values[2] if len(values)==3 else {};require(isinstance(options,dict),'options must be a map')
        function={'graph_hybrid_search':indexed.search,'graph_normalized_search':indexed.normalized_search,'graph_explain':evidence.explain,'graph_inspect_record':indexed.inspect,'research_next':evidence.next_steps}[name]
        return function(store,values[1],**options)
    if name=='graph_suggest_relations':
        require(len(values) in (1,2),'graph_suggest_relations(path, options?)')
        return evidence.suggest(store,**(values[1] if len(values)==2 else {}))
    require(len(values)==2,name+'(path, packet)')
    packet=read_json(values[1])
    return {'graph_propose_relations':evidence.propose,'graph_review_relation':evidence.review,
            'graph_import_documents':intake.ingest}[name](store,packet)


def restore(source,destination):
    """Restore to a fresh path and replay-verify before atomic publication."""
    import os
    import tempfile
    from loom_sqlite import checked_path,read_connection,publish
    src=checked_path(source);dest=checked_path(destination)
    require(src!=dest and not dest.exists(),'restore requires a fresh destination; existing files are preserved')
    dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.loom-restore-',suffix='.sqlite',dir=dest.parent);os.close(fd);tmp=Path(tmp)
    try:
        with read_connection(src) as c:
            target=sqlite3.connect(tmp)
            try:c.backup(target)
            finally:target.close()
        store=GraphStore(tmp);verification=store.verify()
        require(verification.get('ok') is True,'restored journal replay verification failed')
        with store.connection() as c:
            require([r[0] for r in c.execute('PRAGMA integrity_check')]==['ok'],'restored SQLite integrity failed')
            require(not list(c.execute('PRAGMA foreign_key_check')),'restored foreign key check failed')
        indexed.migrate(store);publish(tmp,dest)
        return result('RESTORED',database=str(dest),verification=verification)
    finally:tmp.unlink(missing_ok=True)


def cli(args):
    parser=argparse.ArgumentParser(prog='loom studio',description='Evidence intake, hybrid retrieval, relationship review and explanation')
    subs=parser.add_subparsers(dest='command',required=True)
    cases={n.replace('graph_','').replace('_','-'):n for n in CASE_FUNCTIONS}
    for command in ('migrate','search','normalized-search','explain','inspect','next','suggest','propose','review','import','index','restore',*sorted(cases)):
        p=subs.add_parser(command);p.add_argument('--db',required=True);p.add_argument('--out')
        if command in ('search','normalized-search','explain','inspect','next'):p.add_argument('query');p.add_argument('--options',default='{}')
        if command in ('search','explain','index'):
            p.add_argument('--encoder');p.add_argument('--index');p.add_argument('--reranker')
        if command in ('propose','review','import'):p.add_argument('packet')
        if command in cases:p.add_argument('input')
        if command=='suggest':p.add_argument('--after-revision',type=int,default=0)
        if command=='restore':p.add_argument('--source',required=True)
    a=parser.parse_args(args)
    try:
        if a.command in cases:
            value=a.input if a.command in ('case-episode','case-lifecycle') else read_json(a.input)
            out=call(cases[a.command],[a.db,value])
        elif a.command=='restore':out=restore(a.source,a.db)
        else:
            store=GraphStore(a.db)
            if a.command=='migrate':out=indexed.migrate(store)
            elif a.command=='index':
                from .semantic import sync
                require(a.encoder and a.index,'index needs --encoder and --index');out=sync(store,a.index,a.encoder)
            elif a.command in ('search','normalized-search','explain','inspect','next'):
                options=json.loads(a.options)
                require(isinstance(options,dict),'options must be a map')
                if getattr(a,'encoder',None):
                    from .semantic import rank
                    require(a.index,'semantic retrieval needs --index')
                    options['semantic']=rank(store,a.query,a.index,a.encoder,reranker_dir=a.reranker)
                out=call({'search':'graph_hybrid_search','normalized-search':'graph_normalized_search','explain':'graph_explain','inspect':'graph_inspect_record','next':'research_next'}[a.command],[a.db,a.query,options])
            elif a.command=='suggest':out=evidence.suggest(store,after_revision=a.after_revision)
            else:out=call({'propose':'graph_propose_relations','review':'graph_review_relation','import':'graph_import_documents'}[a.command],[a.db,read_json(a.packet)])
        raw=json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
        if a.out:
            with Path(a.out).open('x') as f:f.write(raw)
        else:print(raw,end='')
        return 0
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error,ImportError) as e:
        print(json.dumps(result('ERROR',diagnostic=str(e))));return 2
