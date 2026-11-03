"""Source-aware retrieval and paired evaluation for Surd. Standard library only.

Scores measure lexical relevance, never truth or calibrated probability. Index files
contain faithful text snapshots and metadata; native locators survive every run.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import statistics
import tempfile
import time
import heapq
import gzip
from surd_stemmer import stem

VERSION = "1.1.0"
TOKENIZER = "nfkc-casefold-identifiers-v1"
LIMIT_BYTES = 64 * 1024 * 1024
LIMIT_DOCS = 100000
LIMIT_QUERIES = 10000
DEFAULTS = {"k1": 1.2, "b": 0.45,
            "fields": {"title": 1.0, "text": 1.0, "id": 4.0, "locator": 2.0},
            "identifier_boost": 6.0, "phrase_boost": 2.0, "aliases": {},
            "analysis": "english", "stopwords": True}
STOPWORDS = frozenset('a an the of to in on at by for with as and or is are was were be been being this that these those it its from into than then which who whom whose what when where how do does did can could would should may might have has had'.split())

def analyze_tokens(ts, enabled=True):
    if not enabled:
        return ts
    # Hyphenated prose gains component terms; intact identifiers remain indexed.
    out=[]
    for token in ts:
        out.append(stem(token))
        if '-' in token and not any(ch.isdigit() for ch in token):
            out.extend(stem(p) for p in token.split('-') if len(p)>1)
    return out
SCHEMA_INDEX = "surd.search.index.v1"
SCHEMA_RUN = "surd.search.run.v1"


from surd_search_core import (SearchError, canonical, digest, tokens, integer, number, options, validate_scope)


def read_json(path):
    path = Path(path).expanduser()
    try:
        if path.stat().st_size > LIMIT_BYTES:
            raise SearchError(f"input exceeds {LIMIT_BYTES} bytes")
        def bad_constant(s):
            raise SearchError(f"nonfinite JSON number: {s}")
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=bad_constant)
    except (OSError, ValueError) as e:
        raise SearchError(f"cannot read {path}: {e}") from e


def save(value, path):
    if isinstance(value, SearchIndex):
        value = value.serialize()
    encoded = canonical(value) + "\n"
    if len(encoded.encode()) > LIMIT_BYTES:
        raise SearchError("output exceeds the 64 MiB snapshot limit")
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        if read_json(tmp) != value:
            raise SearchError("saved JSON failed readback")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(encoded.encode()).hexdigest(), "bytes": len(encoded.encode())}


def load_rows(path, opts=None):
    opts = options(opts, {"query"})
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SearchError(f"missing corpus: {path}")
    ext = path.suffix.lower()
    try:
        if ext in (".sqlite", ".sqlite3", ".db"):
            sql = opts.get("query")
            if not isinstance(sql, str) or not sql.strip():
                raise SearchError("SQLite requires an explicit SELECT query; files open read-only")
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as con:
                con.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 5
                con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                cur = con.execute(sql)
                if cur.description is None:
                    raise SearchError("SQLite corpus query must return rows")
                names = [x[0] for x in cur.description]
                if len(names) != len(set(names)):
                    raise SearchError("SQLite query has duplicate column names")
                rows = [dict(zip(names, r)) for r in cur.fetchmany(LIMIT_DOCS + 1)]
        else:
            if path.stat().st_size > LIMIT_BYTES:
                raise SearchError("corpus exceeds 64 MiB")
            if ext == ".csv":
                with path.open(encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                        raise SearchError("CSV needs unique named columns")
                    rows = list(reader)
            elif ext in (".jsonl", ".ndjson"):
                rows = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]
            else:
                rows = read_json(path)
                if isinstance(rows, dict):
                    rows = rows.get("documents")
        if not isinstance(rows, list) or len(rows) > LIMIT_DOCS:
            raise SearchError(f"corpus must be a list of at most {LIMIT_DOCS} rows")
        return rows
    except (OSError, sqlite3.Error, csv.Error, ValueError) as e:
        raise SearchError(f"cannot load corpus {path}: {e}") from e


def parse_query(query):
    if not isinstance(query, str) or len(query) > 8192:
        raise SearchError("query must be text of at most 8192 characters")
    if query.count('"') % 2:
        raise SearchError("query has an unclosed quoted phrase")
    clauses = []
    for m in re.finditer(r'([+-]?)(?:"([^"]*)"|([^\s"]+))', query):
        ts = tokens(m[2] if m[2] is not None else m[3])
        if ts:
            clauses.append({"kind": "exclude" if m[1] == "-" else "require" if m[1] == "+" else "optional",
                            "phrase": m[2] is not None, "tokens": ts})
    if len(clauses) > 128 or len({t for c in clauses for t in c["tokens"]}) > 256:
        raise SearchError("query exceeds 128 clauses or 256 distinct tokens")
    return clauses


def has_phrase(haystack, needle):
    n = len(needle)
    return any(haystack[i:i+n] == needle for i in range(len(haystack)-n+1))


def normalize_doc(row):
    if not isinstance(row, dict):
        raise SearchError("each document must be a map")
    ident = row.get("id")
    text = row.get("text", row.get("meaning", row.get("body")))
    if not isinstance(ident, str) or not ident.strip():
        raise SearchError("each document needs a nonempty string id")
    if not isinstance(text, str):
        raise SearchError(f"document {ident}: text (or meaning/body) must be a string")
    title, locator = row.get("title", ""), row.get("locator", row.get("source", ""))
    if not isinstance(title, str) or not isinstance(locator, str):
        raise SearchError(f"document {ident}: title and locator must be strings")
    metadata = row.get("metadata", {})
    fields = row.get("fields", {})
    if not isinstance(metadata, dict) or not isinstance(fields, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
        raise SearchError(f"document {ident}: metadata must be a map; fields must map names to text")
    if any(not isinstance(k, str) for k in metadata) or any(not isinstance(k, str) for k in row):
        raise SearchError("document and metadata keys must be strings")
    if set(fields) & {"id", "text", "title", "locator"}:
        raise SearchError("custom fields cannot replace id, text, title or locator")
    extra = {k: v for k, v in row.items() if k not in {"id", "text", "meaning", "body", "title", "locator", "source", "metadata", "fields", "content_hash"}}
    if set(extra) & set(metadata):
        raise SearchError(f"document {ident}: duplicate metadata names")
    doc = {"id": ident, "text": text, "title": title, "locator": locator, "metadata": dict(metadata, **extra), "fields": fields}
    doc = json.loads(canonical(doc))  # independent snapshot; caller cannot mutate the index
    doc["content_hash"] = digest(doc)
    return doc


class SearchIndex:
    def __init__(self, rows, config=None):
        config = options(config, DEFAULTS)
        config = json.loads(canonical(config))
        c = json.loads(canonical(DEFAULTS))
        c.update(config)
        if c['analysis'] not in ('none','english') or type(c['stopwords']) is not bool:
            raise SearchError('analysis must be none or english; stopwords must be boolean')
        c["k1"] = number(c["k1"], "k1", 0.01, 10)
        c["b"] = number(c["b"], "b", 0, 1)
        for name in ("identifier_boost", "phrase_boost"):
            c[name] = number(c[name], name)
        if not isinstance(c["fields"], dict) or not c["fields"] or any(not isinstance(k, str) for k in c["fields"]):
            raise SearchError("fields must map names to positive weights")
        c["fields"] = {k: number(v, f"field weight {k}", 0.001, 100) for k, v in c["fields"].items()}
        if not isinstance(c["aliases"], dict):
            raise SearchError("aliases must map single tokens to lists of explicit alternatives")
        for key, vals in c["aliases"].items():
            if not isinstance(key, str) or tokens(key) != [key] or not isinstance(vals, list) or any(not isinstance(v, str) or tokens(v) != [v] for v in vals):
                raise SearchError("aliases must use normalized single tokens and lists of normalized single tokens")
        if not isinstance(rows, list) or len(rows) > LIMIT_DOCS:
            raise SearchError(f"index needs a list of at most {LIMIT_DOCS} documents")
        docs = sorted((normalize_doc(r) for r in rows), key=lambda d: d["id"])
        if len({d["id"] for d in docs}) != len(docs):
            raise SearchError("duplicate document ids; retain versions under distinct ids")
        if len(canonical(docs).encode()) > LIMIT_BYTES:
            raise SearchError("index documents exceed 64 MiB")
        self._docs, self._config = docs, c
        self._by_id = {d["id"]: d for d in docs}
        self._field_tokens = {}
        self._postings = defaultdict(dict)
        self._df = Counter()
        self._analyzed = defaultdict(dict)
        self._analyzed_df = Counter()
        self._avg = {}
        total = Counter()
        for d in docs:
            fields = dict(d["fields"], **{f: d[f] for f in ("title", "text", "id", "locator")})
            ft = {f: tokens(fields.get(f, "")) for f in c["fields"]}
            self._field_tokens[d["id"]] = ft
            seen = set()
            for f, ts in ft.items():
                total[f] += len(ts)
                for term, tf in Counter(ts).items():
                    self._postings[term].setdefault(d["id"], {})[f] = tf
                    seen.add(term)
            self._df.update(seen)
            seen_analyzed=set()
            for f, ts in ft.items():
                ats=analyze_tokens(ts,c['analysis']=='english' and f not in ('id','locator','identifiers'))
                for term,tf in Counter(ats).items():
                    self._analyzed[term].setdefault(d['id'],{})[f]=tf
                    seen_analyzed.add(term)
            self._analyzed_df.update(seen_analyzed)
        for f in c["fields"]:
            self._avg[f] = total[f] / len(docs) if docs else 0
        self._lengths={ident:{f:len(ts) for f,ts in ft.items()} for ident,ft in self._field_tokens.items()}
        corpus_hash = digest([[d["id"], d["content_hash"]] for d in docs])
        self._manifest = {"engine": VERSION, "tokenizer": TOKENIZER, "documents": len(docs),
                          "corpus_hash": corpus_hash, "config_hash": digest(c),
                          "index_hash": digest([corpus_hash, c, TOKENIZER]),
                          "missing_locators": sum(not d["locator"] for d in docs),
                          "vocabulary": len(self._df), "score_meaning": "lexical relevance; not truth or probability"}

    def __repr__(self):
        return f"search index ({len(self._docs)} documents; {self._manifest['corpus_hash'][:12]})"

    def manifest(self):
        return json.loads(canonical(self._manifest))

    def document(self, ident):
        """Return an independent faithful snapshot, with a structured missing-id error."""
        if not isinstance(ident, str) or ident not in self._by_id:
            raise SearchError(f"document is not in the index: {ident}")
        return json.loads(canonical(self._by_id[ident]))

    def document_tokens(self, ident):
        if not isinstance(ident, str) or ident not in self._field_tokens:
            raise SearchError(f"document is not in the index: {ident}")
        return tuple(sorted({t for ts in self._field_tokens[ident].values() for t in ts}))

    def in_scope(self, ident, where=None, ranges=None):
        where, ranges = validate_scope({} if where is None else where, {} if ranges is None else ranges)
        if not isinstance(ident, str) or ident not in self._by_id:
            raise SearchError(f"document is not in the index: {ident}")
        return self._filter(self._by_id[ident], where, ranges)

    def documents(self, where=None, ranges=None):
        """Iterate independent snapshots in stable id order under validated scope."""
        where, ranges = validate_scope({} if where is None else where, {} if ranges is None else ranges)
        for doc in self._docs:
            if self._filter(doc, where, ranges):
                yield self.document(doc["id"])

    def serialize(self):
        return json.loads(canonical({"schema": SCHEMA_INDEX, "manifest": self._manifest,
                                     "configuration": self._config, "documents": self._docs}))

    def save_compiled(self,path,input_hash):
        """Disposable private accelerator; faithful source index remains authoritative."""
        state={name:getattr(self,name) for name in ('_docs','_config','_manifest','_field_tokens','_postings','_df','_avg','_lengths','_analyzed','_analyzed_df')}
        raw=canonical(state).encode()
        if len(raw)>192*1024**2:return False
        wrapper=canonical({'version':VERSION,'input_hash':input_hash,'sha256':hashlib.sha256(raw).hexdigest(),'state':state}).encode()
        packed=gzip.compress(wrapper,compresslevel=1,mtime=0)
        if len(packed)>32*1024**2:return False
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.index-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(packed)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return True

    @classmethod
    def load_compiled(cls,path,input_hash):
        path=Path(path)
        if path.is_symlink() or path.stat().st_size>32*1024**2:raise SearchError('cache unavailable')
        with gzip.open(path,'rb') as f:raw=f.read(256*1024**2+1)
        if len(raw)>256*1024**2:raise SearchError('cache expansion limit')
        obj=json.loads(raw)
        state=obj.get('state',{})
        if obj.get('version')!=VERSION or obj.get('input_hash')!=input_hash or obj.get('sha256')!=hashlib.sha256(canonical(state).encode()).hexdigest():
            raise SearchError('cache integrity mismatch')
        names={'_docs','_config','_manifest','_field_tokens','_postings','_df','_avg','_lengths','_analyzed','_analyzed_df'}
        if set(state)!=names or len(state['_docs'])>LIMIT_DOCS or state['_manifest'].get('engine')!=VERSION:
            raise SearchError('cache shape mismatch')
        out=cls.__new__(cls)
        for name,value in state.items():setattr(out,name,Counter(value) if name in ('_df','_analyzed_df') else value)
        out._by_id={d['id']:d for d in out._docs}
        return out

    @classmethod
    def load(cls, path):
        raw = read_json(path)
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA_INDEX:
            raise SearchError("expected a surd.search.index.v1 snapshot")
        result = cls(raw.get("documents"), raw.get("configuration"))
        old=raw.get('manifest',{}).get('engine')=='1.0.0'
        if old:
            cfg=raw.get('configuration',{});corpus=digest([[d['id'],d['content_hash']] for d in result._docs])
            expected=dict(result._manifest,engine='1.0.0',config_hash=digest(cfg),index_hash=digest([corpus,cfg,TOKENIZER]))
        else:expected=result._manifest
        if raw.get("manifest") != expected or raw.get("documents") != result._docs:
            raise SearchError("index integrity mismatch; rebuild from the original corpus")
        return result

    def _filter(self, doc, where, ranges):
        data = dict(doc["metadata"], **{f: doc[f] for f in ("id", "locator", "title")})
        for f, values in where.items():
            if f not in data or not any(type(data[f]) is type(v) and data[f] == v for v in (values if isinstance(values, list) else [values])):
                return False
        for f, bounds in ranges.items():
            value = data.get(f)
            if value is None:
                return False
            try:
                if "gte" in bounds and value < bounds["gte"]:
                    return False
                if "lte" in bounds and value > bounds["lte"]:
                    return False
            except TypeError:
                return False
        return True

    def search(self, query, opts=None):
        started = time.perf_counter()
        opts = options(opts, {"k", "mode", "where", "ranges", "explain"})
        k = integer(opts.get("k", 10), "k")
        mode = opts.get("mode", "hybrid")
        if mode not in ("hybrid", "bm25", "overlap"):
            raise SearchError("mode must be hybrid, bm25 or overlap")
        where, ranges = validate_scope(opts.get("where", {}), opts.get("ranges", {}))
        if type(opts.get("explain", True)) is not bool:
            raise SearchError("explain must be true or false")
        clauses = parse_query(query)
        positive = [c for c in clauses if c["kind"] != "exclude"]
        base_terms = list(dict.fromkeys(t for c in positive for t in c["tokens"]))
        terms = list(base_terms)
        expansions = {}
        for t in base_terms:
            for alias in self._config["aliases"].get(t, []):
                if alias not in terms:
                    terms.append(alias)
                    expansions.setdefault(t, []).append(alias)
        analyzed=mode!='overlap' and self._config['analysis']=='english'
        rank_terms=analyze_tokens(terms,analyzed)
        if analyzed and self._config['stopwords']:
            useful=[t for t in rank_terms if t not in STOPWORDS]
            if useful:rank_terms=useful
        rank_terms=list(dict.fromkeys(rank_terms))
        if analyzed:
            for term in terms:
                if term not in rank_terms and any(set(fs)&{'id','locator','identifiers'} for fs in self._postings.get(term,{}).values()):
                    rank_terms.append(term)
        postings=self._analyzed if analyzed else self._postings
        df=self._analyzed_df if analyzed else self._df
        values=defaultdict(float);matched=defaultdict(set)
        N=len(self._docs);c=self._config
        idfs={t:math.log1p((N-df[t]+.5)/(df[t]+.5)) for t in rank_terms}
        # Score each sparse posting once. Explanations are reconstructed for top-k only.
        for term in rank_terms:
            for ident,fields in postings.get(term,{}).items():
                if mode=='overlap':matched[ident].add(term);values[ident]=len(matched[ident]);continue
                for f,tf in fields.items():
                    dl,avg=self._lengths[ident][f],self._avg[f] or 1
                    values[ident]+=c['fields'][f]*idfs[term]*tf*(c['k1']+1)/(tf+c['k1']*(1-c['b']+c['b']*dl/avg))
        scores = []
        eligible = 0
        N = len(self._docs)
        for ident in values:
            doc, ft = self._by_id[ident], self._field_tokens[ident]
            if not self._filter(doc, where, ranges):
                continue
            matches = lambda c: any(has_phrase(ts, c["tokens"]) if c["phrase"] else all(t in ts for t in c["tokens"]) for ts in ft.values())
            if any(c["kind"] == "require" and not matches(c) or c["kind"] == "exclude" and matches(c) for c in clauses):
                continue
            # Quoted positive phrases are constraints, not a bag of their words.
            if any(c["phrase"] and c["kind"] != "exclude" and not matches(c) for c in clauses):
                continue
            eligible += 1
            lexical = values[ident]
            exact_ids = [t for t in base_terms if (any(ch.isdigit() for ch in t) or len(base_terms) == 1 and t == ident.casefold()) and (t in tokens(doc["id"]) or t in tokens(doc["locator"]))]
            phrases = [" ".join(c["tokens"]) for c in positive if c["phrase"] and matches(c)]
            bonus = self._config["identifier_boost"] * len(exact_ids) + self._config["phrase_boost"] * len(phrases) if mode == "hybrid" else 0
            scores.append((lexical + bonus, ident, lexical, bonus, exact_ids, phrases))
        scores=heapq.nsmallest(k,scores,key=lambda x:(-x[0],x[1]))
        hits = []
        for rank, (score, ident, lexical, bonus, exact_ids, phrases) in enumerate(scores, 1):
            d = self._by_id[ident]
            # Snippet offsets address the unmodified snapshot text.
            positions = [m.start() for t in base_terms for m in [re.search(re.escape(t), d["text"], re.I)] if m]
            start = max(0, min(positions, default=0) - 70)
            end = min(len(d["text"]), start + 320)
            hit = {"id": ident, "rank": rank, "score": score, "title": d["title"], "locator": d["locator"],
                   "content_hash": d["content_hash"], "metadata": d["metadata"],
                   "snippet": {"text": d["text"][start:end], "start": start, "end": end}}
            if opts.get("explain", True):
                contributions=[]
                for term in rank_terms:
                    for f,tf in sorted(postings.get(term,{}).get(ident,{}).items()):
                        dl,avg=self._lengths[ident][f],self._avg[f] or 1
                        s=1.0 if mode=='overlap' else c['fields'][f]*idfs[term]*tf*(c['k1']+1)/(tf+c['k1']*(1-c['b']+c['b']*dl/avg))
                        contributions.append({'term':term,'field':f,'tf':tf,'idf':idfs[term],'score':s})
                hit["explanation"] = {"lexical_score": lexical, "bonus": bonus, "matched_identifiers": exact_ids,
                                      "matched_phrases": phrases, "contributions": contributions}
            hits.append(hit)
        return json.loads(canonical({"schema": "surd.search.results.v1", "query": query, "mode": mode, "k": k,
                                    "status": "ok" if hits else "empty_query" if not base_terms else "no_documents" if not N else "no_match",
                                    "corpus_hash": self._manifest["corpus_hash"], "index_hash": self._manifest["index_hash"],
                                    "scope": {"where": where, "ranges": ranges}, "clauses": clauses, "expansions": expansions,
                                    "analysis": self._config['analysis'] if analyzed else 'none',
                                    "ranking_terms": rank_terms,
                                    "matched_documents": eligible, "hits": hits, "elapsed_ms": (time.perf_counter()-started)*1000,
                                    "score_meaning": self._manifest["score_meaning"], "scoring_exact": False}))


def reason(index, question, spec=None):
    from surd_reason import reason as audit_reasoning
    return audit_reasoning(index, question, spec)


def posture(index, question, spec):
    from surd_posture import posture as audit_posture
    return audit_posture(index, question, spec)


def quantum(index, question, spec):
    from surd_quantum import quantum as explore_quantum
    return explore_quantum(index, question, read_json(spec) if isinstance(spec, str) else spec)


def metrics(ids, judgments, cutoffs):
    if len(set(ids)) != len(ids):
        raise SearchError("metric rankings cannot contain duplicate document ids")
    if not isinstance(judgments, dict) or any(not isinstance(k, str) for k in judgments):
        raise SearchError("relevant must map document ids to nonnegative relevance grades")
    grades = {k: number(v, "relevance grade", 0, 100) for k, v in judgments.items()}
    total = sum(v > 0 for v in grades.values())
    out = {}
    for k in cutoffs:
        top = ids[:k]
        rel = [grades.get(d, 0) for d in top]
        found = sum(v > 0 for v in rel)
        ap, running = 0.0, 0
        for i, v in enumerate(rel, 1):
            if v > 0:
                running += 1
                ap += running / i
        ideal = sorted(grades.values(), reverse=True)[:k]
        dcg = sum(v / math.log2(i+2) for i, v in enumerate(rel))
        idcg = sum(v / math.log2(i+2) for i, v in enumerate(ideal))
        out.update({f"precision@{k}": found/k if total else None,
                    f"recall@{k}": found/total if total else None,
                    f"mrr@{k}": next((1/i for i, v in enumerate(rel, 1) if v > 0), 0.0) if total else None,
                    f"map@{k}": ap/min(total, k) if total else None,
                    f"ndcg@{k}": dcg/idcg if idcg else None,
                    f"judged_fraction@{k}": sum(d in grades for d in top)/len(top) if top else None})
    return out


def stable_queries(queries):
    """Hash complete retained results and source context, excluding only elapsed time."""
    return [dict(q, results={k: v for k, v in q["results"].items() if k != "elapsed_ms"}) for q in queries]


def evaluate(index, dataset, opts=None):
    if not isinstance(index, SearchIndex):
        raise SearchError("search_evaluate requires a search index")
    opts = options(opts, {"cutoffs", "mode", "split", "training_query_ids", "explain", "rankings"})
    if isinstance(dataset, str):
        dataset = read_json(dataset)
    if isinstance(dataset, dict):
        dataset = dataset.get("queries")
    if not isinstance(dataset, list) or not dataset or len(dataset) > LIMIT_QUERIES:
        raise SearchError(f"evaluation needs 1..{LIMIT_QUERIES} query records")
    cutoffs = opts.get("cutoffs", [1, 5, 10])
    if not isinstance(cutoffs, list) or not cutoffs:
        raise SearchError("cutoffs must be a nonempty list")
    cutoffs = sorted(set(integer(k, "cutoff") for k in cutoffs))
    split = opts.get("split", "test")
    if not isinstance(split, str) or not split:
        raise SearchError("split must be a nonempty string")
    training = opts.get("training_query_ids", [])
    if not isinstance(training, list) or any(not isinstance(q, str) for q in training):
        raise SearchError("training_query_ids must be a list of ids")
    ids, selected, omitted = set(), [], 0
    for q in dataset:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"] or not isinstance(q.get("query"), str):
            raise SearchError("each query needs nonempty string id and query text")
        if q["id"] in ids:
            raise SearchError("duplicate query ids")
        ids.add(q["id"])
        if q.get("split", "test") != split:
            omitted += 1
            continue
        if q["id"] in training:
            raise SearchError(f"training/evaluation query leakage: {q['id']}")
        selected.append(q)
    if not selected:
        raise SearchError(f"no queries in split {split}")
    rankings = opts.get("rankings")
    if rankings is not None:
        if not isinstance(rankings, dict) or set(rankings) != {q["id"] for q in selected}:
            raise SearchError("external rankings must map every selected query id, with no extra ids")
    results, latencies, sums = [], [], defaultdict(list)
    for q in selected:
        report = index.search(q["query"] if rankings is None else "", {"k": max(cutoffs), "mode": opts.get("mode", "hybrid"),
                                           "where": q.get("where", {}), "ranges": q.get("ranges", {}),
                                           "explain": opts.get("explain", True)})
        if rankings is not None:
            ranked = rankings[q["id"]]
            if not isinstance(ranked, list) or len(ranked) > 1000 or any(not isinstance(d, str) for d in ranked) or len(set(ranked)) != len(ranked):
                raise SearchError("external ranking needs at most 1000 unique document ids")
            if set(ranked) - set(index._by_id):
                raise SearchError(f"external ranking for {q['id']} contains unknown source ids")
            if any(not index._filter(index._by_id[d], q.get("where", {}), q.get("ranges", {})) for d in ranked):
                raise SearchError(f"external ranking for {q['id']} violates its scope")
            report.update({"query": q["query"], "mode": "external", "clauses": [], "status": "ok" if ranked else "no_match",
                           "matched_documents": len(ranked), "ranking_origin": digest(ranked),
                           "score_meaning": "provided rank; original external scores are not converted to probabilities"})
            for rank, ident in enumerate(ranked[:max(cutoffs)], 1):
                d = index._by_id[ident]
                report["hits"].append({"id": ident, "rank": rank, "score": None, "title": d["title"], "locator": d["locator"],
                                       "metadata": d["metadata"], "content_hash": d["content_hash"],
                                       "snippet": {"text": d["text"][:320], "start": 0, "end": min(320, len(d["text"]))}})
        judgments = q.get("relevant")
        state = "unjudged"
        vals = {}
        if judgments is not None:
            if not isinstance(judgments, dict):
                raise SearchError("relevant must be a map")
            unknown = set(judgments) - set(index._by_id)
            if unknown:
                raise SearchError(f"query {q['id']} judges ids outside this corpus: {sorted(unknown)[:5]}")
            if any(not index._filter(index._by_id[d], q.get("where", {}), q.get("ranges", {})) and g > 0 for d, g in judgments.items() if isinstance(g, (int, float))):
                raise SearchError(f"query {q['id']} has relevant ids outside its declared scope")
            vals = metrics([h["id"] for h in report["hits"]], judgments, cutoffs)
            has_positive = any(v > 0 for v in judgments.values())
            if has_positive and q.get("answerable") is False:
                raise SearchError(f"query {q['id']} cannot be no-answer with positive judgments")
            state = "judged" if has_positive else "known_no_answer" if q.get("answerable") is False else "no_positive_judgments"
            if state == "known_no_answer":
                vals["no_answer_accuracy"] = float(not report["hits"])
            elif state == "no_positive_judgments":
                vals = {k: v for k, v in vals.items() if k.startswith("judged_fraction")}
            for key, value in vals.items():
                if value is not None:
                    sums[key].append(value)
        elif q.get("answerable") is False:
            raise SearchError("known no-answer queries require relevant: {}")
        results.append({"id": q["id"], "query_record": json.loads(canonical(q)), "judgment_state": state, "metrics": vals, "results": report,
                        "relevant": judgments, "unjudged_hits": [h["id"] for h in report["hits"] if judgments is None or h["id"] not in judgments]})
        latencies.append(report["elapsed_ms"])
    aggregate = {k: statistics.mean(v) for k, v in sorted(sums.items())}
    states = Counter(r["judgment_state"] for r in results)
    manifest = {"corpus_hash": index._manifest["corpus_hash"], "index_hash": index._manifest["index_hash"],
                "dataset_hash": digest(selected), "cutoffs": cutoffs, "split": split,
                "metric_policy": "linear-gain ndcg; precision denominator k; AP denominator min(R,k); unjudged gains zero",
                "no_positive_policy": "excluded from relevance means; explicit answerable:false scored separately",
                "tie_policy": "provided order" if rankings is not None else "score descending, document id ascending",
                "mode": "external" if rankings is not None else opts.get("mode", "hybrid"),
                "external_rankings_hash": digest(rankings) if rankings is not None else None,
                "training_query_ids": sorted(training), "omitted_queries": omitted,
                "heldout_status": "caller-declared split; independence is not certified by a filename"}
    stable = stable_queries(results)
    manifest["run_hash"] = digest([manifest, stable])
    ordered = sorted(latencies)
    return {"schema": SCHEMA_RUN, "manifest": manifest, "aggregate": aggregate,
            "metric_query_counts": {k: len(v) for k, v in sorted(sums.items())},
            "coverage": dict({"queries": len(results), "corpus_documents": len(index._docs)}, **dict(states)),
            "latency_ms": {"p50": statistics.median(ordered), "p95": ordered[max(0, math.ceil(.95*len(ordered))-1)]},
            "queries": results}


def validate_run(run):
    if not isinstance(run, dict) or run.get("schema") != SCHEMA_RUN or not isinstance(run.get("manifest"), dict) or not isinstance(run.get("queries"), list):
        raise SearchError("expected a surd.search.run.v1 report")
    manifest = run["manifest"]
    cutoffs = manifest.get("cutoffs")
    if not isinstance(cutoffs, list) or not cutoffs or any(type(k) is not int or not 1 <= k <= 1000 for k in cutoffs):
        raise SearchError("run has invalid cutoffs")
    if not run["queries"] or len(run["queries"]) > LIMIT_QUERIES:
        raise SearchError("run must contain a bounded nonempty query set")
    if digest([q.get("query_record") for q in run["queries"]]) != manifest.get("dataset_hash"):
        raise SearchError("retained query records do not match dataset hash")
    ids = []
    sums = defaultdict(list)
    for q in run["queries"]:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("metrics"), dict) or not isinstance(q.get("results"), dict):
            raise SearchError("invalid run query record")
        ids.append(q["id"])
        hits = q["results"].get("hits")
        if not isinstance(hits, list) or any(not isinstance(h, dict) or not isinstance(h.get("id"), str) for h in hits):
            raise SearchError("invalid ranked hits")
        if len({h["id"] for h in hits}) != len(hits):
            raise SearchError("duplicate ids in a ranking")
        qr = q.get("query_record", {})
        if not isinstance(qr, dict) or q.get("relevant") is not None and not isinstance(q["relevant"], dict):
            raise SearchError("run must retain a query map and a relevance map or null")
        if qr.get("id") != q["id"] or qr.get("relevant") != q.get("relevant") or qr.get("query") != q["results"].get("query"):
            raise SearchError("query, judgments and retained query record disagree")
        expected_state = "unjudged" if q.get("relevant") is None else "judged" if any(v > 0 for v in q["relevant"].values()) else "known_no_answer" if qr.get("answerable") is False else "no_positive_judgments"
        if q.get("judgment_state") != expected_state:
            raise SearchError("judgment state disagrees with retained query")
        if q["results"].get("scope") != {"where": qr.get("where", {}), "ranges": qr.get("ranges", {})}:
            raise SearchError("query scope mismatch")
        if q["results"].get("corpus_hash") != manifest.get("corpus_hash"):
            raise SearchError("query corpus mismatch")
        for v in q["metrics"].values():
            if v is not None:
                number(v, "metric", 0, 1)
        judgments = q.get("relevant")
        if judgments is not None:
            expected = metrics([h["id"] for h in hits], judgments, cutoffs)
            state = q.get("judgment_state")
            if state == "known_no_answer":
                expected["no_answer_accuracy"] = float(not hits)
            elif state == "no_positive_judgments":
                expected = {k: v for k, v in expected.items() if k.startswith("judged_fraction")}
            if expected != q["metrics"]:
                raise SearchError("run metrics do not match retained rankings and judgments")
        elif q["metrics"]:
            raise SearchError("unjudged query cannot carry relevance metrics")
        for key, v in q["metrics"].items():
            if v is not None:
                sums[key].append(v)
    if len(set(ids)) != len(ids):
        raise SearchError("duplicate ids in a run")
    if run.get("aggregate") != {k: statistics.mean(v) for k, v in sorted(sums.items())}:
        raise SearchError("aggregate metrics do not match per-query metrics")
    stable = stable_queries(run["queries"])
    unhashed = {k: v for k, v in manifest.items() if k != "run_hash"}
    if manifest.get("run_hash") != digest([unhashed, stable]):
        raise SearchError("run integrity hash mismatch")
    canonical(run)
    return run


def compare(baseline, candidate, opts=None):
    baseline, candidate = validate_run(baseline), validate_run(candidate)
    opts = options(opts, {"metric", "max_drop", "max_query_drop", "bootstrap_samples", "seed"})
    for f in ("corpus_hash", "dataset_hash", "cutoffs", "split", "metric_policy", "no_positive_policy"):
        if baseline["manifest"].get(f) != candidate["manifest"].get(f) or f not in baseline["manifest"]:
            raise SearchError(f"cannot compare runs with different or missing {f}")
    a, b = {q["id"]: q for q in baseline["queries"]}, {q["id"]: q for q in candidate["queries"]}
    if set(a) != set(b):
        raise SearchError("paired comparison requires the same query ids")
    metric = opts.get("metric", f"ndcg@{max(baseline['manifest']['cutoffs'])}")
    if not isinstance(metric, str):
        raise SearchError("metric must be a name")
    max_drop = number(opts.get("max_drop", 0), "max_drop", 0, 1)
    query_drop = number(opts.get("max_query_drop", 1), "max_query_drop", 0, 1)
    rows, excluded = [], []
    for qid in sorted(a):
        x, y = a[qid]["metrics"].get(metric), b[qid]["metrics"].get(metric)
        if (x is None) != (y is None):
            raise SearchError(f"query {qid} has different judgment availability")
        if x is None:
            excluded.append(qid)
        else:
            rows.append({"id": qid, "baseline": x, "candidate": y, "delta": y-x})
    if not rows:
        raise SearchError(f"no paired judged values for metric {metric}")
    deltas = [r["delta"] for r in rows]
    samples = integer(opts.get("bootstrap_samples", 1000), "bootstrap_samples", 100, 10000)
    seed = integer(opts.get("seed", 0), "seed", 0, 2147483647)
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(samples))
    delta = statistics.mean(deltas)
    violations = [r["id"] for r in rows if r["delta"] < -query_drop-1e-12]
    passed = delta >= -max_drop-1e-12 and not violations
    return {"schema": "surd.search.comparison.v1", "passed": passed, "metric": metric,
            "baseline": statistics.mean(r["baseline"] for r in rows), "candidate": statistics.mean(r["candidate"] for r in rows),
            "delta": delta, "paired_queries": len(rows), "excluded_queries": excluded,
            "wins": sum(d > 1e-12 for d in deltas), "losses": sum(d < -1e-12 for d in deltas),
            "ties": sum(abs(d) <= 1e-12 for d in deltas),
            "bootstrap_95": [means[int(.025*samples)], means[min(samples-1, int(.975*samples))]],
            "bootstrap_note": "paired resampling of these queries; not independent proof of generalization",
            "gate": {"max_drop": max_drop, "max_query_drop": query_drop, "violations": violations},
            "queries": sorted(rows, key=lambda r: (r["delta"], r["id"])),
            "baseline_run": baseline["manifest"].get("run_hash"), "candidate_run": candidate["manifest"].get("run_hash")}


def fuse(results, opts=None):
    opts = options(opts, {"k", "rrf_k"})
    k = integer(opts.get("k", 10), "k")
    rrf_k = integer(opts.get("rrf_k", 60), "rrf_k", 1, 10000)
    if not isinstance(results, list) or not results:
        raise SearchError("fusion requires a nonempty list of search result objects")
    first = results[0]
    if not isinstance(first, dict):
        raise SearchError("fusion needs search result maps")
    for r in results:
        if not isinstance(r, dict) or r.get("schema") != "surd.search.results.v1":
            raise SearchError("fusion needs surd.search.results.v1 results")
        for f in ("query", "corpus_hash", "scope"):
            if f not in r or r.get(f) != first.get(f):
                raise SearchError(f"fusion requires identical {f}")
        if not isinstance(r.get("hits"), list):
            raise SearchError("fusion inputs require ranked hit lists")
    scores, docs, contributions = Counter(), {}, defaultdict(list)
    for i, r in enumerate(results):
        seen = set()
        for rank, h in enumerate(r["hits"], 1):
            if not isinstance(h, dict) or not isinstance(h.get("id"), str) or not h.get("content_hash"):
                raise SearchError("fusion hits need id and content_hash")
            ident = h["id"]
            if ident in seen:
                raise SearchError("duplicate id in fusion input")
            seen.add(ident)
            if ident in docs and docs[ident]["content_hash"] != h["content_hash"]:
                raise SearchError("fusion found conflicting source snapshots")
            docs[ident] = h
            value = 1/(rrf_k + rank)
            scores[ident] += value
            contributions[ident].append({"input": i, "rank": rank, "score": value})
    hits = []
    for rank, ident in enumerate(sorted(scores, key=lambda d: (-scores[d], d))[:k], 1):
        h = dict(docs[ident], rank=rank, score=scores[ident], explanation={"rrf": contributions[ident]})
        hits.append(h)
    return json.loads(canonical({"schema": "surd.search.results.v1", "mode": "rrf", "query": first["query"], "scope": first["scope"],
                                "corpus_hash": first["corpus_hash"], "input_index_hashes": [r.get("index_hash") for r in results],
                                "rrf_k": rrf_k, "k": k, "hits": hits, "scoring_exact": False,
                                "score_meaning": "reciprocal rank agreement; not truth or probability"}))


def cli(argv):
    parser = argparse.ArgumentParser(prog="surd search", description="Source-aware search, reproducible evaluation and regression gates")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="build a portable text snapshot from JSON/JSONL/CSV/read-only SQLite")
    build.add_argument("corpus"); build.add_argument("-o", "--output", required=True)
    build.add_argument("--config"); build.add_argument("--sql")
    query = sub.add_parser("query", help="rank documents with source links and score explanations")
    query.add_argument("index"); query.add_argument("query"); query.add_argument("-k", type=int, default=10)
    query.add_argument("--mode", choices=["hybrid", "bm25", "overlap"], default="hybrid")
    query.add_argument("--where", type=json.loads, default={}); query.add_argument("--ranges", type=json.loads, default={})
    query.add_argument("-o", "--output"); query.add_argument("--json", action="store_true")
    ev = sub.add_parser("evaluate", help="evaluate judged queries; keep unknown and no-answer cases visible")
    ev.add_argument("index"); ev.add_argument("dataset"); ev.add_argument("-o", "--output")
    ev.add_argument("--mode", choices=["hybrid", "bm25", "overlap"], default="hybrid")
    ev.add_argument("--cutoffs", type=int, nargs="+", default=[1, 5, 10]); ev.add_argument("--split", default="test")
    ev.add_argument("--training-query-ids", help="JSON list of queries used for tuning")
    ev.add_argument("--rankings", help="JSON map of query id to ranked source ids, from another retriever")
    cmp = sub.add_parser("compare", help="paired comparison; exit 1 when the regression gate fails")
    cmp.add_argument("baseline"); cmp.add_argument("candidate"); cmp.add_argument("--metric", default="ndcg@10")
    cmp.add_argument("--max-drop", type=float, default=0); cmp.add_argument("--max-query-drop", type=float, default=1)
    cmp.add_argument("-o", "--output")
    inspect = sub.add_parser("inspect", help="verify and inspect an index manifest")
    inspect.add_argument("index")
    fusion = sub.add_parser("fuse", help="reciprocal-rank fusion of aligned search results")
    fusion.add_argument("results", nargs="+"); fusion.add_argument("-k", type=int, default=10)
    fusion.add_argument("--rrf-k", type=int, default=60); fusion.add_argument("-o", "--output")
    reasoning = sub.add_parser("reason", help="audit cited premises, counterarguments and inference dependencies")
    reasoning.add_argument("index"); reasoning.add_argument("question")
    reasoning.add_argument("--spec", help="JSON argument contract and optional facet queries")
    reasoning.add_argument("-k", type=int); reasoning.add_argument("--mode", choices=["hybrid", "bm25", "overlap"])
    reasoning.add_argument("--json", action="store_true"); reasoning.add_argument("-o", "--output")
    posturing = sub.add_parser("posture", help="brief record floors, adverse accounts, conditional forks and missing proof bridges")
    posturing.add_argument("index"); posturing.add_argument("question")
    posturing.add_argument("--spec", required=True, help="JSON posture contract")
    posturing.add_argument("--json", action="store_true"); posturing.add_argument("-o", "--output")
    quant = sub.add_parser("quantum", help="exact quantum scenario search and discriminating record requests")
    quant.add_argument("index"); quant.add_argument("question")
    quant.add_argument("--spec", required=True, help="JSON quantum reasoning contract")
    quant.add_argument("--json", action="store_true"); quant.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "build":
            result = SearchIndex(load_rows(args.corpus, {"query": args.sql} if args.sql else {}), read_json(args.config) if args.config else None)
            receipt = save(result, args.output)
            print(json.dumps({"ok": True, "manifest": result.manifest(), "saved": receipt}, indent=2))
            return 0
        if args.cmd == "query":
            index = SearchIndex.load(args.index)
            result = index.search(args.query, {"k": args.k, "mode": args.mode, "where": args.where, "ranges": args.ranges})
            from loom_research.capture import record_search
            record_search(index,args.query,result)
        elif args.cmd == "evaluate":
            result = evaluate(SearchIndex.load(args.index), read_json(args.dataset),
                              {"mode": args.mode, "cutoffs": args.cutoffs, "split": args.split,
                               "training_query_ids": read_json(args.training_query_ids) if args.training_query_ids else [],
                               **({"rankings": read_json(args.rankings)} if args.rankings else {})})
        elif args.cmd == "compare":
            result = compare(read_json(args.baseline), read_json(args.candidate),
                             {"metric": args.metric, "max_drop": args.max_drop, "max_query_drop": args.max_query_drop})
        elif args.cmd == "fuse":
            result = fuse([read_json(p) for p in args.results], {"k": args.k, "rrf_k": args.rrf_k})
        elif args.cmd == "reason":
            spec = read_json(args.spec) if args.spec else {}
            spec = dict(options(spec, {"k", "mode", "where", "ranges", "facets", "claims", "rules", "goal"}))
            if args.k is not None: spec["k"] = args.k
            if args.mode is not None: spec["mode"] = args.mode
            result = reason(SearchIndex.load(args.index), args.question, spec)
        elif args.cmd == "posture":
            result = posture(SearchIndex.load(args.index), args.question, read_json(args.spec))
        elif args.cmd == "quantum":
            result = quantum(SearchIndex.load(args.index), args.question, read_json(args.spec))
        else:
            result = SearchIndex.load(args.index).manifest()
        if getattr(args, "output", None):
            save(result, args.output)
        if args.cmd == "quantum" and not args.json:
            from surd_quantum import render
            print(render(result))
        elif args.cmd == "posture" and not args.json:
            from surd_posture import render
            print(render(result))
        elif args.cmd == "reason" and not args.json:
            from surd_reason import render
            print(render(result))
        elif args.cmd == "query" and not args.json:
            print(f"{result['status']} · {result['matched_documents']} matching documents · {result['elapsed_ms']:.2f} ms")
            for h in result["hits"]:
                print(f"{h['rank']:>2}. {h['id']}  {h['score']:.4f}  {h['title']}\n    {h['locator'] or '(locator missing)'}\n    {h['snippet']['text']}")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 1 if args.cmd == "compare" and not result["passed"] else 0
    except (SearchError, OSError, TypeError, KeyError) as e:
        print(json.dumps({"ok": False, "error": {"code": "S-S001", "message": str(e)}}))
        return 2


def load(path):
    raw = read_json(path)
    if isinstance(raw, dict) and raw.get("schema") == SCHEMA_INDEX:
        return SearchIndex.load(path)
    if isinstance(raw, dict) and raw.get("schema") == SCHEMA_RUN:
        return validate_run(raw)
    if isinstance(raw, dict) and raw.get("schema") == "surd.search.reason.v1":
        from surd_reason import validate_report
        return validate_report(raw)
    if isinstance(raw, dict) and raw.get("schema") == "surd.search.posture.v1":
        from surd_posture import validate_report
        return validate_report(raw)
    if isinstance(raw, dict) and raw.get("schema") == "surd.search.quantum.v1":
        from surd_quantum import validate_report
        return validate_report(raw)
    raise SearchError("search_load expects a saved index, evaluation run, reasoning, posture or quantum report")
