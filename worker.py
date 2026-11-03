"""Bounded inline-data API adapter for the complete Loom runtime."""
import json
import os
from pathlib import Path
import re
import resource
import signal
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime'))
os.environ['LOOM_RESEARCH_DB'] = ''
os.environ['SURD_USAGE'] = '0'
os.environ['SURD_PORTABLE_TEST'] = '1'
from surd_search import SearchIndex, reason, posture, quantum
from loom_research.cli import call, export_jsonld
from loom_research.store import GraphStore
from loom_research.contracts import ResearchConflict
from loom_qulap.model import solve, sensitivity, contradiction_core

SEARCH_OPS = {'search', 'reason', 'posture', 'quantum'}
GRAPH_READ = {'graph_snapshot', 'graph_search', 'graph_paths', 'graph_overview',
              'graph_resolve', 'graph_analyze', 'graph_verify', 'research_methods',
              'graph_hybrid_search','graph_explain','graph_inspect_record','research_next'}
GRAPH_READ |= {'graph_case_episode','graph_case_lifecycle','graph_normalization','graph_normalized_search'}
GRAPH_PACKET = {'graph_ingest', 'graph_compile', 'research_run', 'research_learn',
                'graph_propose_predicate', 'graph_activate_predicate','graph_propose_relations','graph_review_relation'}
GRAPH_PACKET |= {'graph_case_map','graph_case_compare','graph_case_versions','graph_case_recover','graph_case_evaluate'}
GRAPH_PACKET |= {'graph_normalize'}
GRAPH_OTHER = {'graph_init', 'graph_evolve', 'graph_diff', 'graph_withdraw',
               'graph_journal', 'graph_export','graph_suggest_relations'}
MODEL_OPS = {'research_plan', 'qulap_solve', 'qulap_core', 'qulap_sensitivity'}
MODEL_OPS |= {'normalize_value','normalize_record'}
OPERATIONS = sorted(SEARCH_OPS | GRAPH_READ | GRAPH_PACKET | GRAPH_OTHER | MODEL_OPS)


def object_value(data, key):
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(key + ' must be an inline object')
    return value


def graph_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('graph requires a simple name')
    directory = os.environ.get('LOOM_DATA_DIR')
    if not directory:
        raise ValueError('graph storage must be configured by the operator')
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (name + '.sqlite')
    if path.is_symlink() or any(p.is_symlink() for p in (root / (name + suffix) for suffix in ('.sqlite-wal', '.sqlite-shm', '.sqlite-journal'))):
        raise ValueError('linked graph files are not accepted')
    return str(path)


def run(data):
    if not isinstance(data, dict):
        raise ValueError('request must be an object')
    op = data.get('operation', 'search')
    if not isinstance(op, str) or op not in OPERATIONS:
        raise ValueError('unsupported operation')
    allowed = {'operation', 'documents', 'question', 'spec', 'options'} if op in SEARCH_OPS else {
        'operation', 'graph', 'packet', 'options', 'query', 'before', 'after', 'origin', 'batch_id'}
    if set(data) - allowed:
        raise ValueError('unknown request field')
    options = object_value(data, 'options')
    if op in SEARCH_OPS:
        docs = data.get('documents')
        question = data.get('question')
        if not isinstance(docs, list) or len(docs) > 100:
            raise ValueError('documents must be a list of at most 100 items')
        if not isinstance(question, str) or not 1 <= len(question) <= 8192:
            raise ValueError('question must contain 1..8192 characters')
        index = SearchIndex(docs)
        if op == 'search':
            return index.search(question, options)
        return {'reason': reason, 'posture': posture, 'quantum': quantum}[op](index, question, object_value(data, 'spec'))
    if op in MODEL_OPS:
        model = object_value(data, 'packet')
        if op in ('normalize_value','normalize_record'):
            from loom_research.normalization import call as normalization_call
            return normalization_call(op,[model])
        if op == 'research_plan':
            return call(op, [model, options])
        return {'qulap_solve': solve, 'qulap_core': contradiction_core,
                'qulap_sensitivity': sensitivity}[op](model, **options)
    path = graph_path(data.get('graph'))
    if op=='graph_snapshot':
        return GraphStore(path).page(**options)
    if op=='graph_export':
        page=GraphStore(path).page(**options)
        exported=export_jsonld(page);exported['pagination']=page['pagination']
        return exported
    if op in {'graph_init', 'graph_ingest', 'research_run', 'research_learn'}:
        store = GraphStore(path, create=not Path(path).exists())
        if op == 'graph_init':
            return store.page(**options)
    if op in GRAPH_PACKET:
        packet = object_value(data, 'packet')
        if op == 'research_run':
            for key in ('model', 'batch', 'plan', 'snapshot'):
                if key in packet:
                    object_value(packet, key)
        if op=='graph_ingest':return call(op,[path,packet,options])
        return call(op, [path, packet])
    if op in GRAPH_READ or op in ('graph_evolve','graph_suggest_relations'):
        if op=='graph_evolve' and os.environ.get('LOOM_WORKER_PROFILE')=='job':
            options={'max_batches':64,'seconds':90,**options}
        if op in {'graph_search', 'graph_paths','graph_hybrid_search','graph_normalized_search','graph_explain','graph_inspect_record','research_next',
                  'graph_case_episode','graph_case_lifecycle','graph_normalization'}:
            query = data.get('query')
            if not isinstance(query, str) or not 1 <= len(query) <= 8192:
                raise ValueError('query required')
            return call(op, [path, query] if op.startswith('graph_case_') or op=='graph_normalization' else [path, query, options])
        return call(op, [path] if op == 'graph_verify' else [path, options])
    if op == 'graph_diff':
        return call(op, [path, data.get('before'), data.get('after')])
    if op == 'graph_withdraw':
        return call(op, [path, data.get('origin'), data.get('batch_id')])
    store = GraphStore(path)
    return store.journal() if op == 'graph_journal' else export_jsonld(store.snapshot())


def decode(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite number')))


if __name__ == '__main__':
    long=os.environ.get('LOOM_WORKER_PROFILE')=='job'
    # The isolated worker keeps a wall deadline even if its queue parent dies.
    signal.alarm(125 if long else 12)
    resource.setrlimit(resource.RLIMIT_CPU, (120,120) if long else (10,10))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith('linux'):
        budget=(3*1024**3 if long else 512*1024**2)
        resource.setrlimit(resource.RLIMIT_AS, (budget,budget))
    try:
        result = run(decode(sys.stdin.read(256 * 1024 + 1)))
        payload = json.dumps({'ok': True, 'result': result}, allow_nan=False)
        if len(payload.encode()) > 4 * 1024**2:
            sys.stdout.write(json.dumps({'ok':False,'error':'result_limit_use_pagination'}));sys.exit(2)
        sys.stdout.write(payload)
    except ResearchConflict:
        sys.stdout.write(json.dumps({'ok':False,'error':'graph_revision_conflict','hint':'Read the current graph and recompute before submitting a new mutation.'}))
        sys.exit(3)
    except Exception:
        sys.stdout.write(json.dumps({'ok': False, 'error': 'invalid_analysis_request'}))
        sys.exit(2)
