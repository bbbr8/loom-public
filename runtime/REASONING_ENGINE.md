# Reason with inspectable premises

For a multi-issue case brief, use [the posture engine](POSTURE_ENGINE.md). It reuses these argument contracts and adds narrower record floors, adverse accounts, isolated conditional branches, source-origin withdrawals and separate explanation/research priorities.

Ask what a conclusion needs, attach the source passages, and inspect what survives a challenge. Loom checks exact quotations, follows declared rules, keeps conflicts and assumptions visible, and reruns the argument after removing each cited document, assumption and rule.

```sh
loom-lang run ./examples/tools/surd/examples/search_reasoning.surd
loom-lang search reason index.json 'Does this result apply to version 2?'
loom-lang search reason index.json 'Is version 2 ready?' --spec argument.json
loom-lang search reason index.json 'Is version 2 ready?' --spec argument.json --json -o reasoning.json
```

Without an argument contract, the result is `not_assessed`: a retrieval brief with per-hit query coverage, unmatched words, explicit constraints, and the metadata available within scope. It can expose a wording or source-coverage gap before you mistake related records for an answer. Optional facet queries break a broad question into up to eight support, challenge or context searches. Every facet inherits the same metadata filters and ranges.

Vocabulary suggestions separate common question framing (`does`, `this`, `what`) from unmatched content terms. This grouping changes the diagnostic message only: the original query, ranking terms, negation and modal words are retained.

## Declare the argument

Use stable claim ids and exact source quotations. A citation's `document` names an indexed id. Its optional `content_hash` must match that snapshot. Quotes address the document's unmodified `text`; repeated passages need an explicit zero-based character `start`.

```json
{
  "where": {"version": 2},
  "claims": [
    {"id":"passes", "text":"Version 2 passed the test.",
     "support":[{"document":"test-log", "quote":"Version 2 passed the test."}]},
    {"id":"target", "text":"Version 2 is the target.",
     "support":[{"document":"specification", "quote":"Version 2 is the target."}]},
    {"id":"ready", "text":"The target is ready under the declared test rule."}
  ],
  "rules": [{"id":"readiness", "if":["passes","target"], "then":"ready"}],
  "facets": [{"id":"challenge", "query":"failed", "role":"challenge"}],
  "goal":"ready"
}
```

This is a declared argument, not an automatically proven interpretation. You choose whether a passage supports or opposes a claim and whether a rule is sound. The engine verifies that the quotation occurs in the scoped snapshot; it does not certify the passage's truth, authenticity, or entailment. It preserves locator, offsets, content hash and metadata for review. This distinction follows the provenance distinction between quotation and derivation in [W3C PROV-O](https://www.w3.org/TR/prov-o/#wasQuotedFrom); this release does not claim RDF/PROV conformance.

## Read the result

| Status | Meaning within the declared argument |
|---|---|
| `supported` | At least one support path and no opposition path |
| `opposed` | At least one opposition path and no support path |
| `contested` | Both support and opposition paths; neither disappears through averaging |
| `unresolved` | No usable path; missing evidence is not treated as false |
| `conditional` | A supported goal depends on declared assumptions |
| `not_assessed` | No goal selected; retrieval alone does not answer the question |

Add opposing quotations under `oppose`. Duplicate citations do not strengthen an argument. A rule applies only when every `if` premise has supported status without opposition. A contested premise blocks that rule. Rules may produce opposition with `"stance":"oppose"`.

An optional `unless` list names defeater claims. Each defeater must have opposed status to clear the rule: an uninvestigated exception remains open; a supported or contested exception blocks it. This conservative policy is intentional. There is no implicit closed-world assumption or inference from a missing search hit.

Declare an assumption as `{"id":"scope","text":"The versions align.","assumption":true}`. Keep it separate from cited evidence. Assumptions can support conditional paths, and their ids propagate through the trace. An unconditional path clears the goal's assumption dependency. For an opposed goal, inspect `conditional_on` as well: opposition can also depend on assumptions.

The v2 argument policy retains every subset-minimal assumption union of eligible derivations. `assessment.assumption_sets` lists these alternatives in increasing cardinality, then lexical claim-id order; `conditional_on` selects the first. Each claim exposes separate support and opposition families, and each applied rule retains its alternatives. A direct citation has the unconditional family `[[]]`; no eligible path has `[]`. A contested goal has no selected family: inspect both sides of its claim trace.

For example, P follows from A or B, Q follows from B or C, and the goal requires P and Q. Picking a local path too early gives A+B. The engine instead finds `[["b"],["a","c"]]`: B is shared by both premises, while A+C is an alternative. `assumptions_in_every_path` gives the intersection of these families, not a real-world necessity claim. Run `loom-lang run examples/assumption_paths.surd` for this fixture.

These are assumption unions within the currently evaluated graph, not minimum logical models. Rules remain skeptical: contested premises block propagation. Removing other assumptions can resolve a conflict and enable a new opposing rule, so the listed family does not guarantee the same goal status after simultaneous removals. The removal audit reruns the entire graph and captures such changes. This analysis does not verify prose entailment or establish source independence.

The analysis fails explicitly rather than truncating when a frontier exceeds 1,024 minimal sets, a pruning batch exceeds 16,384 distinct candidate unions, or the complete report (including all removals) exceeds 2,000,000 counted candidate/subset operations. Successful reports mark this bounded derivation analysis `complete:true` and record its scope, limits and work used. Narrow the argument contract if a limit is reached.

The graph must be acyclic, including defeater edges. Circular support, unknown references, empty-premise rules, stale source hashes, invented quotations and citations outside scope fail with structured errors. Limits are 64 claims, 128 rules, eight premises and eight defeaters per rule, 256 citation entries, 64 cited documents, eight search facets and 20 returned hits per facet.

## Find the weak bridge

`argument.rules` shows applied and blocked rules with the responsible premises or defeaters. `next_steps` names missing premises, conflicts, assumptions, unmatched vocabulary, and unreviewed challenge results. These checks are local to the selected goal's dependency graph. Challenge retrieval does not silently become an opposing factual claim.

`sensitivity` removes each cited document, assumption and rule in turn and reruns the whole graph. It reports the goal's resulting status, changed claims and `assumption_sets_after`. `changes_goal` retains its existing meaning: status or selected `conditional_on` changed. `changes_assumption_paths` also catches a lost alternative when the selected path stays unchanged. A second path may survive a removal. Removing contrary evidence can also strengthen a result, so inspect the direction of change. These are single removals within your argument; they do not establish real-world causal effects, source independence, or a minimum cut set.

Version, source type, date and stage metadata remain visible in `retrieval.context`, with unknown counts and explicit truncation. Use `where` and `ranges` to enforce the scope you intend. The engine does not infer that two versions, actors, dates or transaction stages refer to the same proposition.

## Program and retain it

```surd
let r = search_reason(ix, "Is version 2 ready?", spec)
print r["assessment"]
print r["argument"]["rules"]
print r["sensitivity"]
search_save(r, "reasoning.json")
let retained = search_load("reasoning.json")
expect retained["report_hash"] == r["report_hash"]
```

Saved reports include the complete specification, quotations, traces, source fingerprints and retrieval results. Their fingerprint excludes only the known search latency fields. `search_load` checks this content fingerprint; it does not reopen original sources. Original v1 reports remain readable under their recorded v1 policy; loading them does not upgrade their historical reasoning. Rerun `search_reason` against the original index to revalidate source membership, quotations and current v2 reasoning. Content hashing detects corruption, not an outside author's false evidence assignment.

`tests/test_reasoning.py` covers source validation, conflict, missing premises, assumptions, defeaters, alternative paths, scope, cycles, persistence, CLI and interpreter integration. `tests/test_assumption_frontiers.py` adds shared assumptions, longer local paths, unconditional dominance, opposition, defeaters, nonmonotonic removals, explicit limits and 225 overlapping-path contracts. `tests/reasoning_oracle.py` enumerates all proof combinations independently, without the engine's incremental pruning, and checks seeded DAGs plus every single removal. These measure contract mechanics; natural-language entailment and independently judged real-world reasoning quality remain unevaluated.

For explicit scenario superpositions and exact phase-oracle exploration, use [quantum reasoning](QUANTUM_REASONING.md). Its circuit measurements describe the declared model; the attached baseline preserves source support and open bridges.
