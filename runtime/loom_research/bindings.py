"""Explicit process/default graph binding; no daemon or corpus scanning."""
import os
from pathlib import Path
from .contracts import read_json, canonical, require, result

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'state/research-config.json'


def bound_db():
    if 'LOOM_RESEARCH_DB' in os.environ:return os.environ['LOOM_RESEARCH_DB'] or None
    if CONFIG.is_file():
        cfg=read_json(str(CONFIG))
        if cfg.get('schema')=='loom.research.binding.v1' and cfg.get('enabled') is True:
            return cfg.get('database')
    return None


def bind(path,enabled=True):
    from .store import GraphStore
    if enabled:GraphStore(path)
    value={'schema':'loom.research.binding.v1','enabled':bool(enabled),'database':str(Path(path).expanduser().resolve()) if enabled else None}
    CONFIG.parent.mkdir(parents=True,exist_ok=True)
    temp=CONFIG.with_name('.research-binding-'+str(os.getpid())+'.tmp')
    with temp.open('x') as stream:stream.write(canonical(value)+'\n')
    os.replace(temp,CONFIG)
    return result('BOUND' if enabled else 'UNBOUND',binding=value,config=str(CONFIG))
