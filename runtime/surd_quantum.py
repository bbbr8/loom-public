"""Source-linked quantum scenario exploration over declared Surd arguments."""
import copy
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction as F

from surd_search_core import SearchError, digest, options
from surd_reason import prepare, audit, FrontierBudget, sequence, ident
from surd_posture import posture, validate_report as validate_posture
from surd_quantum_core import circuit, rational, validate_iterations

SCHEMA = "surd.search.quantum.v1"
POLICY = "exact-phase-oracle-declared-scenarios-v1"
STATUSES = {"supported", "opposed", "contested", "unresolved"}
SCOPE_FIELDS = {"actor", "project", "transaction", "version", "stage"}
BASIS = ("Exact classical simulation of declared assumption assignments. Measurement shares describe "
         "the configured circuit; case confidence, credibility and legal outcomes remain unassessed.")


def text(value, name, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SearchError(name + " needs bounded nonempty text")
    return value


def money(value):
    if not isinstance(value, str) or len(value) > 80:
        raise SearchError("guard amounts require decimal strings")
    try:
        d = Decimal(value)
        if not d.is_finite() or abs(d) > Decimal("1e15") or d.as_tuple().exponent < -2:
            raise ValueError()
        return F(d)
    except (InvalidOperation, ValueError):
        raise SearchError("guard amounts require finite exact cents within 1e15")


def validate_guards(raw, claims):
    result, ids = [], set()
    for g in sequence(raw, "typed guards", 32):
        g = options(g, {"id", "kind", "claims", "field", "expected"})
        gid = ident(g.get("id"), "guard id")
        if gid in ids:
            raise SearchError("duplicate guard id")
        ids.add(gid)
        refs = sequence(g.get("claims"), "guard claims", 8)
        if not refs or any(not isinstance(c, str) or c not in claims for c in refs) or len(set(refs)) != len(refs):
            raise SearchError("guard claims must name distinct existing claims")
        kind = g.get("kind")
        if kind == "fact_kind":
            text(g.get("expected"), "expected fact kind", 160)
            if len(refs) != 1 or "field" in g:
                raise SearchError("fact_kind guard needs one claim and expected kind")
        elif kind == "same_scope":
            if len(refs) < 2 or not isinstance(g.get("field"), str) or g["field"] not in SCOPE_FIELDS or "expected" in g:
                raise SearchError("same_scope needs 2..8 claims and a known scope field")
        elif kind == "before":
            if len(refs) != 2 or not isinstance(g.get("field"), str) or g["field"] not in {"event_date", "statement_date", "received_at"} or "expected" in g:
                raise SearchError("before needs two ordered claims and an explicit clock")
        elif kind == "money_equal":
            if len(refs) != 2 or "field" in g or "expected" in g:
                raise SearchError("money_equal needs exactly two claims")
        else:
            raise SearchError("unknown typed guard kind")
        result.append(dict(g))
    return result


def check_guards(index, claims, guards, removed=frozenset()):
    result = []
    for g in guards:
        field = {"fact_kind": "fact_kind", "money_equal": "amount"}.get(g["kind"], g.get("field"))
        vals, citations, reasons = [], [], []
        for cid in g["claims"]:
            entries = [e for e in claims[cid]["support"] if e["document"] not in removed]
            if not entries:
                reasons.append("No remaining direct support for claim " + cid + ".")
            values = []
            for e in entries:
                value = index.document(e["document"])["metadata"].get(field)
                citations.append(e)
                if value is None or value == "":
                    reasons.append("Missing %s annotation on source %s for claim %s." % (field, e["document"], cid))
                    continue
                if not isinstance(value, str):
                    raise SearchError("typed source metadata must contain text: %s on %s" % (field, e["document"]))
                values.append(value)
            vals.append(sorted(set(values)))
        # Conflicting direct annotations remain visibly blocked even if another
        # source is missing. These guards check annotations, not prose meaning.
        blocked = any(len(v) > 1 for v in vals) if g["kind"] in {"fact_kind", "same_scope"} else False
        if g["kind"] == "fact_kind":
            blocked |= any(v != g["expected"] for group in vals for v in group)
        elif g["kind"] == "same_scope":
            blocked |= len({v for group in vals for v in group}) > 1
        elif g["kind"] == "before":
            try:
                if any(len(v) != 10 or v[4] != "-" or v[7] != "-" for group in vals for v in group):
                    raise ValueError()
                ds = [[date.fromisoformat(v) for v in group] for group in vals]
            except ValueError:
                raise SearchError("before guard needs ISO calendar dates or missing metadata")
            if all(ds):
                blocked |= max(ds[0]) >= min(ds[1])
        elif g["kind"] == "money_equal":
            amounts = [{money(v) for v in group} for group in vals]
            # A known conflict remains a conflict when another source is absent.
            blocked |= len(set.union(*amounts)) > 1
        incomplete = bool(reasons)
        if blocked:
            reasons.insert(0, "Strict calendar-date order is not established by these annotations."
                           if g["kind"] == "before" else "Available source annotations conflict with this guard.")
        result.append({**g, "state": "blocked" if blocked else "open" if incomplete else "passed",
                       "reasons": reasons, "values": vals, "citations": citations,
                       "basis": "Direct support-source metadata annotations; semantic review is caller-supplied."})
    return result


def fingerprint(report):
    stable = copy.deepcopy(report)
    stable.pop("report_hash", None)
    nested = stable.get("posture", {})
    nested.pop("report_hash", None)
    reason = nested.get("reasoning", {})
    reason.pop("report_hash", None)
    reason.get("retrieval", {}).get("results", {}).pop("elapsed_ms", None)
    for facet in reason.get("facets", []):
        facet.get("results", {}).pop("elapsed_ms", None)
    return digest(stable)


def validate_report(report):
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or report.get("policy") != POLICY:
        raise SearchError("expected a Surd quantum reasoning report")
    validate_posture(report.get("posture"))
    if report.get("report_hash") != fingerprint(report):
        raise SearchError("quantum report integrity mismatch; rerun the source index")
    return report


def quantum(index, question, spec):
    spec = copy.deepcopy(options(spec, {"posture", "variables", "target", "requirements", "exclusive_groups", "guards", "records", "iterations"}))
    try:
        validate_iterations(spec.get("iterations"))
    except ValueError as e:
        raise SearchError(str(e)) from e
    p = spec.get("posture")
    if not isinstance(p, dict) or not isinstance(p.get("argument"), dict):
        raise SearchError("quantum reasoning needs a posture contract")
    contract = p["argument"]
    where, ranges = contract.get("where", {}), contract.get("ranges", {})
    claims, rules, order, deps, _ = prepare(index, contract, where, ranges)
    variables = sequence(spec.get("variables"), "quantum variables", 8)
    if not variables or any(not isinstance(c, str) or c not in claims or not claims[c]["assumption"] for c in variables) or len(set(variables)) != len(variables):
        raise SearchError("quantum variables must be 1..8 distinct explicit assumption claims")
    target = spec.get("target")
    if not isinstance(target, str) or target not in claims:
        raise SearchError("quantum target must name a declared claim")
    requirements = []
    for r in sequence(spec.get("requirements", []), "quantum requirements", 16):
        r = options(r, {"claim", "status"})
        if not isinstance(r.get("claim"), str) or r["claim"] not in claims or not isinstance(r.get("status"), str) or r["status"] not in STATUSES:
            raise SearchError("requirements need a known claim and four-valued status")
        requirements.append(dict(r))
    groups = []
    for g in sequence(spec.get("exclusive_groups", []), "exclusive groups", 16):
        g = sequence(g, "exclusive group", 8)
        if len(g) < 2 or any(not isinstance(c, str) or c not in variables for c in g) or len(set(g)) != len(g):
            raise SearchError("exclusive groups need 2..8 distinct quantum variables")
        groups.append(g)
    guards = validate_guards(spec.get("guards", []), claims)
    records, ids = [], set()
    for r in sequence(spec.get("records", []), "quantum record requests", 32):
        r = options(r, {"id", "claim", "record", "custodian", "identifier", "if_confirms", "if_weakens"})
        rid = ident(r.get("id"), "record id")
        if rid in ids or not isinstance(r.get("claim"), str) or r["claim"] not in claims:
            raise SearchError("record requests need distinct ids and known claims")
        ids.add(rid)
        for field in ("record", "custodian", "identifier", "if_confirms", "if_weakens"):
            text(r.get(field), "record " + field)
        records.append(r)
    branch = set()
    for issue in sequence(p.get("issues"), "posture issues", 16):
        if not isinstance(issue, dict):
            raise SearchError("posture issue must be a map")
        for f in sequence(issue.get("forks", []), "posture forks", 2):
            if not isinstance(f, dict):
                raise SearchError("posture fork must be a map")
            for cid in sequence(f.get("assume"), "fork assumptions", 8):
                if not isinstance(cid, str):
                    raise SearchError("fork assumptions must name claims")
                branch.add(cid)
    inactive = set(variables) | branch
    base = copy.deepcopy(p)
    for c in base["argument"]["claims"]:
        if c["id"] in set(variables) - branch:
            c["assumption"] = False
    # The posture layer validates the complete contract and excludes all its
    # branch assumptions. Additional enumerated variables are disabled above.
    baseline = posture(index, question, base)
    budget = FrontierBudget()
    size = 1 << len(variables)
    families = baseline["source_families"]

    def worlds(removed=frozenset()):
        typed = check_guards(index, claims, guards, removed)
        guard_ok = all(g["state"] == "passed" for g in typed)
        scoped_claims = {cid: dict(c, assumption=c["assumption"] and cid not in inactive,
                                 support=[e for e in c["support"] if e["document"] not in removed],
                                 oppose=[e for e in c["oppose"] if e["document"] not in removed]) for cid, c in claims.items()}
        rows = []
        for i in range(size):
            bits = format(i, "0%db" % len(variables))
            active = [v for v, bit in zip(variables, bits) if bit == "1"]
            selected = {cid: dict(c, assumption=c["assumption"] or cid in active) for cid, c in scoped_claims.items()}
            arg, assessment = audit(selected, rules, order, target, budget=budget)
            consistent = guard_ok and all(sum(c in active for c in g) == 1 for g in groups)
            consistent &= all(arg["claims"][r["claim"]]["status"] == r["status"] for r in requirements)
            rows.append({"bits": bits, "active_assumptions": active, "feasible": consistent,
                         "target_status": arg["claims"][target]["status"],
                         "target_assessment": assessment,
                         "claim_statuses": {cid: c["status"] for cid, c in arg["claims"].items()},
                         "marked": consistent and arg["claims"][target]["status"] == "supported"})
        return rows, typed

    rows, typed = worlds()
    marked = [i for i, r in enumerate(rows) if r["marked"]]
    try:
        simulation = circuit(len(variables), marked, spec.get("iterations"))
    except ValueError as e:
        raise SearchError(str(e)) from e
    for mode in simulation["interference"]["spectrum"]:
        mode["parity_variables"] = [cid for cid, bit in zip(variables, mode["mask"]) if bit == "1"]
    feasible = [r for r in rows if r["feasible"]]
    yes = sum(r["marked"] for r in feasible)
    target_pairs = yes * (len(feasible) - yes)
    requests = []
    for r in records:
        partitions = {s: [w for w in feasible if w["claim_statuses"][r["claim"]] == s] for s in sorted(STATUSES)}
        unresolved_pairs = sum(sum(w["marked"] for w in ws) * sum(not w["marked"] for w in ws) for ws in partitions.values())
        separated = target_pairs - unresolved_pairs
        all_pairs = len(feasible)*(len(feasible)-1)//2
        split_pairs = all_pairs - sum(len(ws)*(len(ws)-1)//2 for ws in partitions.values())
        requests.append({**r, "target_pairs_separated": separated, "target_pairs": target_pairs,
                         "scenario_pairs_separated": split_pairs,
                         "target_pair_fraction": rational(F(separated, target_pairs) if target_pairs else 0),
                         "partitions": {s: [w["bits"] for w in ws] for s, ws in partitions.items()},
                         "basis": "Classical partitions of declared feasible scenarios; no record-outcome likelihood assigned."})
    requests.sort(key=lambda r: (-r["target_pairs_separated"], -r["scenario_pairs_separated"], r["id"]))
    withdrawals = []
    for family in families:
        changed, _guards = worlds(frozenset(family["documents"]))
        withdrawals.append({"family_id": family["id"], "documents": family["documents"],
                            "feasible_states": sum(w["feasible"] for w in changed),
                            "marked_states": sum(w["marked"] for w in changed),
                            "lost_marked": [a["bits"] for a, b in zip(rows, changed) if a["marked"] and not b["marked"]],
                            "gained_marked": [a["bits"] for a, b in zip(rows, changed) if not a["marked"] and b["marked"]],
                            "guards": _guards})
    report = {"schema": SCHEMA, "policy": POLICY, "question": question, "specification": spec,
              "basis": BASIS, "posture": baseline, "variables": variables, "target": target,
              "bit_meaning": "1 activates the named assumption; 0 leaves it unasserted, rather than asserting its negation.",
              "state": "guard_blocked" if any(g["state"] == "blocked" for g in typed) else "guard_open" if any(g["state"] == "open" for g in typed) else "no_feasible_scenario" if not feasible else "target_unmarked_in_all_scenarios" if not marked else "scenarios_found",
              "guards": typed, "worlds": rows, "feasible_states": len(feasible), "marked_states": len(marked),
              "quantum": simulation, "next_records": requests, "family_withdrawals": withdrawals,
              "verification": {"execution": "classical exact quantum simulation", "source_semantics": "caller-reviewed annotations and declared inference rules",
                               "circuit_measurement_is_case_confidence": False, "quantum_speedup_claimed": False,
                               "oracle_truth_table_classically_evaluated": True,
                               "classical_world_evaluations": size*(1+len(families)),
                               "shared_frontier_work": budget.work, "typed_guards_requested": bool(guards),
                               "native_files_reopened": False, "legal_outcome_assessed": False}}
    report["report_hash"] = fingerprint(report)
    return report


def render(report):
    validate_report(report)
    lines = [report["question"], "Quantum scenario reasoning: " + report["state"], BASIS,
             "Basis order: " + ", ".join(report["variables"]), report["bit_meaning"],
             "Feasible scenarios: %d; target-support scenarios: %d" % (report["feasible_states"], report["marked_states"]),
             "Exact circuit marked measurement share: %s -> %s (%d Grover iterations)" %
             (report["quantum"]["initial_marked_share"]["exact"], report["quantum"]["final_marked_share"]["exact"], report["quantum"]["iterations"])]
    for g in report["guards"]:
        lines.append("Guard %s [%s]: %s" % (g["id"], g["state"], g["kind"]))
        for reason in g.get("reasons", []):
            lines.append("  " + reason)
        if g["state"] != "passed":
            for cid, values in zip(g["claims"], g["values"]):
                lines.append("  %s: %s" % (cid, ", ".join(values) or "unknown"))
    feasible = [w for w in report["worlds"] if w["feasible"]]
    if len(feasible) > 16:
        feasible.sort(key=lambda w: (not w["marked"], w["bits"]))
        lines.append("Scenario preview: 16 of %d feasible assignments, target-support first; JSON retains every assignment." % len(feasible))
    for w in feasible[:16]:
        lines.append("Scenario %s: target %s; assumptions: %s" % (w["bits"], w["target_status"], ", ".join(w["active_assumptions"]) or "none"))
    modes = [m for m in report["quantum"]["interference"]["spectrum"]
             if m["parity_variables"] and m["measurement_share"]["numerator"]]
    modes.sort(key=lambda m: (-F(m["measurement_share"]["exact"]), m["mask"]))
    lines.append("Oracle parity diagnostic (up to four largest nonconstant modes):")
    for m in modes[:4]:
        lines.append("  %s: amplitude %s; circuit measurement share %s" %
                     (" xor ".join(m["parity_variables"]), m["amplitude"]["exact"], m["measurement_share"]["exact"]))
    if not modes:
        lines.append("  No nonconstant modes in this configured oracle.")
    lines.append("Next discriminating records (declared-scenario partitions):")
    for r in report["next_records"]:
        lines.extend(["  %s · %s · %s; separates %d/%d target-marked/unmarked scenario pairs" %
                      (r["record"], r["custodian"], r["identifier"], r["target_pairs_separated"], r["target_pairs"]),
                      "    If confirms: " + r["if_confirms"], "    If weakens: " + r["if_weakens"]])
    lines.append("Source-family sensitivity:")
    for r in report["family_withdrawals"]:
        lines.append("  Withdraw %s: %d target-support scenarios; lost %s; gained %s" %
                     (", ".join(r["documents"]), r["marked_states"], ", ".join(r["lost_marked"]) or "none", ", ".join(r["gained_marked"]) or "none"))
        affected_guards = [g["id"] + "=" + g["state"] for g in r["guards"] if g["state"] != "passed"]
        if affected_guards:
            lines.append("    Guards after withdrawal: " + ", ".join(affected_guards))
    from surd_posture import render as render_posture
    lines.extend(["", "Baseline source posture (enumerated assumptions excluded):", render_posture(report["posture"])])
    return "\n".join(lines)
