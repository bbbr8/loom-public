"""Document-page to bank-instrument binding (qulap.bind.v1).

A page that prints an amount can be the image or record of any bank instrument with that exact amount. The page's
own evidence narrows which: the dates it prints (a written date or a bank stamp near the instrument's dates) and the
cheque numbers it prints. Each page becomes a one-variable QULAP model whose alternatives are the candidate
instruments; the evidence becomes sourced constraints; every candidate becomes a proposition. The solver then says,
per page:

  BOUND           exactly one candidate survives (FORCED_IN_MODEL)
  AMBIGUOUS       two or more survive: the page alone cannot say which instrument it shows
  CONFLICT        the page's evidence excludes every candidate (INCONSISTENT_MODEL); `core` names the clashing clues.
                  On an invoice, conflict_kind PAID_BEFORE_PRINTED means every same-amount payment left before any
                  date the page prints (a page billing what was already paid, or a different payment that only
                  shares the amount); PAID_AFTER_LAG means none was paid within the lag.
  UNCONSTRAINED   the page prints no usable date or cheque number: every candidate stays possible
  NO_INSTRUMENT   no instrument carries the amount

Input: {"schema": "qulap.bind.v1", "source_tier": ..., "window_days": 3, "invoice_lag_days": 180,
        "instruments": [{"id", "amount_cents", "posted": "YYYY-MM-DD", "written": [..], "check_no", "parties": [..], "locator", "lineage_id"}],
        "pages": [{"id", "amount_cents", "text", "kind": "instrument" | "invoice", "direction": "out" | "in" (optional),
                   "locator", "lineage_id"}]}
Instrument amounts are signed (deposits positive); a page's `direction` keeps only candidates moving that way.
An instrument image prints dates near the instrument's written/posted dates and its serial bare; an invoice prints a
date on or before payment and counts a cheque number only after a cheque cue.
Exact integer cents. Dates are read from the page text in ISO, M/D/YYYY, M/D/YY, M-D-YY and 'Month D, YYYY' forms.
A clue is a constraint the investigator can inspect; the binding is a model result (proof_effect NONE).
"""
import datetime as dt
import re

from .model import ModelError, contradiction_core, solve

MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
DATE_RX = [
    (re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2})\b(?![/-]?\d)"), lambda m: (2000 + int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(20\d\d)\b", re.I),
     lambda m: (int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
]


def page_dates(text):
    out = set()
    for rx, f in DATE_RX:
        for m in rx.finditer(text or ""):
            try:
                y, mo, d = f(m)
                if 2000 <= y <= 2035:
                    out.add(dt.date(y, mo, d))
            except ValueError:
                pass
    return out


def page_check_numbers(text, candidates, bare=True):
    """cheque numbers the page prints. On an instrument image the serial stands bare; on an invoice a bare number is
    usually an invoice or line number, so it counts only after a cheque cue (#, ck, check, chk, no.)."""
    nums = set()
    for c in candidates:
        ck = str(c.get("check_no") or "").lstrip("0")
        if len(ck) < 2:
            continue
        cue = r"(?<![\d.,])" if bare else r"(?:#|\bck\.?\s*#?|\bche(?:ck|que)\s*(?:no\.?|#)?\s*|\bchk\.?\s*#?|\bno\.\s*)"
        if re.search(rf"{cue}{re.escape(ck)}(?![\d.,])", text or "", re.I):
            nums.add(ck)
    return nums


def _day(s):
    return dt.date.fromisoformat(str(s)[:10])


def bind_page(page, candidates, window_days=3, max_nodes=100000, seconds=5.0, lag_days=180):
    if not candidates:
        return {"page": page["id"], "amount_cents": page["amount_cents"], "verdict": "NO_INSTRUMENT", "candidates": [], "possible": [], "clues": []}
    text = page.get("text") or ""
    dates = page_dates(text)
    kind = page.get("kind", "instrument")              # instrument image | invoice (dated before payment)
    nums = page_check_numbers(text, candidates, bare=(kind == "instrument"))
    src = {"id": "PAGE", "locator": page.get("locator", page["id"]), "lineage_id": page.get("lineage_id", page["id"])}
    idx = list(range(len(candidates)))
    constraints, clues = [], []
    if nums:
        ok = [i for i in idx if str(candidates[i].get("check_no") or "").lstrip("0") in nums]
        constraints.append({"id": "cheque_number", "expr": " or ".join(f"inst == {i}" for i in ok) if ok else "inst == -1",
                            "source_ids": ["PAGE"]})
        clues.append({"clue": "cheque_number", "printed": sorted(nums), "allows": [candidates[i]["id"] for i in ok]})
    if dates:
        w = dt.timedelta(days=window_days)
        lag = dt.timedelta(days=lag_days)
        def near(c):
            ds = [_day(c["posted"])] + [_day(x) for x in c.get("written") or []]
            lo, hi = min(ds), max(ds)
            if kind == "invoice":            # paid on or after a date the invoice prints, within the declared lag
                return any(d - w <= hi and lo <= d + lag for d in dates)
            return any(lo - w <= d <= hi + w for d in dates)
        ok = [i for i in idx if near(candidates[i])]
        constraints.append({"id": "printed_dates", "expr": " or ".join(f"inst == {i}" for i in ok) if ok else "inst == -1",
                            "source_ids": ["PAGE"]})
        clues.append({"clue": "printed_dates", "rule": "within window" if kind == "instrument" else f"paid 0..{lag_days} days after",
                      "printed": sorted(d.isoformat() for d in dates),
                      "allows": [candidates[i]["id"] for i in ok]})
    named = [i for i in idx for party in candidates[i].get("parties") or []
             if len(party) >= 5 and party.lower() in text.lower()]
    if named:                                            # the page names a payee that only some candidates carry
        constraints.append({"id": "payee", "expr": " or ".join(f"inst == {i}" for i in sorted(set(named))),
                            "source_ids": ["PAGE"]})
        clues.append({"clue": "payee", "printed": sorted({p_ for i in named for p_ in candidates[i]["parties"]
                                                          if p_.lower() in text.lower()}),
                      "allows": [candidates[i]["id"] for i in sorted(set(named))]})
    model = {"schema": "qulap.model.v1", "source_tier": "derived",
             "question": f"Which bank instrument does {page['id']} show?",
             "variables": [{"id": "inst", "domain": idx, "unit": "integer", "source_ids": ["PAGE"]}],
             "constraints": constraints, "metrics": [],
             "propositions": [{"id": f"is_{i}", "expr": f"inst == {i}", "category": "accounting"} for i in idx],
             "sources": [src]}
    r = solve(model, max_nodes=max_nodes, seconds=seconds, retain_worlds=len(idx))
    status = {i: r["propositions"][f"is_{i}"]["status"] for i in idx}
    possible = [i for i in idx if status[i] in ("FORCED_IN_MODEL", "POSSIBLE_IN_MODEL")]
    if r["state"] == "INCONSISTENT_MODEL":
        verdict = "CONFLICT"
    elif not constraints:
        verdict = "UNCONSTRAINED"
    elif len(possible) == 1:
        verdict = "BOUND"
    else:
        verdict = "AMBIGUOUS"
    out = {"page": page["id"], "locator": src["locator"], "amount_cents": page["amount_cents"], "verdict": verdict,
           "bound_to": candidates[possible[0]]["id"] if verdict == "BOUND" else None,
           "possible": [candidates[i]["id"] for i in possible], "candidates": [c["id"] for c in candidates],
           "clues": clues, "candidate_dates": {c["id"]: [c["posted"]] + list(c.get("written") or []) for c in candidates},
           "model_state": r["state"], "model_sha256": r["model_sha256"]}
    if verdict == "CONFLICT" and kind == "invoice" and dates:
        cds = [_day(d) for c in candidates for d in [c["posted"]] + list(c.get("written") or [])]
        if cds and max(cds) < min(dates) - dt.timedelta(days=window_days):
            out["conflict_kind"] = "PAID_BEFORE_PRINTED"     # every same-amount payment left before the page's dates
        elif cds and min(cds) > max(dates) + dt.timedelta(days=lag_days):
            out["conflict_kind"] = "PAID_AFTER_LAG"
        else:
            out["conflict_kind"] = "MIXED"
    if verdict == "CONFLICT":
        out["core"] = (contradiction_core(model, max_nodes, seconds).get("constraints") if len(constraints) > 1
                       else [constraints[0]["id"]])
    return out


def bind(packet, max_nodes=100000, seconds=5.0):
    if not isinstance(packet, dict) or packet.get("schema") != "qulap.bind.v1":
        raise ModelError("expected schema qulap.bind.v1")
    if packet.get("source_tier") not in ("synthetic", "derived", "native_review_annotations"):
        raise ModelError("declare source_tier: synthetic, derived, or native_review_annotations")
    window = int(packet.get("window_days", 3))
    lag = int(packet.get("invoice_lag_days", 180))
    if not 0 <= window <= 31:
        raise ModelError("window_days 0..31")
    by_amount = {}
    for c in packet.get("instruments", []):
        if type(c.get("amount_cents")) is not int or not c.get("id") or not c.get("posted"):
            raise ModelError("each instrument needs id, integer amount_cents and posted date")
        _day(c["posted"])
        by_amount.setdefault(abs(c["amount_cents"]), []).append(c)
    results = []
    for p in packet.get("pages", []):
        if type(p.get("amount_cents")) is not int or not p.get("id"):
            raise ModelError("each page needs id and integer amount_cents")
        cands = sorted(by_amount.get(abs(p["amount_cents"]), []), key=lambda c: (c["posted"], c["id"]))
        if p.get("direction") in ("out", "in"):            # an invoice is paid by money going out
            want = -1 if p["direction"] == "out" else 1
            cands = [c for c in cands if (c["amount_cents"] > 0) == (want > 0)]
        if len(cands) > 64:
            raise ModelError("more than 64 candidate instruments for one amount")
        results.append(bind_page(p, cands, window, max_nodes, seconds, lag))
    counts = {}
    for x in results:
        counts[x["verdict"]] = counts.get(x["verdict"], 0) + 1
    return {"schema": "qulap.bind-result.v1", "engine": "QULAP page binding", "source_tier": packet["source_tier"],
            "proof_effect": "NONE", "window_days": window, "counts": counts, "pages": results,
            "boundary": "A binding is relative to the declared instruments and the page's printed dates and cheque "
                        "numbers. Open the page image before relying on a binding."}
