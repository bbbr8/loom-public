"""Validated graph contracts and canonical exact serialization (stdlib only)."""
from copy import deepcopy
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import unicodedata

MAX_NODES, MAX_EDGES, MAX_BATCH = 20000, 100000, 4096
MAX_BYTES = 16 * 1024 * 1024
TYPES = {"source", "passage", "entity", "instrument", "event", "claim", "concept",
         "hypothesis", "variable", "constraint", "metric", "proposition", "model",
         "analysis", "question", "search", "method", "benchmark", "correction", "predicate"}
STATUSES = {"observed", "annotated", "candidate", "hypothesis", "derived", "proposed", "active", "held", "retired"}
PREDICATES = {"quoted_from", "supports", "opposes", "refers_to", "version_of", "supersedes",
              "derived_from", "requested", "funded", "paid", "credited", "received_by",
              "tested_by", "depends_on", "same_identifier", "same_entity", "mentions",
              "proposed_relation", "corrects", "answers", "uses_method", "related_to"}


class ResearchError(ValueError):
    pass


class ResearchConflict(ResearchError):
    """A plan's declared read revision changed before its atomic commit."""
    pass


def require(condition, message):
    if not condition:
        raise ResearchError(message)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as e:
        raise ResearchError("bounded finite JSON required: " + str(e)) from e


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, name="text", limit=2000):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit, name + " must be nonempty bounded text")
    return value


def integer(value, lo, hi, name="integer"):
    require(type(value) is int and lo <= value <= hi, name + f" must be an integer in {lo}..{hi}")
    return value


def fields(value, required=(), optional=()):
    require(isinstance(value, dict), "expected an object")
    require(set(required) <= set(value), "missing fields: " + ", ".join(sorted(set(required)-set(value))))
    require(not set(value)-set(required)-set(optional), "unknown fields: " + ", ".join(sorted(set(value)-set(required)-set(optional))))


def read_json(value):
    if isinstance(value, dict):
        require(len(canonical(value).encode()) <= MAX_BYTES, "packet exceeds 16 MiB")
        return deepcopy(value)
    p = Path(value).expanduser()
    require(p.stat().st_size <= MAX_BYTES, "packet exceeds 16 MiB")
    def unique(pairs):
        result = {}
        for k,v in pairs:
            require(k not in result, "duplicate JSON key: " + k)
            result[k] = v
        return result
    try:
        out = json.loads(p.read_text(), object_pairs_hook=unique,
                         parse_constant=lambda v: (_ for _ in ()).throw(ResearchError("nonfinite JSON number")))
    except (ValueError, RecursionError) as e:
        raise ResearchError(str(e)) from e
    require(isinstance(out, dict), "packet must be an object")
    return out


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace("+00:00", "Z")


def timestamp(value):
    text(value, "date/time", 80)
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(d.tzinfo is not None, "timestamps require a zone; use date-only for day precision")
        return d.astimezone(timezone.utc).isoformat(timespec='microseconds').replace("+00:00", "Z")
    except ValueError as e:
        raise ResearchError("invalid date/time: " + value) from e


def interval(value):
    if value is None:
        return None
    require(isinstance(value, list) and len(value) == 2, "clock must be null or [earliest,latest]")
    out = [timestamp(v) if v is not None else None for v in value]
    require(out[0] is None or out[1] is None or boundary(out[0]) <= boundary(out[1],True), "clock interval is reversed")
    return out


def boundary(value,end=False):
    value=timestamp(value)
    return value+('T23:59:59.999999Z' if end else 'T00:00:00.000000Z') if len(value)==10 else value


def clocks(raw):
    fields(raw, (), ("event", "statement", "valid", "receipt"))
    out = {k:interval(v) for k,v in raw.items() if k != "receipt"}
    if "receipt" in raw:
        require(isinstance(raw["receipt"], dict), "receipt clocks must map actors to intervals")
        out["receipt"] = {text(k,"actor"):interval(v) for k,v in raw["receipt"].items()}
    return out


def identifiers(raw):
    require(isinstance(raw, list) and len(raw) <= 64, "at most 64 identifiers per node")
    out = []
    for item in raw:
        fields(item, ("kind","namespace","value"), ("unique",))
        require(type(item.get("unique",False)) is bool, "unique must be boolean")
        out.append({**{k:text(item[k],k,500) for k in ("kind","namespace","value")},"unique":item.get("unique",False)})
    unique={}
    for item in out:
        if item['unique']:
            key=(item['kind'],item['namespace'])
            require(key not in unique or unique[key]==item['value'],'conflicting unique identifiers need distinct scopes/namespaces')
            unique[key]=item['value']
    return sorted(out,key=canonical)


def refs(raw):
    require(isinstance(raw,list) and len(raw)<=1000, "source_ids must be a list with at most 1000 ids")
    return sorted(set(text(v,"source id") for v in raw))


def normalize_node(raw):
    fields(raw,("id","type"),("revision","label","text","origin","locator","source_ids","status","space","data","clocks","identifiers","depends_on"))
    require(raw["type"] in TYPES,"unknown node type")
    out = {"id":text(raw["id"],"node id"),"type":raw["type"],"revision":integer(raw.get("revision",1),1,10**9),
           "label":text(raw.get("label",raw["id"]),"label"),"text":raw.get("text",""),
           "source_ids":refs(raw.get("source_ids",[])),"status":raw.get("status","annotated"),
           "space":raw.get("space","evidence"),"data":deepcopy(raw.get("data",{})),
           "clocks":clocks(raw.get("clocks",{})),"identifiers":identifiers(raw.get("identifiers",[])),
           "depends_on":deepcopy(raw.get("depends_on",[]))}
    require(isinstance(out["text"],str) and len(out["text"])<=100000,"node text exceeds 100000 characters")
    require(out["status"] in STATUSES,"unknown node status")
    require(out["space"] in ("evidence","hypothesis","research"),"unknown graph space")
    require(isinstance(out["data"],dict),"data must be an object")
    if raw['type']=='benchmark':
        require(isinstance(out['data'].get('definition'),dict) and out['data'].get('dataset_hash')==digest(out['data']['definition']),
                'benchmark labels must match their frozen dataset hash')
    if raw["type"] == "source":
        out["origin"] = text(raw.get("origin"),"source origin")
        out["locator"] = text(raw.get("locator"),"source locator",8000)
        require(not out["source_ids"],"source nodes cannot cite themselves or other sources; use origin/derived_from")
    else:
        if "origin" in raw: out["origin"] = text(raw["origin"],"origin")
        if "locator" in raw: out["locator"] = text(raw["locator"],"locator",8000)
    require(len(canonical(out).encode())<=2*1024*1024,"node exceeds 2 MiB")
    dependencies(out["depends_on"])
    return out


def dependencies(raw):
    require(isinstance(raw,list) and len(raw)<=2000,"at most 2000 explicit dependencies")
    for d in raw:
        fields(d,("kind","id"),())
        require(d["kind"] in ("node","edge"),"dependency kind must be node or edge")
        text(d["id"],"dependency id")
    return raw


def normalize_edge(raw):
    fields(raw,("id","subject","predicate","object"),("revision","source_ids","status","space","scope","clocks","data","depends_on"))
    out = {k:text(raw[k],k) for k in ("id","subject","predicate","object")}
    out.update(revision=integer(raw.get("revision",1),1,10**9),source_ids=refs(raw.get("source_ids",[])),
               status=raw.get("status","annotated"),space=raw.get("space","evidence"),scope=raw.get("scope",""),
               clocks=clocks(raw.get("clocks",{})),data=deepcopy(raw.get("data",{})),depends_on=dependencies(raw.get("depends_on",[])))
    require(out["status"] in STATUSES and out["space"] in ("evidence","hypothesis","research"),"unknown edge state/space")
    require(isinstance(out["scope"],str) and isinstance(out["data"],dict),"scope must be text; data must be an object")
    require(bool(out["source_ids"]) or out["status"] in ("candidate","hypothesis","proposed") or bool(out["depends_on"]),"an assertion needs sources, explicit hypothesis status or dependencies")
    return out


def normalize_batch(raw):
    fields(raw,("schema","batch_id"),("nodes","edges","remove_nodes","remove_edges","note"))
    require(raw["schema"]=="loom.graph.batch.v1","expected loom.graph.batch.v1")
    out={"schema":raw["schema"],"batch_id":text(raw["batch_id"],"batch id"),"note":raw.get("note","")}
    for name,normalizer in (("nodes",normalize_node),("edges",normalize_edge)):
        entries=raw.get(name,[]);require(isinstance(entries,list) and len(entries)<=MAX_BATCH,"batch array exceeds limit")
        out[name]=[normalizer(x) for x in entries]
    for name in ("remove_nodes","remove_edges"):
        entries=raw.get(name,[]);require(isinstance(entries,list) and len(entries)<=MAX_BATCH,"batch array exceeds limit")
        out[name]=[]
        for x in entries:
            fields(x,("id","revision"));out[name].append({"id":text(x["id"]),"revision":integer(x["revision"],1,10**9)})
    for name,removed in (("nodes","remove_nodes"),("edges","remove_edges")):
        ids=[x["id"] for x in out[name]+out[removed]];require(len(ids)==len(set(ids)),"duplicate upsert/removal id in batch")
    require(len(canonical(out).encode())<=MAX_BYTES,"batch exceeds 16 MiB")
    return out


def norm(value):
    return " ".join(re.findall(r"\w+",unicodedata.normalize("NFKC",str(value)).casefold()))


def rational(value):
    require(type(value) is int or isinstance(value,str),"exact quantities/costs require integers or rational/decimal strings")
    require(not isinstance(value,str) or len(value)<=128,'exact rational text exceeds 128 characters')
    try:
        return Fraction(value)
    except (ValueError,ZeroDivisionError) as e:
        raise ResearchError("invalid exact rational") from e


def result(state="COMPLETE", **values):
    return {"schema":"loom.research.result.v1","state":state,"proof_effect":"NONE",**values}
