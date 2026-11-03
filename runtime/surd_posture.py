"""Source-bound posture briefs over caller-declared arguments; no legal scoring."""
import json

from surd_search_core import SearchError, canonical, digest
from surd_reason import FrontierBudget, audit, citation, ident, prepare, reason, sequence

SCHEMA = "surd.search.posture.v1"
POLICY = "record-floor-and-adversarial-branches-v1"
MATERIALITY = {"high": 0, "medium": 1, "low": 2}
BASIS = "Caller-declared evidence links, source families, arguments and branch consequences; no automatic entailment, authenticity, independence or legal outcome assessment."


def obj(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise SearchError(f"{name} must be a map using: {', '.join(sorted(allowed))}")
    return value


def text(value, name, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SearchError(f"{name} needs 1..{maximum} characters")
    return value


def reference(value, claims, name):
    value = ident(value, name)
    if value not in claims:
        raise SearchError(f"{name} references unknown claim: {value}")
    return value


def assessment(argument, cid):
    c = argument["claims"][cid]
    side = "support" if c["status"] == "supported" else "oppose" if c["status"] == "opposed" else None
    families = c["assumption_sets"][side] if side else []
    conditions = families[0] if families else []
    return {"claim": cid, "text": c["text"],
            "status": "conditional" if c["status"] == "supported" and conditions else c["status"],
            "conditional_on": conditions, "assumption_sets": families}


def ancestors(cid, deps):
    seen, todo = set(), [cid]
    while todo:
        item = todo.pop()
        if item not in seen:
            seen.add(item)
            todo.extend(deps[item])
    return seen


def supporting_documents(argument, cid):
    seen, sources, todo = set(), set(), [(cid, "support")]
    while todo:
        key = todo.pop()
        if key in seen:
            continue
        seen.add(key)
        for e in argument["claims"][key[0]][key[1]]:
            if e["kind"] == "citation":
                sources.add(e["document"])
            elif e["kind"] == "rule":
                todo.extend((c, "support") for c in e["premises"])
                todo.extend((c, "oppose") for c in e["defeaters"])
    return sources


def source_groups(index, documents, declared):
    """Merge declared lineages and exact text duplicates; unknown lineage stays pooled."""
    parent = {d: d for d in documents}

    def root(d):
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    families, texts = {}, {}
    for did in sorted(documents):
        family = declared.get(did)  # Missing origins cannot be counted as independent.
        if family in families:
            union(did, families[family])
        else:
            families[family] = did
        content = digest(index.document(did)["text"])
        if content in texts:
            union(did, texts[content])
        else:
            texts[content] = did
    groups = {}
    for did in sorted(documents):
        groups.setdefault(root(did), []).append(did)
    return [{"id": "family-" + digest(docs)[:16], "documents": docs,
             "declared_families": sorted({declared[d] for d in docs if d in declared}),
             "unknown_lineage": any(d not in declared for d in docs),
             "independence_verified": False} for _, docs in sorted(groups.items())]


def evaluate(claims, rules, order, branches, active=(), removed=(), budget=None):
    selected, removed = set(active), set(removed)
    copied = {cid: dict(c, assumption=c["assumption"] and (cid not in branches or cid in selected),
                        **{side: [e for e in c[side] if e["document"] not in removed] for side in ("support", "oppose")})
              for cid, c in claims.items()}
    return audit(copied, rules, order, None, budget=budget)[0]


def fingerprint(report):
    stable = json.loads(canonical(report))
    stable.pop("report_hash", None)
    nested = stable.get("reasoning", {})
    nested.pop("report_hash", None)
    nested.get("retrieval", {}).get("results", {}).pop("elapsed_ms", None)
    for f in nested.get("facets", []):
        f.get("results", {}).pop("elapsed_ms", None)
    return digest(stable)


def validate_report(report):
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or report.get("policy") != POLICY:
        raise SearchError("expected a supported Surd posture report")
    from surd_reason import validate_report as validate_reason
    validate_reason(report.get("reasoning"))
    if report.get("report_hash") != fingerprint(report):
        raise SearchError("posture report integrity mismatch; rerun against the source index")
    return report


def posture(index, question, spec):
    spec = obj(spec, {"argument", "issues", "source_families", "records"}, "posture specification")
    spec = json.loads(canonical(spec))
    contract = obj(spec.get("argument"), {"claims", "rules", "facets", "where", "ranges", "k", "mode"}, "posture argument")
    # The existing engine validates all citations, scopes, references and graph cycles.
    preliminary = reason(index, question, contract)
    where, ranges = contract.get("where", {}), contract.get("ranges", {})
    claims, rules, order, deps, _ = prepare(index, contract, where, ranges)
    raw_issues = sequence(spec.get("issues"), "posture issues", 16)
    if not raw_issues:
        raise SearchError("posture needs at least one issue")
    issues, ids, branches, story_documents = [], set(), set(), set()
    for value in raw_issues:
        value = obj(value, {"id", "title", "goal", "floor", "materiality", "bridges", "opponent", "forks", "coordinates"}, "posture issue")
        iid = ident(value.get("id"), "issue id")
        if iid in ids:
            raise SearchError(f"duplicate issue id: {iid}")
        ids.add(iid)
        goal = reference(value.get("goal"), claims, "issue goal")
        floor = reference(value.get("floor"), claims, "record floor") if value.get("floor") is not None else None
        materiality = value.get("materiality", "medium")
        if not isinstance(materiality, str) or materiality not in MATERIALITY:
            raise SearchError("materiality must be high, medium or low")
        coords = obj(value.get("coordinates", {}), {"actor", "property", "transaction", "stage", "event_date", "statement_date", "knowledge_as_of"}, "issue coordinates")
        for key, val in coords.items():
            if val is not None:
                text(val, key, 500)
        bridges, bridge_ids = [], set()
        for b in sequence(value.get("bridges", []), "issue bridges", 16):
            b = obj(b, {"role", "claim"}, "bridge")
            role = ident(b.get("role"), "bridge role")
            if role in bridge_ids:
                raise SearchError("duplicate bridge role")
            bridge_ids.add(role)
            bridges.append({"role": role, "claim": reference(b.get("claim"), claims, "bridge claim")})
        opponent = obj(value.get("opponent", {}), {"text", "citations"}, "opponent account")
        passages = [citation(index, c, where, ranges) for c in sequence(opponent.get("citations", []), "opponent citations", 8)]
        story_documents.update(c["document"] for c in passages)
        if passages and "text" not in opponent:
            raise SearchError("a documented opponent account needs text")
        opponent = {"text": text(opponent["text"], "opponent account") if "text" in opponent else None,
                    "status": "documented_account" if passages else "hypothetical" if "text" in opponent else "not_supplied",
                    "citations": passages, "entailment_verified": False}
        forks, fork_ids = [], set()
        for f in sequence(value.get("forks", []), "issue forks", 2):
            f = obj(f, {"id", "label", "assume", "goal"}, "posture fork")
            fid = f.get("id")
            if fid not in ("concede", "hold") or fid in fork_ids:
                raise SearchError("fork ids must be distinct concede or hold")
            fork_ids.add(fid)
            active = sequence(f.get("assume"), "fork assumptions", 8)
            for cid in active:
                reference(cid, claims, "fork assumption")
                if not claims[cid]["assumption"]:
                    raise SearchError("a fork may activate only explicitly declared assumptions")
            if not active or len(set(active)) != len(active):
                raise SearchError("each fork needs distinct, explicit branch assumptions")
            branches.update(active)
            forks.append({"id": fid, "label": text(f.get("label"), "fork label"),
                          "assume": sorted(active), "goal": reference(f.get("goal"), claims, "fork goal")})
        if len(forks) == 2 and set(forks[0]["assume"]) & set(forks[1]["assume"]):
            raise SearchError("concede and hold need distinct branch assumptions")
        issues.append({"id": iid, "title": text(value.get("title"), "issue title"), "goal": goal,
                       "floor": floor, "materiality": materiality, "bridges": bridges,
                       "opponent": opponent, "forks": forks, "coordinates": coords})

    documents = sorted({e["document"] for c in claims.values() for side in ("support", "oppose") for e in c[side]} | story_documents)
    if len(documents) > 64:
        raise SearchError("posture supports at most 64 cited documents, including opponent accounts")
    declared = spec.get("source_families", {})
    if not isinstance(declared, dict) or any(d not in documents for d in declared):
        raise SearchError("source_families must map cited documents to origin ids")
    for value in declared.values():
        ident(value, "source family")
    groups = source_groups(index, documents, declared)
    budget = FrontierBudget()
    baseline = evaluate(claims, rules, order, branches, budget=budget)
    # Branch assumptions are never silently treated as the baseline case facts.
    baseline_contract = json.loads(canonical(contract))
    for c in baseline_contract.get("claims", []):
        if c["id"] in branches:
            c["assumption"] = False
    reasoning = reason(index, question, baseline_contract) if branches else preliminary
    removals = [(g, evaluate(claims, rules, order, branches, removed=g["documents"], budget=budget)) for g in groups]
    relevant_by_issue, rows, actions = {}, [], []
    for issue in issues:
        iid, goal = issue["id"], issue["goal"]
        refs = [goal] + ([issue["floor"]] if issue["floor"] else []) + [b["claim"] for b in issue["bridges"]]
        baseline_relevant = set().union(*(ancestors(c, deps) for c in refs))
        relevant = set(baseline_relevant)
        for f in issue["forks"]:
            relevant.update(ancestors(f["goal"], deps))
        relevant_by_issue[iid] = relevant
        current = assessment(baseline, goal)
        floor = assessment(baseline, issue["floor"]) if issue["floor"] else None
        bridges = [dict(b, assessment=assessment(baseline, b["claim"])) for b in issue["bridges"]]
        forks = []
        for f in issue["forks"]:
            arg = evaluate(claims, rules, order, branches, active=f["assume"], budget=budget)
            result = assessment(arg, f["goal"])
            closed = [xs for xs in result["assumption_sets"] if xs and set(xs) <= set(f["assume"])]
            usable = result["status"] == "conditional" and bool(closed)
            forks.append(dict(f, assessment=result, supported_under_stated_branch=usable, branch_supported_assumption_sets=closed,
                              blocked_rules=[r for r in arg["rules"] if r["then"] in ancestors(f["goal"], deps) and r["status"] == "blocked"],
                              kind="conditional scenario; not an observed concession or refusal"))
        sources = sorted({e["document"] for c in relevant for side in ("support", "oppose") for e in claims[c][side]})
        withdrawals = []
        for group, after in removals:
            if not set(group["documents"]) & set(sources):
                continue
            changed = [c for c in sorted(relevant) if assessment(baseline, c) != assessment(after, c)]
            withdrawals.append({"family": group["id"], "documents": group["documents"],
                                "goal_after": assessment(after, goal),
                                "floor_after": assessment(after, issue["floor"]) if issue["floor"] else None,
                                "changes_goal": assessment(after, goal) != current,
                                "changed_claims": changed})
        own_material = sorted(d for d in supporting_documents(baseline, goal) if index.document(d)["metadata"].get("author_side") in ("opponent", "third_party"))
        opposition = [{"claim": cid, "text": claims[cid]["text"], "citations": claims[cid]["oppose"]}
                      for cid in sorted(relevant) if claims[cid]["oppose"]]
        blockers = [r for r in baseline["rules"] if r["then"] in baseline_relevant and r["status"] == "blocked"]
        row = dict(issue, assessment=current, record_floor=floor, bridges=bridges, forks=forks,
                   two_branches_supported=len(forks) == 2 and all(f["supported_under_stated_branch"] for f in forks),
                   sources=sources, related_claims=sorted(relevant), opponent_or_third_party_sources=own_material,
                   source_authorship="caller-supplied metadata; not independently verified",
                   adverse_evidence=opposition, blocked_rules=blockers, family_withdrawals=withdrawals,
                   explanation_band="record_supported" if current["status"] == "supported" else "conditional" if current["status"] == "conditional" else "disputed_or_open")
        rows.append(row)
        for cid in sorted(baseline_relevant - branches):
            state = assessment(baseline, cid)
            if state["status"] in ("contested", "unresolved", "conditional") or claims[cid]["assumption"]:
                actions.append({"issue": iid, "claim": cid, "status": state["status"], "text": state["text"],
                                "action": "Resolve opposing source accounts on aligned scope and clocks." if state["status"] == "contested" else "Obtain evidence for this bridge or retain its stated assumption/open status."})
        for f in forks:
            if not f["supported_under_stated_branch"]:
                actions.append({"issue": iid, "kind": "fork_open", "fork": f["id"],
                                "action": "The proposed branch consequence needs a branch-dependent support path without additional unresolved assumptions.",
                                "assessment": f["assessment"]})
        if not issue["opponent"]["citations"]:
            actions.append({"issue": iid, "kind": "opponent_account_missing", "action": "Inspect the actual opposing account; the supplied alternative is hypothetical or absent."})

    records, record_ids = [], set()
    for r in sequence(spec.get("records", []), "next records", 64):
        r = obj(r, {"id", "claims", "record", "custodian", "identifier", "if_confirms", "if_weakens"}, "next record")
        rid = ident(r.get("id"), "record task id")
        if rid in record_ids:
            raise SearchError("duplicate record task id")
        record_ids.add(rid)
        links = sequence(r.get("claims"), "record claims", 16)
        for cid in links:
            reference(cid, claims, "record claim")
        if not links or len(set(links)) != len(links):
            raise SearchError("a next record needs distinct claim links")
        affected = sorted(iid for iid, refs in relevant_by_issue.items() if refs & set(links))
        if not affected:
            raise SearchError("a next record must address a selected issue")
        records.append(dict(r, **{key: text(r.get(key), key, 2000) for key in ["record", "custodian", "identifier", "if_confirms", "if_weakens"]},
                            issues=affected, claim_states=[assessment(baseline, c) for c in sorted(links)]))
    rank = {"record_supported": 0, "conditional": 1, "disputed_or_open": 2}
    lead_order = [r["id"] for r in sorted(rows, key=lambda r: (rank[r["explanation_band"]], MATERIALITY[r["materiality"]], not bool(r["opponent_or_third_party_sources"]), r["id"]))]
    research_order = [r["id"] for r in sorted(rows, key=lambda r: (MATERIALITY[r["materiality"]], not bool(r["adverse_evidence"]), r["assessment"]["status"] == "supported", r["id"]))]
    records.sort(key=lambda r: (min(research_order.index(i) for i in r["issues"]), r["id"]))
    report = {"schema": SCHEMA, "policy": POLICY, "question": question, "specification": spec,
              "basis": BASIS, "issues": rows, "lead_order": lead_order, "research_order": research_order,
              "ordering": "Explain unconditional record-supported points first, then caller materiality and declared opponent/third-party authorship. Research prioritizes caller materiality and adverse evidence. No strength probability is computed.",
              "source_families": groups, "family_policy": "Declared origins and exact text duplicates merge; unknown origins share one conservative group. Distinct groups do not prove independence.",
              "next_records": records, "open_checks": actions, "branch_assumptions_excluded_from_baseline": sorted(branches),
              "reasoning": reasoning, "corpus_hash": preliminary["corpus_hash"], "index_hash": preliminary["index_hash"],
              "verification": {"quotes": "exact substrings in scoped indexed snapshots", "entailment_verified": False,
                               "engine_reopened_native_sources": False, "legal_elements_assessed": False,
                               "saved_load": "content integrity only; rerun against the source index and reopen native context"}}
    report["report_hash"] = fingerprint(report)
    return json.loads(canonical(report))


def render(report):
    validate_report(report)
    lines = [report["question"], "Case posture: record-supported statements, adverse evidence and open bridges.",
             "Basis: declared evidence/rules; no legal outcome or entailment score.",
             "Lead order: " + ", ".join(report["lead_order"]), "Research order: " + ", ".join(report["research_order"])]
    by_id = {r["id"]: r for r in report["issues"]}
    for iid in report["lead_order"]:
        r = by_id[iid]
        lines += ["", r["title"], f"  Position [{r['assessment']['status']}]: {r['assessment']['text']}"]
        if r["assessment"]["conditional_on"]:
            lines.append("  Depends on: " + ", ".join(r["assessment"]["conditional_on"]))
        if r["record_floor"]:
            f = r["record_floor"]
            lines.append(f"  Record floor [{f['status']}]: {f['text']}")
            if f["conditional_on"]:
                lines.append("    Floor depends on: " + ", ".join(f["conditional_on"]))
        o = r["opponent"]
        lines.append(f"  Opponent [{o['status']}]: {o['text'] or 'not supplied'}")
        for c in o["citations"]:
            lines.append(f"  Opponent source: {c['document']} · {c['locator']} · {c['quote']}")
        for b in r["bridges"]:
            lines.append(f"  {b['role']} [{b['assessment']['status']}]: {b['assessment']['text']}")
            if b["assessment"]["conditional_on"]:
                lines.append("    Bridge depends on: " + ", ".join(b["assessment"]["conditional_on"]))
        for f in r["forks"]:
            lines.append(f"  If {f['id']} ({f['label']}) [{f['assessment']['status']}]: {f['assessment']['text']}")
            lines.append("    Scenario assumptions: " + ", ".join(f["assume"]))
            if f["assessment"]["conditional_on"]:
                lines.append("    Consequence depends on: " + ", ".join(f["assessment"]["conditional_on"]))
        for a in r["adverse_evidence"]:
            for c in a["citations"]:
                lines.append(f"  Adverse: {c['document']} · {c['locator']} · {c['quote']}")
        for did in r["sources"]:
            for cid in r["related_claims"]:
                c = report["reasoning"]["argument"]["claims"][cid]
                for side in ("support", "oppose"):
                    for e in c[side]:
                        if e["kind"] == "citation" and e["document"] == did:
                            line = f"  {side}: {did} · {e['locator']} · {e['quote']}"
                            if line not in lines:
                                lines.append(line)
        for f in r["family_withdrawals"]:
            if f["changes_goal"]:
                lines.append(f"  Withdraw family ({', '.join(f['documents'])}): position becomes {f['goal_after']['status']}")
    lines.append("\nNext discriminating records:")
    for r in report["next_records"]:
        lines += [f"  {r['record']} · {r['custodian']} · {r['identifier']}",
                  f"    If confirms: {r['if_confirms']}", f"    If weakens: {r['if_weakens']}"]
    for a in report["open_checks"]:
        lines.append(f"  Open ({a['issue']}): {a.get('text', '')} {a['action']}")
    return "\n".join(lines)
