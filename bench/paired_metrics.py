"""Pair exact AP@10 scores for the same judged queries in two benchmark runs."""
from fractions import Fraction


def scored_queries(report):
    scores = {}
    for row in report["rankings"]:
        qid = row["qid"]
        if qid in scores:
            raise ValueError(f"Duplicate ranked query: {qid}")
        score = Fraction(row["ap10_fraction"])
        if not 0 <= score <= 1:
            raise ValueError(f"AP@10 outside [0, 1] for {qid}")
        scores[qid] = (row["query"], row["relevant"], score)
    if not scores or len(scores) != report["queries"]:
        raise ValueError("Benchmark query count does not match ranked queries")
    return scores


def paired_comparison(current, baseline):
    if not current["dataset_sha256"] or current["dataset_sha256"] != baseline["dataset_sha256"]:
        raise ValueError("Paired comparison requires matching dataset hashes")
    after, before = scored_queries(current), scored_queries(baseline)
    if before.keys() != after.keys():
        raise ValueError("Paired comparison requires the same query IDs")
    changes = {}
    for qid, (query, judgments, score) in after.items():
        if (query, judgments) != before[qid][:2]:
            raise ValueError(f"Query text or judgments changed for {qid}")
        changes[qid] = score - before[qid][2]
    count = len(changes)
    total = sum(changes.values(), Fraction(0))
    result = {
        "queries": count,
        "improved": sum(delta > 0 for delta in changes.values()),
        "tied": sum(delta == 0 for delta in changes.values()),
        "declined": sum(delta < 0 for delta in changes.values()),
        "map10_change_fraction": str(total / count),
        "leave_one_out": None,
    }
    if count > 1:
        # Removing the largest improvement gives the smallest remaining mean.
        low = min(changes.values())
        high = max(changes.values())
        result["leave_one_out"] = {
            "minimum_mean_change": str((total - high) / (count - 1)),
            "maximum_mean_change": str((total - low) / (count - 1)),
            "minimum_when_omitting": sorted(q for q, delta in changes.items() if delta == high),
            "maximum_when_omitting": sorted(q for q, delta in changes.items() if delta == low),
        }
    return result
