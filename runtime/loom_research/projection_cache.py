"""Bounded, checksum-validated disposable graph read projections (stdlib only)."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zlib

MAX_COMPRESSED=32*1024**2
MAX_EXPANDED=128*1024**2


def load(path,key):
    path=Path(path)
    if path.parent.is_symlink() or path.is_symlink():return None
    try:
        if path.stat().st_size>MAX_COMPRESSED:return None
        with path.open('rb') as source:
            header=json.loads(source.readline(512))
            if not isinstance(header,dict) or header.get('key')!=key:return None
            with gzip.GzipFile(fileobj=source,mode='rb') as packed:
                raw=packed.read(MAX_EXPANDED+1)
        if len(raw)>MAX_EXPANDED or hashlib.sha256(raw).hexdigest()!=header.get('sha256'):return None
        value=json.loads(raw)
        return value if isinstance(value,dict) and isinstance(value.get('nodes'),list) and isinstance(value.get('edges'),list) else None
    except (OSError,ValueError,EOFError,RecursionError,zlib.error):return None


def save(path,key,value):
    path=Path(path)
    if path.parent.is_symlink() or path.is_symlink():return False
    temp=None
    try:
        raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>MAX_EXPANDED:return False
        header=json.dumps({'key':key,'sha256':hashlib.sha256(raw).hexdigest()}).encode()+b'\n'
        packed=gzip.compress(raw,compresslevel=1,mtime=0)
        if len(header)+len(packed)>MAX_COMPRESSED:return False
        path.parent.mkdir(mode=0o700,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.projection-',dir=path.parent)
        with os.fdopen(fd,'wb') as target:
            target.write(header);target.write(packed);target.flush();os.fsync(target.fileno())
        os.replace(temp,path);return True
    except (OSError,ValueError,RecursionError):return False
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)
