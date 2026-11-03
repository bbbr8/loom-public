"""Receipt-bounded model projection. Receipt never becomes actual understanding."""
import copy
from datetime import date

from .model import ModelError, compile_model


def asof_receipt(packet, actor, cutoff):
    compile_model(packet)
    try:
        anchor = date.fromisoformat(cutoff)
    except (TypeError, ValueError):
        raise ModelError("cutoff must be an ISO calendar date")
    if not isinstance(actor, str) or not actor:
        raise ModelError("declare an actor")
    eligible, excluded_sources = set(), []
    for source in packet.get("sources", []):
        receipt = source.get("receipt_by_actor", {}).get(actor)
        state = "UNKNOWN_RECEIPT"
        if receipt is not None:
            interval = receipt if isinstance(receipt, list) else [receipt, receipt]
            if len(interval) != 2:
                raise ModelError("receipt must be ISO date or [earliest,latest]")
            try:
                lo, hi = [date.fromisoformat(x) for x in interval]
            except (TypeError, ValueError):
                raise ModelError("invalid receipt interval")
            if lo > hi:
                raise ModelError("receipt interval is reversed")
            state = "BY_CUTOFF" if hi <= anchor else "AFTER_CUTOFF" if lo > anchor else "OVERLAPS_CUTOFF"
        if state == "BY_CUTOFF":
            eligible.add(source["id"])
        else:
            excluded_sources.append({"source": source["id"], "state": state, "receipt": receipt})
    projected = copy.deepcopy(packet)
    changes, excluded_constraints = [], []
    for var in projected["variables"]:
        deps = set(var.get("source_ids", []))
        if deps and not deps.issubset(eligible):
            prior = var.get("prior_domain")
            if prior is None:
                raise ModelError("as-of projection needs an explicit prior_domain for unavailable input " + var["id"])
            changes.append({"variable": var["id"], "later_domain": var["domain"], "prior_domain": prior,
                            "unavailable_sources": sorted(deps - eligible)})
            var["domain"] = prior
            var["source_ids"] = []
    kept = []
    for constraint in projected.get("constraints", []):
        deps = set(constraint.get("source_ids", [])) | set(constraint.get("bridge", {}).get("source_ids", []))
        if deps and not deps.issubset(eligible):
            excluded_constraints.append({"constraint": constraint["id"], "unavailable_sources": sorted(deps - eligible)})
        else:
            kept.append(constraint)
    projected["constraints"] = kept
    projected["receipt_projection"] = {"actor": actor, "cutoff": cutoff, "eligible_sources": sorted(eligible),
                                       "excluded_sources": excluded_sources, "domain_changes": changes,
                                       "excluded_constraints": excluded_constraints,
                                       "boundary": "Supplied receipt annotations only; delivery does not establish awareness, understanding, approval or reliance."}
    projected.setdefault("coverage_gaps", []).append("As-of receipt model retains unsourced modeling assumptions, which are not actor knowledge.")
    compile_model(projected)
    return projected
