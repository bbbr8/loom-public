"""surd_bank — native bank statements for Surd: load, foot, pair returned items, reconcile two versions.

Pure functions over exact Fractions and day ordinals; surd.py wraps them as language functions
(statement, foot, returns, reconcile). Nothing here rounds: a month ties or it does not.
"""
import csv
import datetime as _dt
import re
from fractions import Fraction as F

F0 = F(0)
# a returned / reversed deposit item; word boundaries so "TRANSFER" never reads as NSF
RETURN_RE = re.compile(r"\b(RTRN|RETURN(ED)?\s+(ITEM|CK|CHECK|DEPOSIT)|NSF|REVERSAL\s+OF\s+DEPOSIT|DEPOSIT\s+REVERS\w*)\b", re.I)


class BankError(Exception):
    pass


def _day(text):
    try:
        return _dt.date.fromisoformat(str(text)[:10]).toordinal()
    except ValueError:
        raise BankError(f"{text!r} is not a date (YYYY-MM-DD)")


def _money(text):
    t = str(text).replace("$", "").replace(",", "").strip()
    if t in ("", "None"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    try:
        x = F(t.strip("()"))
    except (ValueError, ZeroDivisionError):
        raise BankError(f"{text!r} is not money")
    return -x if neg else x


def load_statement(path):
    """rows of a statement CSV -> dicts {day, amount (signed F), memo, check, type, month, page, begin, end}.
    Accepts the generic CSV native schema (amount + direction) or a signed amount column."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not r.get("date") or r.get("date") == "date":
                continue
            amt = _money(r.get("amount"))
            if amt is None:
                continue
            d = (r.get("direction") or "").strip().lower()
            if d == "debit":
                amt = -abs(amt)
            elif d == "credit":
                amt = abs(amt)
            out.append({"day": _day(r["date"]), "amount": amt,
                        "memo": (r.get("description") or r.get("memo") or "").strip(),
                        "check": (r.get("check_number") or r.get("check") or "").strip(),
                        "type": (r.get("type") or "").strip(),
                        "month": (r.get("statement_month") or r.get("month") or "").strip(),
                        "page": (r.get("source_page") or r.get("page") or "").strip(),
                        "begin": _money(r.get("beginning_balance_for_month") or r.get("begin") or ""),
                        "end": _money(r.get("ending_balance_for_month") or r.get("end") or "")})
    return out


def foot(rows):
    """per statement month: beginning + credits - debits against the printed ending balance"""
    months = {}
    for r in rows:
        m = r.get("month")
        if not m:
            raise BankError("foot needs a month on every row (statement_month)")
        g = months.setdefault(m, {"month": m, "begin": r.get("begin"), "end": r.get("end"),
                                  "credits": F0, "debits": F0, "rows": 0, "pages": set()})
        if r.get("begin") is not None and g["begin"] is None: g["begin"] = r["begin"]
        if r.get("end") is not None and g["end"] is None: g["end"] = r["end"]
        if r["amount"] > 0: g["credits"] += r["amount"]
        else: g["debits"] -= r["amount"]
        g["rows"] += 1
        if r.get("page"): g["pages"].add(r["page"])
    out = []
    for m in sorted(months):
        g = months[m]
        if g["begin"] is None or g["end"] is None:
            raise BankError(f"month {m} has no printed beginning/ending balance")
        computed = g["begin"] + g["credits"] - g["debits"]
        out.append({"month": m, "begin": g["begin"], "credits": g["credits"], "debits": g["debits"],
                    "computed_end": computed, "statement_end": g["end"], "gap": computed - g["end"],
                    "ties": computed == g["end"], "rows": g["rows"], "pages": sorted(g["pages"])})
    return out


def is_return(memo, typ=""):
    return (typ or "").lower() == "returned" or bool(RETURN_RE.search(memo or ""))


def pair_returns(evs, memos, types, window=45):
    """evs: [(i, day, amount, cls, page)]. Each returned item is paired with the latest earlier credit of the same
    amount (within `window` days) not already paired. Returns [(deposit_i, return_i)] and unpaired return ids."""
    by_i = {e[0]: e for e in evs}
    used, pairs, orphans = set(), [], []
    for e in sorted(evs, key=lambda e: (e[1], e[0])):
        i, day, amt = e[0], e[1], e[2]
        if amt >= 0 or not is_return(memos.get(i, ""), types.get(i, "")):
            continue
        cands = [c for c in evs if c[2] == -amt and c[0] not in used and c[0] != i
                 and 0 <= day - c[1] <= window and c[2] > 0]
        if not cands:
            orphans.append(i); continue
        dep = max(cands, key=lambda c: (c[1], c[0]))
        used.add(dep[0]); pairs.append((dep[0], i))
    return pairs, orphans


def reconcile(a, b, days=5):
    """a, b: [(day, amount, memo, page)] for the same account from two sources.
    Exact multiset match on (day, amount); leftovers explained as netted deposit+return pairs or date shifts."""
    from collections import Counter
    ca = Counter((d, x) for d, x, *_ in a)
    cb = Counter((d, x) for d, x, *_ in b)
    matched = sum((ca & cb).values())
    only_a = sorted((ca - cb).elements())
    only_b = sorted((cb - ca).elements())
    rows_a = {}
    for d, x, memo, pg in a: rows_a.setdefault((d, x), []).append((memo, pg))
    rows_b = {}
    for d, x, memo, pg in b: rows_b.setdefault((d, x), []).append((memo, pg))

    def info(rows, key):
        lst = rows.get(key) or [("", "")]
        return lst[0]

    netted, shifted = [], []
    la, lb = list(only_a), list(only_b)

    def net_pairs(side, rows, label):
        res = []
        for (d1, x1) in [k for k in side if k[1] > 0]:
            for (d2, x2) in [k for k in side if k[1] < 0]:
                if x2 == -x1 and 0 <= d2 - d1 <= 45 and (d1, x1) in side and (d2, x2) in side:
                    side.remove((d1, x1)); side.remove((d2, x2))
                    res.append({"in": label, "deposit_day": d1, "return_day": d2, "amount": x1,
                                "deposit": info(rows, (d1, x1)), "return": info(rows, (d2, x2))})
                    break
        return res

    netted += net_pairs(la, rows_a, "a")
    netted += net_pairs(lb, rows_b, "b")
    for k in list(la):
        best = None
        for k2 in lb:
            if k2[1] == k[1] and abs(k2[0] - k[0]) <= days and (best is None or abs(k2[0] - k[0]) < abs(best[0] - k[0])):
                best = k2
        if best is not None:
            la.remove(k); lb.remove(best)
            shifted.append({"amount": k[1], "a_day": k[0], "b_day": best[0], "shift": best[0] - k[0],
                            "a": info(rows_a, k), "b": info(rows_b, best)})
    # one row on one side = several same-day rows on the other (a deposit split or merged); zero-amount rows
    split, zero = [], []
    import itertools as _it
    for side, other, label in ((la, lb, "a"), (lb, la, "b")):
        for k in [k for k in side if k[1] == 0]:
            side.remove(k); zero.append({"in": label, "day": k[0], "row": info(rows_a if label == "a" else rows_b, k)})
        for k in sorted(side, key=lambda k: -abs(k[1])):
            if k not in side: continue
            same = [k2 for k2 in other if k2[0] == k[0] and (k2[1] > 0) == (k[1] > 0)]
            hit = None
            for r in range(2, min(5, len(same)) + 1):
                for combo in _it.combinations(same, r):
                    if sum(c[1] for c in combo) == k[1]: hit = combo; break
                if hit: break
            if hit:
                side.remove(k)
                for c in hit: other.remove(c)
                split.append({"whole_in": label, "day": k[0], "amount": k[1], "parts": [c[1] for c in hit]})
    # running balance divergence, day by day
    da, db = {}, {}
    for d, x, *_ in a: da[d] = da.get(d, F0) + x
    for d, x, *_ in b: db[d] = db.get(d, F0) + x
    ba = bb = F0
    first = worst_day = None
    worst = F0
    divergent = 0
    for d in sorted(set(da) | set(db)):
        ba += da.get(d, F0); bb += db.get(d, F0)
        gap = ba - bb
        if gap:
            divergent += 1
            if first is None: first = d
            if abs(gap) > abs(worst): worst, worst_day = gap, d
    return {"matched": matched, "only_a": la, "only_b": lb, "netted": netted, "shifted": shifted, "split": split, "zero": zero,
            "first_divergence": first, "max_divergence": worst, "max_divergence_on": worst_day,
            "divergent_days": divergent, "final_gap": ba - bb,
            "explained": not la and not lb}


NOTICE_RE = re.compile(r"(\d+)\s+Overdraft\s+Items?\s+on\s+(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)


def notices(rows):
    """the bank's own overdraft notices: [(row index, items, notice day, posted day)]"""
    out = []
    for i, r in enumerate(rows):
        m = NOTICE_RE.search(r.get("memo") or "")
        if not m:
            continue
        y = int(m.group(4)); y = y + 2000 if y < 100 else y
        out.append((i, int(m.group(1)), _dt.date(y, int(m.group(2)), int(m.group(3))).toordinal(), r["day"]))
    return out


def replay_dips(rows, flip=frozenset(), default="deposits_first", skip=frozenset()):
    """intraday replay; each day posts deposits first (or withdrawals first on days in `flip`); rows in `skip`
    (the notices and the fee they carry) move the balance but are not counted as items.
    Returns {day: (lowest balance that day, items that posted below zero)}"""
    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r["day"], []).append((i, r))
    bal, dips = F0, {}
    for d in sorted(days):
        ins = [x for x in days[d] if x[1]["amount"] > 0]
        outs = [x for x in days[d] if x[1]["amount"] < 0]
        dep_first = (default == "deposits_first") != (d in flip)
        low, items = None, 0
        for i, r in (ins + outs if dep_first else outs + ins):
            bal += r["amount"]
            if bal < 0:
                low = bal if low is None else min(low, bal)
                if r["amount"] < 0 and i not in skip: items += 1
        if low is not None:
            dips[d] = (low, items)
    return dips


def calibrate(rows):
    """which same-day posting order the bank's own overdraft notices confirm. A notice 'n Overdraft Items on D',
    printed on posting day P, is explained when the replay dips below zero on a day in [D, P] with n items.
    A dip on no notice day is an overdraft the bank never noticed (invented by that order)."""
    nts = notices(rows)
    skip = frozenset(i for i, *_ in nts)                   # the notice rows (and the fee they carry) are not items

    def judge(dips):
        explained, missed = [], []
        covered = set()
        for i, n, d, p in nts:
            hits = [x for x in dips if d <= x <= p]
            if hits:
                covered.update(hits)
                items = sum(dips[x][1] for x in hits)
                explained.append({"notice": rows[i]["memo"], "page": rows[i]["page"], "items": n, "replayed_items": items,
                                  "items_match": items == n, "day": min(hits), "low": min(dips[x][0] for x in hits)})
            else:
                missed.append({"notice": rows[i]["memo"], "page": rows[i]["page"], "items": n})
        # a day that only carries yesterday's overdraft (no item posted below zero) is not an overdraft event
        invented = [{"day": x, "low": dips[x][0], "items": dips[x][1]} for x in sorted(dips) if x not in covered and dips[x][1]]
        return {"explained": explained, "missed": missed, "invented": invented,
                "consistent": not missed and not invented and all(e["items_match"] for e in explained)}

    out = {"notices": len(nts)}
    for order in ("deposits_first", "withdrawals_first"):
        flip = frozenset() if order == "deposits_first" else frozenset(r["day"] for r in rows)
        out[order] = judge(replay_dips(rows, flip, skip=skip))
    # smallest set of withdrawals-first days that explains every notice and invents nothing
    flip = set()
    for i, n, d, p in nts:
        if judge(replay_dips(rows, frozenset(flip), skip=skip))["consistent"]:
            break
        for x in range(d, p + 1):
            trial = judge(replay_dips(rows, frozenset(flip | {x}), skip=skip))
            if len(trial["missed"]) < len(judge(replay_dips(rows, frozenset(flip), skip=skip))["missed"]) and not trial["invented"]:
                flip.add(x); break
    final = judge(replay_dips(rows, frozenset(flip), skip=skip))
    out["calibrated"] = {"default": "deposits_first", "withdrawals_first_on": sorted(flip), **final}
    return out
