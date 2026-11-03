"""Bounded read-only OLAP intake. QULAP models are a separate layer."""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from .model import ModelError


def minor_units(raw, mode="signed", direction=None):
    """Exact legacy money intake; the caller declares whether the source is signed."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise ModelError("decimal source amounts must be strings; floats are not money input")
    text = raw.strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
        if text.startswith(("-", "+")):
            raise ModelError("ambiguous accounting sign")
    if text.startswith(("-$", "+$")):
        text = text[0] + text[2:]
    elif text.startswith("$"):
        text = text[1:]
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?", text):
        raise ModelError("unsupported amount syntax or precision")
    clean = text.replace(",", "")
    sign = -1 if clean.startswith("-") else 1
    clean = clean.lstrip("+-")
    whole, _, fraction = clean.partition(".")
    cents = sign * (int(whole) * 100 + int(fraction.ljust(2, "0") or "0"))
    if negative:
        cents = -cents
    if abs(cents) > 2 ** 120:
        raise ModelError("amount exceeds model magnitude limit")
    if mode == "magnitude":
        if cents < 0 or type(direction) is not int or direction not in (-1, 1):
            raise ModelError("magnitude intake needs a nonnegative amount and a declared +1/-1 direction")
        cents *= direction
    elif mode != "signed":
        raise ModelError("source mode must be signed or magnitude")
    return cents


def small_hash(path):
    p = Path(path)
    if not p.exists():
        return None
    if p.stat().st_size > 64 * 1024 * 1024:
        return {"bytes": p.stat().st_size, "sha256": None, "reason": "hash exceeds lightweight intake budget"}
    return {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def query(db, sql, parameters=(), max_rows=1000, seconds=3, max_bytes=5000000):
    if not 1 <= max_rows <= 10000 or not 0 < seconds <= 30 or not 1 <= max_bytes <= 10000000:
        raise ModelError("reader limits: rows 1..10000, seconds (0,30], bytes 1..10M")
    if not isinstance(sql, str) or len(sql) > 50000:
        raise ModelError("SQL text limit is 50000 characters")
    p = Path(db).expanduser().resolve()
    out = {"schema": "qulap.cube-read.v1", "state": "UNAVAILABLE", "rows": [],
           "database": str(p), "sql": sql, "parameters": parameters, "proof_effect": "NONE",
           "truncated": False, "coverage": "bounded query only; no source-completeness inference"}
    if not p.is_file():
        out["diagnostic"] = "database is unavailable"
        return out
    before = {"main": small_hash(p), "wal": small_hash(str(p) + "-wal")}
    start = time.monotonic()
    con = None
    try:
        con = sqlite3.connect("file:" + quote(str(p), safe="/") + "?mode=ro", uri=True, timeout=.25)
        if hasattr(con, "setlimit"):
            con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max_bytes)
            con.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 200)
            out["sqlite_value_size_limit"] = max_bytes
        else:
            out["sqlite_value_size_limit"] = None
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        def authorize(action, a, b, dbname, trigger):
            if action not in allowed or (action == sqlite3.SQLITE_FUNCTION and (b or "").lower() in {"load_extension", "writefile", "readfile"}):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(authorize)
        con.set_progress_handler(lambda: int(time.monotonic() - start >= seconds), 1000)
        cur = con.execute(sql, parameters)
        if cur.description is None:
            raise sqlite3.OperationalError("query does not return rows")
        cols = [x[0] for x in cur.description]
        if len(cols) != len(set(x.casefold() for x in cols)):
            raise sqlite3.OperationalError("duplicate output columns; give every selected column a unique alias")
        out["columns"] = cols
        used = 0
        while True:
            row = cur.fetchone()
            if row is None:
                break
            if len(out["rows"]) == max_rows:
                out["truncated"] = True
                out["limit"] = "rows"
                break
            record = dict(zip(cols, row))
            encoded = json.dumps(record, allow_nan=False).encode()
            if used + len(encoded) > max_bytes:
                out["truncated"] = True
                out["limit"] = "serialized_bytes"
                break
            used += len(encoded)
            out["rows"].append(record)
        out["state"] = "TRUNCATED" if out["truncated"] else "ROWS" if out["rows"] else "QUERY_EMPTY"
        out["serialized_bytes"] = used
    except (sqlite3.Error, TypeError, ValueError) as exc:
        out["state"] = "ERROR"
        out["diagnostic"] = str(exc)
        out["rows"] = []
    finally:
        if con is not None:
            con.close()
    after = {"main": small_hash(p), "wal": small_hash(str(p) + "-wal")}
    out["input_fingerprints"] = before
    hashed = all(x is None or x.get("sha256") is not None for x in list(before.values()) + list(after.values()))
    out["bytes_stable_during_read"] = (before == after) if hashed else None
    out["fingerprint_coverage"] = "full main/WAL hashes" if hashed else "some files exceed the lightweight hashing budget"
    out["seconds"] = time.monotonic() - start
    return out


def rollup(read, contract):
    """One event once; never quietly count a join fan-out or version conflict."""
    if contract.get("schema") != "qulap.cube-contract.v1":
        raise ModelError("expected qulap.cube-contract.v1")
    axes = contract.get("axes", {})
    if any(k not in axes for k in ("currency", "scope", "stage", "basis", "version")):
        raise ModelError("declare currency, scope, stage, basis and version axes")
    mapping = contract.get("columns", {})
    if any(not mapping.get(k) for k in ("event_id", "locator", "lineage_id")) or ("amount_cents" in mapping) == ("amount_text" in mapping):
        raise ModelError("map event_id, locator, lineage_id and exactly one of amount_cents or amount_text")
    for key, spec in axes.items():
        if not isinstance(spec, dict) or ("column" in spec) == ("fixed" in spec):
            raise ModelError("each axis needs exactly one of column or fixed")
    groups, events, rejected, excluded = {}, {}, [], []
    if read["state"] not in ("ROWS", "QUERY_EMPTY", "TRUNCATED"):
        return {"state": read["state"], "diagnostic": read.get("diagnostic"), "proof_effect": "NONE", "groups": []}
    for i, raw in enumerate(read["rows"]):
        money = contract.get("money", {})
        required = list(mapping.values()) + [x["column"] for x in axes.values() if "column" in x]
        if money.get("direction_column"):
            required.append(money["direction_column"])
        if any(k not in raw for k in required):
            raise ModelError("mapped column absent from query output")
        row = {k: raw[v] for k, v in mapping.items()}
        if "amount_text" in row:
            try:
                mode = money.get("source_mode")
                if mode is None:
                    raise ModelError("text amount contract must declare money.source_mode")
                sign = money.get("direction_map", {}).get(raw.get(money.get("direction_column")))
                row["amount_cents"] = minor_units(row["amount_text"], mode, sign)
            except ModelError as exc:
                rejected.append({"row": i, "reason": str(exc), "raw": raw})
                continue
        row["axes"] = {k: raw[spec["column"]] if "column" in spec else spec["fixed"] for k, spec in axes.items()}
        row["raw_row_number"] = i
        if not row["event_id"] or not row["locator"] or not row["lineage_id"]:
            rejected.append({"row": i, "reason": "missing event or native source identity", "raw": raw})
            continue
        if row["amount_cents"] is not None and type(row["amount_cents"]) is not int:
            rejected.append({"row": i, "reason": "amount_cents must be SQLite INTEGER or NULL", "raw": raw})
            continue
        if any(v is None or v == "" for v in row["axes"].values()):
            rejected.append({"row": i, "reason": "unknown aggregation axis", "raw": raw})
            continue
        events.setdefault(str(row["event_id"]), []).append(row)
    deduplicated = 0
    for eid, observations in events.items():
        signatures = {json.dumps({"amount_cents": x["amount_cents"], "axes": x["axes"]}, sort_keys=True) for x in observations}
        if len(signatures) > 1:
            excluded.append({"event_id": eid, "reason": "conflicting amount, stage, scope or version; resolve event identity", "observations": observations})
            continue
        deduplicated += len(observations) - 1
        row = observations[0]
        key = json.dumps(row["axes"], sort_keys=True)
        group = groups.setdefault(key, {"axes": row["axes"], "known_sum_cents": 0, "known_count": 0,
                                       "unknown_count": 0, "events": []})
        if row["amount_cents"] is None:
            group["unknown_count"] += 1
        else:
            group["known_sum_cents"] += row["amount_cents"]
            group["known_count"] += 1
        group["events"].append({"event_id": eid, "amount_cents": row["amount_cents"],
                                "source_observations": [{k: x[k] for k in ("locator", "lineage_id", "raw_row_number", "amount_text") if k in x} for x in observations]})
    complete = read["state"] != "TRUNCATED" and not rejected and not excluded
    for g in groups.values():
        g["complete_total_cents"] = g["known_sum_cents"] if complete and not g["unknown_count"] else None
    return {"schema": "qulap.rollup.v1", "state": "COMPLETE_QUERY_COHORT" if complete else "PARTIAL",
            "input_state": read["state"], "groups": list(groups.values()), "rejected": rejected,
            "conflicting_events": excluded, "duplicate_observations_suppressed": deduplicated,
            "proof_effect": "NONE", "contract": contract,
            "boundary": "SQL cohort arithmetic only; QULAP alternative states require an explicit separate model."}
