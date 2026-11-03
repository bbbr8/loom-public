# Loom 1.14.0 upgrade and validation

The identified retrieval, graph-ranking, evolution, overload and large-graph API failures have been addressed. The local canonical runtime and private full-stack repository use the same 174 fingerprinted runtime files. The previous 1.13.0 source, measurements and failures remain preserved.

## Measured before and after

| Workload | Loom 1.13.0 | Loom 1.14.0 |
|---|---|---|
| SciFact: 5,183 papers, 300 test queries — Recall@10 | 67.62% | **81.49%** |
| SciFact — nDCG@10 | 0.5602 | **0.6707** |
| SciFact — complete top-ten misses | 91 / 300 | **51 / 300** |
| SciFact lexical query median, same M6 host | 63.2 ms | **19.3 ms** |
| NFCorpus: 3,633 abstracts, 323 test queries — Recall@10 | 13.11% | **15.43%** |
| NFCorpus — nDCG@10 | 0.2771 | **0.3255** |
| Real corpus automatic evolution | Partial at 1,000 concepts / 2,000 links | **Complete: 5,883 concepts / 25,915 links**, 16,249 total nodes, no pending work |
| Graph retrieval quality | Concept hubs displaced evidence in the 12-query pilot | **All 300 test queries: Recall@10 81.49%, nDCG@10 0.6707; zero concept result slots** |
| Real graph query median, all 300 queries | Full baseline graph cohort unavailable | **323 ms**, p95 398 ms, using the reusable index |
| 45 direct API requests / 12 clients | 1 completed, 41 HTTP 429, 3 dropped | **1 completed, 44 HTTP 429, zero dropped** |
| 45 queued API requests / 12 clients | Durable job route unavailable | **45 accepted and completed, zero dropped**, 4.19 s in the final graph/job run |
| 20,000-node / 100,000-link HTTP snapshot | HTTP 422 | **HTTP 200, paginated**; cold first page 2.70 s, warmed first page 0.124 s |
| Maximum-size graph journal verification over HTTP | HTTP 422 at about 10 s | **Queued verification succeeds**, 36.7 s |
| Treasury: 1,000 actual daily rows / 3,000 amounts | Exact checks passed | **Still exact; 100/100 injected one-cent errors detected** |
| 100,000 short synthetic documents | Capacity checks passed | **All 851 checks passed**; build 4.29 s, p95 763 ms, process peak about 2.03 GiB |

Recall@10 measures the average fraction of each query's published relevant documents found among ten results. nDCG@10 measures ranking quality. Scientific relevance is distinct from verification of a scientific assertion. NFCorpus has an average 38.2 labeled relevant abstracts per query; its mean ideal Recall@10 ceiling is about 61.5%, which helps interpret its lower recall numbers.

The richer analyzed index has a measurable cost: the 100,000-document synthetic build increased from 2.73 to 4.29 seconds, and its process peak increased from about 1.60 to 2.03 GiB. Query distributions and system load matter; these are bounded engineering measurements, not latency guarantees. Document and byte limits remain explicit. Large journals/diffs still require native exports when they exceed the API output budget.

## Changes delivered

**Retrieval:** Canonical English Porter normalization for unquoted relevance queries, hyphen handling that preserves numeric identifiers, sparse posting scoring, and top-k-only explanations. Literal quoted/required/excluded constraints, metadata filters, identifiers, original text and snippet offsets remain intact. Development-only selection chose equal title/body weights and b=0.45.

**Graph ranking and reuse:** Matching evidence passages rank before graph-only neighbors. Structural concepts are explicit context, rather than evidence result slots. Memory indexes are bounded and keyed by actual rows/configuration; disposable disk accelerators serve repeated worker processes. Dense traversal now respects its reported visit budget exactly.

**Graph evolution:** Concept/link budgets are page sizes. Multi-page commits continue to the full desired plan within explicit budgets. Popular concepts use source-reference shards. Continuations bind to unchanged underlying evidence; pending nodes/edges, time/capacity/pair limits and completeness are visible. The real corpus completed in 13 atomic pages, about 36 seconds.

**HTTP/API:** Valid bounded request bodies are drained before busy responses; connection admission is larger and saturation returns structured Retry-After responses. The synchronous route retains its one-analysis limit; the durable route provides two workers, bounded queues, authenticated retained status/results and idempotent submission identities. Same-graph operations serialize across both routes. Queued work survives restart; interrupted work is flagged without automatic mutation replay.

**Large graphs and browser:** Revision-bound, byte-bounded snapshot/JSON-LD pages replace oversized HTTP snapshots. Page caches accelerate continuation. Expensive verification uses a bounded queued worker. The browser shows queued/running states, can recover a retained identity, displays total graph counts, navigates pages and clearly exports the current page. Desktop 1440×1000 and phone 390×844 layouts were inspected; the exported final page contained the expected five synthetic records.

**Upgrade compatibility:** A saved-recipe regression was reproduced against a pinned 1.14 candidate using two distinct legacy graphs. Evaluation identities and cached research now include the retrieval policy. Old recipes display REVALIDATION_REQUIRED and are excluded from automatic selection until re-evaluated. Re-evaluation preserves old receipts and frozen benchmark labels. Stateful regression scenarios copy their synthetic fixtures, preserving source database bytes.

## Validation and provenance

- Final shipping runtime archive SHA-256: `e708687797c750052821b8ce3fe29be54d0508bbed0a6ebb3927789fa41932b0`.
- Shipping snapshot: full portable release profile, numerical fuzz profile, API suite and durable-job suite passed with zero failed/skipped release checks. The release profile contains 139 check groups; the API and job suites contain 24 and 3 tests respectively.
- Numerical fuzz includes 1,500 malformed programs and independent NumPy comparisons; zero crash timeouts.
- Nine retained curriculum cases / 27 original-transfer-control assertions pass on the installed runtime. Three fixes were promoted through pinned-baseline cycles, including clean failing originals/transfers and passing controls.
- The original complex exact-model, 100,000-document, 20,000-node/100,000-link, HTTP-burst and Treasury workloads were rerun successfully. The finite-model cohort independently checked 20 ten-variable models and every 1/2/3-record bundle; exact arithmetic, transitive staleness, cycle rollback and replay checks remain intact.
- Public data came from the official [BEIR catalog](https://github.com/beir-cellar/beir/wiki/Datasets-available) and [Treasury Debt to the Penny](https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/). The Treasury snapshot spans 2020-01-02 through 2023-12-21. Original bytes and hashes remain retained outside the source repository.
- Parameters were selected only on SciFact's 809 training queries; the 300 test-query labels were excluded from selection. The test set had previously been measured for the baseline, so these are regression measurements rather than an untouched blind audit. NFCorpus was an untuned cross-corpus check.
- Final code matches the evaluated retrieval/model code byte-for-byte and the evaluated graph-search/evolution functions structurally. The local validation delivery retains that code mapping. The final shipping snapshot was separately validated after the saved-recipe repair.
- Heavy work ran on M6 `validation worker`, M4 `validation worker`, and Intel `validation worker`; every returned output was hash-verified. The local delivery retains remote IDs and artifact hashes. The primary Mac ran inspection, small checks and temporary browser testing; that preview was stopped.

Remaining limits are explicit: 51/300 SciFact questions still missed all labeled relevant papers in the top ten; retrieval is not exhaustive. Automatic concepts are lexical candidates. The service uses a shared owner/trusted-team key, rather than tenant authorization. Finite-model guarantees remain conditional on declared inputs and complete traversal. No long-duration production soak, Windows execution, OCR amount extraction or native-case accuracy certification is claimed.

The original 1.13 baseline is retained in Git history. The checked-in `bench/real_eval.py` reproduces the retrieval metric calculations from original BEIR ZIPs; runtime regression guards and the synthetic compatibility fixtures are included in the source distribution. Native case evidence and machine-specific state are excluded.

## Loom 1.15

The final 1.15 source passed 140 portable release check groups, numerical fuzz, 27 API tests, 9 job tests and all 9 retained curriculum cases. Thirty repeated storage/job suite pairs passed on the Intel worker. Public SciFact ranking parity, final first-100 timing confirmation, and the 20,000-node/100,000-link graph checks are documented in [the measured upgrade report](UPGRADE_1_15_RESULTS.md). Warm median graph-query latency fell about 45%; cold startup incurs projection-cache construction.

## Loom 1.16 SQLite bootstrap

142 release groups and numerical fuzz passed. Intel validation passed 17 SQLite tests, 29 API tests, 9 job tests and the packaged native example. A private copy of the 20,000-node/100,000-link graph upgraded without graph/history changes; its backup passed replay. Compact receipts are in [validation-1.16](validation-1.16/summary.json). [SQLite setup guide](../runtime/SQLITE_BOOTSTRAP.md).
