"""Loom's read-only timeline dialect, ported from loom.strata with bounded intake."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import shlex
import sqlite3
import time

COMMANDS = {"window", "around", "layers", "hidden", "mail", "ghosts", "money",
            "timeline", "count", "collide", "save", "say"}
LAYERS = ["deleted", "comment", "superseded", "metadata", "external", "cached", "orphaned", "hidden"]


def parse_time(value):
    try:
        text = str(value).strip()
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            return None  # keep an unknown zone visible rather than invent UTC
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        return None


def statements(source):
    if not isinstance(source, str) or len(source.encode()) > 1000000:
        raise ValueError("timeline program must be text under 1 MB")
    lexer = shlex.shlex(source, posix=True, punctuation_chars=";\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if token and all(c in ";\n" for c in token):
            if current:
                yield current
                current = []
        else:
            current.append(token)
    if current:
        yield current


def run(program, store=None, ledger=None, max_rows=10000, seconds=5):
    if type(max_rows) is not int or not 1 <= max_rows <= 100000:
        raise ValueError("max_rows must be 1..100000")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError("seconds must be finite and in (0,60]")
    store = store or os.environ.get("LOOM_TIMELINE_STORE")
    ledger = ledger or os.environ.get("LOOM_LEDGER")
    deadline = time.monotonic() + seconds
    events, seen, messages, diagnostics, collisions, intake = [], set(), [], [], [], []
    lo, hi, layers = "2017-01-01", "2026-12-31", list(LAYERS)
    mail_times, subjects = [], {}
    complete, unknown_dates = True, 0

    def query(path, sql, parameters=()):
        nonlocal complete
        if time.monotonic() >= deadline:
            raise ValueError("timeline time budget exhausted")
        if not path:
            raise ValueError("Supply --store/--ledger or LOOM_TIMELINE_STORE/LOOM_LEDGER explicitly")
        path = str(Path(path).expanduser())
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=min(seconds, 5))) as con:
            con.execute("PRAGMA query_only=ON")
            con.row_factory = sqlite3.Row
            con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = con.execute(sql + " LIMIT ?", (*parameters, max_rows + 1)).fetchall()
        truncated = len(rows) > max_rows
        if truncated:
            complete = False
            diagnostics.append("row limit reached: " + path)
        intake.append({"path": path, "rows": min(len(rows), max_rows), "truncated": truncated})
        return [dict(r) for r in rows[:max_rows]]

    def add(t, kind, layer, mech, text, where, path, **extra):
        key = (kind, mech, t, str(text), str(where))
        if key in seen:
            return False
        if len(events) >= max_rows:
            nonlocal_complete()
            return False
        seen.add(key)
        events.append(dict(day=t[:10], time=t[11:16] if len(t) > 10 else "", t=t,
                           kind=kind, layer=layer, mech=mech, text=text, where=where,
                           path=path, file=Path(path).name, precision="day" if len(t) == 10 else "timestamp", **extra))
        return True

    def nonlocal_complete():
        nonlocal complete
        complete = False
        if "event limit reached" not in diagnostics:
            diagnostics.append("event limit reached")

    def headers():
        nonlocal mail_times, subjects, unknown_dates
        rows = query(store, "SELECT sha256,path,text,json_extract(detail,'$.header') AS header FROM findings WHERE mech='eml_header'")
        subjects = {r["sha256"]: r["text"] for r in rows if r["header"] == "Subject"}
        mail_times = []
        dated = []
        for r in rows:
            if r["header"] == "Date":
                t = parse_time(r["text"])
                if t and len(t) > 10:
                    mail_times.append(datetime.fromisoformat(t.replace("Z", "+00:00")))
                if t:
                    dated.append((r, t))
                else:
                    unknown_dates += 1
        return dated

    def hidden():
        nonlocal unknown_dates
        if not layers:
            return 0
        rows = query(store, "SELECT sha256,path,mech,layer,where_ AS w,text,detail FROM findings WHERE layer IN (" + ",".join("?" for _ in layers) + ") AND mech!='eml_header'", layers)
        n = 0
        for r in rows:
            if time.monotonic() >= deadline:
                raise ValueError("timeline time budget exhausted")
            detail = json.loads(r["detail"] or "{}")
            raw = next((detail[k] for k in ("date", "when", "signed_at", "refreshed") if detail.get(k) is not None), None)
            if raw is None and (r["mech"] in ("eml_thread_prior_message", "eml_first_hop_time") or
                                r["mech"] == "ooxml_core_property" and detail.get("field") in ("created", "modified", "lastPrinted")):
                raw = r["text"]
            t = parse_time(raw) if raw is not None else None
            if t is None:
                unknown_dates += 1
                continue
            if not lo <= t[:10] <= hi:
                continue
            ghost = r["mech"] == "eml_thread_prior_message"
            if ghost and not subjects:
                headers()
            n += add(t, "ghost?" if ghost else "layer", r["layer"], r["mech"],
                     'earlier message in “' + subjects.get(r["sha256"], "?") + '”' if ghost else r["text"],
                     "" if ghost else r["w"], r["path"], source_sha256=r["sha256"],
                     date_role=r["mech"], raw_date=str(raw))
        return n

    for w in statements(program):
        if time.monotonic() >= deadline:
            raise ValueError("timeline time budget exhausted")
        cmd = w[0]
        if cmd not in COMMANDS:
            raise ValueError("unknown statement '" + cmd + "'")
        if cmd in ("hidden", "mail", "ghosts", "count") and len(w) != 1:
            raise ValueError(cmd + " takes no arguments")
        if cmd == "window":
            if len(w) != 4 or w[2] != "..":
                raise ValueError("window START .. END")
            lo, hi = date.fromisoformat(w[1]).isoformat(), date.fromisoformat(w[3]).isoformat()
            if lo > hi:
                raise ValueError("window start exceeds end")
        elif cmd == "around":
            if len(w) != 4 or w[2] != "days" or int(w[3]) < 0:
                raise ValueError("around DATE days NONNEGATIVE_INTEGER")
            d, n = date.fromisoformat(w[1]), int(w[3])
            lo, hi = (d-timedelta(days=n)).isoformat(), (d+timedelta(days=n)).isoformat()
            messages.append(f"window {lo} .. {hi}")
        elif cmd == "layers":
            if any(x not in LAYERS + ["live"] for x in w[1:]):
                raise ValueError("unknown layer")
            layers = w[1:]
        elif cmd == "hidden":
            messages.append(f"dated hidden-layer events: {hidden()}")
        elif cmd == "mail":
            n = sum(add(t, "mail", "live", "email_on_disk", subjects.get(r["sha256"], "(no subject)"), "Date", r["path"], source_sha256=r["sha256"], date_role="message_header_date")
                    for r,t in headers() if lo <= t[:10] <= hi)
            messages.append(f"emails on disk in window: {n}")
        elif cmd == "ghosts":
            if not any(e["kind"] == "ghost?" for e in events):
                hidden()
            headers()
            n = 0
            for e in events:
                if time.monotonic() >= deadline:
                    raise ValueError("timeline time budget exhausted")
                if e["kind"] != "ghost?":
                    continue
                dt = datetime.fromisoformat(e["t"].replace("Z", "+00:00")) if len(e["t"]) > 10 else None
                if dt is None:
                    e["kind"] = "thread-time-open"
                    continue
                nearby = dt is not None and any(abs((dt-m).total_seconds()) <= 120 for m in mail_times)
                e["kind"] = "thread-msg" if nearby else "ghost"
                e["match_scope"] = "timestamp proximity across inspected store; message identity remains open"
                n += not nearby
            messages.append(f"Thread-Index earlier messages with no email file within 120 s: {n}")
        elif cmd == "money":
            if len(w) not in (2,4) or w[1] != "ledger" or len(w) == 4 and w[2] != "min":
                raise ValueError("money ledger [min AMOUNT]")
            threshold = Decimal(w[3]) if len(w) == 4 else Decimal(0)
            if not threshold.is_finite() or threshold < 0:
                raise ValueError("minimum must be finite and nonnegative")
            rows = query(ledger, "SELECT date,amount,sign,type,check_no,counterparty,bates FROM transactions WHERE date BETWEEN ? AND ? AND type IN ('debit','check','deposit') ORDER BY date,bates", (lo,hi))
            n = 0
            for r in rows:
                amount = Decimal(str(r["amount"]))
                if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
                    raise ValueError("ledger amount must be finite exact cents")
                if amount < threshold:
                    continue
                n += add(date.fromisoformat(r["date"]).isoformat(), "money", "live", "ledger",
                         f"{r['sign']}${amount:,.2f} {r['type']} {r['counterparty'] or ''}", r["bates"], ledger,
                         amount_text=str(amount), source_sign=r["sign"], check_no=r["check_no"], date_role="ledger_transaction_date")
            messages.append(f"generic CSV money moves: {n}")
        elif cmd == "timeline":
            if len(w) not in (1,3) or len(w) == 3 and w[1] != "limit":
                raise ValueError("timeline [limit POSITIVE_INTEGER]")
            limit = int(w[2]) if len(w) == 3 else 400
            if limit < 1:
                raise ValueError("timeline limit must be positive")
            for e in sorted(events, key=lambda e:(e["day"],e["kind"] != "money",e["t"]))[:limit]:
                messages.append(f"{e['day']} {e['time']:5} {e['kind']:10} {e['mech']:28} {e['text']} {e['where'] or e['file']}")
        elif cmd == "count":
            for key,n in Counter(e["kind"] + " / " + e["mech"] for e in events).items():
                messages.append(f"  {n:6}  {key}")
        elif cmd == "collide":
            if len(w) not in (1,3) or len(w) == 3 and w[1] != "days":
                raise ValueError("collide [days NONNEGATIVE_INTEGER]")
            n = int(w[2]) if len(w) == 3 else 1
            if n < 0:
                raise ValueError("collision days must be nonnegative")
            money = [e for e in events if e["kind"] == "money"]
            for m in money:
                if time.monotonic() >= deadline:
                    raise ValueError("timeline time budget exhausted")
                near = [e for e in events if e["kind"] not in ("money","mail") and abs((date.fromisoformat(e["day"])-date.fromisoformat(m["day"])).days) <= n]
                if near:
                    collisions.append({"day":m["day"],"money":m["text"],"bates":m["where"],"n":len(near),"events":near})
            messages.append(f"money moves with dated hidden-layer events within ±{n} day(s): {len(collisions)} of {len(money)}")
        elif cmd == "save":
            if len(w) != 2:
                raise ValueError("save PATH")
            with Path(w[1]).open("x") as stream:
                for e in events:
                    stream.write(json.dumps(e,ensure_ascii=False)+"\n")
            messages.append(f"saved {len(events)} events → {w[1]}")
        elif cmd == "say":
            messages.append(" ".join(w[1:]))
    return {"schema":"loom.timeline.v1","state":"COMPLETE" if complete else "PARTIAL",
            "complete":complete,"proof_effect":"NONE","window":{"from":lo,"to":hi},
            "events":events,"collisions":collisions,"messages":messages,"intake":intake,
            "unknown_date_rows":unknown_dates,"diagnostics":diagnostics,
            "boundary":"UTC dated-source navigation and temporal proximity; actor receipt, authorization and event identity require separate inputs."}


def cli(args):
    parser = argparse.ArgumentParser(description="Loom timeline dialect: read-only Strata and money-event intake")
    parser.add_argument("program",nargs="?")
    parser.add_argument("-e","--execute")
    parser.add_argument("--store")
    parser.add_argument("--ledger")
    parser.add_argument("--max-rows",type=int,default=10000)
    parser.add_argument("--seconds",type=float,default=5)
    parser.add_argument("--json",action="store_true")
    if args and args[0] == "run":
        args = args[1:]
    a = parser.parse_args(args)
    try:
        if bool(a.program) == bool(a.execute):
            raise ValueError("supply one program path or -e PROGRAM")
        if a.program and Path(a.program).stat().st_size > 1000000:
            raise ValueError("timeline program exceeds 1 MB")
        result = run(a.execute if a.execute is not None else Path(a.program).read_text(),
                     a.store,a.ledger,a.max_rows,a.seconds)
        print(json.dumps(result,ensure_ascii=False) if a.json else "\n".join(result["messages"] + result["diagnostics"]))
        return 0 if result["complete"] else 3
    except (ValueError,OSError,sqlite3.Error,InvalidOperation,OverflowError,TypeError,KeyError) as e:
        print(json.dumps({"state":"ERROR","diagnostic":str(e),"proof_effect":"NONE"}))
        return 2
