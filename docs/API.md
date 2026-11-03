# Loom API v3 — synchronous analysis and durable jobs

`GET /` serves the browser workbench. `GET /health` reports runtime version, storage configuration and queue availability. All `/v1/` routes require `Authorization: Bearer <key>`. POST bodies use `Content-Type: application/json`; duplicate keys and non-finite JSON are rejected.

## Short synchronous work

`POST /v1/analyze` returns `{"ok":true,"result":{...}}`. It uses a single admission slot, 10 CPU / 12 wall seconds and a 4 MiB output limit. Inline search, reasoning and standalone finite models use transient inputs on this route. Graph operations retain their declared state. Engine states such as PARTIAL, OPEN_INPUT, HELD and INCOMPLETE remain inside the result. HTTP success means evaluated, rather than complete or factually verified.

```json
{"operation":"search","documents":[{"id":"s1","text":"Synthetic credit applied.","locator":"synthetic:s1"}],"question":"credit"}
```

`search`, `reason`, `posture`, `quantum` accept inline documents and question; the latter three also accept a structured `spec`. Unquoted relevance ranking uses English morphology; quoted phrases, required/excluded terms, exact identifiers and metadata filters stay literal. Graph search returns evidence passages before generated concept nodes. External semantic rankings, where used by the runtime, must be explicitly aligned to the exact query and graph snapshot.

## Durable queued work

Submit the same operation packet to `POST /v1/jobs` with an additional `Idempotency-Key` header. Keys contain 8–128 ASCII letters, digits, dots, underscores, colons or hyphens. The service returns HTTP 202 and `{"ok":true,"job":{"id":"...","state":"queued","status_url":"/v1/jobs/..."}}`. Poll that authenticated status URL. States are queued, running, succeeded, failed and interrupted. A succeeded job includes its `result`.

A repeated key with identical content returns the original job, including its completed result. Different content under that key returns 409. If a submission response is lost, resubmit the **same key and body** to recover its identity. The browser retains the identity before sending, shows queue progress and provides Check retained job after a connection interruption. Its retained identity lives in page memory; preserve the job ID yourself before closing/reloading a page when needed.

Two bounded workers execute queued jobs. Operations on the same graph are serialized across both HTTP routes. Queued work survives a service restart. Previously running work becomes `interrupted` and is never automatically replayed; a mutation may already have committed. Inspect its graph and batch IDs before making a deliberate new submission. No autonomous retry of an uncertain mutation occurs.

Jobs use 120 CPU / 125 wall seconds, 4 MiB output, and a 3 GiB address-space limit on Linux. macOS does not enforce that address-space cap; CPU/wall limits and bounded work/input contracts still apply. Use queued work for expensive `graph_verify` and multi-page `graph_evolve`. `graph_evolve` jobs default to up to 64 atomic pages / 90 seconds; inspect remaining work, continuation and explicit limits. Further calls with the returned continuation require unchanged underlying evidence.

Configure `LOOM_JOB_DIR`, or let it default to `LOOM_DATA_DIR`. A private `.loom-jobs/jobs.sqlite` stores job identities, statuses, requests until completion, and completed results. Completed request bodies are removed; result text/snippets, queries and metadata persist. The queue is bounded to 64 pending/running jobs, 512 history entries and 256 MiB retained payloads. Capacity exhaustion returns an explicit archive-required state; no history is silently deleted. Operator backups may retain earlier contents.

## Graph operations and pagination

Every graph operation includes `"graph":"simple-name"`. Names use 1–64 ASCII letters, digits, hyphens or underscores, beginning with a letter or digit. The operator configures `LOOM_DATA_DIR`; clients never supply filesystem paths. Graph init, ingest, research run and research learn initialize an absent graph. Native programs, arbitrary SQL/file intake and corpus adapters remain local CLI capabilities.

| Operations | Additional fields |
|---|---|
| `graph_init`, `graph_verify`, `graph_journal` | none |
| `graph_snapshot`, `graph_export` | optional inline `options`: page `limit`, `cursor`, and supported snapshot/actor/time options |
| `graph_overview`, `graph_resolve`, `graph_analyze`, `graph_evolve`, `research_methods` | optional inline `options` |
| `graph_search`, `graph_paths` | `query`, optional `options`; paths use a starting node ID as query |
| `graph_ingest`, `graph_compile`, `research_run`, `research_learn`, `graph_propose_predicate`, `graph_activate_predicate` | inline `packet` |
| `graph_diff` | `before`, optional `after` revisions |
| `graph_withdraw` | `origin`, unique `batch_id` |

HTTP graph initialization, snapshots and JSON-LD exports return **pages**, default limit 200 and maximum 1,000 records, with byte bounds. `pagination` contains total node/edge counts, offset, returned count, next_cursor and complete. Pass the next cursor with the same limit/view to continue. An incompatible graph revision or view change rejects the cursor; restart pagination. An empty `edges` array on an early node page is a page boundary, rather than a claim that the graph has no relationships.

Disposable private caches reuse compiled search indexes and graph pages; source/configuration/revision changes invalidate them. Native `graph_snapshot` retains the full runtime contract. Very large journals or diffs can still exceed the structured output budget; use the native CLI to export those full artifacts. A queued job increases compute time, rather than removing output caps.

Edited sources need higher revisions; changed batches need new batch IDs. Repeating an unchanged research packet reuses a cached result; evidence or retrieval-policy changes invalidate that cache. Source, candidate, reviewed assertion and research-result states remain distinct.

## Standalone models

`research_plan`, `qulap_solve`, `qulap_core`, `qulap_sensitivity` take an inline QULAP `packet` and optional operation-specific `options`. Units, money stages, assumptions and sources remain explicit. Exact planning requires complete enumeration within the declared budget.

## Admission, errors and access

Request limit: 256 KiB and 100 inline documents. Synchronous attempts: 30/minute; queued submissions: 120/minute. Status polling is authenticated and separate. The server admits up to 64 HTTP connections and returns structured Retry-After responses on saturation. Direct synchronous overload returns 429; use the queued route to retain work.

401: check key. 409: idempotency or graph-revision conflict. 413: narrow input. 422: inspect schemas, storage or pagination; the response may identify a result-limit condition. 429/503: queue/rate/admission capacity. 408: execution resource/time budget reached. Job failure/interruption and mutation timeouts require inspecting the graph before a deliberate retry. Worker isolation is for structured operations, rather than arbitrary-code execution.

One shared key grants owner-level access to every graph and retained job. Graph names are namespaces; this release does not provide separate accounts or tenant isolation. Keep hosted services behind HTTPS and configure durable operator-owned storage.

## Conditional ingest in 1.15

`graph_ingest` additionally accepts `options` with `expected_revision` and `evolution` (defaults true). For example: `{"operation":"graph_ingest","graph":"project","packet":{"schema":"loom.graph.batch.v1","batch_id":"edit-002","nodes":[]},"options":{"expected_revision":12,"evolution":false}}`. A revision mismatch returns 409 and leaves the batch unapplied. A previously committed identical batch remains idempotent. Concurrent evolution stops with `replan_required:true` and an explicit restart limit. Earlier committed pages remain inspectable.

One live POSIX service owns each queue directory; a second owner receives 503. Stop pre-1.15 services before upgrading. The queue skips busy graphs while retaining per-graph FIFO, and shutdown retains ownership until running work finishes. See [operating contracts](ARCHITECTURE_1_15.md).

A synchronous call to a busy graph returns 429 immediately. A queued worker may wait up to 15 seconds for an already admitted synchronous graph operation before returning a busy failure; its subprocess budget then applies separately.

`GET /v1/jobs` returns authenticated worker counts, backlog by state, oldest queued age and capacity. It excludes request/result bodies. Graceful SIGTERM/keyboard shutdown drains running jobs for up to 145 seconds. Queued worker subprocesses inherit the ownership descriptor: if the service dies first, a new owner waits for the old bounded worker to exit before recovery.

## Research Studio operations (1.17)

New read operations: `graph_hybrid_search`, `graph_explain`, `graph_inspect_record`, `research_next`. Each accepts a named `graph`, `query` (a record ID for inspection) and optional `options`. `graph_propose_relations` and `graph_review_relation` accept inline `packet` objects. `graph_suggest_relations` accepts `options` with `after_revision` and `limit`. These work through both synchronous and durable routes. Native file intake, model directories and restore destinations remain local CLI operations.

`LOOM_ACCESS_FILE` optionally supplies hashed tokens with reader/editor/admin roles and named graph grants. Readers can perform read operations; editors can mutate permitted graphs; operator diagnostics require admin access. Job status retains project scope after request payload removal; inline jobs additionally check their submitting principal. The legacy operator key remains administrative. See [configuration and operator archive/restore commands](../runtime/RESEARCH_STUDIO.md).

Indexed search is bounded to its candidate pool and is opt-in; it preserves hard query constraints after semantic fusion. `semantic_state=NOT_CONFIGURED` means FTS-only. Supplied semantic rankings must align to the query and current commit; caller-provided model hashes describe declared provenance. Offline CLI indexing computes full installed-model fingerprints. The initial case-cohort semantic blend remains experimental because it underperformed the lexical baseline.
