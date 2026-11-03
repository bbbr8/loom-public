"""surd_portal — qubit portals: every search opens a superposed state across realms and teaches the next search.

A portal takes the anchors in a query (exact amounts, ISO and US dates, Bates tokens, check numbers, proper names)
and looks for them in every registered realm. The portal state is a meaning over realms with weight = the anchor
hits each realm returned, and a list of tunnels: anchors that land in two or more realms (a collision worth
opening). Anchors that land in exactly one realm are recorded as one-sided — the places a second witness has not
yet been found. Every portal is appended to a LOCAL-ONLY log, and portal_stats() turns that log into per-realm
Beta(hits+1, misses+1) yields so the next fan-out can try the most productive realms first (Thompson sampling,
seeded). This is a precise notation for search state; no quantum computation is claimed.
"""
import json
import os
import random
import re
import time
from fractions import Fraction as F

LOG = os.environ.get("SURD_PORTAL_LOG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state",
                                                         "portals_LOCAL_ONLY.jsonl")
AMT = re.compile(r"\$?\b\d{1,3}(?:,\d{3})*\.\d{2}\b|\$\d+(?:\.\d{2})?\b")
ISO = re.compile(r"\b(20\d\d|19\d\d)-(\d\d)-(\d\d)\b")
US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
BATES = re.compile(r"\b([A-Z][A-Z\-]{1,15})[ _]?(\d{4,7})\b")
CHECK = re.compile(r"\b(?:ck|check|chk)\s*#?\s*(\d{2,6})\b", re.I)
NAME = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
STOP = {"The", "This", "That", "And", "For", "From", "With", "Check", "Deposit", "Wire", "Transfer", "Draw", "Invoice",
        "Paid", "Total", "Date", "Amount", "Page", "Bank", "Account", "Statement", "Balance"}


def anchors(text):
    t = str(text or "")
    out = set()
    for m in AMT.finditer(t):
        v = m.group(0).replace("$", "").replace(",", "")
        try: out.add(("amount", str(F(v).limit_denominator(100))))
        except (ValueError, ZeroDivisionError): pass
    for m in ISO.finditer(t): out.add(("date", f"{m.group(1)}-{m.group(2)}-{m.group(3)}"))
    for m in US.finditer(t):
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
        try: out.add(("date", f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"))
        except ValueError: pass
    for m in BATES.finditer(t): out.add(("bates", f"{m.group(1).replace('-', '_')}_{int(m.group(2))}"))
    for m in CHECK.finditer(t): out.add(("check", str(int(m.group(1)))))
    for m in NAME.finditer(t):
        if m.group(1) not in STOP and m.group(1).split()[0] not in STOP: out.add(("name", m.group(1).lower()))
    return out


def row_text(row):
    if isinstance(row, dict):
        return " ".join(str(v) for v in row.values() if v is not None)
    return str(row)


def open_portal(query, realms, order=None):
    """realms: {name: [rows]}. Returns {anchors, weights {realm: hits}, tunnels [{anchor, realms}], one_sided, hits}"""
    qa = anchors(query)
    hits, where = {}, {}
    for name in (order or list(realms)):
        rows = realms[name]
        n = 0
        for i, r in enumerate(rows):
            ra = anchors(row_text(r))
            shared = qa & ra
            if shared:
                n += len(shared)
                for a in shared:
                    where.setdefault(a, {}).setdefault(name, []).append(i)
        if n: hits[name] = n
    tunnels = [{"anchor": a, "realms": {k: v[:20] for k, v in rs.items()}} for a, rs in sorted(where.items()) if len(rs) >= 2]
    one_sided = [{"anchor": a, "realm": next(iter(rs))} for a, rs in sorted(where.items()) if len(rs) == 1]
    missing = sorted(a for a in qa if a not in where)
    return {"query": str(query), "anchors": sorted(qa), "weights": hits, "tunnels": tunnels, "one_sided": one_sided,
            "unmatched_anchors": missing, "realms_tried": list(order or realms)}


def log_portal(portal, log=None, source="surd"):
    path = log or LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "query": portal["query"],
           "anchors": [list(a) for a in portal["anchors"]], "weights": portal["weights"],
           "realms_tried": portal["realms_tried"], "tunnels": len(portal["tunnels"]),
           "one_sided": [[list(x["anchor"]), x["realm"]] for x in portal["one_sided"]]}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def read_log(log=None):
    path = log or LOG
    if not os.path.exists(path): return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try: out.append(json.loads(line))
            except ValueError: pass
    return out


def stats(log=None):
    """per realm: tries, productive tries, Beta posterior mean; most frequent one-sided anchors (second-witness gaps)"""
    recs = read_log(log)
    per, gaps = {}, {}
    for r in recs:
        for name in r.get("realms_tried", []):
            p = per.setdefault(name, [0, 0])
            p[1] += 1
            if r.get("weights", {}).get(name): p[0] += 1
        for a, realm in r.get("one_sided", []):
            k = (tuple(a), realm)
            gaps[k] = gaps.get(k, 0) + 1
    yields = {n: {"tries": t, "productive": h, "mean": F(h + 1, t + 2)} for n, (h, t) in per.items()}
    top = sorted(gaps.items(), key=lambda kv: -kv[1])[:25]
    return {"portals": len(recs), "realms": yields,
            "one_sided": [{"anchor": list(k[0]), "realm": k[1], "times": v} for k, v in top]}


def thompson_order(names, log=None, seed=None):
    """order realms by a draw from each realm's Beta(hits+1, misses+1) posterior (seeded; untried realms first-class)"""
    st = stats(log)["realms"]
    rng = random.Random(seed)
    draws = []
    for n in names:
        h = st.get(n, {}).get("productive", 0); t = st.get(n, {}).get("tries", 0)
        draws.append((rng.betavariate(h + 1, t - h + 1), n))
    return [n for _, n in sorted(draws, reverse=True)]
