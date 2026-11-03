# Unified Loom 1.12

`loom` and `loom-lang` share the same runtime. Quantum/analytical Loom, the former
Strata timeline dialect, and QULAP alternative-state analysis are available together.
`surd` and the standalone `qulap` command retain their compatible entry points.

```sh
loom version
loom run examples/unified.loom
loom timeline examples/timeline.loom --json
loom -e 'around 2020-01-02 days 3; hidden; mail; ghosts; timeline'
loom timeline old-program.loom --store /path/to/hidden.sqlite --ledger /path/to/money.sqlite
loom qulap solve examples/data/qulap-credit.model.json
loom qulaps core examples/data/qulap-contradiction.model.json
loom qulap sensitivity examples/data/qulap-mechanism.model.json
loom qulap quantum examples/data/qulap-mechanism.model.json --proposition owner_funding_required
```

An explicit `loom timeline FILE` selects the timeline dialect. Legacy `loom FILE`
and `loom -e PROGRAM` remain supported. `loom run FILE` inspects the first meaningful
statement, since both dialects use `.loom`; use explicit `timeline` for unusual or
empty timeline programs. `loom run` retains analytical syntax and `.surd` support.

## Compose within a Loom program

```loom
let solved = qulap_solve("examples/data/qulap-credit.model.json")
expect solved["feasible_states"] == 3
expect solved["proof_effect"] == "NONE"
let analysis = qulap_sensitivity("examples/data/qulap-mechanism.model.json")
let simulation = qulap_quantum("examples/data/qulap-mechanism.model.json",
                              {"proposition": "owner_funding_required"})
let dated = timeline_run("window 2020-01-01 .. 2020-01-31; hidden; timeline",
                         {"store": "/path/to/hidden.sqlite", "max_rows": 1000})
```

QULAP built-ins accept a JSON path or packet map. `qulap_solve(packet, options?)`
accepts `max_nodes`, `seconds`, `retain_worlds`. `qulap_core` and
`qulap_sensitivity` accept `max_nodes`, `seconds`. `qulap_quantum` also accepts
`proposition`; it uses this release's adjacent simulator. `qulap_allocations` and
`qulap_bind` compile their existing packet contracts. `qulap_asof(packet, actor,
cutoff)` preserves receipt-bounded projection. Results preserve QULAP's exact
integer units, completeness, witnesses, source annotations and `proof_effect`.
Use `loom qulap --help` for the full CLI, including read-only cube intake, evidence
annotations, witness export/replay, binding and actor receipt projection.

## Timeline contract

The former `loom.strata` commands are supported: `window`, `around`, `layers`,
`hidden`, `mail`, `ghosts`, `money ledger [min N]`, `timeline [limit N]`, `count`,
`collide [days N]`, `save PATH`, `say TEXT`. Quoted semicolons stay inside strings.
`save` creates a new JSONL file exclusively. Existing files are preserved.

Supply the timeline path with `--store` or `LOOM_TIMELINE_STORE`, and the ledger
path with `--ledger` or `LOOM_LEDGER`. Stores are never discovered automatically.
Missing stores raise an error; the tool never creates or updates input databases.
Document-family configuration is described in `DOCUMENT_CONFIGURATION.md`.

`timeline_run(program, options?)` accepts `store`, `ledger`, `max_rows` (1..100000,
default 10000), and `seconds` (0..60 exclusive at zero, default 5). The JSON result
contains events, time precision, raw source dates, date roles, source locators,
collisions, intake coverage, unknown-date row observations and diagnostics. A row
or event limit returns `PARTIAL`; timeout returns an error. CLI exit 3 identifies
partial intake; exit 2 identifies invalid inputs or unavailable stores.

Date-only money records keep day precision. Zone-qualified timestamps normalize
to UTC. Unknown/unzoned dates remain in coverage counts. Thread-message matching
uses ±120-second proximity across the inspected store and leaves message identity
open. Collisions describe temporal proximity. Repeated loads can repeat coverage
observations; counts are not a census of unique source rows.

## Packaging and lineage

QULAP 0.2.0 is embedded as `loom_qulap`, with upstream file hashes in
`loom_qulap/UPSTREAM.json`; the standalone installation is preserved. The timeline
implementation is ported from the retained `tools/loom/loom.strata`; the original
Strata interpreter program remains available. Portable builds include the new
engines, synthetic fixtures and tests, with executable Loom launchers. They contain
no timeline store or native ledger. Existing private-release restrictions still apply.

The release gate tests actual extracted launchers, synthetic read-only timeline
intake, embedded QULAP regressions, independent finite-state enumeration, quantum
parity and witness replay. Classical finite-state analysis, circuit simulation,
source navigation and factual review each retain their own meaning.

SQLite setup: [bootstrap, inspection, backup and native program functions](SQLITE_BOOTSTRAP.md). Use `loom sqlite init --db project.sqlite --demo` for the synthetic starter graph.
