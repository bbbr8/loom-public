# Loom 1.13 research engine

Loom now has a persistent analytical research graph, historical and actor-specific
views, reversible entity links, source-backed graph retrieval, exact diagnostics,
graph-to-QULAP compilation, complementary record planning, and measured recipe and
relation-schema evolution. Everything runs locally with Python's standard library.

## Start

```sh
loom graph init --db /absolute/project/research.sqlite
loom graph ingest examples/data/research-graph.json --db /absolute/project/research.sqlite
loom graph search 'blue invoice' --db /absolute/project/research.sqlite
loom graph overview --db /absolute/project/research.sqlite
loom graph verify --db /absolute/project/research.sqlite
loom research demo --directory /absolute/new-demo-directory
loom research plan examples/data/qulap-credit.model.json --options '{"max_bundle":2}'
loom research run examples/data/research-run.json --db /absolute/project/research.sqlite
```

Commands use the explicitly bound graph when `--db` is omitted; otherwise the
default path is `state/research.sqlite` beside this runtime. Initialize a new path
once. Existing stores are preserved; `init` refuses replacement.

Bind a project graph for ordinary Loom source searches:

```sh
loom research bind --db /absolute/project/research.sqlite
loom search query index.json 'credit memo' --json
loom research unbind
LOOM_RESEARCH_DB=/absolute/project/research.sqlite loom search query index.json 'credit memo'
```

The binding is local configuration in `state/research-config.json`. Environment
`LOOM_RESEARCH_DB` overrides it, including the empty string to disable capture.
`search(...)` inside Loom programs and `loom search query` capture up to 50 returned
indexed document snapshots, create source/concept/identifier links, and retain the
query receipt. Distinct indexed versions remain separate observations. Missing
native locators, supplied origin IDs and unverified source independence stay visible.
This optional capture warns on failure and preserves the original query result.
There is no background process, external inference, recursive corpus scan or index
mutation. Portable validation explicitly disables the binding.

## Graph inputs, revisions and exact provenance

`loom.graph.batch.v1` has `batch_id`, arrays `nodes`, `edges`, `remove_nodes` and
`remove_edges`. Every object has a stable `id` and positive integer `revision`.
Identical batches are idempotent; reusing a batch id with other content fails.
Changed objects must increase their revisions. Deletions retain tombstones and
block stale resurrection. All prospective references and dependency cycles are
checked before committing the transaction.

```json
{
  "schema":"loom.graph.batch.v1",
  "batch_id":"intake-001",
  "nodes":[
    {"id":"source-A","type":"source","origin":"native-family-A","locator":"original/path:42"},
    {"id":"observation-A","type":"passage","label":"Source passage",
     "text":"The source's exact words.","source_ids":["source-A"],
     "identifiers":[{"kind":"invoice","namespace":"vendor/project","value":"INV-42","unique":true}],
     "clocks":{"event":["2020-01-01","2020-01-03"],"receipt":{"Actor A":["2020-01-04","2020-01-04"]}}}
  ]
}
```

Node types: source, passage, entity, instrument, event, claim, concept, hypothesis,
variable, constraint, metric, proposition, model, analysis, question, search,
method, benchmark, correction and predicate. Logical spaces are `evidence`,
`hypothesis`, `research`. Statuses preserve observed/supplied annotations,
candidates, hypotheses, derived outputs and proposed/active/held/retired methods.
Source status `observed` describes the ingested observation, not authenticity.

Edges have `subject`, `predicate`, `object`, `source_ids`, `status`, optional scope,
clocks/data and explicit `depends_on:[{"kind":"node|edge","id":"..."}]`.
Source, endpoint and explicit dependencies capture object revisions. A changed or
withdrawn dependency stales transitive derived objects. Research answers and recipes
also retain an evidence-projection fingerprint, so new contrary material makes an
older analytical result stale even when its original citations remain unchanged.
Stale outputs remain inspectable and are excluded from default analytical views.

Source families share `origin`; duplicate source copies remain one family for
lineage reporting. Different origins carry no automatic independence guarantee.
Derived model results and query outputs occupy research/hypothesis space and are
kept distinct from source observations. SHA-256 chains detect corruption and drift;
they are not authenticated signatures against a malicious local writer.

## Explicit file and timeline intake

`loom graph ingest-files MANIFEST --db DB` reads `loom.graph.files.v1`:

```json
{"schema":"loom.graph.files.v1","id":"project-docs","files":[
  {"path":"/absolute/source.md","origin":"source-family-A"}
]}
```

This route computes SHA-256 over the actual UTF-8 bytes read and records the byte
count/extraction. It accepts up to 100 explicit files, each at most 1 MiB and
100,000 characters, with 16 MiB total. It never modifies originals. Changed files
increment derived graph revisions; stable bytes are idempotent. Ordinary supplied
record hashes remain explicitly caller-supplied until a reader computes them.

`loom graph import-timeline REPORT --batch-id ID --db DB` preserves each timeline
event's date role, precision, source locator and upstream coverage. Limit intake to
1,000 events. Date-only money records retain day precision.

## Time and correction queries

```sh
loom graph asof --db DB --options '{"revision":3}'
loom graph asof --db DB --options '{"recorded_before":"2020-05-01"}'
loom graph asof --db DB --options '{"actor":"Actor A","receipt_before":"2020-05-01"}'
loom graph asof --db DB --options '{"event_before":"2020-05-01","unknown":"retain"}'
loom graph diff --db DB --before 2 --after 4
loom graph withdraw --db DB --origin source-family-A --batch-id withdrawal-001
```

`event`, `statement`, `valid` and actor `receipt` are separate clock intervals.
Timestamps need zones and normalize to UTC. A date-only cutoff includes that whole
day; a date-only valid-time query tests overlap with that day. Receipt must be
established by the interval's latest bound. Unknown actor receipt is excluded from
the actor view and counted as open; it never becomes negative knowledge. Receipt
is distinct from understanding, assent and authorization. Temporal views also close
over every captured dependency, so an earlier-labeled derivative cannot import a
later or unavailable source into that actor/time view. Historical recorded-time
snapshots remain available after source correction or withdrawal.

`verify` replays the entire hash chain into a fresh store and compares current
state, historical versions, adjacency, dependency and text indexes. `journal`
exports `loom.graph.journal.v1`; `replay INPUT --db NEW_DB` restores into a fresh
destination. `export --format jsonld` emits JSON-LD 1.1 with PROV derivation links
and typed reified assertions. No full SHACL or OWL conformance is claimed.

## Automatic evolution and retrieval

Ingestion and completed research intake evolve lexical concept nodes, source
provenance links, exact namespaced shared-identifier candidates and multi-field
entity candidates. This is bounded and versioned. Concepts represent observed
word usage, and shared identifiers retain their declared namespace/scope.
`loom graph evolve` recomputes the bounded projection and changes only affected
stored objects; stable results are idempotent. Partial evolution retains coverage
diagnostics and preserves preceding links instead of claiming completeness.

`graph search` reuses Loom's BM25/identifier/phrase query semantics. Required and
excluded terms and quoted phrases remain constraints. It adds reciprocal-rank
seeds and bounded personalized graph diffusion, returning actual source-linked
edge paths. Scope filters restrict traversal. Optional external semantic rankings
must carry the exact query and graph snapshot hash; no embedding model loads
automatically. Retrieval weights are relevance scores, not factual probabilities.

`graph paths START --options '{"end":"TARGET","max_hops":3}'` returns typed paths.
`graph overview` reports exact connected components, degree hubs and lexical topic
nodes within its work budget. These are structural diagnostics; degree and origin
counts carry no evidentiary strength or causation meaning.

`graph resolve` uses exact declared keys, multi-field comparisons and blocked
candidate matching. Names or amounts alone do not merge identities. Accepted
`same_entity` edges require an explicit `data.reviewed:true` annotation and compatible
entity types. Canonical views follow accepted links; raw IDs survive. Deleting an
alias edge reverses the view without destroying records.

## Quantitative and claim analytics

`graph analyze` compares explicitly annotated claim key/scope/clock/stance and
quantities with aligned measure/unit/stage/scope/instrument/version/clock. Exact
quantity values use integer or rational/decimal strings; float inputs are refused.
Source-pair conflicts, repeated event observations and open axes remain separate.
Declared linear invariants return exact residuals. An invariant crossing axes is
rejected; model an explicit sourced bridge with QULAP for cross-stage questions.

## Compile the graph into finite models

Graph variable/constraint/metric/proposition nodes have `data.reviewed:true` and a
matching `data.variable`, `data.constraint`, `data.metric`, `data.proposition` entry
in the existing QULAP format. `source_ids` reference active graph source nodes.
Bridge source IDs must also be in the graph provenance of the selected statements.

```json
{"id":"credit-model","nodes":["variable:issued","variable:applied",
 "variable:refunded","constraint:single_disposition","metric:unresolved_credit",
 "proposition:credit_not_delivered","proposition:double_satisfaction"],
 "source_tier":"synthetic"}
```

`loom research compile SPEC --db DB` returns the compiled model, source mapping,
graph/object hashes and revisions. Missing, stale or unreviewed inputs return
`OPEN_INPUT`. `allow_hypotheses:true` allows explicit hypothetical inputs and lists
them as assumptions. QULAP checks finite domains, exact units, lanes and bridges.
These are declared interpretations; review flags are supplied annotations.

## Cost-aware joint-record planning

`research plan` accepts an ordinary QULAP model plus options. Default records come
from model variables with `next_record`. Explicit candidates declare `id`,
`variables` and exact string/integer `cost`. Set one objective metric; other metrics
retain their own units. Options include `max_bundle`, `max_bundles`, `max_worlds`,
`budget` and ranking `gain_per_cost` or `guaranteed_gain`.

The planner requires complete model traversal and all retained feasible worlds.
For every evaluated bundle it partitions worlds by possible acquired observations,
computes worst remaining metric widths, exact guaranteed reductions, gain/cost,
complementarity and the cost/gain Pareto frontier. Incomplete traversal abstains;
bundle/time/work limits return partial coverage. No outcome prior or global greedy
optimality guarantee is assumed. The synthetic credit demo finds that applied and
refunded together remove a 60,000-cent uncertainty that either single record leaves
unchanged in the worst case.

## Measured methods and schema evolution

`research learn EVALUATION --db DB` takes a frozen query set, baseline/candidate
recipe parameters, scope and optional activation request. Every query has relevant
node IDs, optional full-chain labels, and context `original`, `transfer` or
`adversarial`. Empty labels require explicit `answerable:false`. Both recipes use
the same cutoff. The engine executes actual searches and compares nDCG, recall,
chain coverage and known-absence retrieval. All three contexts and all regression
gates must pass before activation. Failed candidates remain held and preserve the
preceding active recipe. Ordinary JSON graph batches cannot bypass the activation
gate by supplying an active method node. Frozen benchmark IDs refuse changed labels. Supplied
labels and authorship remain visible; independence is never inferred from a name.
Active scoped recipes are used by `research run` and stale when the evidence
projection changes. This learns tested configurations, not model weights.

`graph propose` records a new predicate name and subjects/objects/source-required
shape. `graph activate` requires the candidate hash plus distinct original,
transfer and adversarial trial edges, including positive transfers and a negative
control. Actual structural validation must match every expectation. Activation is
rechecked by the store transaction. A successful schema gate establishes a typed
relation contract; semantic truth remains the supplied interpretation. Old schema
revisions stay in history. Built-in predicates are immutable.

## Complete research runs and language functions

`loom.research.run.v1` includes an `id`, explicit `records` or `batch`, bounded
`questions`, optional scoped recipe selection, model specification, planning
options and declared numerical invariants. A run ingests/evolves, searches,
diagnoses, resolves, compiles, solves and plans as requested; it persists search
and analysis receipts with their dependencies and open questions. Identical inputs
on unchanged evidence/methods reuse the retained run. New evidence stales preceding
outputs, and rerunning records a new analytical revision.

Language functions: `graph_init`, `graph_ingest`, `graph_ingest_files`,
`graph_import_timeline`, `graph_snapshot`, `graph_diff`, `graph_withdraw`,
`graph_verify`, `graph_search`, `graph_paths`, `graph_overview`, `graph_resolve`,
`graph_analyze`, `graph_compile`, `graph_evolve`, `research_plan`, `research_run`,
`research_learn`, `research_methods`, `graph_propose_predicate`,
`graph_activate_predicate`. Graph functions take an explicit path first; packet
arguments accept a map or JSON path, and optional settings are maps.

## Bounds and validation

Store caps: 20,000 active nodes, 100,000 active edges, 10,000 commits and 250,000
historical versions, with a 64 MiB current projection and 256 MiB combined history/journal payload budget. Batches have at most 4,096 entries per array and 16 MiB total.
Replay refuses more than 10,000 commits. Search/path/overview work, identity pairs,
concept/link evolution, planner worlds/bundles/time and query evaluations have
explicit bounds. Stale or partial outputs retain their state. These are trusted
local analytical tools, not an untrusted-code sandbox or a calibrated factual model.

Portable release tests include adversarial regression cases and independent
brute-force record-bundle, historical projection, replay and concurrent-writer
oracles. Frozen before/after sources and actual host receipts are saved in the task
workspace. Native-corpus effectiveness, semantic extraction quality, calibrated
matching, web-service security and externally judged research quality remain
separate evaluations.
