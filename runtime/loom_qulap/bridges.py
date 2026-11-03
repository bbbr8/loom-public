"""Actor-specific, four-state evidence bookkeeping; no RICO sufficiency score."""
from collections import defaultdict
from .model import ModelError


SLOTS = {
    "1962c": ["person", "enterprise", "person_enterprise_distinction", "commerce",
              "conduct_participation", "qualifying_predicate_acts", "relatedness", "continuity",
              "plaintiff_business_property_injury", "direct_causation"],
    "1962d": ["agreement_to_substantive_violation", "actor_agreement_knowledge",
              "plaintiff_business_property_injury", "direct_causation"],
}


def evidence_matrix(packet):
    if packet.get("schema") != "qulap.rico-bridges.v1":
        raise ModelError("expected qulap.rico-bridges.v1")
    subsection = packet.get("subsection")
    if subsection not in SLOTS:
        raise ModelError("implemented evidence templates are 1962c and 1962d; other subsections require their own reviewed template")
    actors = packet.get("actors")
    if not isinstance(actors, list) or not actors or len(actors) > 100 or any(not isinstance(a, str) or not a for a in actors):
        raise ModelError("declare 1..100 named actors")
    if len(set(actors)) != len(actors):
        raise ModelError("duplicate actor")
    annotations = packet.get("annotations", [])
    if len(annotations) > 2000:
        raise ModelError("annotation limit is 2000")
    grouped = defaultdict(list)
    seen = set()
    for row in annotations:
        if row.get("id") in seen or not row.get("id"):
            raise ModelError("annotation IDs must be nonempty and unique")
        seen.add(row["id"])
        if row.get("actor") not in actors or row.get("slot") not in SLOTS[subsection]:
            raise ModelError("annotation actor or slot is outside the declared template")
        if row.get("stance") not in ("support", "contrary"):
            raise ModelError("stance must be support or contrary")
        if not row.get("locator") or not row.get("lineage_id") or not row.get("proposition"):
            raise ModelError("each annotation needs proposition, locator and native lineage")
        grouped[(row["actor"], row["slot"])].append(row)
    matrix = []
    for actor in actors:
        for slot in SLOTS[subsection]:
            rows = grouped[(actor, slot)]
            support = [r for r in rows if r["stance"] == "support"]
            contrary = [r for r in rows if r["stance"] == "contrary"]
            status = "BOTH_ANNOTATED" if support and contrary else "SUPPORT_ANNOTATED" if support else "CONTRARY_ANNOTATED" if contrary else "OPEN"
            matrix.append({"actor": actor, "slot": slot, "state": status, "annotations": rows,
                           "distinct_native_lineages": sorted({r["lineage_id"] for r in rows}),
                           "clock_gaps": [r["id"] for r in rows if not r.get("event_time") or not r.get("knowledge_time")],
                           "next_record": packet.get("next_records", {}).get(actor, {}).get(slot)})
    return {"schema": "qulap.rico-matrix.v1", "subsection": subsection, "matrix": matrix,
            "source_tier": packet.get("source_tier", "derived"), "proof_effect": "NONE",
            "legal_determination": "NONE", "legal_coverage": packet.get("legal_coverage", "UNREVIEWED"),
            "boundary": "These are supplied evidence annotations, not element findings. Different lineage IDs do not prove independence. Two acts do not automatically establish a RICO pattern.",
            "unresolved_legal_checks": ["current and event-date governing authority", "jurisdiction-specific elements and defenses",
                                       "each predicate offense's elements", "relationship and continuity", "direct injury and causation"]}
