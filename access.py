"""Operator-configured per-project API principals. Tokens stay outside logs/jobs."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

READ_OPERATIONS={'search','reason','posture','quantum','graph_snapshot','graph_search','graph_paths','graph_overview',
                 'graph_resolve','graph_analyze','graph_verify','research_methods','graph_diff','graph_journal','graph_export',
                 'research_plan','qulap_solve','qulap_core','qulap_sensitivity','graph_hybrid_search','graph_explain',
                 'graph_inspect_record','research_next','graph_case_episode','graph_case_lifecycle',
                 'graph_case_compare','graph_case_versions','graph_case_evaluate'}
READ_OPERATIONS |= {'normalize_value','normalize_record','graph_normalization','graph_normalized_search'}


def principal(header,owner_key):
    if not isinstance(header,str) or not header.startswith('Bearer '):return None
    token=header[7:]
    if owner_key and hmac.compare_digest(token.encode(),owner_key.encode()):
        return {'id':'operator','role':'admin','graphs':['*']}
    config=os.environ.get('LOOM_ACCESS_FILE')
    if not config:return None
    p=Path(config)
    if not p.is_file() or p.stat().st_size>65536:return None
    try:
        data=json.loads(p.read_text())
        if data.get('schema')!='loom.access.v1' or not isinstance(data.get('principals'),list) or len(data['principals'])>100:return None
        hashed=hashlib.sha256(token.encode()).hexdigest();matched=None
        for entry in data['principals']:
            if not isinstance(entry,dict) or set(entry)!={'id','sha256','role','graphs'}:return None
            if entry['role'] not in ('reader','editor','admin') or not isinstance(entry['graphs'],list) or len(entry['graphs'])>100:return None
            if not isinstance(entry['id'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',entry['id']):return None
            if not isinstance(entry['sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',entry['sha256']):return None
            if not all(isinstance(g,str) and (g=='*' or re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',g)) for g in entry['graphs']):return None
            if hmac.compare_digest(hashed,entry['sha256']):
                if matched is not None:return None
                matched=entry
        return matched if len(token)>=32 else None
    except (OSError,ValueError,TypeError,KeyError):return None


def permits(who,packet):
    if not who or not isinstance(packet,dict):return False
    graph=packet.get('graph')
    if graph is not None and '*' not in who['graphs'] and graph not in who['graphs']:return False
    if who['role']=='reader' and packet.get('operation','search') not in READ_OPERATIONS:return False
    return True
