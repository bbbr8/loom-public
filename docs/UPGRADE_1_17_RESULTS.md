# Loom 1.17 research studio results

The release adds incremental FTS5 retrieval, an offline semantic adapter,
source-span relation proposals and named review, context-preserving document
intake, structural next-record planning, browser source exploration and
operator recovery/project access controls. The installed dynamic ontology
upgrade is preserved with its inherited shapes and bounded expansion.

## Bounded private real-data cohort

Seventy-one extracts from eight retained source files were evaluated with twelve
frozen same-agent relevance questions. Native records remain outside this source
repository; the reproducible private manifests and receipts stay with their owner.

| Method | Relevant source first | Relevant source within five | Mean reciprocal rank |
|---|---:|---:|---:|
| Existing lexical graph search | 10/12 | 12/12 | 0.903 |
| Indexed SQLite search | 12/12 | 12/12 | 1.000 |
| Initial offline semantic blend | 10/12 | 10/12 | 0.833 |

The semantic blend remains experimental. It is available explicitly through the
local adapter; default browser explanations use indexed lexical retrieval. A
scalar CrossEncoder adapter is implemented, but a real reranker run remains
unverified because a suitable local model was unavailable.

Eight cohort checks passed: exact excerpts, native locators, forwarding and
reconstruction context, spreadsheet cell/heading/formula preservation,
discriminating next-record tests, wrong-scope hard filtering, verified restore
and withdrawal/stale-semantic invalidation. Five PDF pages need OCR or sparse-text
review. The native PDF reader also processed ten explicitly selected pages from
files up to approximately 462 MiB without loading the whole file into memory.

These results measure retrieval and context preservation on a small selected
cohort. Independent human comprehension, full-case accuracy and legal conclusions
remain separately assessed. Source-to-question graph review has one native
example; general relation precision remains open. Research priorities are
structural and acquire no automatic source-truth status.

## Synthetic scale

A dedicated Intel validation worker evaluated 10,000 short synthetic passages / 20,000
active nodes at the current graph cap. Warm median query time fell from 780.5 ms
to 93.6 ms, a measured 8.34x speedup. The cold indexed query took 104.0 ms;
additive indexing took 272.6 ms. Five unique-anchor checks passed.

This compares a bounded 400-candidate SQL read with the full-projection search
path. Ranking policies differ; full ranking parity and million-row graph/model
support are outside these measurements. The graph retains its existing node,
edge, byte, history and solver budgets.

## Validation and placement

Runtime release/fuzz and retained curriculum checks run on the faster validation mini.
API, queue, access/archive and scale checks also run on the Intel worker. The
native offline embedding evaluation runs on the corpus mini. Native byte reads,
small local reader checks, browser rendering and machine-wide routing validation
remain on the primary validation worker. Hash-verified validation receipts are retained by the
owner.

An initial Intel fuzz case reached its five-second child deadline; the complete
fuzz profile passed on the faster worker. The first API pass exposed a changed
duplicate-key status; the established 422 response was restored and retested.
Original failed receipts remain retained. No queued job is counted as complete.

## Boundaries

- Explanations are source-linked extractive drafts with visible open context.
- Named review records an interpretation; automatic suggestions stay candidates.
- Offline model loading requires explicitly installed local models and optional
  dependencies. No model download or hosted inference occurs automatically.
- Per-project tokens enforce API access; direct filesystem access remains an
  operator boundary.
- Restore publishes a fresh destination after replay/integrity checks. It preserves
  existing files. Job archival preserves idempotency tombstones; old mutations
  receive an archived-state conflict rather than replay.
- Existing `graph_search` remains available; indexed retrieval is an explicit new
  operation with its own disclosed candidate and projection scope.

Shipping validation passed 145/145 release groups, 2/2 fuzz groups, and 27 retained
assertions across nine active curriculum cases. The studio suite contains 19
behavior checks; the preserved ontology suite contains 13. The API suite passed
29 checks, the queue suite nine, and project-access/archive checks seven. Seven
browser interaction/layout checks passed at desktop and mobile widths. The
strict source routing scan reported zero high findings and zero warnings.
