# Loom 1.15 architecture

The application remains a portable standard-library service. Search, finite models, graph contracts and provenance remain in the runtime; HTTP and durable execution are adapters. This release improves consistency and reuse without introducing another database service or an embedding dependency.

## Consistent graph reads and conditional writes

A snapshot reads its journal revision and item rows inside one explicit SQLite read transaction. Concurrent writes therefore produce a coherent old or new view. Verification uses SQLite online backup to pin a complete database copy, then replays and compares that copy; live writers can continue without producing a false corruption diagnosis.

GraphStore.apply accepts optional expected_revision and expected_projection preconditions. Checks run inside BEGIN IMMEDIATE before mutation. A conflict rolls back the complete batch. An already committed batch with identical content retains its idempotent receipt, even when its original revision precondition is now old. Preconditions affect admission, rather than changing historical journal payloads or replay hashes.

Evolution binds every atomic page to its read revision and compares underlying evidence between pages. Concurrent changes stop the old plan with replan_required, an explicit limit and no reusable continuation. Earlier committed pages remain recorded. Research saves, recipe evaluations, predicate activation and source withdrawal also guard their read basis before installing derived work. These controls cover cooperative journaled writes; external SQL edits remain detectable through verification.

HTTP graph_ingest accepts options {"expected_revision":N,"evolution":false}. A conflict returns HTTP 409 graph_revision_conflict. Read the latest graph, recompute the intended change, and use a new logical operation identity deliberately. Never retry an uncertain mutation with a different identity automatically.

## Disposable read models

Search and cold pagination can reuse a private graph projection. Cache identities include the commit-chain head and exact view options, rather than only a numeric revision. Replacing a graph with a different history at the same revision invalidates both projection and page caches. A coherent prior snapshot is a valid read while a concurrent writer commits; it is never labeled with the writer's newer revision.

The projection artifact has a SHA-256 checksum, a 32 MiB compressed cap and a 128 MiB expanded cap, private file permissions and atomic replacement. Corrupt, incompatible, oversized, linked or unavailable artifacts fall back to an authoritative snapshot. Only one projection artifact per graph is retained; different views may evict one another. The cache duplicates source/derived text on the same operator-owned storage. Native snapshots, write planning and verification bypass it. Delete disposable caches only while coordinating readers if storage needs explicit maintenance.

## Durable execution ownership and scheduling

Exactly one live POSIX service process owns a job directory. An OS advisory lock is acquired before startup recovery, so a second instance cannot mark another owner's live work interrupted. The ownership descriptor is inherited only by queued worker subprocesses. Service death therefore retains the lock until those bounded workers exit; recovery cannot overlap an orphan mutation. Running operations remain outcome-unknown after a crash and are marked interrupted by the next owner; queued work resumes. Upgrading from a pre-1.15 process requires stopping the old service first because old versions do not participate in this ownership protocol.

Two workers reserve runnable graphs when claiming a job. Work for a busy graph stays queued; an unrelated graph can use the free worker. FIFO is preserved within each graph. Reservations are removed after persistence, keeping scheduler memory bounded. The database claim remains transactional. Shutdown stops claims/admission and retains ownership until running executions finish. The server handles SIGTERM and keyboard interruption with a bounded 145-second drain. A short close timeout reports undrained work; it does not release ownership early. Storage failures stop admission and require owner restart/reconciliation.

One shared service key still grants trusted-team access. This is one bounded queue process with concurrent workers, not a distributed or multi-tenant job platform. POSIX local-file lock semantics are required. An authenticated GET /v1/jobs reports worker liveness, backlog counts, oldest queued age and capacity without exposing request/result bodies. Queue/history, graph, solver and resource budgets remain explicit.

## Research basis

- SQLite explains read transactions, serializable writers and BEGIN IMMEDIATE: https://www.sqlite.org/isolation.html
- Python's SQLite interface documents online backups while other clients access the database: https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup
- AWS describes logical client identities and handling retries without repeating side effects: https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/

These references support architectural choices. Repository tests and measured reports establish the behavior actually checked in Loom. Retrieval scoring and model algorithms are unchanged in 1.15; performance and ranking parity require their own measurements.
