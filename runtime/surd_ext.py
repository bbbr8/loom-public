"""surd_ext — language wrappers for the multi-bank ledger, observers/perspectives/language auditor, and qubit portals.

surd.py hands in its own types through a context object K (so this module never imports surd and works when surd.py
runs as __main__). Each wrapper converts Surd values (Money, Date, R2, maps, lists, realms) to plain exact Python,
calls the pure module, and converts back.
"""
from fractions import Fraction as F
import os

import surd_docs as DOC
import surd_ledger as L
import surd_observe as O
import surd_physics as PH
import surd_portal as P
import surd_network as NET
import surd_timing as TIM

LEDGER = {"accounts", "transfers", "consolidate", "cycles", "floats", "carry"}
OBSERVE = {"perspectives", "every_perspective", "pooled", "knew", "audit_language", "audit_thread"}
PORTAL = {"portal", "portal_realm", "portal_stats", "portal_order", "last_portal"}
DOCS = {"docs", "kinds", "anchor_check", "stages", "collections"}
PHYSICS = {"rayleigh", "spectral_lines", "zeno_test", "detailed_balance", "residence"}
NETWORK = {"net_bounds", "timing"}
FUNCS = LEDGER | OBSERVE | PORTAL | DOCS | PHYSICS | NETWORK


def _lang(K, v):
    """a plain stored value -> a Surd value (exact numbers, dates kept as text unless the field is a date)"""
    if v is None: return K.NONE
    if isinstance(v, bool): return v
    if isinstance(v, int): return K.R2(v)
    if isinstance(v, F): return K.R2(v)
    if isinstance(v, float): return K.R2(F(str(v)))
    if isinstance(v, (list, tuple)): return [_lang(K, x) for x in v]
    if isinstance(v, dict): return {str(k): _lang(K, x) for k, x in v.items()}
    return str(v)


def _doc_row(K, r):
    out = {}
    for k, v in r.items():
        if k == "amount" or k in ("scheduled", "change_orders", "work_completed", "deposits", "balance", "owner_paid"):
            out[k] = K.Money(v) if isinstance(v, F) else K.NONE if v is None else _lang(K, v)
        elif k == "date":
            try: out[k] = K.Date.parse(v) if v else K.NONE
            except K.SurdError: out[k] = str(v)
        else:
            out[k] = _lang(K, v)
    return out


def _err(K, msg, pos, code="S-E009"):
    return K.SurdError(code, msg, pos)


def _need(K, name, v, counts, sig, pos):
    if len(v) not in counts:
        raise K.SurdError("S-E004", f"{name}{sig}", pos)


def _rows(K, x, pos, name, account=None):
    """events (maps or a realm) -> plain rows {account, day, amount, memo, page, cls, type}"""
    if isinstance(x, K.Realm): x = list(x.recs)
    if not isinstance(x, list): raise _err(K, f"{name} needs events (a list) or a realm", pos)
    out = []
    for e in x:
        if isinstance(e, K.Record):
            if e.day is None or e.cents is None: continue
            row = e.row
            out.append({"account": str(row.get("account") or account or ""), "day": e.day, "amount": F(e.cents),
                        "memo": str(row.get("memo") or row.get("description") or ""), "page": e.loc,
                        "cls": str(row.get("cls") or ""), "type": str(row.get("type") or "")})
        elif isinstance(e, dict):
            out.append({"account": str(e.get("account") or account or ""), "day": K.date_of(e.get("date"), pos),
                        "amount": K.money_of(e.get("amount"), pos), "memo": str(e.get("memo") or ""),
                        "page": str(e.get("page") or ""), "cls": str(e.get("cls") or ""), "type": str(e.get("type") or "")})
        else:
            raise _err(K, f"an event is a map {{date, amount, …}}, got {K.type_name(e)}", pos)
    return out


def _days(K, v, pos):
    return int(v.a) if isinstance(v, K.R2) and v.is_rational() else 3


def _pair_out(K, rows, p):
    return {"from": p["from"], "to": p["to"], "amount": K.Money(p["amount"]), "out_date": K.Date(p["out_day"]),
            "in_date": K.Date(p["in_day"]), "lag": K.R2(p["lag"]), "hint": p["hint"],
            "out_page": rows[p["out"]]["page"], "in_page": rows[p["in"]]["page"],
            "out_memo": rows[p["out"]]["memo"], "in_memo": rows[p["in"]]["memo"]}


def _frac(K, x):
    return K.R2(x) if isinstance(x, (F, int)) else x


def _facts(K, x, pos):
    if not isinstance(x, list): raise _err(K, "facts are a list of maps {id, date, holders, quantity, value}", pos)
    out = []
    for f in x:
        if not isinstance(f, dict): raise _err(K, "a fact is a map {id, date, holders, quantity, value}", pos)
        hold = f.get("holders") or []
        if isinstance(hold, str): hold = [hold]
        out.append({"id": str(f.get("id") or ""), "day": K.date_of(f.get("date"), pos), "holders": [str(h) for h in hold],
                    "quantity": None if f.get("quantity") in (None, K.NONE) else str(f.get("quantity")),
                    "value": None if f.get("value") in (None, K.NONE) else K.fmt_value(f.get("value")),
                    "text": str(f.get("text") or ""), "page": str(f.get("page") or ""),
                    "lenses": [str(z) for z in (f.get("lenses") or [])] or None})
    return out


def _realm_rows(K, x):
    if isinstance(x, K.Realm): return [dict(r.row) for r in x.recs]
    if isinstance(x, list):
        return [{k: K.fmt_value(v) for k, v in e.items()} if isinstance(e, dict) else K.fmt_value(e) for e in x]
    return []


def gather_realms(it, K):
    realms = dict(getattr(it, "portal_realms", {}))
    for name, val in it.globals.items():
        if isinstance(val, K.Realm) and name not in realms:
            realms[name] = val
    return {n: _realm_rows(K, v) for n, v in realms.items()}


def portal_value(it, K, po, pos):
    w = {k: F(v) for k, v in po["weights"].items()}
    state = K.NONE
    if w:
        sp = K.Space("PORTAL", sorted(w))
        state = it._weights_meaning((sp,), w, pos)
    return {"anchors": [f"{k}:{v}" for k, v in po["anchors"]], "weights": {k: K.R2(v) for k, v in po["weights"].items()},
            "tunnels": [{"anchor": f"{t['anchor'][0]}:{t['anchor'][1]}", "realms": sorted(t["realms"])} for t in po["tunnels"]],
            "one_sided": [{"anchor": f"{x['anchor'][0]}:{x['anchor'][1]}", "realm": x["realm"]} for x in po["one_sided"]],
            "unmatched": [f"{k}:{v}" for k, v in po["unmatched_anchors"]], "state": state}


def auto_portal(it, K, query, source):
    """called after every search-family function: open a portal over every realm in scope and log it locally"""
    if not getattr(it, "portals_on", False):
        return None
    realms = gather_realms(it, K)
    if not realms:
        return None
    order = P.thompson_order(list(realms), seed=it.rng.random())
    po = P.open_portal(query, realms, order)
    P.log_portal(po, source=source)
    it.last_portal = po
    return po


def _net_bounds(it, K, v, pos):
    """net_bounds(pipes, fn(p) => picks pipes, sources): the class money the picked pipes carry together, floor..ceiling,
    under EVERY allocation across every account. A class = the own money of the source accounts (what they pay out
    beyond what reached them). Pipes run in the order given; each is {from, to, amount}."""
    sig = "(pipes, fn(p) => picks pipes, source account or list)"
    _need(K, "net_bounds", v, (3,), sig, pos)
    raw = list(v[0].recs) if isinstance(v[0], K.Realm) else v[0]
    if not isinstance(raw, list):
        raise _err(K, "net_bounds needs pipes: a realm or a list of {from, to, amount}", pos)
    srcs = {str(x) for x in v[2]} if isinstance(v[2], list) else {str(v[2])}
    pipes, picked = [], set()
    for p in raw:
        if isinstance(p, K.Record):
            f, t, a = p.row.get("from"), p.row.get("to"), p.cents
        elif isinstance(p, dict):
            f, t, a = p.get("from"), p.get("to"), K.money_of(p.get("amount"), pos)
        else:
            raise _err(K, f"a pipe is a map {{from, to, amount}}, got {K.type_name(p)}", pos)
        if f is None or t is None or a is None:
            raise _err(K, "every pipe needs from, to and amount", pos)
        c = F(a) * 100
        if c.denominator != 1 or c < 0:
            raise _err(K, f"pipe amounts are positive whole cents, got {a}", pos)
        r = it.call_value(v[1], [p], pos)
        if it.truthy(r, pos):
            picked.add(len(pipes))
        pipes.append((str(f), str(t), int(c)))
    try:
        lo, hi, cin = NET.bounds(pipes, picked, srcs)
    except ValueError as e:
        raise _err(K, str(e), pos)
    lo, hi, cin = F(lo, 100), F(hi, 100), F(cin, 100)
    total = F(sum(pipes[i][2] for i in picked), 100)
    return {"pipes": K.R2(len(pipes)), "picked": K.R2(len(picked)), "total": K.Money(total),
            "class_in": K.Money(cin), "floor": K.Money(lo), "ceiling": K.Money(hi), "range": K.Bound(lo, hi)}


def _day_of(K, x, pos):
    if isinstance(x, K.Date): return x.n
    if isinstance(x, K.Record): return x.day
    if isinstance(x, dict): return K.date_of(x.get("date"), pos).n
    if isinstance(x, str): return K.Date.parse(x, pos).n
    raise _err(K, f"timing reads dates, maps with a date, or records; got {K.type_name(x)}", pos)


def _timing(it, K, v, pos):
    """timing(statements, events, before, after): exact circular-shift test of statements landing near events"""
    _need(K, "timing", v, (4,), "(statements, events, days before, days after)", pos)
    seqs = []
    for x in v[:2]:
        x = list(x.recs) if isinstance(x, K.Realm) else x
        if not isinstance(x, list): raise _err(K, "timing takes two lists (or realms) of dated items", pos)
        seqs.append([d for d in (_day_of(K, y, pos) for y in x) if d])
    try:
        b, a = int(K.money_of(v[2], pos)), int(K.money_of(v[3], pos))
        r = TIM.test(seqs[0], seqs[1], b, a)
    except (ValueError, TypeError) as e:
        raise _err(K, str(e), pos)
    D = K.Date
    return {"observed": K.R2(r["observed"]), "statements": K.R2(r["statements"]), "events": K.R2(r["events"]),
            "expected": K.R2(r["expected"]), "p": K.R2(r["p"]), "offsets": K.R2(r["offsets"]),
            "from": D(r["lo"]), "to": D(r["hi"]),
            "hits": [{"statement": D(x), "event": D(y), "lag": K.R2(y - x)} for x, y in r["hits"]]}


def call(it, K, name, v, pos):
    M, D, R2 = K.Money, K.Date, K.R2
    if name == "timing":
        return _timing(it, K, v, pos)
    if name in NETWORK:
        return _net_bounds(it, K, v, pos)
    if name in LEDGER:
        if name == "accounts":
            _need(K, name, v, (1,), "({\"account\": events, …})", pos)
            if not isinstance(v[0], dict): raise _err(K, "accounts takes a map of account name to events", pos)
            out = []
            for acct, evs in v[0].items():
                for r in _rows(K, evs, pos, name, account=str(acct)):
                    out.append({"account": r["account"], "date": D(r["day"]), "amount": M(r["amount"]), "memo": r["memo"],
                                "page": r["page"], "cls": r["cls"], "type": r["type"]})
            out.sort(key=lambda e: (e["date"].n, e["account"]))
            return out
        _need(K, name, v, (1, 2, 3), f"({'events, doctrine' if name == 'carry' else 'events'} [, days])", pos)
        rows = _rows(K, v[0], pos, name)
        if any(not r["account"] for r in rows):
            raise _err(K, f"{name} needs an account on every event (build them with accounts({{…}}))", pos)
        if name == "carry":
            doc = v[1] if len(v) >= 2 else "fifo"
            if doc not in ("fifo", "lifo", "prorata"): raise _err(K, "carry doctrines: fifo, lifo, prorata", pos)
            days = _days(K, v[2], pos) if len(v) == 3 else 3
        else:
            days = _days(K, v[1], pos) if len(v) == 2 else 3
        pairs = L.transfers(rows, days)
        if name == "transfers":
            return [_pair_out(K, rows, p) for p in pairs]
        if name == "floats":
            return [_pair_out(K, rows, p) for p in L.float_days(pairs)]
        if name == "consolidate":
            c = L.consolidate(rows, pairs)
            return {"accounts": {a: M(x) for a, x in sorted(c["accounts"].items())}, "group": M(c["group"]),
                    "external_in": M(c["external_in"]), "external_out": M(c["external_out"]),
                    "moved_between": M(c["moved_between"]), "transfers": R2(c["transfers"])}
        if name == "cycles":
            return [{"accounts": c["accounts"], "bottleneck": M(c["bottleneck"]), "days": R2(c["days"]),
                     "legs": [_pair_out(K, rows, p) for p in c["legs"]]} for c in L.cycles(pairs, 5, 30)]
        comp, left = L.carry(rows, pairs, doc)
        outs = [{"account": rows[i]["account"], "date": D(rows[i]["day"]), "amount": M(rows[i]["amount"]),
                 "memo": rows[i]["memo"], "page": rows[i]["page"], "from": {c: M(x) for c, x in comp[i].items()},
                 "transfer": i in {p["out"] for p in pairs}}
                for i in sorted(comp, key=lambda i: (rows[i]["day"], i)) if rows[i]["amount"] < 0]
        return {"outflows": outs, "left": {a: {c: M(x) for c, x in sorted(m.items())} for a, m in sorted(left.items())}}

    if name in OBSERVE:
        if name in ("perspectives", "every_perspective"):
            _need(K, name, v, (5, 6, 7) if name == "perspectives" else (5,),
                  "(facts, observers, quantity, value, days" + (" [, n, seed])" if name == "perspectives" else ")"), pos)
            facts = _facts(K, v[0], pos)
            obs = [str(o) for o in v[1]]
            days = [K.date_of(d, pos) for d in v[4]]
            q, val = str(v[2]), K.fmt_value(v[3])
            if name == "every_perspective":
                return [{"observer": r["observer"], "date": D(r["day"]), "verdict": r["verdict"], "values": r["values"]}
                        for r in O.every_perspective(facts, obs, q, val, days)]
            n = int(v[5].a) if len(v) >= 6 else 200
            seed = int(v[6].a) if len(v) == 7 else 1
            if not 1 <= n <= 100000: raise K.SurdError("S-E010", "perspectives draws 1 to 100000 times", pos)
            r = O.perspectives(facts, obs, q, val, days, n, seed)
            sp = K.Space("BELNAP", ["T", "F", "B", "N"])
            state = it._weights_meaning((sp,), {k: x for k, x in r["overall"].items() if x}, pos)
            return {"overall": {k: R2(x) for k, x in sorted(r["overall"].items())},
                    "per_observer": {o: {k: R2(x) for k, x in sorted(d.items())} for o, d in sorted(r["per_observer"].items())},
                    "state": state, "n": R2(n), "seed": R2(seed),
                    "draws": [{"observer": d["observer"], "date": D(d["day"]), "verdict": d["verdict"]} for d in r["draws"][:50]]}
        if name == "pooled":
            _need(K, name, v, (5,), "(facts, group, quantity, value, date)", pos)
            r = O.pooled(_facts(K, v[0], pos), [str(o) for o in v[1]], str(v[2]), K.fmt_value(v[3]), K.date_of(v[4], pos))
            return {"alone": r["alone"], "pooled": r["pooled"], "only_when_pooled": r["only_when_pooled"], "values": r["values"]}
        if name == "knew":
            _need(K, name, v, (1, 2, 3), "(facts [, quantity, value])", pos)
            r = O.knew(_facts(K, v[0], pos), str(v[1]) if len(v) >= 2 else None, K.fmt_value(v[2]) if len(v) == 3 else None)
            return {h: {"date": D(x["day"]), "fact": x["fact"], "page": x["page"] or K.NONE}
                    for h, x in sorted(r.items(), key=lambda kv: kv[1]["day"])}
        if name == "audit_language":
            _need(K, name, v, (1, 2), "(text [, {speaker, audience, date, facts, quantity, said}])", pos)
            if not isinstance(v[0], str): raise _err(K, "audit_language reads a text", pos)
            o = v[1] if len(v) == 2 else {}
            if not isinstance(o, dict): raise _err(K, "the second argument is a map {speaker, audience, date, …}", pos)
            facts = _facts(K, o["facts"], pos) if "facts" in o else None
            day = K.date_of(o["date"], pos) if "date" in o else None
            r = O.audit_message(v[0], o.get("speaker"), o.get("audience"), day, facts,
                                str(o["quantity"]) if "quantity" in o else None, K.fmt_value(o["said"]) if "said" in o else None)
            out = {"speaker": r["speaker"] or K.NONE, "audience": r["audience"] or K.NONE, "frame": r["frame"] or K.NONE,
                   "features": {k: list(vv) for k, vv in r["features"].items()},
                   "functions": [{"function": f["function"], "weight": R2(f["weight"])} for f in r["functions"]],
                   "words": R2(r["words"])}
            if r["functions"]:
                sp = K.Space("FUNCTION", [f["function"] for f in r["functions"]])
                out["state"] = it._weights_meaning((sp,), {f["function"]: f["weight"] for f in r["functions"]}, pos)
            if "held_records" in r: out["held_records"] = r["held_records"]
            if "said_vs_held" in r:
                out["said_vs_held"] = r["said_vs_held"]; out["said_against_own_records"] = r["said_against_own_records"]
            return out
        _need(K, name, v, (1,), "([{speaker, audience, date, text}, …])", pos)
        if not isinstance(v[0], list): raise _err(K, "audit_thread takes a list of messages", pos)
        msgs = [{"speaker": str(m.get("speaker")), "audience": str(m.get("audience")),
                 "day": K.date_of(m["date"], pos) if "date" in m else None, "text": str(m.get("text") or "")} for m in v[0]]
        r = O.audit_thread(msgs)
        return {"by_speaker": {s: {a: {"messages": R2(x["messages"]), "features": {k: R2(c) for k, c in x["features"].items()},
                                       "per_message": {k: R2(c) for k, c in x["per_message"].items()}}
                                   for a, x in d.items()} for s, d in r["by_speaker"].items()},
                "audience_split": [{"speaker": x["speaker"], "a": x["a"], "b": x["b"],
                                    "feature_counts": {k: [R2(c[0]), R2(c[1])] for k, c in x["feature_counts"].items()},
                                    "per_message": {k: [R2(c[0]), R2(c[1])] for k, c in x["per_message"].items()},
                                    "messages": [R2(x["messages"][0]), R2(x["messages"][1])],
                                    "only_to_a": x["only_to_a"], "only_to_b": x["only_to_b"]} for x in r["audience_split"]]}

    if name in PHYSICS:
        def day(x):
            return K.date_of(x, pos) if isinstance(x, (K.Date, str)) else (int(x.a) if isinstance(x, R2) and x.is_rational() else
                   (_ for _ in ()).throw(_err(K, "a day is a date or a whole number", pos)))
        if name == "rayleigh":
            _need(K, name, v, (2,), "([days], period)", pos)
            if not isinstance(v[0], list): raise _err(K, "rayleigh takes a list of days", pos)
            per = float(v[1].a) if isinstance(v[1], R2) and v[1].is_rational() else float(v[1])
            ts = [float(x.a) if isinstance(x, R2) and x.is_rational() else day(x) for x in v[0]]   # numeric days keep fractions
            return PH.rayleigh(ts, per)
        if name == "spectral_lines":
            _need(K, name, v, (1, 2), "(events [, minimum count])", pos)
            rows = _rows(K, v[0], pos, name)
            ev = [(r["day"], abs(r["amount"]), (r["account"] or "") + (" in" if r["amount"] > 0 else "")) for r in rows if r["amount"]]
            ls = PH.lines(ev, min_count=int(v[1].a) if len(v) == 2 else 4)
            return [{"label": l["label"], "amount": M(l["amount"]), "count": R2(l["count"]), "period": R2(l["period"]),
                     "period_uncertainty": l["period_uncertainty"], "first": D(l["first"]), "last": D(l["last"]),
                     "chance": l["chance"], "scan_chance": l["scan_chance"], "regularity_chance": l["regularity_chance"],
                     "stragglers": [D(x) for x in l["stragglers"]]} for l in ls]
        if name == "residence":
            _need(K, name, v, (2, 3), "(events, class [, threshold])", pos)
            if isinstance(v[0], K.Realm): x = list(v[0].recs)
            else: x = v[0]
            if not isinstance(x, list): raise _err(K, "residence needs events (a list) or a realm", pos)
            evs = K.norm_events(x, pos)
            cls = {str(c) for c in v[1]} if isinstance(v[1], list) else {str(v[1])}
            dep = [e for e in evs if e[2] > 0 and e[3] in cls]
            if not dep: raise _err(K, "no deposits of that class", pos)
            inflow = sum((e[2] for e in dep), F(0))
            thr = K.money_of(v[2], pos) if len(v) == 3 else min(F(100), inflow / 100)   # "gone": at most $100 or 1%
            last_dep = max(e[1] for e in dep)
            end = max(e[1] for e in evs)
            out = {}
            for label, first in (("spent_last", "O"), ("spent_first", "C")):
                best = None
                for order in ("deposits_first", "withdrawals_first"):
                    _, _, traj = K.two_pool(evs, order, cls, first)
                    pts = []
                    for day, C, O_ in traj:
                        if pts and pts[-1][0] == day: pts[-1] = (day, C)
                        else: pts.append((day, C))
                    r = PH.residence(pts, inflow, end)
                    dpl = PH.depletion(pts, last_dep, thr)
                    cand = (r["mean_days"], r["dollar_days"], dpl, order)
                    if best is None or (cand[0] > best[0] if first == "O" else cand[0] < best[0]): best = cand
                out[label] = best
            hi, lo = out["spent_last"], out["spent_first"]
            return {"deposited": M(inflow), "deposits": R2(len(dep)), "last_deposit": D(last_dep),
                    "mean_days": K.Bound(lo[0], hi[0], False), "floor_days": R2(lo[0]), "ceiling_days": R2(hi[0]),
                    "dollar_days_ceiling": R2(hi[1]), "depleted_spent_last": D(hi[2]) if hi[2] else K.NONE,
                    "depleted_spent_first": D(lo[2]) if lo[2] else K.NONE, "threshold": M(thr)}
        if name == "zeno_test":
            _need(K, name, v, (2, 4), "(events, [observation days] [, start, end])", pos)
            rows = _rows(K, v[0], pos, name)
            ev = [(r["day"], abs(r["amount"])) for r in rows if r["amount"]]
            obs = [day(x) for x in v[1]]
            st, en = (day(v[2]), day(v[3])) if len(v) == 4 else (None, None)
            try:
                z = PH.zeno(ev, obs, st, en)
            except ValueError as e:
                raise _err(K, str(e), pos)
            return {"mean_distance": R2(z["mean_distance"]), "null_mean": R2(z["null_mean"]), "shifts": R2(z["shifts"]),
                    "p_far": R2(z["p_far"]), "p_near": R2(z["p_near"]), "early_share": R2(z["early_share"]),
                    "weight": M(z["weight"])}
        _need(K, name, v, (1, 2), "(events with accounts [, days])", pos)
        rows = _rows(K, v[0], pos, name)
        pairs = L.transfers(rows, _days(K, v[1], pos) if len(v) == 2 else 3)
        flows = {}
        for q in pairs:
            flows[(q["from"], q["to"])] = flows.get((q["from"], q["to"]), F(0)) + q["amount"]
        b = PH.balance(flows)
        return {"total": M(b["total"]), "reciprocal": M(b["reciprocal"]), "reciprocal_share": R2(b["reciprocal_share"]),
                "entropy_production": b["entropy_production"], "circulating": M(b["circulating"]),
                "pairs": [{"a": p["a"], "b": p["b"], "a_to_b": M(p["a_to_b"]), "b_to_a": M(p["b_to_a"]),
                           "reciprocal": M(p["reciprocal"]), "net": M(p["net"])} for p in b["pairs"]],
                "cycles": [{"accounts": c["accounts"], "dollars": M(c["dollars"])} for c in b["cycles"]],
                "one_way": [{"from": o["from"], "to": o["to"], "dollars": M(o["dollars"])} for o in b["one_way"]],
                "divergence": {k: M(x) for k, x in sorted(b["divergence"].items())}}

    if name in DOCS:
        try:
            if name == "kinds":
                return [{"kind": k["kind"], "store": k["store"], "what": k["what"], "present": k["present"]} for k in DOC.kinds()]
            if name == "docs":
                _need(K, name, v, (1, 2), "(\"kind\" [, {field: value, …}])", pos)
                where = {str(k): (x if isinstance(x, str) else K.fmt_value(x)) for k, x in v[1].items()} if len(v) == 2 else None
                rows = [_doc_row(K, r) for r in DOC.docs(str(v[0]), where)]
                if not hasattr(it, "portal_realms"): it.portal_realms = {}
                it.portal_realms[str(v[0])] = rows                    # every family read becomes a portal realm
                return rows
            if name == "anchor_check":
                _need(K, name, v, (2, 3), "(\"BATES\", figure or text [, reach])", pos)
                what = K.fmt_value(v[1]).lstrip("$") if isinstance(v[1], K.Money) else K.fmt_value(v[1])
                r = DOC.anchor_check(str(v[0]), what, int(v[2].a) if len(v) == 3 else 3)
                return {"page": r["page"], "page_exists": r["page_exists"], "prints_it": r["prints_it"], "found_on": r["found_on"]}
            if name == "stages":
                _need(K, name, v, (1, 2), "(amount [, loan_file])", pos)
                amt = K.money_of(v[0], pos)
                r = DOC.stages(amt, str(v[1]) if len(v) == 2 else None)
                return {"amount": K.Money(r["amount"]), "reached": r["reached"], "skipped": r["skipped"],
                        "stages": {st: [_lang(K, h) for h in hs] for st, hs in r["stages"].items()}}
            _need(K, name, v, (2, 3, 4), "(items, claims [, group field, [parts]])", pos)
            def plain(rows):
                if not isinstance(rows, list): raise _err(K, "collections takes two lists of rows", pos)
                out = []
                for e in rows:
                    if not isinstance(e, dict): continue
                    out.append({k: (K.money_of(x, pos) if k == "amount" and x is not K.NONE else (None if x is K.NONE else
                                (x if isinstance(x, str) else K.fmt_value(x)))) for k, x in e.items()})
                return out
            parts = tuple(K.money_of(x, pos) for x in v[3]) if len(v) == 4 else (F(1),)
            r = DOC.collections(plain(v[0]), plain(v[1]), str(v[2]) if len(v) >= 3 else "draw_no", parts)
            return [{"item": x["item"], "amount": K.Money(x["amount"]), "party": x["party"], "page": x["page"], "groups": x["groups"],
                     "claims": [{"claim": c["claim"], "group": str(c["group"]), "part": K.R2(c["part"]), "page": c["page"]} for c in x["claims"]]}
                    for x in r]
        except DOC.DocsError as e:
            raise _err(K, str(e), pos)

    # portals
    if name == "portal_realm":
        _need(K, name, v, (2,), "(\"name\", rows or realm)", pos)
        if not hasattr(it, "portal_realms"): it.portal_realms = {}
        it.portal_realms[str(v[0])] = v[1]
        return R2(len(it.portal_realms))
    if name == "portal":
        _need(K, name, v, (1, 2), "(query [, {\"realm\": rows, …}])", pos)
        realms = {str(k): _realm_rows(K, x) for k, x in v[1].items()} if len(v) == 2 and isinstance(v[1], dict) \
            else gather_realms(it, K)
        if not realms: raise _err(K, "portal needs realms: load one, portal_realm(name, rows), or pass a map", pos)
        order = P.thompson_order(list(realms), seed=it.rng.random()) if getattr(it, "portals_on", False) else list(realms)
        po = P.open_portal(K.fmt_value(v[0]) if not isinstance(v[0], str) else v[0], realms, order)
        if getattr(it, "portals_on", False): P.log_portal(po, source="portal")
        it.last_portal = po
        return portal_value(it, K, po, pos)
    if name == "last_portal":
        po = getattr(it, "last_portal", None)
        return portal_value(it, K, po, pos) if po else K.NONE
    if name == "portal_stats":
        _need(K, name, v, (0, 1), "([log path])", pos)
        s = P.stats(str(v[0]) if v else None)
        return {"portals": R2(s["portals"]),
                "realms": {n: {"tries": R2(x["tries"]), "productive": R2(x["productive"]), "mean": R2(x["mean"])}
                           for n, x in sorted(s["realms"].items())},
                "one_sided": [{"anchor": f"{a['anchor'][0]}:{a['anchor'][1]}", "realm": a["realm"], "times": R2(a["times"])}
                              for a in s["one_sided"]]}
    _need(K, name, v, (1, 2), "([realm names] [, seed])", pos)
    return P.thompson_order([str(x) for x in v[0]], seed=int(v[1].a) if len(v) == 2 else None)
