# Loom 1.15 architectural upgrade

Loom 1.15 strengthens concurrent graph research, durable execution and repeated graph queries. The canonical runtime and private full-stack source preserve the prior release and its historical receipts. This is engineering and retrieval-performance tuning; it does not train model weights or change the lexical ranking policy.

## Measured real-data results

The BEIR SciFact graph contains 16,249 nodes and 25,915 links. It completed deterministic evolution in 13 atomic pages. All 300 upgraded query rankings exactly matched the retained 1.14 rankings: Recall@10 81.49%, nDCG@10 0.6707, and 51 complete misses. This preserves measured retrieval behavior; it adds no accuracy gain.

The same M6 host, graph bytes and first 100 sorted test queries were used for the timing comparison. The first cold query is excluded from warm statistics. Full GraphStore.search calls include reading/materializing the graph and retrieval, so these timings differ from earlier benchmarks that searched an already materialized snapshot.

| Measurement | 1.14 | Development 1.15 |
|---|---:|---:|
| Warm median, common 99-query cohort | 1345.8 ms | 724.9 ms |
| Warm p95, common cohort | 1496.6 ms | 858.0 ms |
| First cold query | 3740.7 ms | 3926.5 ms |

Warm median wait fell 46.1% (1.86x median-latency ratio). Cold startup was slightly slower because it builds the projection cache. The final shipping build independently confirmed all 100 rankings and a warm median of 737.9 ms / p95 870.8 ms on that cohort: 45.2% lower median latency (1.82x ratio). Its first cold query was 3986.9 ms. The original train/test selection and source snapshots remain unchanged; these are regression checks on a previously measured test set, not a blind accuracy audit or a calibrated probability estimate.

## Implemented architecture

1. **Coherent graph reads.** Explicit read transactions bind item rows and their journal revision. Verification pins an online SQLite backup before replay, preserving consistency while writers continue.
2. **Guarded graph updates.** Optional expected revision/projection preconditions are checked inside the atomic write transaction. Research saves, recipe activation/evaluation, source withdrawal and evolution pages guard their read basis. Evolution stops and requests replanning after concurrent evidence changes, preserving prior committed pages.
3. **Disposable read projections.** Search and cold pagination reuse commit-head-bound snapshots. Cache files have bounded decompression, checksums, private permissions, atomic publication and corruption fallback. Replacing a database at the same numeric revision invalidates its read models. Native snapshots and integrity verification bypass caches.
4. **Exclusive, fair durable jobs.** Ownership is acquired before startup reconciliation. Runnable-graph claims skip a busy graph while preserving FIFO within that graph. Graph reservations are removed after persistence. Service death retains ownership in a surviving queued worker until it exits; interrupted mutations remain outcome-unknown and are never automatically replayed.
5. **Bounded operation and diagnostics.** Synchronous busy graphs return promptly. Workers enforce their own CPU and wall limits, including after parent death. Graceful server termination drains bounded active jobs. Authenticated queue status exposes liveness, backlog, age and capacity without returning request/result bodies.

## Reproduced defects and validation

Pinned 1.14 counterexamples reproduced mixed revision/row reads, stale cached pages after same-revision replacement, false verification failure during a later valid write, competing-owner interference and head-of-line blocking. The repaired storage suite has twelve tests, with independent expected outcomes, distinct transfer cases and controls. Thirty repetitions of the original twelve storage and eight job tests passed on the Intel worker (600 test executions). The final job suite additionally tests an orphan worker retaining its lock, and the final API suite covers conditional ingest, queue diagnostics and prompt busy responses.

The nine existing DSL curriculum cases remain retained. Concurrency faults are retained in the mandatory Python guard suite; the program-based improvement-case format does not itself express concurrent clients. These checks are synthetic software oracles, kept distinct from the public real-corpus ranking measurements.

The final release and fuzz validation, maximum graph run, final read confirmation, source mapping, routing checks and exact Git commit are recorded in the accompanying JSON receipts. Earlier successful and failed development snapshots are retained. Only public SciFact data and synthetic test graphs were used. Native case sources, working investigation graphs, model weights and global memory were unchanged.

## Research sources and operating limits

SQLite's [isolation model](https://www.sqlite.org/isolation.html) supports explicit consistent reads and serialized conditional writes. Python's [online backup contract](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup) supports pinned verification during access by other clients. AWS's [idempotent API guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) informs logical identities and deliberate treatment of uncertain side effects. The [BEIR dataset catalog](https://github.com/beir-cellar/beir/wiki/Datasets-available) identifies the public retrieval benchmark.

Graph limits remain 20,000 nodes / 100,000 links and explicit byte/history/solver budgets. Queue storage remains bounded to 64 pending jobs, 512 history entries and 256 MiB of retained payloads; exhausted history requires explicit operator archival. Access remains one shared trusted-team key. POSIX local-file locks are required, and pre-1.15 services must stop before upgrade. Read models duplicate source/derived text on the same private operator-owned storage. Cache artifacts are disposable and do not replace original data or backups. There is no production duration soak or multi-tenant isolation claim.

## Final shipping checks

- Full portable release: 140 check groups passed, zero failed/skipped.
- Numerical fuzz: both groups passed, including malformed programs and independent numerical comparisons.
- Final API: 27 tests passed; durable jobs: 9 tests passed; storage architecture: 12 tests passed as a mandatory release guard.
- Retained curriculum: 9 cases / 27 assertions passed.
- Maximum graph: 20,000 nodes / 100,000 links; first 1,000-record page 2.97 s, second page 0.11 s; cached HTTP search 1.41 s; pinned replay verification 36.73 s.
- Real queued burst: 45/45 requests completed across twelve clients, no transport failures; completed identities recovered without repeating work.
- Heavy validation ran on validation worker, validation worker and validation worker, with fetched output hashes verified. Local work covered source edits, small checks and the native language demonstration. No persistent preview server or recurring automation was created.

The shipping source fingerprint and module mapping are recorded in SHIPPING_BUILD.json and VALIDATED_CODE_MAP.json; VALIDATION_RECEIPTS.json retains all job IDs, actual hosts and output hashes.

Compact inspectable receipts: [summary](validation-1.15/summary.json), [release/fuzz checks](validation-1.15/validation.json), [source mapping](validation-1.15/source-map.json), [real-data benchmark](validation-1.15/real-benchmark.json), [shipping confirmation](validation-1.15/shipping-read.json), [maximum graph](validation-1.15/maximum-graph.json) and [validation output hashes](validation-1.15/validation-receipts.json). Full logs remain retained in the local task outputs.
