# Search and evaluation in Loom

Search your text, inspect why each record ranked, and measure changes against saved judgments. The search layer runs classically alongside Loom's exact quantum simulator. Its scores describe relevance. Source facts and model truth states retain their existing meanings.

Run the working language example:

```sh
loom-lang run ./examples/tools/surd/examples/search_eval.surd
```

## Start with your records

Each row needs a stable string `id` and faithful `text`. Keep the native `locator`, title, and contextual metadata. Missing locators remain visible in the manifest. Distinct versions need distinct ids; duplicate ids fail. No near-duplicate records are silently merged.

```json
[
  {"id":"document-17","title":"Approval email","text":"The transfer was not approved.",
   "locator":"originals/email.txt:42",
   "metadata":{"source_tier":"native","event_date":"2020-05-01","receipt_date":"2020-05-03","stage":"requested"}}
]
```

JSON, JSONL and CSV use the same columns. `meaning` or `body` may supply `text`. Extra columns become metadata; they are preserved rather than automatically added to retrieval text. Custom searchable text belongs in `fields`, with corresponding weights in the index configuration. Metadata and source text remain unmodified snapshots. Dates are not inferred; range comparisons use supplied numbers or strings (use ISO dates for chronological ordering). Requested, received, paid and applied stages stay caller-defined, separate fields.

```sh
loom-lang search build documents.json -o index.json
loom-lang search inspect index.json
loom-lang search query index.json 'transfer "not approved"' --json
loom-lang search query index.json 'document-17' --where '{"source_tier":"native"}'
loom-lang search query index.json 'transfer' --ranges '{"receipt_date":{"gte":"2020-05-01","lte":"2020-06-01"}}'
```

SQLite opens read-only, requires an explicit row-returning query, and has a five-second query budget:

```sh
loom-lang search build records.sqlite --sql 'SELECT id, text, locator, stage FROM records' -o index.json
```

The snapshot cap is 64 MiB and 100,000 documents. Query length is capped at 8,192 characters, returned ranks at 1,000 and evaluation queries at 10,000. This release is an in-memory engine for bounded corpora. Use existing corpus services for larger collections; import their ranked ids for evaluation. No embeddings or paid services are loaded automatically.

## Retrieval and explanations

The default `hybrid` mode adds structured identifier and phrase signals to field-weighted BM25. An ordinary word only receives an identifier bonus when it is the whole positive query; a topic word inside a longer query does not. It combines lexical signals; it does not imply an embedding model. `bm25` omits bonuses; `overlap` is a simple distinct-token baseline. Each hit retains id, locator, snapshot hash, metadata, source snippet with offsets, and score contributions. Snippets are exact substrings of retained text, not generated summaries. The snapshot hash identifies the indexed text/metadata, not necessarily the native PDF bytes.

- Bare words are optional lexical terms. There must be at least one positive term.
- `"quoted words"` requires that token sequence within one field.
- `+required` requires the term; `-excluded` excludes matches; `-"excluded phrase"` excludes that sequence.
- `where` filters metadata by exact typed value (a list means alternatives). Unknown metadata fails the filter.
- `ranges` uses inclusive `gte` and `lte`. Unknown or incompatible values fail the filter.
- Unicode NFKC/case folding and numeric comma removal affect retrieval tokens; original text is retained. Identifiers preserve underscores, hyphens, decimal points and date separators.
- No stopword removal drops `not`. A bare word such as `approved` can retrieve both approved and not-approved records. Use the quoted wording or explicit metadata when polarity matters. This engine does not infer subjective meaning or intent.
- Ties sort by document id. Configurable aliases are explicit, single-token alternatives and are reported in each query result.

Configuration example (`--config configuration.json` during build):

```json
{"k1":1.2,"b":0.75,"fields":{"title":3,"text":1,"id":4,"locator":2},
 "identifier_boost":6,"phrase_boost":2,"aliases":{"reimbursed":["reimbursement"]}}
```

Reciprocal-rank fusion combines already-ranked result files using `sum(1/(60+rank))`. Query, corpus fingerprint and filters must align; conflicting source hashes or duplicate ranked ids fail. Scores from different engines are not added directly.

```sh
loom-lang search fuse keyword-results.json semantic-results.json -o fused-results.json
```

To use an existing semantic service, retain its ranked native document ids and align them to this corpus; evaluate those ids with `--rankings`. The engine does not invoke case retrieval services or mutate their indexes.

## Evaluate and compare

Write query judgments independently of the engine being evaluated. Grades are finite, nonnegative relevance numbers. A missing judgment is unknown; an explicit zero is judged nonrelevant. A query with no positive judgments is excluded from relevance averages. To test a known absence, explicitly set `answerable:false` and `relevant:{}`.

```json
{"queries":[
  {"id":"approval-phrase","query":"\"not approved\"","split":"test",
   "where":{"source_tier":"native"},"relevant":{"document-17":2}},
  {"id":"unknown","query":"other topic","split":"test"},
  {"id":"known-absence","query":"missing_identifier_999","split":"test",
   "answerable":false,"relevant":{}}
]}
```

```sh
loom-lang search evaluate index.json judgments.json --mode overlap -o baseline.json
loom-lang search evaluate index.json judgments.json -o candidate.json
loom-lang search compare baseline.json candidate.json --metric ndcg@10 --max-drop 0 -o comparison.json
```

`compare` exits 0 when the requested gate passes, 1 on a measured regression, and 2 on invalid or incomparable inputs. `--max-query-drop` also gates individual query losses. Reports retain all paired deltas, wins/losses/ties, excluded queries, and a seeded paired bootstrap interval. That interval reflects these query judgments; it cannot certify independent labels or generalization.

Evaluate another retriever with a JSON map of every selected query id to its ranked ids. Empty rankings must be explicit. Unknown ids, duplicates, missing queries and out-of-scope results fail:

```json
{"approval-phrase":["document-17"],"unknown":[],"known-absence":[]}
```

```sh
loom-lang search evaluate index.json judgments.json --rankings external-rankings.json -o external.json
```

Metrics at each requested cutoff:

| Metric | Convention |
|---|---|
| precision@k | positive judged hits divided by k, including unfilled slots |
| recall@k | positive judged hits divided by all positive judgments |
| mrr@k | reciprocal rank of the first positive judgment, or zero |
| map@k | sum of precision at positive ranks divided by min(total positives, k), then macro averaged |
| ndcg@k | linear relevance gain, discount log2(rank+1), normalized against ideal judged ranking |
| judged_fraction@k | judged returned hits / returned hits; null when none returned |
| no_answer_accuracy | fraction of explicitly known-absence queries with empty results |

Unjudged documents receive zero gain for metric calculation while remaining explicitly listed as unjudged. Sparse judgments can understate relevance; query counts and coverage accompany every mean. Relevant judgments outside the corpus or query filters are rejected. `--split` selects a caller-declared partition; `--training-query-ids train-ids.json` rejects query overlap. Split names alone do not prove an independent held-out study.

Comparisons require matching corpus, query/judgment dataset, split, cutoffs and metric conventions. Index and run hashes are stable across timing differences. Loading an index recomputes its contents and manifest; loading a run checks retained queries, rankings, judgments, aggregates and hash. Writes are atomic and read back before replacement. These checks detect corruption; they are not cryptographic proof that an outside author supplied true labels.

## Program it in Loom

Use `search_reason(index, question [, contract])` for a readable retrieval brief and a source-bound argument audit. It checks quotations, exposes missing or contested premises, tracks assumptions, and tests source/rule removals. See [Reason with inspectable premises](REASONING_ENGINE.md) for the contract and working example.

```surd
let ix = search_index("documents.json")
let found = search(ix, "document-17", {"k": 5})
print found["hits"][0]["locator"]
let trial = search_evaluate(ix, "judgments.json", {"cutoffs": [1,5,10]})
search_save(trial, "trial.json")
let previous = search_load("baseline.json")
expect search_compare(previous, trial)["passed"] == true
```

Functions: `search_index(rows_or_path_or_realm [, configuration])`, `search_load(path)`, `search_save(index_or_report,path)`, `search_info(index)`, `search(index,query [, options])`, `search_evaluate(index,queries_or_path [, options])`, `search_compare(baseline,candidate [, options])`, `search_fuse(results [, options])`. Evaluation options also accept a `rankings` map. A realm without ids receives stable-within-that-snapshot `realm_name:row_index` ids; provide explicit stable ids for comparisons across reordered exports. Exact money values and date objects bridge as strings. Fractional Loom numbers bridge as floats; retain exact noninteger metadata as strings when it must survive without rounding.

Strings now support JSON escapes, including quoted phrases `"\"not approved\""`. Repeated list/map indexing works at arbitrary nesting depth. Invalid search input raises positioned `S-E016` errors. JSON run output declares `exact_scope: quantum_state` and `circuit_exact`; search scores and evaluation ratios use floating point.

## Reliability and repeatable benchmarks

This extension fixes exact-real ordering near cancellation using certified integer intervals (and integer-square comparisons for Q(sqrt(2))), rejects overlapping `encode` registers before mutation, validates meaning-function arity, and requires global purity before reporting reduced linear entropy as `entanglement`. `linear_entropy` remains available for mixedness. Float density purity is tested within 1e-10; it is not an exact certification of a floating-point state.

`tests/test_search.py` covers hand-calculated metrics, unknown judgments, leakage, scope, corrupt artifacts, external ranking regressions, identifier/negation cases and the language counterexamples. The release gate includes it. The NumPy oracle battery remains separate.

```sh
python3 tests/test_search.py
./gate.sh
python3 bench/search_benchmark.py --output search-benchmark --size 3000
```

Benchmark output includes real Loom example-file retrieval, a labeled synthetic stress corpus, overlap/BM25/hybrid comparisons, shuffled-label and random-ranking controls, latency, and retained failures. Labels are author-created regression judgments. They do not establish case retrieval quality; a larger independently judged native corpus is still needed. Software-corpus locators are relative to Loom's examples directory.

Metric background: [Stanford ranked retrieval evaluation](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html), [BEIR evaluator](https://github.com/beir-cellar/beir/blob/main/beir/retrieval/evaluation.py), [original reciprocal rank fusion paper](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf). Metric conventions above govern this implementation where libraries differ.
