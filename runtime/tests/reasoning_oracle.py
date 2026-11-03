"""Seeded DAG contract oracle, using a separate recursive set-based evaluator.

Synthetic mechanics only. This tests the documented policy, not whether prose
entails a claim or whether a declared inference is sound in the real world.
"""
import argparse
from itertools import product
import json
from pathlib import Path
import platform
import random
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd_search as s


def fixture(rng, n):
    docs = [{"id": f"d{i}", "text": f"Fixture record {i}.", "locator": f"fixture:{i}"} for i in range(8)]
    claims, rules = [], []
    for i in range(n):
        c = {"id": f"c{i}", "text": f"Synthetic proposition {i}."}
        state = rng.randrange(5)
        for stance, active in [("support", state in (1, 3)), ("oppose", state in (2, 3))]:
            if active:
                d = rng.choice(docs)
                c[stance] = [{"document": d["id"], "quote": d["text"]}]
        if state == 4:
            c["assumption"] = True
        claims.append(c)
        if i:
            for j in range(rng.randrange(3)):
                rules.append({"id": f"r{i}_{j}", "if": [f"c{x}" for x in rng.sample(range(i), min(i, rng.randrange(1, 4)))],
                              "unless": [f"c{x}" for x in rng.sample(range(i), min(i, rng.randrange(3)))],
                              "then": f"c{i}", "stance": rng.choice(["support", "oppose"])})
    rng.shuffle(claims); rng.shuffle(rules)
    return docs, {"claims": claims, "rules": rules, "goal": f"c{n-1}"}


def reference(spec, remove_document=None, remove_assumption=None, remove_rule=None):
    claims = {c["id"]: c for c in spec["claims"]}
    cache = {}
    def choose(paths):
        return min(paths, key=lambda xs: (len(xs), sorted(xs)))
    def visit(cid):
        if cid in cache:
            return cache[cid]
        c = claims[cid]
        pro = [frozenset() for e in c.get("support", []) if e["document"] != remove_document]
        con = [frozenset() for e in c.get("oppose", []) if e["document"] != remove_document]
        if c.get("assumption") and cid != remove_assumption:
            pro.append(frozenset([cid]))
        for rule in spec["rules"]:
            if rule["then"] != cid or rule["id"] == remove_rule:
                continue
            requirements = [(visit(p), True) for p in rule["if"]] + [(visit(p), False) for p in rule.get("unless", [])]
            if any(not (a if positive else b) or (b if positive else a) for (a, b), positive in requirements):
                continue
            # Enumerate all proof combinations, including supersets. This oracle
            # deliberately does not use the engine's incremental pruning algorithm.
            choices = [a if positive else b for (a, b), positive in requirements]
            used = {frozenset().union(*parts) for parts in product(*choices)}
            (pro if rule.get("stance", "support") == "support" else con).extend(used)
        pro, con = list(set(pro)), list(set(con))
        cache[cid] = (pro, con)
        return pro, con
    statuses = {}
    for cid in claims:
        yes, no = visit(cid)
        statuses[cid] = "contested" if yes and no else "supported" if yes else "opposed" if no else "unresolved"
    yes, no = visit(spec["goal"])
    status = statuses[spec["goal"]]
    assumptions = sorted(choose(yes if status == "supported" else no)) if status in ("supported", "opposed") else []
    paths = set(yes if status == "supported" else no) if status in ("supported", "opposed") else set()
    minimal = [sorted(path) for path in paths if not any(other < path for other in paths)]
    minimal.sort(key=lambda xs: (len(xs), xs))
    return statuses, "conditional" if status == "supported" and assumptions else status, assumptions, minimal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=2000)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 1 <= args.cases <= 10000:
        parser.error("--cases must be in 1..10000")
    rng, checks, failures = random.Random(2026101203), 0, []
    started = time.perf_counter()
    def check(actual, expected, label, case, spec):
        nonlocal checks
        checks += 1
        if actual != expected:
            failures.append({"case": case, "check": label, "actual": actual, "expected": expected, "contract": spec})
    for case in range(args.cases):
        docs, spec = fixture(rng, rng.randrange(2, 20))
        result = s.reason(s.SearchIndex(docs), "Fixture record", spec)
        statuses, status, assumptions, paths = reference(spec)
        check({c: v["status"] for c, v in result["argument"]["claims"].items()}, statuses, "claims", case, spec)
        check((result["assessment"]["status"], result["assessment"]["conditional_on"]), (status, assumptions), "goal", case, spec)
        check(result["assessment"]["assumption_sets"], paths, "goal assumption frontier", case, spec)
        for category, key in [("documents", "document"), ("assumptions", "assumption"), ("rules", "rule")]:
            for removal in result["sensitivity"][category]:
                after, status, assumptions, paths = reference(spec, **{"remove_" + key: removal[key]})
                check((removal["goal_after"], removal["conditional_after"]), (status, assumptions), category, case, spec)
                check(removal["assumption_sets_after"], paths, category + " assumption frontier", case, spec)
    report = {"schema": "surd.reasoning.oracle.v1", "ok": not failures, "cases": args.cases, "checks": checks,
              "host": platform.node(), "elapsed_seconds": time.perf_counter()-started, "seed": 2026101203,
              "scope": "synthetic DAG mechanics, exhaustive proof combinations and all single document/assumption/rule removals; no prose entailment assessment",
              "failures": failures[:20], "failure_count": len(failures)}
    if args.output:
        s.save(report, args.output)
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
