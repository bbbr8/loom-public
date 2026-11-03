"""surd_observe — observers, randomized perspectives, pooled knowledge, knowledge clocks, and a language auditor.

Facts are dicts {id, day, holders: [names], quantity, value, text, page}. An observer sees a fact once a holder
list names them and the fact's day has come. Everything here is counting over stated records: the perspective
randomizer samples (observer, as-of day, lens) with a seeded generator and reports exact fractions; the language
auditor reads surface features of a message and ranks communication functions by those features. Neither one
reads minds; each result names the words and records it rests on.
"""
import random
import re
from fractions import Fraction as F

F0 = F(0)


def visible(facts, observer, day):
    return [f for f in facts if f["day"] <= day and observer in (f.get("holders") or [])]


def view(facts, observer, day):
    """quantity -> set of values the observer's records show by `day`"""
    out = {}
    for f in visible(facts, observer, day):
        if f.get("quantity") is not None:
            out.setdefault(f["quantity"], set()).add(f.get("value"))
    return out


def belnap_of(values, claim_value):
    """T: only claim_value witnessed; F: only other values; B: both; N: none"""
    w = claim_value in values
    c = any(v != claim_value for v in values)
    return {(True, False): "T", (False, True): "F", (True, True): "B", (False, False): "N"}[(w, c)]


def perspectives(facts, observers, quantity, claim_value, days, n=200, seed=1, lenses=None):
    """seeded random draws of (observer, as-of day[, lens]); each draw judges `quantity == claim_value` from that
    observer's records alone (Belnap). Returns per-observer and overall exact distributions plus the draws."""
    rng = random.Random(seed)
    lenses = lenses or [None]
    draws, per, overall = [], {}, {}
    for _ in range(n):
        o = rng.choice(observers); d = rng.choice(days); lens = rng.choice(lenses)
        fs = visible(facts, o, d)
        if lens is not None:
            fs = [f for f in fs if lens in (f.get("lenses") or [lens])]
        vals = {f.get("value") for f in fs if f.get("quantity") == quantity}
        b = belnap_of(vals, claim_value)
        draws.append({"observer": o, "day": d, "lens": lens, "verdict": b})
        per.setdefault(o, {}).setdefault(b, 0)
        per[o][b] += 1
        overall[b] = overall.get(b, 0) + 1
    dist = {o: {k: F(c, sum(v.values())) for k, c in v.items()} for o, v in per.items()}
    return {"draws": draws, "per_observer": dist, "overall": {k: F(c, n) for k, c in overall.items()}, "n": n, "seed": seed}


def every_perspective(facts, observers, quantity, claim_value, days):
    """the exhaustive version: every observer at every day"""
    out = []
    for o in observers:
        for d in days:
            vals = {f.get("value") for f in visible(facts, o, d) if f.get("quantity") == quantity}
            out.append({"observer": o, "day": d, "verdict": belnap_of(vals, claim_value), "values": sorted(map(str, vals))})
    return out


def pooled(facts, group, quantity, claim_value, day):
    """distributed knowledge D_G: each member alone vs the group's pooled records. A contradiction that appears only
    when the group pools (every member T or N alone, pooled B) is the D_G ⊥ case."""
    alone = {}
    for o in group:
        vals = {f.get("value") for f in visible(facts, o, day) if f.get("quantity") == quantity}
        alone[o] = belnap_of(vals, claim_value)
    pool_vals = set()
    for o in group:
        pool_vals |= {f.get("value") for f in visible(facts, o, day) if f.get("quantity") == quantity}
    together = belnap_of(pool_vals, claim_value)
    return {"alone": alone, "pooled": together,
            "only_when_pooled": together == "B" and all(v != "B" for v in alone.values()),
            "values": sorted(map(str, pool_vals))}


def knew(facts, quantity=None, value=None, text=None):
    """knowledge clock: for each holder, the earliest day a record put the fact in their hands"""
    out = {}
    for f in sorted(facts, key=lambda f: f["day"]):
        if quantity is not None and f.get("quantity") != quantity: continue
        if value is not None and f.get("value") != value: continue
        if text is not None and text.lower() not in (f.get("text") or "").lower(): continue
        for h in f.get("holders") or []:
            if h not in out:
                out[h] = {"day": f["day"], "fact": f.get("id"), "page": f.get("page")}
    return out


# ───────────────────────── language auditor ─────────────────────────
LEX = {
    "hedge": r"\b(i think|i believe|maybe|probably|possibly|should be|about|around|roughly|approximately|kind of|sort of|i guess|might)\b",
    "agentless": r"\b(was|were|has been|have been|had been|is being|got|will be)\s+(paid|applied|taken care of|covered|handled|sent|moved|used|spent|transferred|credited|reimbursed|returned|deposited|approved|signed|done)\b",
    "promise": r"\b(will (come back|be covered|be paid|be reimbursed|get paid|take care|be returned|be fine)|i('ll| will) (pay|cover|send|get|take care|make sure)|come back in the draw|promise)\b",
    "reassure": r"\b(don'?t worry|no worries|no problem|it'?s fine|all good|taken care of|we'?re good|not a big deal|nothing to worry)\b",
    "urgency": r"\b(asap|today|right away|immediately|urgent|now|tonight|need (it|this|you|the money)|by (tomorrow|friday|monday))\b",
    "request": r"(\?|\b(can you|could you|please|need you to|send me|wire|deposit)\b)",
    "vague": r"\b(stuff|things|some|a few|various|whatever|etc|and so on|the rest)\b",
    "deflect": r"\b(not my|ask (the manager|the contractor|the bank|the lender)|that'?s on|i don'?t (know|remember|recall)|no idea|wasn'?t me)\b",
    "builder_money": r"\b(paid|loan(ed)?|loaned yourself|reimburs\w*|profit|commission|fee)\b",
    "owner_money": r"\b(advanc\w*|fronted|front(ed)? money|owner advance|out of pocket|my money|your money)\b",
    # Linguistic features: who is said to have chosen, what is said to have been known, cost said to rise
    "assign_choice": r"\b(the owner|owner|you|he|she|reader|they|the buyer|the client)\s+(had\s+|have\s+|has\s+)?(decided|chose|chosen|wanted|elected|opted|asked for|requested|insisted)\b",
    "shared_knowledge": r"\b(we all knew|we both knew|everyone knew|you knew|you were aware|as you know|you understood|you agreed)\b",
    "cost_rise": r"(\b(came|come|coming) in higher\b|\$[\d,]+(\.\d\d)? higher\b|\bhigher than\b|\bmaxed out\b|\bover (the )?budget\b|\bcost(s)? more\b|\bwent up\b)",
}
LEXC = {k: re.compile(v, re.I) for k, v in LEX.items()}
NUM = re.compile(r"\$?\d[\d,]*(\.\d{2})?")

FUNCTIONS = {
    # function: features that carry it (weight 1 each); specificity carries "inform"
    "inform": ["specific"],
    "request": ["request", "urgency"],
    "reassure": ["reassure", "promise"],
    "persuade": ["promise", "urgency", "reassure"],
    "deflect": ["deflect", "agentless", "vague"],
    "hedge_cover": ["hedge", "vague", "agentless"],
    "assign_responsibility": ["assign_choice", "shared_knowledge"],
    "justify_cost": ["cost_rise"],
}


def audit_message(text, speaker=None, audience=None, day=None, facts=None, quantity=None, said_value=None):
    """surface features of one message, the communication functions they carry (ranked, exact shares), the
    money frame, and — when facts are given — what the speaker's own records showed on that day"""
    t = text or ""
    feats = {}
    for k, rx in LEXC.items():
        hits = [m.group(0) for m in rx.finditer(t)]
        if hits: feats[k] = hits
    nums = [m.group(0) for m in NUM.finditer(t) if any(ch.isdigit() for ch in m.group(0))]
    if len(nums) >= 1: feats["specific"] = nums
    score = {}
    for fn, keys in FUNCTIONS.items():
        s = sum(len(feats.get(k, [])) for k in keys)
        if s: score[fn] = s
    tot = sum(score.values())
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    frame = "builder" if len(feats.get("builder_money", [])) > len(feats.get("owner_money", [])) else \
            ("owner" if feats.get("owner_money") else None)
    out = {"speaker": speaker, "audience": audience, "day": day, "features": feats, "frame": frame,
           "functions": [{"function": k, "weight": F(v, tot)} for k, v in ranked],
           "words": len(re.findall(r"\w+", t))}
    if facts is not None and speaker is not None and day is not None:
        held = visible(facts, speaker, day)
        out["held_records"] = [f.get("id") for f in held]
        if quantity is not None and said_value is not None:
            vals = {f.get("value") for f in held if f.get("quantity") == quantity}
            out["said_vs_held"] = belnap_of(vals, said_value)
            out["said_against_own_records"] = bool(vals) and said_value not in vals
    return out


def audit_thread(msgs):
    """msgs: [{speaker, audience, day, text}]. Per speaker: features by audience, and the words a speaker uses with
    one audience and never with another (audience split)."""
    by = {}
    for m in msgs:
        a = audit_message(m.get("text"), m.get("speaker"), m.get("audience"), m.get("day"))
        s = by.setdefault(m.get("speaker"), {})
        aud = s.setdefault(m.get("audience"), {"messages": 0, "features": {}, "words": set()})
        aud["messages"] += 1
        for k, v in a["features"].items():
            aud["features"][k] = aud["features"].get(k, 0) + len(v)
        aud["words"] |= {w.lower() for w in re.findall(r"[A-Za-z']+", m.get("text") or "") if len(w) > 3}
    split = []
    for sp, auds in by.items():
        names = list(auds)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                fa, fb = auds[a]["features"], auds[b]["features"]
                diff = {k: (fa.get(k, 0), fb.get(k, 0)) for k in set(fa) | set(fb) if fa.get(k, 0) != fb.get(k, 0)}
                if diff:
                    rates = {k: (F(fa.get(k, 0), auds[a]["messages"]), F(fb.get(k, 0), auds[b]["messages"])) for k in diff}
                    split.append({"speaker": sp, "a": a, "b": b, "feature_counts": diff, "per_message": rates,
                                  "messages": (auds[a]["messages"], auds[b]["messages"]),
                                  "only_to_a": sorted(auds[a]["words"] - auds[b]["words"])[:25],
                                  "only_to_b": sorted(auds[b]["words"] - auds[a]["words"])[:25]})
    summary = {sp: {aud: {"messages": v["messages"], "features": v["features"],
                          "per_message": {k: F(c, v["messages"]) for k, c in v["features"].items()}}
                    for aud, v in auds.items()} for sp, auds in by.items()}
    return {"by_speaker": summary, "audience_split": split}
