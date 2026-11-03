# Case reasoning and explanation — Loom 1.18

The case adapter connects source spans to named annotations, existing reviewed
findings, dated roles, budget versions, communications and money stages. Native
bytes and prior graph revisions remain inspectable. Each mapping captures exact
text, source IDs, content hashes, a reviewer and dependency revisions. A changed
source invalidates dependent annotations; recompilation identifies stale items.

## Workflow

1. Import selected documents with `studio import`. For email attachment cells,
   set `attachments: true` and optional `attachment_sheets` in each file object.
   Byte signatures identify OOXML even when MIME is text/plain. Filenames and
   MIME declarations remain recorded. ZIP count/expansion and passage budgets apply.
2. Supply a `studio case-map` packet with `batch_id`, `expected_revision`,
   `reviewer` and `mappings`. Each mapping contains `id`, `episode`, `kind`,
   `context` and 1..20 exact `evidence` spans (`node_id`, `start`, `end`, `quote`).
   Optional `summary` is explicitly named annotation text. `finding` preserves
   external ID, status and exact scoped statement; it does not promote that status.
3. Context fields are actor, property, loan, account, instrument, scope,
   obligation, version, basis_clock and money_stage. Unknown fields stay omitted.
   `role` declares entity/capacity/authority_scope; clocks retain valid intervals.
4. Map original statements, quoted statements and wrappers separately. Knowledge
   states distinguish visible_recipient, transmission_recorded, receipt_confirmed,
   understanding_reported, acknowledged, scoped_approval and later_discovery.
   Scope and exact version are required for approval annotations. Dates and
   knowledge claims are reviewer-supplied interpretations tied to source spans.
5. `studio case-episode --db DB EPISODE` compiles narrative sentences with exact
   source spans, a multi-clock timeline, findings, comparisons, mapping actions
   and the money lifecycle. Unknown dates and stale annotations stay visible.
   `studio explain --db DB QUERY --options '{"episode":"EPISODE"}'` uses this view.

## Comparison and money

`case-compare` accepts left/right case node IDs. Property, loan, scope, obligation,
version, basis_clock and money_stage plus shared claim key must be explicit.
Opposed stances over aligned coordinates produce SUPPORTED_COLLISION in supplied
annotations. Differing coordinates produce OPEN_COORDINATE_COMPARISON; further
review decides whether the difference explains the underlying accounts.

Money mappings need exact integer/string values, event ID, stage, unit and
project/loan/instrument/scope/obligation coordinates. `case-lifecycle` keeps
requested, authorized, funded, received, paid, applied, credited, refunded and
claimed_loss separate. Duplicate observations, conflicting event values and
reversal references remain visible. Totals require an explicit aligned exact
model through the existing analytics/QULAP interfaces.

`case-versions` compares left_hash/right_hash workbook cells under a declared
scope. It reports raw sheet/cell changes; formulas stay unevaluated. Different
layouts require semantic row mapping by a reviewer before financial comparison.

## Recovery and evaluation

`case-recover` accepts source_node, native_sha256, page, text, method, reviewer,
batch_id and expected_revision. It attaches reviewed OCR to a precise current
native page and hash, retaining original extraction and correction dependencies.
An OCR engine runs separately; the runtime never silently invokes cloud OCR.

`case-evaluate` accepts dataset_id, split (development/audit), questions and
optional definition_hash. Each question declares id, query, relevant_ids,
optional forbidden_ids and where filters. Output includes actual top-five
candidates, top1/top5 counts, MRR and forbidden hits. Save the definition hash
before running candidates; designate untouched audit questions independently.

## Interfaces and limits

Language functions: graph_case_map, graph_case_episode, graph_case_lifecycle,
graph_case_compare, graph_case_versions, graph_case_recover, graph_case_evaluate.
The local CLI exposes the corresponding `studio case-*` commands. The API exposes
the same operations: episode/lifecycle use `query`, others use an inline `packet`.
Reader roles can compare/evaluate/read; mapping and OCR require write access.
No local filesystem intake route is added to the HTTP API.

Native case data belongs outside source releases. Named annotation review,
software tests, retrieval relevance, independent comprehension and legal/factual
findings retain their respective assessment requirements. This release supplies
the interfaces; it does not autonomously annotate an entire case or establish
actor knowledge from email headers.
