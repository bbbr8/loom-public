"""Portable SQLite bootstrap for Loom's native journaled research graph."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from loom_research.contracts import ResearchError,fields,require,result,digest,normalize_batch
from loom_research.store import DDL,GraphStore
from loom_research.engine import records_batch,evolve

APPLICATION_ID=0x4c4f4f4d
SCHEMA_VERSION=1
FUNCTIONS={'sqlite_bootstrap','sqlite_status','sqlite_backup'}
NATIVE_TABLES={'meta','commits','items','versions','dependencies','adjacency','node_text'}
VIEWS={
'loom_node_rows':"""SELECT id,revision,active AS materialized_active,
 json_extract(payload,'$.type') AS node_type,json_extract(payload,'$.label') AS label,
 json_extract(payload,'$.text') AS text,json_extract(payload,'$.origin') AS origin,
 json_extract(payload,'$.locator') AS locator,json_extract(payload,'$.space') AS space,
 json_extract(payload,'$.status') AS stored_status,
 json_extract(payload,'$.data') AS data_json,json_extract(payload,'$.clocks') AS clocks_json,
 json_extract(payload,'$.source_ids') AS source_ids_json,
 json_extract(payload,'$._deps') AS captured_dependencies_json,content_hash,seq
 FROM items WHERE kind='node'""",
'loom_edge_rows':"""SELECT id,revision,active AS materialized_active,
 json_extract(payload,'$.subject') AS subject,json_extract(payload,'$.predicate') AS predicate,
 json_extract(payload,'$.object') AS object,json_extract(payload,'$.status') AS stored_status,
 json_extract(payload,'$.source_ids') AS source_ids_json,
 json_extract(payload,'$.data') AS data_json,content_hash,seq FROM items WHERE kind='edge'""",
'loom_journal_rows':"SELECT seq,batch_id,input_hash,recorded_at,previous_hash,commit_hash FROM commits"}
EXTENSION="CREATE TABLE IF NOT EXISTS loom_bootstrap_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL);\n"+''.join('CREATE VIEW IF NOT EXISTS '+name+' AS '+sql+';\n' for name,sql in VIEWS.items())
MIGRATION_SHA256=hashlib.sha256((DDL+EXTENSION).encode()).hexdigest()
DEMO_BATCH='loom-bootstrap-demo-v1'
DEMO_RECORDS=[
 {'id':'demo:release','origin':'synthetic:bootstrap:release','locator':'SYNTHETIC:release:1','label':'Synthetic release approval','text':'The recorded release approved a credit of 12500 cents.','clocks':{'event':['2026-01-02','2026-01-02']},'data':{'source_tier':'synthetic','amount_cents':12500,'currency':'USD','money_stage':'approved'}},
 {'id':'demo:credit','origin':'synthetic:bootstrap:ledger','locator':'SYNTHETIC:ledger:1','label':'Synthetic credit ledger','text':'The ledger records the applied credit of 12500 cents.','clocks':{'event':['2026-01-03','2026-01-03']},'data':{'source_tier':'synthetic','amount_cents':12500,'currency':'USD','money_stage':'applied'}},
 {'id':'demo:invoice','origin':'synthetic:bootstrap:invoice','locator':'SYNTHETIC:invoice:1','label':'Synthetic invoice','text':'The invoice lists an amount of 45000 cents before the credit.','clocks':{'event':['2026-01-01','2026-01-01']},'data':{'source_tier':'synthetic','amount_cents':45000,'currency':'USD','money_stage':'billed'}}]


def schema_sql():
    """Raw SQL is for an empty database; the Python adapter guards existing files."""
    return ('-- Loom SQLite bootstrap v1. Empty database only. Synthetic data is separate.\n'
            '-- SQL views expose stored rows; use graph_snapshot for effective stale/temporal states.\n'
            'BEGIN IMMEDIATE;\n'+DDL+"\nINSERT INTO meta(key,value) VALUES('schema','loom.graph.store.v1') ON CONFLICT(key) DO NOTHING;\n"+
            EXTENSION+"INSERT INTO loom_bootstrap_migrations VALUES(1,'native-graph-and-inspection-views','"+MIGRATION_SHA256+"',strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(version) DO NOTHING;\n"+
            'PRAGMA application_id='+str(APPLICATION_ID)+';\nPRAGMA user_version=1;\nCOMMIT;\n')


def checked_path(value):
    require(isinstance(value,str) and value.strip() and value!=':memory:','an explicit persistent SQLite path is required')
    path=Path(value).expanduser().absolute()
    require(not path.is_symlink(),'linked database files are not accepted')
    for suffix in ('-wal','-shm','-journal'):
        require(not Path(str(path)+suffix).is_symlink(),'linked SQLite sidecars are not accepted')
    return path.resolve()


def read_connection(path):
    require(path.is_file(),'SQLite database does not exist')
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row
    return closing(c)


def native_check(c):
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require(NATIVE_TABLES<=tables,'existing file is not a complete Loom graph')
    row=c.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    require(row is not None and row[0]=='loom.graph.store.v1','existing file is not a Loom graph')
    require(c.execute('PRAGMA application_id').fetchone()[0] in (0,APPLICATION_ID),'database application id belongs to another application')
    require(c.execute('PRAGMA user_version').fetchone()[0] in (0,SCHEMA_VERSION),'unsupported SQLite bootstrap schema version')
    for table,needed in {'commits':{'seq','batch_id','commit_hash'},'items':{'kind','id','payload','active','revision'},'versions':{'kind','id','seq','payload'}}.items():
        require(needed<={r[1] for r in c.execute('PRAGMA table_info('+table+')')},'incompatible native graph table '+table)
    # JSON1 is required by the additive inspection views; FTS5 is native to Loom.
    require(c.execute("SELECT json_valid('{}')").fetchone()[0]==1,'SQLite JSON support is required')
    return tables


def migration_check(c,tables):
    if 'loom_bootstrap_migrations' not in tables:return False
    rows=list(c.execute('SELECT version,sha256 FROM loom_bootstrap_migrations ORDER BY version'))
    require(len(rows)==1 and rows[0]['version']==1 and rows[0]['sha256']==MIGRATION_SHA256,'bootstrap migration fingerprint mismatch')
    views={r[0]:r[1] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='view'")}
    require(set(VIEWS)<=set(views),'bootstrap inspection views are missing')
    for name,sql in VIEWS.items():
        actual=' '.join(views[name].split()).rstrip(';')
        expected=' '.join(('CREATE VIEW '+name+' AS '+sql).split()).rstrip(';')
        require(actual==expected,'bootstrap inspection view fingerprint mismatch: '+name)
    require(c.execute('PRAGMA application_id').fetchone()[0]==APPLICATION_ID and c.execute('PRAGMA user_version').fetchone()[0]==1,'bootstrap metadata mismatch')
    return True


def status(database,check=False):
    path=checked_path(database);require(type(check) is bool,'check must be boolean')
    with read_connection(path) as c:
        c.execute('BEGIN');tables=native_check(c);bootstrapped=migration_check(c,tables)
        counts={kind:{'active':0,'inactive':0} for kind in ('node','edge')}
        for r in c.execute('SELECT kind,active,COUNT(*) AS n FROM items GROUP BY kind,active'):
            counts[r['kind']]['active' if r['active'] else 'inactive']=r['n']
        head=c.execute('SELECT seq,commit_hash FROM commits ORDER BY seq DESC LIMIT 1').fetchone()
        integrity=None
        if check:
            deadline=time.monotonic()+10
            c.set_progress_handler(lambda: int(time.monotonic()>deadline),10000)
            integrity=[r[0] for r in c.execute('PRAGMA quick_check')]
            require(integrity==['ok'],'SQLite quick_check failed')
        return result('READY' if bootstrapped else 'NATIVE_GRAPH',database=str(path),bootstrap_schema=1 if bootstrapped else None,
                      migration_sha256=MIGRATION_SHA256 if bootstrapped else None,sqlite_version=sqlite3.sqlite_version,
                      revision=head['seq'] if head else 0,commit_hash=head['commit_hash'] if head else '0'*64,
                      counts=counts,views=sorted(VIEWS) if bootstrapped else [],quick_check=integrity,
                      boundary='SQL counts/views describe stored materialization. Native graph snapshots calculate stale and temporal eligibility.')


def publish(temp,destination):
    # Same-directory link publication is atomic and refuses an existing target.
    with temp.open('rb') as stream:os.fsync(stream.fileno())
    os.link(temp,destination)
    temp.unlink()
    fd=os.open(destination.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def seed_demo(path):
    store=GraphStore(path);store.apply(records_batch(DEMO_BATCH,DEMO_RECORDS),expected_revision=0)
    progress=evolve(store,max_batches=64,seconds=10)
    require(progress['evolution_complete'],'synthetic demo evolution did not complete')


def existing_demo(c):
    row=c.execute('SELECT input_hash FROM commits WHERE batch_id=?',(DEMO_BATCH,)).fetchone()
    if row:
        require(row[0]==digest(normalize_batch(records_batch(DEMO_BATCH,DEMO_RECORDS))),'existing demo batch has different content')
    return row is not None


def apply_schema(c,existing=False,demo=False):
    c.execute('BEGIN IMMEDIATE')
    try:
        if existing:
            tables=native_check(c);migration_check(c,tables)
            require(not demo or not c.execute('SELECT COUNT(*) FROM commits').fetchone()[0] or existing_demo(c),'demo seeding requires an empty graph or the existing bootstrap demo')
            # Refuse collisions with unrelated, differently defined SQL views.
            for name,sql in c.execute("SELECT name,sql FROM sqlite_master WHERE type='view'"):
                if name in VIEWS:
                    require(' '.join(sql.split()).rstrip(';')==' '.join(('CREATE VIEW '+name+' AS '+VIEWS[name]).split()),'existing bootstrap view name collision')
        statements=''
        for line in schema_sql().splitlines():
            if line.startswith('--') or line in ('BEGIN IMMEDIATE;','COMMIT;'):continue
            statements+=line+'\n'
            if sqlite3.complete_statement(statements):c.execute(statements);statements=''
        require(not statements.strip(),'incomplete embedded migration SQL')
        c.commit()
    except Exception:c.rollback();raise


def initialize(database,demo=False):
    require(type(demo) is bool,'demo must be boolean');path=checked_path(database)
    if path.exists():
        with read_connection(path) as c:
            c.execute('BEGIN');tables=native_check(c);ready=migration_check(c,tables)
            head=c.execute('SELECT COALESCE(MAX(seq),0) FROM commits').fetchone()[0]
            seeded=existing_demo(c)
            require(not demo or not head or seeded,'demo seeding requires an empty graph or the existing bootstrap demo')
        if not ready:
            with closing(sqlite3.connect(path,timeout=5)) as c:
                apply_schema(c,existing=True,demo=demo)
        if demo and not seeded:seed_demo(path)
        return {**status(str(path)), 'initialization':'IDEMPOTENT' if ready and (not demo or seeded) else 'UPGRADED_NATIVE_GRAPH','demo_seeded':bool(demo and (seeded or not head))}
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.loom-bootstrap-',suffix='.sqlite',dir=path.parent);os.close(fd);temp=Path(name)
    try:
        with closing(sqlite3.connect(temp,timeout=5)) as c:apply_schema(c)
        if demo:seed_demo(temp)
        status(str(temp),check=True)
        publish(temp,path)
    finally:
        for suffix in ('','-wal','-shm','-journal'):
            p=Path(str(temp)+suffix)
            if p.exists():p.unlink()
    return {**status(str(path)), 'initialization':'CREATED','demo_seeded':demo}


def backup(database,destination):
    source=checked_path(database);target=checked_path(destination)
    require(source!=target,'backup destination must differ from source')
    require(not target.exists(),'backup destination already exists')
    target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.loom-backup-',suffix='.sqlite',dir=target.parent);os.close(fd);temp=Path(name)
    try:
        with read_connection(source) as left,closing(sqlite3.connect(temp,timeout=5)) as right:
            native_check(left)
            deadline=time.monotonic()+30
            def progress(*_):require(time.monotonic()<deadline,'backup exceeded 30 second budget')
            left.backup(right,pages=256,progress=progress)
        info=status(str(temp),check=True);publish(temp,target)
        hashed=hashlib.sha256()
        with target.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):hashed.update(chunk)
        return result(database=str(source),backup=str(target),revision=info['revision'],commit_hash=info['commit_hash'],
                      sha256=hashed.hexdigest(),boundary='Pinned SQLite backup; replay verification is a separate graph_verify operation.')
    finally:
        for suffix in ('','-wal','-shm','-journal'):
            p=Path(str(temp)+suffix)
            if p.exists():p.unlink()


def call(name,values):
    if name=='sqlite_bootstrap':
        require(len(values) in (1,2),'sqlite_bootstrap(path, options?)');options=values[1] if len(values)==2 else {};fields(options,(),('demo',))
        return initialize(values[0],**options)
    if name=='sqlite_status':
        require(len(values) in (1,2),'sqlite_status(path, options?)');options=values[1] if len(values)==2 else {};fields(options,(),('check',))
        return status(values[0],**options)
    require(name=='sqlite_backup' and len(values)==2,'sqlite_backup(path, new_destination)')
    return backup(*values)


def cli(argv):
    parser=argparse.ArgumentParser(prog='loom sqlite',description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ('init','inspect','backup','schema'):
        p=sub.add_parser(name)
        if name!='schema':p.add_argument('--db',required=True)
        if name=='init':p.add_argument('--demo',action='store_true')
        if name=='inspect':p.add_argument('--check',action='store_true')
        if name in ('backup','schema'):p.add_argument('--out',required=name=='backup')
    args=parser.parse_args(argv)
    try:
        if args.command=='schema':
            sql=schema_sql()
            if args.out:
                with Path(args.out).open('x') as stream:stream.write(sql)
                out=result(output=args.out,migration_sha256=MIGRATION_SHA256)
            else:print(sql,end='');return 0
        elif args.command=='init':out=initialize(args.db,args.demo)
        elif args.command=='inspect':out=status(args.db,args.check)
        else:out=backup(args.db,args.out)
        print(json.dumps(out,sort_keys=True,allow_nan=False));return 0
    except (ResearchError,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}));return 2

if __name__=='__main__':
    import sys
    sys.exit(cli(sys.argv[1:]))
