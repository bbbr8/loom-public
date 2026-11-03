# Loom SQLite bootstrap

Create a persistent Loom research database with one command. Python 3.9+, SQLite FTS5 and SQLite JSON support are required; no database server or extra Python package is needed.

```sh
./bin/loom sqlite init --db .loom-data/research.sqlite --demo
./bin/loom sqlite inspect --db .loom-data/research.sqlite --check
./bin/loom graph search --db .loom-data/research.sqlite 'release credit'
./bin/loom graph verify --db .loom-data/research.sqlite
./bin/loom sqlite backup --db .loom-data/research.sqlite --out .loom-data/research-backup.sqlite
```

In the full-stack checkout use `./loom` instead of `./bin/loom`. Configure `LOOM_DATA_DIR` to the absolute `.loom-data` directory and use `"graph":"research"` in authenticated API requests. Job storage is created separately by the existing service when first used. The new filesystem bootstrap commands/functions are local capabilities; the HTTP API retains named graphs and never accepts arbitrary database paths.

Omit `--demo` for an empty production graph. `--demo` installs three explicitly synthetic source/passage pairs and grows their lexical/provenance graph to 17 nodes and 15 links. Billed, approved and applied amounts retain distinct stages. An existing populated graph receives no demo records unless it is already the matching bootstrap demo. Repeating a completed initialization is idempotent and leaves database bytes unchanged.

## Programming interface

```text
let db = "/absolute/project/research.sqlite"
let initialized = sqlite_bootstrap(db,{"demo":true})
expect initialized["state"] == "READY"
let inspected = sqlite_status(db,{"check":true})
expect inspected["bootstrap_schema"] == 1
let saved = sqlite_backup(db,"/absolute/project/new-backup.sqlite")
expect saved["revision"] == inspected["revision"]
```

`sqlite_bootstrap(path, options?)` accepts only Boolean `demo`. `sqlite_status(path, options?)` accepts only Boolean `check`. `sqlite_backup(path, new_destination)` refuses an existing destination. These are explicit local file operations; native source-read APIs retain their read-only contracts. The bundled `examples/sqlite_bootstrap.loom` reads the synthetic starter fixture without changing it.

## Schema and SQL

`loom sqlite schema` prints the schema. `loom sqlite schema --out NEW.sql` saves it with exclusive creation. `sqlite-bootstrap.sql` is the same generated SQL and can initialize a **new empty** database directly:

```sh
sqlite3 new-research.sqlite < sqlite-bootstrap.sql
```

Use the Loom initializer for existing files: it checks ownership, native tables, schema version and migration/view fingerprints before adding anything. Raw SQL is a lower-level route for empty databases; it lacks the Python adapter's existing-file preflight and private atomic publication.

The graph retains its existing `loom.graph.store.v1` contract and native tables: `meta`, `commits`, `items`, `versions`, `dependencies`, `adjacency` and FTS5 `node_text`. The additive bootstrap includes a SHA-256 migration ledger, application ID `0x4c4f4f4d`, user version 1, and three inspection views:

| View | Contents |
|---|---|
| `loom_node_rows` | Stored node revisions, source IDs, clocks, text, origin, locator and JSON data |
| `loom_edge_rows` | Stored link revisions, subject/predicate/object and provenance |
| `loom_journal_rows` | Commit identities, exact journal timestamps and hash-chain references |

```sql
SELECT node_type, COUNT(*)
FROM loom_node_rows
WHERE materialized_active=1
GROUP BY node_type;

SELECT id, json_extract(data_json,'$.amount_cents') AS cents, locator
FROM loom_node_rows
WHERE node_type='passage'
  AND materialized_active=1
  AND json_extract(data_json,'$.money_stage')='applied';
```

SQL inspection exposes **stored materialization**. `materialized_active` and `stored_status` retain that meaning. Native `graph_snapshot` calculates effective staleness, dependency closure and actor/time eligibility. A stored active passage can be excluded from a native snapshot after its source changes; SQL counts alone do not imply current evidence eligibility. Avoid direct SQL writes to native journal/materialization tables; use graph batches so revisions, dependency captures and replay remain consistent.

## Preservation and recovery

New databases and backups use private temporary files and atomic no-overwrite publication. Linked database files/sidecars and unrelated or incompatible databases are rejected. Existing native graphs can receive additive bootstrap metadata/views while preserving their journal and graph projection. Changed migration hashes or view definitions require explicit review, rather than silent replacement.

Backups use SQLite's online backup operation and return a SHA-256, graph revision and commit hash. They capture a consistent revision while other clients may write. Run `graph verify` against a backup to check full journal replay separately. Inspection with `--check` runs bounded SQLite `quick_check`; that check has a different scope from replay. Source graphs, archived releases and backups remain distinct artifacts.

This is a SQLite setup for Loom. The separate Bootstrap mathematical-invention language remains independently installed.

Reference: [SQLite online backup](https://www.sqlite.org/backup.html), [application/user version pragmas](https://www.sqlite.org/pragma.html), and [JSON functions](https://www.sqlite.org/json1.html).
