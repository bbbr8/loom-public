"""Document adapters for explicitly supplied datasets.

No stores are discovered automatically. Set LOOM_DOCUMENT_CONFIG to a JSON file
containing families and pages. A family contains rows, or an explicit SQLite path
and SELECT query. Amount columns are decimal dollar strings. Paths are resolved
relative to that config file. SQLite connections are read-only.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from fractions import Fraction as F


class DocsError(Exception):
    pass


def _config():
    name = os.environ.get("LOOM_DOCUMENT_CONFIG")
    if not name:
        return {}, None
    path = Path(name).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("families", {}), dict):
            raise ValueError("expected an object containing families")
        return data, path.parent
    except (OSError, ValueError) as exc:
        raise DocsError(f"cannot read document configuration: {exc}") from exc


def kinds():
    data, base = _config()
    out = []
    for kind, spec in data.get("families", {}).items():
        if not isinstance(spec, dict):
            raise DocsError(f"family {kind!r} must be an object")
        path = (base / spec["path"]).resolve() if spec.get("path") else None
        out.append({"kind": kind, "store": str(path) if path else "configured rows",
                    "what": spec.get("description", kind), "present": path.is_file() if path else "rows" in spec})
    return out


def docs(kind, where=None):
    data, base = _config()
    spec = data.get("families", {}).get(kind)
    if not isinstance(spec, dict):
        raise DocsError(f"document family {kind!r} is not configured; set LOOM_DOCUMENT_CONFIG")
    try:
        if "rows" in spec:
            rows = spec["rows"]
        else:
            path = (base / spec["path"]).resolve()
            con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                con.execute("PRAGMA query_only=ON")
                con.row_factory = sqlite3.Row
                rows = [dict(r) for r in con.execute(spec["query"]).fetchmany(10001)]
            finally:
                con.close()
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("family must contain at most 10000 rows")
        out = []
        for row in rows:
            r = dict(row, kind=kind)
            for key in ("amount", "scheduled"):
                if r.get(key) is not None:
                    r[key] = F(str(r[key]).replace("$", "").replace(",", ""))
            if where and any(not (str(v).lower() in r[k].lower() if isinstance(v, str) and isinstance(r.get(k), str)
                                 else r.get(k) == v) for k, v in where.items()):
                continue
            out.append(r)
        return out
    except (KeyError, TypeError, ValueError, ZeroDivisionError, sqlite3.Error, OSError) as exc:
        raise DocsError(f"cannot read family {kind!r}: {exc}") from exc


def _norm_bates(page):
    return str(page).strip()


def _page_text(page):
    data, _ = _config()
    if "pages" not in data:
        raise DocsError("page text is not configured; set pages in LOOM_DOCUMENT_CONFIG")
    return data["pages"].get(page)


def _variants(what):
    w = str(what).strip()
    out = {w}
    m = re.fullmatch(r"-?\$?\(?([\d,]+(?:\.\d+)?)\)?", w)
    if m:
        x = F(m.group(1).replace(",", ""))
        whole, cents = divmod(abs(x) * 100, 100)
        s2 = f"{int(whole):,}.{int(cents):02d}"
        out |= {s2, s2.replace(",", ""), "$" + s2}
    return out


def _contains(text, what):
    t = re.sub(r"\s+", " ", text or "").lower()
    return any(re.sub(r"\s+", " ", v).lower() in t for v in _variants(what))


def anchor_check(bates, what, reach=3):
    b = _norm_bates(bates)
    text = _page_text(b)
    here = text is not None and _contains(text, what)
    near = []
    if not here:
        m = re.match(r"^(.*?)(\d+)$", b)
        if m:
            pre, num = m.group(1), m.group(2)
            for d in [x for k in range(1, reach + 1) for x in (k, -k)]:
                n = int(num) + d
                if n < 0: continue
                nb = f"{pre}{n:0{len(num)}d}"
                try: t2 = _page_text(nb)
                except DocsError: t2 = None
                if t2 and _contains(t2, what):
                    near.append(nb)
    return {"page": b, "page_exists": text is not None, "prints_it": here, "found_on": near}


STAGES = ["budgeted", "billed", "requested", "advanced", "received", "paid"]


def stages(amount, loan_file=None):
    """Match exact amounts in configured families, optionally restricted to one project."""
    configured = {r["kind"] for r in kinds()}
    if not configured:
        raise DocsError("no document families configured; set LOOM_DOCUMENT_CONFIG")
    x = abs(F(amount))
    hits = {s: [] for s in STAGES}
    families = {"budget_lines": "budgeted", "invoices": "billed", "draw_lines": "requested",
                "advice_lines": "advanced", "banks": "received", "payments": "paid"}
    for kind, stage in families.items():
        if kind not in configured:
            continue
        for r in docs(kind):
            if loan_file is not None and r.get("loan_file") != loan_file:
                continue
            amount_match = r.get("amount") is not None and abs(r["amount"]) == x
            if not amount_match and not (kind == "budget_lines" and r.get("scheduled") == x):
                continue
            actual_stage = "paid" if kind == "banks" and r["amount"] < 0 else stage
            hits[actual_stage].append({"kind": kind, "id": str(r.get("id")), "date": r.get("date"),
                                      "page": r.get("page") or "", "text": (r.get("text") or "")[:120]})
    reached = [s for s in STAGES if hits[s]]
    gaps = [s for s in STAGES if not hits[s] and reached and STAGES.index(s) < STAGES.index(reached[-1])]
    return {"amount": x, "stages": hits, "reached": reached, "skipped": gaps}


def collections(items, claims, group_key="draw_no", parts=(F(1),)):
    """items: [{id, amount, …}] (e.g. invoices); claims: [{id, amount, group}] (e.g. draw request lines).
    An item is collected in every group where a claim carries its amount (or a listed fraction of it).
    Items collected in two or more groups are returned first."""
    by_amt = {}
    for c in claims:
        if c.get("amount") is None: continue
        by_amt.setdefault(abs(c["amount"]), []).append(c)
    out = []
    for it in items:
        a = it.get("amount")
        if not a: continue
        found = []
        for p in parts:
            for c in by_amt.get(abs(a) * p, []):
                found.append({"claim": str(c.get("id")), "group": c.get(group_key), "part": p, "page": c.get("page") or ""})
        groups = sorted({str(f["group"]) for f in found})
        if found:
            out.append({"item": str(it.get("id")), "amount": a, "party": it.get("party") or "", "page": it.get("page") or "",
                        "groups": groups, "claims": found})
    out.sort(key=lambda r: (-len(r["groups"]), -r["amount"]))
    return out
