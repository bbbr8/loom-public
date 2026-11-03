"""Independent finite-state checks for source-family withdrawals and isolated branches."""
import argparse
import json
from pathlib import Path
import platform
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from surd_search import SearchIndex
from surd_posture import posture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=500)
    parser.add_argument("--seed", type=int, default=2026101209)
    args = parser.parse_args()
    if not 1 <= args.cases <= 5000:
        parser.error("cases must be in 1..5000")
    rng = random.Random(args.seed); checks = 0; failures = []; start = time.monotonic()
    for trial in range(args.cases):
        nd, nc = rng.randint(1, 6), rng.randint(1, 5)
        docs = [{"id": "d"+str(i), "text": f"Synthetic trial {trial} record {i}.", "locator": f"synthetic/{trial}/{i}"} for i in range(nd)]
        claims, supports, opposition = [], {}, {}
        for i in range(nc):
            cid = "p"+str(i)
            supports[cid] = set(rng.sample(range(nd), rng.randint(1, min(3, nd))))
            opposition[cid] = {rng.randrange(nd)} if rng.random() < .2 else set()
            claims.append({"id": cid, "text": "Synthetic proposition "+cid,
                           **{side: [{"document":"d"+str(d), "quote":docs[d]["text"]} for d in sorted(items)] for side, items in [("support", supports[cid]), ("oppose", opposition[cid])]}})
        claims += [{"id":"goal", "text":"All selected premises support the goal."}, {"id":"c", "text":"Assume concession.", "assumption":True},
                   {"id":"h", "text":"Assume refusal.", "assumption":True}, {"id":"cg", "text":"Concession consequence."}, {"id":"hg", "text":"Refusal consequence."}]
        used = set().union(*supports.values(), *opposition.values())
        families = {d: ("f"+str(rng.randrange(3)) if rng.random() < .8 else None) for d in used}
        spec = {"argument":{"claims":claims, "rules":[{"id":"all", "if":["p"+str(i) for i in range(nc)], "then":"goal"},
                    {"id":"cf", "if":["goal","c"], "then":"cg"}, {"id":"hf", "if":["p0","h"], "then":"hg"}]},
                "issues":[{"id":"i", "title":"Synthetic issue", "goal":"goal", "floor":"p0", "forks":[{"id":"concede","label":"concede","assume":["c"],"goal":"cg"}, {"id":"hold","label":"hold","assume":["h"],"goal":"hg"}]}],
                "source_families":{"d"+str(d): f for d,f in families.items() if f is not None}}
        result = posture(SearchIndex(docs), "synthetic proposition", spec); issue = result["issues"][0]

        def state(cid, removed):
            yes = bool(supports[cid]-removed); no = bool(opposition[cid]-removed)
            return "contested" if yes and no else "supported" if yes else "opposed" if no else "unresolved"

        def goal(removed):
            return "supported" if all(state("p"+str(i), removed) == "supported" for i in range(nc)) else "unresolved"

        def check(label, actual, expected):
            nonlocal checks
            checks += 1
            if actual != expected:
                failures.append({"trial":trial,"label":label,"actual":actual,"expected":expected})

        check("baseline", issue["assessment"]["status"], goal(set()))
        check("record floor", issue["record_floor"]["status"], state("p0", set()))
        check("branch isolation c", result["reasoning"]["argument"]["claims"]["cg"]["status"], "unresolved")
        check("branch isolation h", result["reasoning"]["argument"]["claims"]["hg"]["status"], "unresolved")
        by_fork = {f["id"]: f for f in issue["forks"]}
        check("concession", by_fork["concede"]["supported_under_stated_branch"], goal(set()) == "supported")
        check("refusal", by_fork["hold"]["supported_under_stated_branch"], state("p0",set()) == "supported")
        expected_groups = {}
        for d, family in families.items(): expected_groups.setdefault(family, set()).add("d"+str(d))
        check("lineage grouping", sorted(sorted(v) for v in expected_groups.values()), sorted(g["documents"] for g in result["source_families"]))
        for withdrawal in issue["family_withdrawals"]:
            removed = {int(d[1:]) for d in withdrawal["documents"]}
            check("family goal", withdrawal["goal_after"]["status"], goal(removed))
            check("family floor", withdrawal["floor_after"]["status"], state("p0",removed))
    report = {"schema":"surd.posture.oracle.v1", "ok":not failures, "cases":args.cases, "checks":checks, "seed":args.seed,
              "host":platform.node(), "seconds":round(time.monotonic()-start,3), "failures":failures[:20], "failure_count":len(failures),
              "scope":"Synthetic finite-state conjunctions, four-valued premises, pooled origins, family withdrawals and isolated branch scenarios; no case facts or prose entailment."}
    print(json.dumps(report)); return 0 if report["ok"] else 1


if __name__ == "__main__": sys.exit(main())
