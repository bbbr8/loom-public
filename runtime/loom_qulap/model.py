"""Finite, exact QULAP models. No Python eval, hidden priors, or evidence promotion."""
import ast
import hashlib
import itertools
import json
import math
import re
import time
from collections import Counter


class ModelError(ValueError):
    pass


UNKNOWN = object()
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_INT = 2 ** 120


def exact(value):
    if type(value) not in (int, bool):
        raise ModelError("domains and literals must be integers or booleans; money uses integer cents")
    if type(value) is int and abs(value) > MAX_INT:
        raise ModelError("integer magnitude exceeds 120-bit model limit")
    return value


class Expression:
    """A deliberately small, typed, exact expression language."""
    def __init__(self, text, names):
        if not isinstance(text, str) or len(text) > 4000:
            raise ModelError("expression must be text of at most 4000 characters")
        try:
            self.tree = ast.parse(text, mode="eval").body
        except (SyntaxError, RecursionError) as exc:
            raise ModelError("invalid expression: " + str(exc))
        nodes = list(ast.walk(self.tree))
        if len(nodes) > 250:
            raise ModelError("expression is too complex")
        allowed = (ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
                   ast.BoolOp, ast.Compare, ast.Add, ast.Sub, ast.Mult, ast.USub,
                   ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt,
                   ast.LtE, ast.Gt, ast.GtE)
        for node in nodes:
            if not isinstance(node, allowed):
                raise ModelError("unsupported expression node: " + type(node).__name__)
            if isinstance(node, ast.Constant):
                exact(node.value)
            if isinstance(node, ast.Name) and node.id not in names:
                raise ModelError("unknown variable: " + node.id)
        self.names = {n.id for n in nodes if isinstance(n, ast.Name)}
        self.text = text

    def value(self, world):
        def integer(v):
            if type(v) is not int:
                raise ModelError("arithmetic requires integer values, not booleans")
            return v

        def boolean(v):
            if type(v) is not bool:
                raise ModelError("logical operators require booleans")
            return v

        def walk(n):
            if isinstance(n, ast.Constant):
                return n.value
            if isinstance(n, ast.Name):
                return world.get(n.id, UNKNOWN)
            if isinstance(n, ast.BinOp):
                a, b = walk(n.left), walk(n.right)
                if a is UNKNOWN or b is UNKNOWN:
                    return UNKNOWN
                a, b = integer(a), integer(b)
                v = a + b if isinstance(n.op, ast.Add) else a - b if isinstance(n.op, ast.Sub) else a * b
                return exact(v)
            if isinstance(n, ast.UnaryOp):
                v = walk(n.operand)
                if v is UNKNOWN:
                    return UNKNOWN
                if isinstance(n.op, ast.Not):
                    return not boolean(v)
                return integer(v) if isinstance(n.op, ast.UAdd) else -integer(v)
            if isinstance(n, ast.BoolOp):
                vals = [walk(v) for v in n.values]
                for v in vals:
                    if v is not UNKNOWN:
                        boolean(v)
                if isinstance(n.op, ast.And):
                    if any(v is False for v in vals):
                        return False
                    return UNKNOWN if any(v is UNKNOWN for v in vals) else True
                if any(v is True for v in vals):
                    return True
                return UNKNOWN if any(v is UNKNOWN for v in vals) else False
            if isinstance(n, ast.Compare):
                vals = [walk(n.left)] + [walk(x) for x in n.comparators]
                unknown = False
                for a, b, op in zip(vals, vals[1:], n.ops):
                    if a is UNKNOWN or b is UNKNOWN:
                        unknown = True
                        continue
                    if type(a) is not type(b):
                        raise ModelError("comparison operands must have the same type")
                    if type(a) is bool and not isinstance(op, (ast.Eq, ast.NotEq)):
                        raise ModelError("booleans support only equality comparisons")
                    ok = (a == b if isinstance(op, ast.Eq) else a != b if isinstance(op, ast.NotEq)
                          else a < b if isinstance(op, ast.Lt) else a <= b if isinstance(op, ast.LtE)
                          else a > b if isinstance(op, ast.Gt) else a >= b)
                    if not ok:
                        return False
                return UNKNOWN if unknown else True
            raise ModelError("unhandled expression")
        return walk(self.tree)


def _unique(items, kind):
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ModelError(kind + " entries must be objects")
    ids = [i.get("id") for i in items]
    if any(not isinstance(x, str) or not NAME.fullmatch(x) for x in ids):
        raise ModelError(kind + " IDs must be simple identifier strings")
    if len(set(ids)) != len(ids):
        raise ModelError("duplicate " + kind + " ID")


def compile_model(packet):
    if not isinstance(packet, dict):
        raise ModelError("model must be a JSON object")
    if packet.get("schema") != "qulap.model.v1":
        raise ModelError("expected schema qulap.model.v1")
    if packet.get("source_tier") not in ("synthetic", "derived", "native_review_annotations"):
        raise ModelError("declare source_tier: synthetic, derived, or native_review_annotations")
    variables = packet.get("variables", [])
    if not 1 <= len(variables) <= 40:
        raise ModelError("a model needs 1..40 finite variables")
    _unique(variables, "variable")
    names = {v["id"] for v in variables}
    for v in variables:
        domain = v.get("domain")
        if not isinstance(domain, list) or not 1 <= len(domain) <= 64:
            raise ModelError("each variable needs 1..64 explicit alternatives")
        for x in domain:
            exact(x)
        if len({(type(x).__name__, x) for x in domain}) != len(domain):
            raise ModelError("duplicate domain alternative")
        if len({type(x) for x in domain}) != 1:
            raise ModelError("a variable domain cannot mix booleans and integers")
        if v.get("unit") not in ("cents", "boolean", "integer"):
            raise ModelError("variable unit must be cents, boolean or integer")
        if (v["unit"] == "boolean") != (type(domain[0]) is bool):
            raise ModelError("variable unit and domain type disagree")
        if v["unit"] == "cents" and not isinstance(v.get("lane"), dict):
            raise ModelError("money variables require a lane declaring currency, scope, stage and basis")
        if v["unit"] == "cents" and any(not v["lane"].get(k) for k in ("currency", "scope", "stage", "basis")):
            raise ModelError("money lane is incomplete")
    compiled = {}
    for kind in ("constraints", "metrics", "propositions"):
        entries = packet.get(kind, [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise ModelError(kind + " must contain at most 100 entries")
        _unique(entries, kind)
        compiled[kind] = [(x, Expression(x.get("expr"), names)) for x in entries]
    sources = packet.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 1000:
        raise ModelError("at most 1000 source annotations")
    _unique(sources, "source")
    for s in sources:
        if not s.get("locator") or not s.get("lineage_id"):
            raise ModelError("source annotations need locator and lineage_id")
    source_ids = {s["id"] for s in sources}
    for x in variables + packet.get("constraints", []) + packet.get("propositions", []):
        if any(s not in source_ids for s in x.get("source_ids", [])):
            raise ModelError("unknown source annotation reference")
    # Type and money-lane validation traverses all expressions, independent of feasibility.
    for kind, entries in compiled.items():
        for spec, expr in entries:
            sample = {v["id"]: v["domain"][0] for v in variables}
            value = expr.value(sample)
            if kind in ("constraints", "propositions") and type(value) is not bool:
                raise ModelError(kind + " expressions must return booleans")
            if kind == "metrics" and type(value) is not int:
                raise ModelError("metrics must return integer values")
            units = {v["id"]: v["unit"] for v in variables}
            def unit(n):
                if isinstance(n, ast.Constant):
                    return "boolean" if type(n.value) is bool else "literal"
                if isinstance(n, ast.Name):
                    return units[n.id]
                if isinstance(n, ast.UnaryOp):
                    return "boolean" if isinstance(n.op, ast.Not) else unit(n.operand)
                if isinstance(n, ast.BinOp):
                    a, b = unit(n.left), unit(n.right)
                    if isinstance(n.op, ast.Mult):
                        if a == b == "cents":
                            raise ModelError("cents times cents is not a money amount")
                        return "cents" if "cents" in (a, b) else "integer" if "integer" in (a, b) else "literal"
                    if a != b and "literal" not in (a, b):
                        raise ModelError("cannot add or subtract different units")
                    return b if a == "literal" else a
                if isinstance(n, ast.Compare):
                    us = [unit(n.left)] + [unit(x) for x in n.comparators]
                    if len(set(us) - {"literal"}) > 1:
                        raise ModelError("comparison mixes money, integer or boolean units")
                    return "boolean"
                if isinstance(n, ast.BoolOp):
                    for x in n.values:
                        unit(x)
                    return "boolean"
                raise ModelError("unknown unit expression")
            derived_unit = unit(expr.tree)
            if kind == "metrics" and spec.get("unit", "integer") not in (derived_unit, "integer" if derived_unit == "literal" else derived_unit):
                if not (derived_unit == "literal" and spec.get("unit") == "cents"):
                    raise ModelError("metric unit disagrees with expression")
            money_vars = [v for v in variables if v["id"] in expr.names and v["unit"] == "cents"]
            lanes = {json.dumps(v["lane"], sort_keys=True) for v in money_vars}
            if len(lanes) > 1:
                bridge = spec.get("bridge")
                if not isinstance(bridge, dict) or not bridge.get("reason") or not bridge.get("source_ids"):
                    raise ModelError("cross-lane expression " + spec["id"] + " requires an explicit sourced bridge")
                if any(s not in source_ids for s in bridge["source_ids"]):
                    raise ModelError("unknown bridge source")
    return compiled


def solve(packet, max_nodes=1000000, seconds=10.0, retain_worlds=128, constraint_ids=None):
    if not 1 <= max_nodes <= 10000000 or not 0 < seconds <= 120 or not 0 <= retain_worlds <= 10000:
        raise ModelError("limits: nodes 1..10M, seconds (0,120], retained worlds 0..10000")
    compiled = compile_model(packet)
    cons = compiled["constraints"]
    if constraint_ids is not None:
        cons = [(s, e) for s, e in cons if s["id"] in constraint_ids]
    degree = Counter(n for _, e in cons for n in e.names)
    order = sorted(packet["variables"], key=lambda v: (-degree[v["id"]], len(v["domain"]), v["id"]))
    total = math.prod(len(v["domain"]) for v in order)
    metric_stats = {s["id"]: {"min": None, "max": None, "min_witness": None, "max_witness": None,
                               "unit": s.get("unit", "integer")} for s, _ in compiled["metrics"]}
    props = {s["id"]: {"true_count": 0, "false_count": 0, "witness": None, "counterexample": None,
                        "category": s.get("category", "accounting"), "expression": e.text}
             for s, e in compiled["propositions"]}
    partitions = {v["id"]: {} for v in order}
    worlds, assigned = [], {}
    nodes = leaves = feasible = pruned = constraint_evaluations = full_assignments = 0
    start = time.monotonic()
    cutoff = None

    def search(depth):
        nonlocal nodes, leaves, feasible, pruned, cutoff, constraint_evaluations, full_assignments
        if nodes >= max_nodes or time.monotonic() - start >= seconds:
            cutoff = "node_limit" if nodes >= max_nodes else "time_limit"
            return False
        nodes += 1
        if depth == len(order):
            full_assignments += 1
        for _, expr in cons:
            constraint_evaluations += 1
            if expr.value(assigned) is False:
                pruned += 1
                return True
        if depth == len(order):
            leaves += 1
            feasible += 1
            witness = dict(sorted(assigned.items()))
            if len(worlds) < retain_worlds:
                worlds.append(witness)
            metric_values = {}
            for s, e in compiled["metrics"]:
                v = e.value(assigned)
                if type(v) is not int:
                    raise ModelError("metric changed type")
                metric_values[s["id"]] = v
                st = metric_stats[s["id"]]
                for op in ("min", "max"):
                    if st[op] is None or (v < st[op] if op == "min" else v > st[op]):
                        st[op], st[op + "_witness"] = v, witness
            for s, e in compiled["propositions"]:
                v = e.value(assigned)
                if type(v) is not bool:
                    raise ModelError("proposition changed type")
                st = props[s["id"]]
                st["true_count" if v else "false_count"] += 1
                key = "witness" if v else "counterexample"
                if st[key] is None:
                    st[key] = witness
            for name, value in assigned.items():
                key = json.dumps(value)
                part = partitions[name].setdefault(key, {"value": value, "worlds": 0, "metrics": {}})
                part["worlds"] += 1
                for mid, v in metric_values.items():
                    b = part["metrics"].setdefault(mid, {"min": v, "max": v})
                    b["min"], b["max"] = min(b["min"], v), max(b["max"], v)
            return True
        var = order[depth]
        for value in var["domain"]:
            assigned[var["id"]] = value
            if not search(depth + 1):
                assigned.pop(var["id"], None)
                return False
        assigned.pop(var["id"], None)
        return True

    complete = search(0)
    state = "COMPLETE" if complete and feasible else "INCONSISTENT_MODEL" if complete else "INCOMPLETE"
    for st in props.values():
        t, f = st["true_count"], st["false_count"]
        st["status"] = ("INCONSISTENT_MODEL" if complete and not feasible else
                        "UNRESOLVED_INCOMPLETE" if not complete else
                        "FORCED_IN_MODEL" if t and not f else "IMPOSSIBLE_IN_MODEL" if f and not t else "POSSIBLE_IN_MODEL")
        st["observed_both_outcomes"] = bool(t and f)
    next_records = []
    for var in order:
        if not var.get("next_record"):
            continue
        parts = list(partitions[var["id"]].values())
        reductions = {}
        if complete and feasible:
            for mid, stat in metric_stats.items():
                width = stat["max"] - stat["min"]
                worst = max((p["metrics"][mid]["max"] - p["metrics"][mid]["min"] for p in parts), default=width)
                reductions[mid] = {"unit": stat["unit"], "current_width": width,
                                   "worst_width_after_resolution": worst, "guaranteed_width_reduction": width - worst}
        next_records.append({"variable": var["id"], "record": var["next_record"],
                             "partitions": parts, "metric_reductions": reductions,
                             "ranking_basis": "per-metric worst-case interval reduction; no probabilities or pooled units"})
    issues = []
    for var in packet["variables"]:
        if len(var["domain"]) == 1 and not var.get("source_ids"):
            issues.append({"id": var["id"], "issue": "fixed input has no source annotation"})
    for spec in packet.get("constraints", []):
        if not spec.get("source_ids"):
            issues.append({"id": spec["id"], "issue": "constraint is an unsourced modeling assumption"})
    return {"schema": "qulap.result.v1", "engine": "QULAP alternative-state engine", "state": state,
            "complete": complete, "cutoff": cutoff, "source_tier": packet["source_tier"],
            "proof_effect": "NONE", "legal_determination": "NONE",
            "model_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "candidate_states": total, "visited_nodes": nodes, "evaluated_feasible_leaves": leaves,
            "classical_constraint_evaluations": constraint_evaluations, "full_assignments_evaluated": full_assignments,
            "metric_evaluations": feasible * len(compiled["metrics"]), "proposition_evaluations": feasible * len(compiled["propositions"]),
            "pruned_branches": pruned, "feasible_states": feasible, "seconds": time.monotonic() - start,
            "metrics": metric_stats, "metric_scope": "exact global bounds" if complete and feasible else "observed bounds only",
            "propositions": props, "next_records": next_records, "worlds": worlds,
            "worlds_truncated": feasible > len(worlds), "input_issues": issues,
            "sources": packet.get("sources", []), "unknowns": packet.get("coverage_gaps", []),
            "boundary": "Exhaustiveness is relative to declared finite domains and constraints, not the real evidence universe."}


def contradiction_core(packet, max_nodes=1000000, seconds=10):
    """Deletion-minimal inconsistent constraint set; never claim a minimum-size core."""
    if len(packet.get("constraints", [])) > 24:
        raise ModelError("core analysis is bounded to 24 constraints")
    deadline = time.monotonic() + seconds
    ids = [s["id"] for s in packet.get("constraints", [])]
    def run(keep):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return solve(packet, max_nodes, min(remaining, 120), 0, keep)
    base = run(ids)
    if base is None or base["state"] != "INCONSISTENT_MODEL":
        return {"state": "NO_PROVED_CONTRADICTION", "base_state": None if base is None else base["state"], "proof_effect": "NONE"}
    complete = True
    checks = []
    for cid in ids[:]:
        trial = [x for x in ids if x != cid]
        r = run(trial)
        checks.append({"removed": cid, "state": None if r is None else r["state"]})
        if r is None or not r["complete"]:
            complete = False
            break
        if r["state"] == "INCONSISTENT_MODEL":
            ids = trial
    return {"state": "IRREDUCIBLE_CORE" if complete else "INCOMPLETE_CORE_REDUCTION", "constraints": ids,
            "checks": checks, "minimality": "deletion-minimal, not minimum cardinality" if complete else "not established",
            "proof_effect": "NONE", "source_annotations": packet.get("sources", [])}


def sensitivity(packet, max_nodes=100000, seconds=10):
    if len(packet.get("constraints", [])) > 24:
        raise ModelError("sensitivity is bounded to 24 constraints")
    deadline = time.monotonic() + seconds
    base = solve(packet, max_nodes, max(.001, min(seconds, 120)), 0)
    rows = []
    for spec in packet.get("constraints", []):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        other = dict(packet, constraints=[s for s in packet["constraints"] if s["id"] != spec["id"]])
        r = solve(other, max_nodes, min(left, 120), 0)
        rows.append({"removed_constraint": spec["id"], "state": r["state"],
                     "feasible_states": r["feasible_states"], "metrics": r["metrics"],
                     "propositions": {k: v["status"] for k, v in r["propositions"].items()}})
    return {"schema": "qulap.sensitivity.v1", "base": base, "knockouts": rows,
            "knockouts_complete": len(rows) == len(packet.get("constraints", [])), "proof_effect": "NONE"}
