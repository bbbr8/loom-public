# Dynamic ontology format for Loom

An executable ontology contract defines domain-specific types and relationships on top of Loom's existing graph. It supports canonical formatting, schema revisions, closed nested shapes, inheritance, validation, bounded rule expansion and source-preserving graph projections. It uses Python's standard library; it requires no service, model, credentials or third-party package.

The schema is `loom.ontology.v1`; data is `loom.ontology.instances.v1`. Files carry an explicit ontology ID and positive integer version. New versions are explicit files. The engine never rewrites a graph or migrates instances automatically.

## Run it

From the Loom runtime directory:

```sh
bin/loom-lang ontology check examples/data/ontology-schema.json
bin/loom-lang ontology format examples/data/ontology-schema.json --out canonical-schema.json
bin/loom-lang ontology validate examples/data/ontology-schema.json examples/data/ontology-instances.json
bin/loom-lang ontology expand examples/data/ontology-schema.json examples/data/ontology-instances.json
bin/loom-lang run examples/ontology.loom
```

CLI `--out` saves JSON. Exit 0 means the requested contract check passed; 1 means instance validation failed; 2 means malformed input, unavailable files or an execution limit. Expansion reports `complete` separately: false means the round cap was reached and closure has not been established, even when the partial expansion validates. CLI output files are ordinary explicit writes; choose versioned output paths when preserving earlier reports.

The language functions are:

```text
ontology_check(schema)
ontology_validate(schema, instances)
ontology_expand(schema, instances)
ontology_project(schema, graph_snapshot)
ontology_diff(before_schema, after_schema)
```

Arguments accept JSON paths or Loom maps. The Python module `loom_research.ontology` exposes the same functions as `check`, `validate`, `expand`, `project`, and `diff`; `compile_ontology` returns canonical schema, resolved inherited properties, ancestor sets and SHA-256. Python `expand` also accepts `max_rounds` and `max_derived`.

## Define types and shapes

```json
{
  "schema": "loom.ontology.v1",
  "id": "organization",
  "version": 1,
  "types": {
    "Named": {
      "abstract": true,
      "properties": {"name": {"kind": "string", "required": true}}
    },
    "Person": {
      "extends": ["Named"],
      "properties": {
        "balance": {"kind": "decimal"},
        "manager": {"kind": "reference", "target": "Person"},
        "profile": {
          "kind": "object",
          "properties": {"active": {"kind": "boolean", "required": true}}
        },
        "tags": {"kind": "array", "items": {"kind": "string"}, "max_items": 8}
      }
    }
  },
  "relations": {
    "reports_to": {"domain": ["Person"], "range": ["Person"], "functional": true, "acyclic": true}
  }
}
```

Multiple inheritance is supported. Inheritance cycles, unknown parents, inherited disjoint types and incompatible property definitions are rejected. A child may repeat an inherited shape exactly; changing its meaning requires a separate type or schema revision. `abstract` types cannot be instantiated but can serve as reference and relationship targets. A type's `disjoint` list declares types with which it cannot share an inheritance ancestry. Instances have one declared type and its inherited ancestors; union-typed instances are not supported.

Properties are closed at every level. Supported kinds:

| Kind | Accepted value |
| --- | --- |
| `string` | JSON text |
| `integer` | JSON integer, excluding booleans |
| `boolean` | `true` or `false` |
| `decimal` | Exact decimal string, such as `"123.45"`; binary floats and exponent notation are rejected |
| `date` | ISO date or timezone-aware timestamp; naive timestamps are rejected |
| `reference` | Existing instance ID whose type inherits `target` |
| `object` | Nested closed `properties` map |
| `array` | Values matching `items`, with `min_items`/`max_items` |
| `enum` | Exact JSON member of the `enum` list |

`required` controls presence, while `nullable` independently permits explicit null. Defaults are false. Missing values are never silently filled. Decimal strings preserve spelling and precision; the format does not attach currency or units automatically. Declare currency, unit or stage as separate typed properties where meaningful.

## Instance format

```json
{
  "schema": "loom.ontology.instances.v1",
  "ontology_id": "organization",
  "ontology_version": 1,
  "nodes": [
    {"id": "a", "type": "Person", "properties": {"name": "A"}},
    {"id": "b", "type": "Person", "properties": {"name": "B"}}
  ],
  "edges": [
    {"id": "ab", "subject": "a", "predicate": "reports_to", "object": "b", "source_ids": ["source-1"]}
  ]
}
```

Nodes and edges may retain `source_ids`, `clocks`, `status`, and `native`. Existing Loom clock validation applies, including actor-specific receipt intervals. `native` retains caller-supplied context without treating it as a validated proof. Source IDs are opaque provenance identifiers here; this engine does not resolve their existence or truth. Status annotations also remain caller supplied. Validation is about declared shapes, not epistemic or legal sufficiency.

Validation returns stable node/edge ordering, canonical instance and ontology hashes, and path-specific issues. It detects unknown fields and types, duplicates, missing required values, invalid nested values, references, dangling endpoints, domain/range mismatches, functional conflicts and cycles. Domain/range lists accept any listed ancestor. Functional means one distinct object per subject and predicate, with duplicate assertions to that same object allowed. Unknown and missing context remain explicit.

## Bounded dynamic relations

Relations can declare `inverse`, `symmetric`, `functional`, and `acyclic`. Inverse endpoint types must reverse, and symmetric endpoint types must match. Two-premise rules compose relationships:

```json
{"id": "management-chain", "chain": ["reports_to", "reports_to"], "infer": "ancestor"}
```

The inferred relation must be declared. `expand` repeatedly applies inverses, symmetry and declared chains, then validates the complete output against the same contract. A resulting domain/range, functional or cycle conflict remains a failure. No natural-language interpretation or arbitrary expression execution occurs.

Every generated edge has `status: derived`, a deterministic ID, unioned source IDs, and `native.rule`, `native.premise_ids`, and `native.round`. One deterministic derivation per subject/predicate/object is retained; this is not an exhaustive proof enumerator. Clocks and actor knowledge are not inferred from sources or premises. Self-relations are allowed unless a declared acyclic constraint rejects them.

Always expand the original base assertions again after changes. Refeeding a previous expansion is rejected because its derived edges could otherwise outlive their premises. Derived results are returned as data and never committed to the graph automatically.

Bounds: 16 MiB packets, 256 types/relations/properties per map, 16 nesting/inheritance levels, 4,096 nodes and edges per instance packet, 64 two-relation rules. Expansion defaults to 8 rounds, at most 4,096 derived edges and 200,000 rule candidates; Python can select 1–32 rounds and a smaller derived bound. Output still must fit instance bounds. Limit failures are explicit; these are cooperative finite checks rather than a process memory sandbox.

## Graph projection and evolution

Keep Loom's core graph node type (`entity`, `instrument`, `event`, etc.). Put the domain type in `data.ontology_type` and domain values in `data.properties`:

```json
{"id": "a", "type": "entity", "data": {"ontology_type": "Person", "properties": {"name": "A"}}}
```

`ontology_project(schema, graph_snapshot(db))` creates a pure, validated projection. It retains each original node/edge under `native`, source links, clocks, graph hash and projection hash. It reports omitted untyped/stale nodes and relationships outside the selected ontology. Unknown declared domain types produce validation issues. It does not broaden the core graph predicate allowlist; existing reviewed custom-predicate activation remains necessary for graph ingestion. Instance packets can use declared ontology relations directly.

Use `ontology diff old.json new.json` to compare increasing versions of the same ontology. Changes include complete before/after type and relation definitions and rule lists. `requires_instance_revalidation` flags structural changes; compatibility is never assumed. Update instance version declarations explicitly and revalidate retained instances. Formatting and projection do not activate a new schema, infer evidence truth, or overwrite native records.

This is a bounded structural ontology system, not RDF/OWL entailment, SHACL conformance, automated schema discovery, a database migration framework or an ontology visualization. The included organization example and all tests use synthetic data.
