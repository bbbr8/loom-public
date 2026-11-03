"""Bounded argument auditing over Surd source snapshots. No generated entailment.

The caller supplies claim/evidence relationships and rules. The engine validates
quotations, checks an acyclic dependency graph, exposes conflict and assumptions,
and reruns the graph with individual sources/assumptions removed.
"""
from collections import Counter
import json

from surd_search_core import (SearchError, ReasoningCorpus, canonical, digest, integer,
                         options, tokens)

SCHEMA = "surd.search.reason.v1"
POLICY = "skeptical-acyclic-arguments-v2"
LEGACY_POLICY = "skeptical-acyclic-arguments-v1"
MAX_FRONTIER_SETS = 1024
MAX_FRONTIER_CANDIDATES = 16384
MAX_FRONTIER_WORK = 2000000
ASSUMPTION_SCOPE = ("subset-minimal assumption unions of eligible derivations in the evaluated graph; "
                    "not minimum models, independent evidence, or guarantees after simultaneous removals")
# Diagnostic grouping only: the original query and its ranking terms are retained.
# Negation and modal strength are intentionally absent from this framing list.
QUESTION_FRAMING = set("is are am be been being do does did what which who whom whose when where why how a an the this that these those to of for in on at by with and or it its as".split())
BASIS = ("caller-declared evidence links, assumptions and inference rules; exact "
         "quotation validation does not verify entailment, authenticity or source truth")


def ident(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise SearchError(f"{name} needs nonempty text of at most 160 characters")
    return value


def sequence(value, name, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise SearchError(f"{name} must be a list with at most {maximum} entries")
    return value


def citation(index, value, where, ranges):
    value = options(value, {"document", "quote", "start", "content_hash"})
    doc_id = ident(value.get("document"), "citation document")
    doc = index.document(doc_id)
    if not index.in_scope(doc_id, where, ranges):
        raise SearchError(f"citation is outside the reasoning scope: {doc_id}")
    if "content_hash" in value and value["content_hash"] != doc["content_hash"]:
        raise SearchError(f"stale citation snapshot: {doc_id}")
    quote = value.get("quote")
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 2000:
        raise SearchError("citation quote needs 1..2000 characters of exact source text")
    if "start" in value:
        start = integer(value["start"], "quote start", 0, len(doc["text"]))
    else:
        start = doc["text"].find(quote)
        if start >= 0 and doc["text"].find(quote, start + 1) >= 0:
            raise SearchError(f"ambiguous quote in {doc_id}; supply its character start")
    if start < 0 or doc["text"][start:start+len(quote)] != quote:
        raise SearchError(f"quote is not an exact source substring: {doc_id}")
    return {"kind": "citation", "document": doc_id, "quote": quote,
            "start": start, "end": start + len(quote), "locator": doc["locator"],
            "content_hash": doc["content_hash"], "metadata": doc["metadata"],
            "entailment": "caller-declared; not verified"}


def prepare(index, spec, where, ranges):
    claims = {}
    citation_count = 0
    for c in sequence(spec.get("claims", []), "claims", 64):
        c = options(c, {"id", "text", "support", "oppose", "assumption"})
        cid = ident(c.get("id"), "claim id")
        if cid in claims:
            raise SearchError(f"duplicate claim id: {cid}")
        text = c.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise SearchError("claim text needs 1..4000 characters")
        if type(c.get("assumption", False)) is not bool:
            raise SearchError("claim assumption must be true or false")
        sides = {}
        for side in ("support", "oppose"):
            entries = sequence(c.get(side, []), f"claim {side}", 64)
            citation_count += len(entries)
            if citation_count > 256:
                raise SearchError("reasoning supports at most 256 citation entries")
            unique = {canonical(e): e for e in [citation(index, e, where, ranges) for e in entries]}
            sides[side] = [unique[k] for k in sorted(unique)]
        if c.get("assumption") and (sides["support"] or sides["oppose"]):
            raise SearchError("an assumption must be separate from cited support or opposition")
        claims[cid] = {"id": cid, "text": text, "assumption": c.get("assumption", False), **sides}
    rules, names, deps = [], set(), {c: set() for c in claims}
    for rule in sequence(spec.get("rules", []), "rules", 128):
        rule = options(rule, {"id", "if", "then", "stance", "unless"})
        rid = ident(rule.get("id"), "rule id")
        if rid in names:
            raise SearchError(f"duplicate rule id: {rid}")
        names.add(rid)
        antecedents = sequence(rule.get("if"), "rule if", 8)
        defeaters = sequence(rule.get("unless", []), "rule unless", 8)
        conclusion = ident(rule.get("then"), "rule then")
        if not antecedents:
            raise SearchError("a rule needs at least one premise; declare an assumption explicitly")
        for ref in antecedents + defeaters + [conclusion]:
            if not isinstance(ref, str) or ref not in claims:
                raise SearchError(f"rule {rid} references an unknown claim: {ref}")
        if len(set(antecedents)) != len(antecedents) or len(set(defeaters)) != len(defeaters):
            raise SearchError("rule premises and defeaters must not contain duplicates")
        stance = rule.get("stance", "support")
        if stance not in ("support", "oppose"):
            raise SearchError("rule stance must be support or oppose")
        deps[conclusion].update(antecedents + defeaters)
        rules.append({"id": rid, "if": antecedents, "then": conclusion, "stance": stance, "unless": defeaters})
    # All possible dependencies count, even if a rule would currently be blocked.
    pending = {c: set(ds) for c, ds in deps.items()}
    order = []
    while pending:
        ready = sorted(c for c, ds in pending.items() if not ds)
        if not ready:
            raise SearchError("circular reasoning dependency; supply independent premises and an acyclic graph")
        order.extend(ready)
        for c in ready:
            pending.pop(c)
        for ds in pending.values():
            ds.difference_update(ready)
    goal = spec.get("goal")
    if goal is not None and (not isinstance(goal, str) or goal not in claims):
        raise SearchError("goal must name a declared claim")
    if len({e["document"] for c in claims.values() for side in ("support", "oppose") for e in c[side]}) > 64:
        raise SearchError("reasoning supports at most 64 cited source documents")
    return claims, sorted(rules, key=lambda r: r["id"]), order, deps, goal


class FrontierBudget:
    """One deterministic work allowance shared by the baseline and all removals."""
    def __init__(self):
        self.work = 0

    def spend(self, amount=1):
        self.work += amount
        if self.work > MAX_FRONTIER_WORK:
            raise SearchError("assumption frontier work limit exceeded; narrow the argument contract")


def minimal_sets(paths, budget):
    """Exact antichain, ordered by cardinality then claim ids; never truncate."""
    unique = set()
    for path in paths:
        budget.spend()
        unique.add(frozenset(path))
        if len(unique) > MAX_FRONTIER_CANDIDATES:
            raise SearchError("assumption frontier candidate limit exceeded; narrow the argument contract")
    kept = []
    for path in sorted(unique, key=lambda xs: (len(xs), sorted(xs))):
        redundant = False
        for smaller in kept:
            budget.spend()
            if smaller <= path:
                redundant = True
                break
        if not redundant:
            kept.append(path)
            if len(kept) > MAX_FRONTIER_SETS:
                raise SearchError("assumption frontier set limit exceeded; narrow the argument contract")
    return [sorted(xs) for xs in kept]


def combine_sets(requirements, budget):
    frontier = [[]]
    # Small families first reduce intermediate unions without changing the result.
    for family in sorted(requirements, key=lambda xs: (len(xs), xs)):
        frontier = minimal_sets((set(a) | set(b) for a in frontier for b in family), budget)
    return frontier


def audit(claims, rules, order, goal, remove_document=None, remove_assumption=None, remove_rule=None, budget=None):
    budget = budget if budget is not None else FrontierBudget()
    evaluated, traces = {}, {}
    for cid in order:
        c = claims[cid]
        sides = {side: [dict(e, conditional_on=[]) for e in c[side] if e["document"] != remove_document]
                 for side in ("support", "oppose")}
        if c["assumption"] and cid != remove_assumption:
            sides["support"].append({"kind": "assumption", "claim": cid, "conditional_on": [cid]})
        for rule in (r for r in rules if r["then"] == cid):
            blocked, requirements = [], []
            for pid in rule["if"]:
                premise = evaluated[pid]
                if premise["status"] != "supported":
                    blocked.append({"claim": pid, "role": "premise", "status": premise["status"]})
                else:
                    requirements.append(premise["assumption_sets"]["support"])
            for did in rule["unless"]:
                defeater = evaluated[did]
                if defeater["status"] != "opposed":
                    blocked.append({"claim": did, "role": "defeater", "status": defeater["status"]})
                else:
                    requirements.append(defeater["assumption_sets"]["oppose"])
            removed = rule["id"] == remove_rule
            status = "removed" if removed else "blocked" if blocked else "applied"
            family = combine_sets(requirements, budget) if status == "applied" else []
            conditional = family[0] if family else []
            traces[rule["id"]] = dict(rule, status=status, blockers=blocked,
                                      conditional_on=conditional, assumption_sets=family)
            if status == "applied":
                sides[rule["stance"]].append({"kind": "rule", "rule": rule["id"], "premises": rule["if"],
                                             "defeaters": rule["unless"], "conditional_on": conditional,
                                             "assumption_sets": family})
        yes, no = bool(sides["support"]), bool(sides["oppose"])
        status = "contested" if yes and no else "supported" if yes else "opposed" if no else "unresolved"
        families = {side: minimal_sets((path for e in sides[side]
                    for path in e.get("assumption_sets", [e["conditional_on"]])), budget)
                    for side in ("support", "oppose")}
        evaluated[cid] = {"id": cid, "text": c["text"], "status": status, "assumption": c["assumption"],
                          "assumption_sets": families, **sides}
    assessment = {"goal": goal, "status": "not_assessed", "conditional_on": [], "basis": BASIS,
                  "entailment_verified": False, "status_scope": "declared argument contract",
                  "assumption_sets": [], "assumptions_in_every_path": [],
                  "assumption_analysis": {"complete": True, "scope": ASSUMPTION_SCOPE,
                                          "selection": "fewest assumptions, then lexical claim ids",
                                          "max_sets": MAX_FRONTIER_SETS, "max_candidates": MAX_FRONTIER_CANDIDATES,
                                          "max_work": MAX_FRONTIER_WORK}}
    if goal is not None:
        c = evaluated[goal]
        family = c["assumption_sets"]["support"] if c["status"] == "supported" else c["assumption_sets"]["oppose"] if c["status"] == "opposed" else []
        conditional = family[0] if family else []
        common = sorted(set.intersection(*(set(xs) for xs in family))) if family else []
        assessment.update(status="conditional" if c["status"] == "supported" and conditional else c["status"],
                          conditional_on=conditional, assumption_sets=family, assumptions_in_every_path=common)
    return {"claims": evaluated, "rules": [traces[r["id"]] for r in rules]}, assessment


def fingerprint(report):
    stable = json.loads(canonical(report))
    stable.pop("report_hash", None)
    stable.get("retrieval", {}).get("results", {}).pop("elapsed_ms", None)
    for facet in stable.get("facets", []):
        facet.get("results", {}).pop("elapsed_ms", None)
    return digest(stable)


def validate_report(report):
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or report.get("policy") not in (POLICY, LEGACY_POLICY):
        raise SearchError("expected a supported Surd reasoning report")
    if report.get("report_hash") != fingerprint(report):
        raise SearchError("reasoning report integrity mismatch; rerun against the source index")
    return report


def annotated_search(index, query, opts):
    result = index.search(query, opts)
    terms = list(dict.fromkeys(t for c in result["clauses"] if c["kind"] != "exclude" for t in c["tokens"]))
    for hit in result["hits"]:
        ft = set(index.document_tokens(hit["id"]))
        matched = [t for t in terms if t in ft]
        aliases = {t: [a for a in alternatives if a in ft] for t, alternatives in result["expansions"].items()}
        aliases = {t: xs for t, xs in aliases.items() if xs}
        covered = set(matched) | set(aliases)
        hit["coverage"] = {"matched_terms": matched, "alias_matches": aliases,
                           "missing_terms": [t for t in terms if t not in covered],
                           "fraction": len(covered)/len(terms) if terms else None,
                           "meaning": "lexical query coverage; not evidence strength"}
    return result, terms


def reason(index, question, spec=None):
    if not isinstance(index, ReasoningCorpus):
        raise SearchError("search_reason requires a search index")
    spec = options(spec, {"k", "mode", "where", "ranges", "facets", "claims", "rules", "goal"})
    spec = json.loads(canonical(spec))
    k = integer(spec.get("k", 5), "reasoning k", 1, 20)
    search_opts = {"k": k, "mode": spec.get("mode", "hybrid"),
                   "where": spec.get("where", {}), "ranges": spec.get("ranges", {})}
    results, terms = annotated_search(index, question, search_opts)
    where, ranges = search_opts["where"], search_opts["ranges"]
    claims, rules, order, deps, goal = prepare(index, spec, where, ranges)
    budget = FrontierBudget()
    argument, assessment = audit(claims, rules, order, goal, budget=budget)
    facets, facet_ids = [], set()
    for f in sequence(spec.get("facets", []), "facets", 8):
        f = options(f, {"id", "query", "role"})
        fid = ident(f.get("id"), "facet id")
        if fid in facet_ids:
            raise SearchError(f"duplicate facet id: {fid}")
        facet_ids.add(fid)
        role = f.get("role", "context")
        if role not in ("support", "challenge", "context"):
            raise SearchError("facet role must be support, challenge or context")
        facet_results, _ = annotated_search(index, f.get("query"), search_opts)
        facets.append({"id": fid, "query": f["query"], "role": role, "results": facet_results,
                       "meaning": "retrieval leads; no automatic claim assignment"})
    scoped = list(index.documents(where, ranges))
    vocabulary = {t for d in scoped for t in index.document_tokens(d["id"])}
    missing = [t for t in terms if t not in vocabulary]
    content_missing = [t for t in missing if t not in QUESTION_FRAMING]
    context = {}
    for field in sorted({f for d in scoped for f in d["metadata"]})[:32]:
        values = Counter(canonical(d["metadata"][field]) for d in scoped if field in d["metadata"])
        context[field] = {"values": [{"value": json.loads(v), "documents": n} for v, n in sorted(values.items(), key=lambda x: (-x[1], x[0]))[:10]],
                          "distinct_values": len(values), "missing_documents": sum(field not in d["metadata"] for d in scoped)}
    documents = sorted({e["document"] for c in claims.values() for side in ("support", "oppose") for e in c[side]})
    sensitivity = {"documents": [], "assumptions": [], "rules": [],
                   "meaning": "single removals within this declared contract; not causal effects or proof of necessity"}
    if goal is not None:
        for category, items, key in [("documents", documents, "document"),
                                     ("assumptions", sorted(c for c in claims if claims[c]["assumption"]), "assumption"),
                                     ("rules", [r["id"] for r in rules], "rule")]:
            for item in items:
                after, after_assessment = audit(claims, rules, order, goal, budget=budget, **{"remove_" + key: item})
                sensitivity[category].append({key: item, "goal_after": after_assessment["status"],
                    "conditional_after": after_assessment["conditional_on"],
                    "assumption_sets_after": after_assessment["assumption_sets"],
                    "changes_assumption_paths": assessment["assumption_sets"] != after_assessment["assumption_sets"],
                    "changes_goal": (assessment["status"], assessment["conditional_on"]) != (after_assessment["status"], after_assessment["conditional_on"]),
                    "changed_claims": [c for c in order if argument["claims"][c]["status"] != after["claims"][c]["status"]]})
    relevant = set()
    def ancestors(cid):
        if cid in relevant:
            return
        relevant.add(cid)
        for d in sorted(deps[cid]):
            ancestors(d)
    if goal:
        ancestors(goal)
    next_steps = []
    for cid in order:
        if cid not in relevant:
            continue
        c = argument["claims"][cid]
        if c["status"] == "contested":
            next_steps.append({"kind": "resolve_conflict", "claim": cid, "text": c["text"], "action": "Inspect both cited accounts and verify that version, timing and scope align."})
        elif c["assumption"]:
            next_steps.append({"kind": "verify_assumption", "claim": cid, "text": c["text"], "action": "Find independent evidence for this declared assumption."})
        elif c["status"] == "unresolved" and not any(r["then"] == cid for r in rules):
            next_steps.append({"kind": "missing_premise", "claim": cid, "text": c["text"], "action": "Supply source evidence or leave this premise unresolved."})
    reviewed = {e["document"] for cid, c in claims.items() if cid in relevant
                for e in c["oppose"]}
    for f in facets:
        unseen = [h["id"] for h in f["results"]["hits"] if h["id"] not in reviewed]
        if f["role"] == "challenge" and unseen:
            next_steps.append({"kind": "challenge_unreviewed", "facet": f["id"], "documents": unseen,
                               "action": "Inspect these challenge leads before assigning them to claims."})
    if content_missing:
        next_steps.append({"kind": "vocabulary_gap", "terms": content_missing, "action": "Check wording; add explicit aliases or a scoped alternate facet query."})
    if claims and not any(f["role"] == "challenge" for f in facets):
        next_steps.append({"kind": "challenge_not_searched", "action": "Declare a challenge facet to search for contrary records; citation absence does not establish their absence."})
    if claims and goal is None:
        next_steps.insert(0, {"kind": "goal_not_selected", "action": "Select a declared claim as the goal to assess dependencies and source removal sensitivity."})
    assessment["assumption_analysis"]["work_used"] = budget.work
    report = {"schema": SCHEMA, "policy": POLICY, "question": question, "specification": spec,
              "corpus_hash": index.manifest()["corpus_hash"], "index_hash": index.manifest()["index_hash"],
              "scope": results["scope"], "assessment": assessment, "argument": argument,
              "retrieval": {"results": results, "unmatched_terms": missing, "unmatched_content_terms": content_missing,
                            "framing_terms": [t for t in terms if t in QUESTION_FRAMING], "scoped_documents": len(scoped),
                            "context": context, "context_fields_truncated": len({f for d in scoped for f in d["metadata"]}) > 32,
                            "meaning": "metadata values are supplied snapshots; dates, identity and polarity are not inferred"},
              "facets": facets, "sensitivity": sensitivity, "next_steps": next_steps,
              "verification": {"quotes": "exact source substrings in the indexed snapshot",
                               "rules": "caller-declared; not independently proved",
                               "saved_load": "report content fingerprint only; rerun with original index to revalidate membership and quotes"}}
    report["report_hash"] = fingerprint(report)
    return json.loads(canonical(report))


def render(report):
    """Readable terminal brief; detailed source/rule traces remain in JSON."""
    a = report["assessment"]
    lines = [report["question"], f"Argument: {a['status']}" + (f" · goal {a['goal']}" if a["goal"] else ""),
             "Basis: declared evidence links and rules; quotation matches are checked.",
             "Entailment: caller-declared; inspect every source-to-claim link."]
    if a["conditional_on"]:
        lines.append("Conditional on: " + ", ".join(a["conditional_on"]))
    if len(a.get("assumption_sets", [])) > 1:
        lines.append("Alternative assumption paths: " + " OR ".join(" + ".join(xs) or "unconditional" for xs in a["assumption_sets"]))
    if a.get("assumptions_in_every_path"):
        lines.append("Assumptions in every eligible path: " + ", ".join(a["assumptions_in_every_path"]))
    for cid, c in report["argument"]["claims"].items():
        lines.append(f"  {cid}: {c['status']} · {c['text']}")
        for side in ("support", "oppose"):
            for e in c[side]:
                if e["kind"] == "citation":
                    lines.append(f"    {side}: {e['document']} · {e['locator'] or '(locator missing)'} [{e['start']}:{e['end']}] · {e['quote']}")
                elif e["kind"] == "rule":
                    lines.append(f"    {side}: rule {e['rule']} from " + ", ".join(e["premises"]))
                else:
                    lines.append("    support: declared assumption")
    if report["next_steps"]:
        lines.append("Next checks:")
        for step in report["next_steps"]:
            label = step.get("claim", step.get("facet", ", ".join(step.get("terms", []))))
            lines.append(f"  {label + ': ' if label else ''}{step['action']}")
    hits = report["retrieval"]["results"]["hits"]
    lines.append(f"Search: {report['retrieval']['scoped_documents']} documents in scope · {len(hits)} returned")
    for h in hits:
        lines.append(f"  {h['rank']}. {h['id']} · {h['locator'] or '(locator missing)'} · query coverage {h['coverage']['fraction']:.0%}")
    return "\n".join(lines)
