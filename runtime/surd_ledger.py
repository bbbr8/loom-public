"""surd_ledger — many bank accounts as one ledger, exact.

Rows are dicts {account, day, amount (signed Fraction), memo, page, cls}. Functions:
  transfers(rows, days)      debit in one account matched to the same-amount credit in another within `days`
  consolidate(rows, pairs)   group balance with inter-account transfers eliminated; external in/out
  cycles(pairs)              money that leaves an account and comes back through other accounts
  float_days(pairs)          transfers whose credit lands before its debit leaves (counted in two places)
  carry(rows, pairs, doc)    class tracing that follows money through transfers: the debit's composition in the
                             source account becomes the class mix of the matching credit in the destination
Nothing rounds; pro-rata keeps fractional cents.
"""
from fractions import Fraction as F
import re

F0 = F(0)
HINT = re.compile(r"\b(TRANSFER|XFER|INTERNET\s+(FROM|TO)|ONLINE\s+(FROM|TO)|WIRE|FROM\s+CHECKING|TO\s+CHECKING|FROM\s+\*?\d{3,})\b", re.I)


ACCT_TOKEN = re.compile(r"(?:\bTO|\bFROM|\bFRM|\*)\s*(?:CHECKING|SAVINGS|ACCT|ACCOUNT)?\s*#?\s*\*?(\d{4,})\b", re.I)


def named_account(memo, accounts):
    """the counter-account a memo names ('XFER TO 1001', 'INTERNET FROM CHECKING 1002', 'XFER FROM *1003'), when it
    matches the tail of a known account number"""
    for m in ACCT_TOKEN.finditer(memo or ""):
        tok = m.group(1)
        hits = [a for a in accounts if a.endswith(tok[-4:])]
        if len(hits) == 1:
            return hits[0]
    return None


def transfers(rows, days=3, need_hint=False):
    """pair each debit with a same-amount credit in a DIFFERENT account, nearest in time within `days`.
    A memo hint (TRANSFER, XFER, INTERNET FROM, WIRE …) on either side is recorded; need_hint=True requires one."""
    idx = sorted(range(len(rows)), key=lambda i: (rows[i]["day"], i))
    credits = {}
    for i in idx:
        r = rows[i]
        if r["amount"] > 0:
            credits.setdefault(r["amount"], []).append(i)
    used, pairs = set(), []
    accounts = sorted({r["account"] for r in rows})
    for i in idx:
        r = rows[i]
        if r["amount"] >= 0:
            continue
        best = None
        named = named_account(r.get("memo"), accounts)           # a memo that names the other account binds the match
        for j in credits.get(-r["amount"], []):
            c = rows[j]
            if j in used or c["account"] == r["account"] or abs(c["day"] - r["day"]) > days:
                continue
            if named and c["account"] != named:
                continue
            back = named_account(c.get("memo"), accounts)
            if back and back != r["account"]:
                continue
            hint = bool(HINT.search(r.get("memo") or "") or HINT.search(c.get("memo") or ""))
            if need_hint and not hint:
                continue
            key = (abs(c["day"] - r["day"]), not hint, j)
            if best is None or key < best[0]:
                best = (key, j, hint)
        if best is not None:
            used.add(best[1])
            j = best[1]
            pairs.append({"out": i, "in": j, "from": r["account"], "to": rows[j]["account"], "amount": -r["amount"],
                          "out_day": r["day"], "in_day": rows[j]["day"], "lag": rows[j]["day"] - r["day"], "hint": best[2]})
    return pairs


def consolidate(rows, pairs):
    """per-account and group balances with transfers eliminated; what came from and went to the outside"""
    inner = {p["out"] for p in pairs} | {p["in"] for p in pairs}
    acct = {}
    ext_in = ext_out = F0
    for i, r in enumerate(rows):
        acct[r["account"]] = acct.get(r["account"], F0) + r["amount"]
        if i in inner:
            continue
        if r["amount"] > 0: ext_in += r["amount"]
        else: ext_out -= r["amount"]
    moved = sum((p["amount"] for p in pairs), F0)
    return {"accounts": acct, "group": sum(acct.values(), F0), "external_in": ext_in, "external_out": ext_out,
            "moved_between": moved, "transfers": len(pairs)}


def cycles(pairs, max_len=5, max_days=30):
    """account cycles A -> B -> … -> A through matched transfers in date order (each leg on or after the last),
    completed within `max_days` of the first leg; the bottleneck is the smallest leg"""
    legs = sorted(pairs, key=lambda p: (p["out_day"], p["out"]))
    found = []

    def walk(path, start):
        if len(found) > 200:
            return
        last = path[-1]
        for p in legs:
            if p in path or p["from"] != last["to"] or p["out_day"] < last["in_day"]:
                continue
            if p["in_day"] - path[0]["out_day"] > max_days:
                continue
            if p["to"] == start:
                cyc = path + [p]
                found.append({"accounts": [q["from"] for q in cyc] + [start], "legs": cyc,
                              "bottleneck": min(q["amount"] for q in cyc), "days": p["in_day"] - path[0]["out_day"]})
            elif len(path) + 1 < max_len and p["to"] not in [q["from"] for q in path]:
                walk(path + [p], start)

    for p in legs:
        walk([p], p["from"])
    return found


def float_days(pairs):
    """a transfer whose credit posts BEFORE its debit leaves is money counted in two accounts at once"""
    return [p for p in pairs if p["lag"] < 0]


def _order(rows, ids):
    return sorted(ids, key=lambda i: (rows[i]["day"], 0 if rows[i]["amount"] > 0 else 1, i))


def carry(rows, pairs, doctrine="fifo"):
    """multi-account tracing. Each account keeps lots (fifo/lifo) or a pool (prorata) by class; an overdraft is a
    debt repaid by the next deposits. A matched transfer credit takes the composition its debit carried out of the
    source account. Returns ({row index: composition}, {account: remaining pool by class})."""
    by_acct = {}
    for i, r in enumerate(rows):
        by_acct.setdefault(r["account"], []).append(i)
    in_of = {p["in"]: p["out"] for p in pairs}
    state = {a: {"lots": [], "pool": {}, "deficit": F0} for a in by_acct}
    comp = {}
    pending = _order(rows, range(len(rows)))
    done = set()
    guard = 0
    while pending and guard < 10 * len(rows) + 10:
        guard += 1
        nxt = []
        progressed = False
        for i in pending:
            r = rows[i]
            src = in_of.get(i)
            if src is not None and src not in done:
                nxt.append(i)
                continue
            # an account processes its rows in order: wait if an earlier row of the same account is still pending
            earlier = [k for k in nxt if rows[k]["account"] == r["account"]]
            if earlier:
                nxt.append(i)
                continue
            st = state[r["account"]]
            amt = r["amount"]
            if amt > 0:
                mix = dict(comp[src]) if src is not None else {r.get("cls") or "(unclassed)": amt}
                if src is not None and "(overdraft)" in mix:
                    od = mix.pop("(overdraft)")
                    mix["(overdraft of " + rows[src]["account"] + ")"] = od
                cover = min(st["deficit"], amt)
                st["deficit"] -= cover
                if cover:
                    k = cover / amt
                    mix = {c: x * (1 - k) for c, x in mix.items()}
                comp[i] = {c: x for c, x in mix.items() if x}
                for c, x in comp[i].items():
                    if doctrine == "prorata": st["pool"][c] = st["pool"].get(c, F0) + x
                    else: st["lots"].append([c, x])
            else:
                need, out = -amt, {}
                if doctrine == "prorata":
                    tot = sum(st["pool"].values(), F0)
                    take = min(tot, need)
                    if tot:
                        for c in list(st["pool"]):
                            share = st["pool"][c] * take / tot
                            st["pool"][c] -= share
                            if share: out[c] = out.get(c, F0) + share
                    need -= take
                else:
                    seq = st["lots"] if doctrine == "fifo" else list(reversed(st["lots"]))
                    for lot in seq:
                        if need <= 0: break
                        t = min(lot[1], need)
                        if t:
                            lot[1] -= t; need -= t
                            out[lot[0]] = out.get(lot[0], F0) + t
                    st["lots"][:] = [l for l in st["lots"] if l[1] > 0]
                if need > 0:
                    out["(overdraft)"] = need
                    st["deficit"] += need
                comp[i] = out
            done.add(i)
            progressed = True
        pending = nxt
        if not progressed:
            # a transfer credit that posts before its debit (float): seed it as its own class and continue
            i = pending.pop(0)
            rows_i = rows[i]
            comp[i] = {"(float from " + rows[in_of[i]]["account"] + ")": rows_i["amount"]}
            st = state[rows_i["account"]]
            for c, x in comp[i].items():
                if doctrine == "prorata": st["pool"][c] = st["pool"].get(c, F0) + x
                else: st["lots"].append([c, x])
            done.add(i)
    left = {}
    for a, st in state.items():
        if doctrine == "prorata":
            left[a] = {c: x for c, x in st["pool"].items() if x}
        else:
            agg = {}
            for c, x in st["lots"]: agg[c] = agg.get(c, F0) + x
            left[a] = agg
    return comp, left
