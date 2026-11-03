-- Loom SQLite bootstrap v1. Empty database only. Synthetic data is separate.
-- SQL views expose stored rows; use graph_snapshot for effective stale/temporal states.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commits(seq INTEGER PRIMARY KEY,batch_id TEXT UNIQUE NOT NULL,
 input_hash TEXT NOT NULL,recorded_at TEXT NOT NULL,previous_hash TEXT NOT NULL,
 commit_hash TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
 active INTEGER NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,seq INTEGER NOT NULL,
 PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS versions(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
 active INTEGER NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,seq INTEGER NOT NULL,
 PRIMARY KEY(kind,id,revision));
CREATE INDEX IF NOT EXISTS history_by_seq ON versions(seq,kind,id);
CREATE TABLE IF NOT EXISTS dependencies(kind TEXT,id TEXT,dep_kind TEXT,dep_id TEXT,
 dep_revision INTEGER,dep_hash TEXT,PRIMARY KEY(kind,id,dep_kind,dep_id));
CREATE INDEX IF NOT EXISTS reverse_dependencies ON dependencies(dep_kind,dep_id);
CREATE TABLE IF NOT EXISTS adjacency(id TEXT PRIMARY KEY,subject TEXT,predicate TEXT,object TEXT);
CREATE INDEX IF NOT EXISTS outgoing ON adjacency(subject,predicate);
CREATE INDEX IF NOT EXISTS incoming ON adjacency(object,predicate);
CREATE VIRTUAL TABLE IF NOT EXISTS node_text USING fts5(id UNINDEXED,label,text);

INSERT INTO meta(key,value) VALUES('schema','loom.graph.store.v1') ON CONFLICT(key) DO NOTHING;
CREATE TABLE IF NOT EXISTS loom_bootstrap_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS loom_node_rows AS SELECT id,revision,active AS materialized_active,
 json_extract(payload,'$.type') AS node_type,json_extract(payload,'$.label') AS label,
 json_extract(payload,'$.text') AS text,json_extract(payload,'$.origin') AS origin,
 json_extract(payload,'$.locator') AS locator,json_extract(payload,'$.space') AS space,
 json_extract(payload,'$.status') AS stored_status,
 json_extract(payload,'$.data') AS data_json,json_extract(payload,'$.clocks') AS clocks_json,
 json_extract(payload,'$.source_ids') AS source_ids_json,
 json_extract(payload,'$._deps') AS captured_dependencies_json,content_hash,seq
 FROM items WHERE kind='node';
CREATE VIEW IF NOT EXISTS loom_edge_rows AS SELECT id,revision,active AS materialized_active,
 json_extract(payload,'$.subject') AS subject,json_extract(payload,'$.predicate') AS predicate,
 json_extract(payload,'$.object') AS object,json_extract(payload,'$.status') AS stored_status,
 json_extract(payload,'$.source_ids') AS source_ids_json,
 json_extract(payload,'$.data') AS data_json,content_hash,seq FROM items WHERE kind='edge';
CREATE VIEW IF NOT EXISTS loom_journal_rows AS SELECT seq,batch_id,input_hash,recorded_at,previous_hash,commit_hash FROM commits;
INSERT INTO loom_bootstrap_migrations VALUES(1,'native-graph-and-inspection-views','f21b59f33cfaff1fc3f9f21900a8da5090e7bd818d05c244d85972516adf71c6',strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(version) DO NOTHING;
PRAGMA application_id=1280266061;
PRAGMA user_version=1;
COMMIT;
