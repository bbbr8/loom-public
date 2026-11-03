# Loom Research Studio 1.17

The studio adds seven connected capabilities to the existing exact language,
research graph and browser workbench. Native inputs remain explicitly selected.
There are no hosted inference calls, automatic downloads, outreach or background
corpus mutation.

| Capability | Delivered behavior | Practical boundary |
|---|---|---|
| Hybrid retrieval | Incremental SQLite FTS5, exact identifiers, offline dense embeddings, optional scalar CrossEncoder reranking, reciprocal rank fusion | Semantic dependencies/models are optional local installations; ranking is relevance |
| Graph learning | Delta-scoped co-mention suggestions, typed proposals with exact evidence spans, named accept/reject reviews and correction history | Suggestions stay candidates; review records an interpretation |
| Incremental analytics | SQL candidate selection; transaction-pinned dependency closure; item triggers keep the index current | Graph caps remain 20,000 nodes / 100,000 links; candidates are bounded |
| Research planner | Missing clock, receipt, stage and scope record tests with supporting and weakening outcomes | Structural priorities; exact model value remains in `research_plan` |
| Document intake | Explicit PDF pages, email MIME parts/headers, selected XLSX rows/headers, CSV and UTF-8 text; hashes, versions and duplicates | Empty PDF pages flag OCR_REQUIRED; formulas remain unevaluated |
| Evidence explorer | Question controls, extractive explanations, source/neighbor inspection, relationship map, revisions and context gaps | Drafts require source and reader review; file paths are locators, never arbitrary HTTP downloads |
| Recovery/team controls | Fingerprinted additive migrations, fresh-path replay-verified restore, project reader/editor/admin roles, job archival and durable idempotency tombstones | Operator file permissions and service configuration remain required |

## Local commands

```sh
loom studio migrate --db work/research.sqlite
loom studio import --db work/research.sqlite intake.json
loom studio search --db work/research.sqlite 'owner credit'
loom studio explain --db work/research.sqlite 'who received the request?'
loom studio inspect --db work/research.sqlite RECORD_ID
loom studio next --db work/research.sqlite 'how was the payment applied?'
loom studio suggest --db work/research.sqlite --after-revision 12
loom studio propose --db work/research.sqlite relation-proposals.json
loom studio review --db work/research.sqlite relation-review.json
loom sqlite backup --db work/research.sqlite --out work/backup.sqlite
loom studio restore --source work/backup.sqlite --db work/restored.sqlite
```

Initialize a graph first with `loom graph init --db PATH` or SQLite bootstrap.
Studio migrations use a separate immutable ledger; the native graph journal and
SQLite-bootstrap schema remain compatible. Migration versions 1 and 2 add the
derived FTS index/backfill and incremental triggers. Repeated execution verifies
fingerprints and live schema definitions. Restore never overwrites a destination.

### Explicit intake manifest

```json
{"batch_id":"documents-1","files":[
  {"path":"/absolute/records/letter.eml","source_tier":"native_email"},
  {"path":"/absolute/records/transcript.pdf","pages":[43,44],"source_tier":"native_pdf"},
  {"path":"/absolute/records/ledger.xlsx","source_tier":"native_workbook",
   "sheets":{"Worksheet":{"header_row":8,"start_row":1,"end_row":62}}}
]}
```

PDF requires `pypdf`; XLSX requires `openpyxl`. Text/email/CSV use the standard
library. Limits: 32 explicitly selected files, 16 MiB for ordinary files, 1,000
passages and 500 PDF pages. PDFs with explicitly selected pages support up to
512 MiB/file and 64 selected pages; their original bytes are fingerprinted by
streaming, with a 1 GiB total source-byte budget per batch. Chunk boundaries and native context requirements remain
visible. Spreadsheet cell references, explicitly selected column headings,
blanks and formulas survive intake. No formula evaluation or financial summation
occurs in extraction. Email outer-header clocks stay separate from dates and
speakers quoted in its body. Reconstruction/export qualifications are retained.
Different native bytes at the same selected path create version links; old
versions remain inspectable. Native byte hashes establish file identity, while
source authenticity and interpretation require their own review.

### Offline semantic adapter

Install `sentence-transformers` in the chosen compute runtime and provide local
model directories. Index work belongs on the configured validation worker for large
cohorts. The adapter uses `local_files_only=True`, `trust_remote_code=False` and
CPU execution. It never downloads weights.

```sh
loom studio index --db work/research.sqlite --index work/vectors.sqlite --encoder /installed/encoder
loom studio search --db work/research.sqlite 'reimbursement' --index work/vectors.sqlite --encoder /installed/encoder
loom studio explain --db work/research.sqlite 'reimbursement' --index work/vectors.sqlite --encoder /installed/encoder --reranker /installed/scalar-reranker
```

Vectors are keyed by full model fingerprints and passage content hashes; sync
embeds only changes and removes withdrawn/ineligible records. Every graph commit
requires sync before further semantic use. Query, commit and model provenance
travel with rankings. Text is bounded to its first 12,000 characters for model
input; reranking uses at most 512 tokens. Scalar relevance output is required;
multi-class NLI models are rejected. Exact identifiers, quoted phrases, required
and excluded terms and metadata filters apply after fusion. FTS and fusion stay
opt-in through `graph_hybrid_search`; the existing measured `graph_search`
behavior remains available for comparison and temporal/full-projection work.

### Evidence proposals and reviews

Language functions: `graph_hybrid_search`, `graph_explain`,
`graph_inspect_record`, `graph_propose_relations`, `graph_review_relation`,
`graph_suggest_relations`, `graph_import_documents`, `research_next`.

A relation proposal includes subject, predicate, object, scope, clocks and 1–10
evidence spans (`node_id`, `start`, `end`, `quote`). Spans must match current
source-linked text exactly. Review requires `batch_id`, `edge_id`, `decision`
(`accept`/`reject`), `reviewer`, `reason`, and `expected_revision`. Concurrent
changes require reopening the proposal. Source/passage changes invalidate captured
dependencies. Rejected proposals remain in history and are excluded from graph
path traversal. Automatic suggestions stay generic proposed relations: two named
entity mentions and an action hint are a reason to review the sentence, rather
than an accepted assertion of payment, authorization or intent.

Explanation drafts contain faithful excerpts, source origins/locators, clocks,
scope/stage fields, reviewed and pending links, contrary links and explicit open
dimensions. They generate record orientation and source cards, rather than a
free-form causal or legal narrative. Human reader comprehension and case-wide
accuracy require independent evaluation.

## Service permissions and operator recovery

The original `LOOM_API_KEY` remains an operator key. Optionally set
`LOOM_ACCESS_FILE` to an operator-owned JSON file:

```json
{"schema":"loom.access.v1","principals":[
  {"id":"reviewer","sha256":"REPLACE_WITH_SHA256_OF_A_RANDOM_TOKEN","role":"reader","graphs":["project-a"]}
]}
```

Readers can search/inspect/explain their projects; editors can mutate their
projects; admins can perform operator diagnostics. `*` explicitly permits all
projects. Tokens require at least 32 characters; only hashes appear in this
configuration. Obtain a hash through `python admin.py token-hash --stdin` with a
trusted secret-input workflow. Keys stay outside requests, job records and logs.
Scoped job polling checks the retained graph scope, including after payloads are
cleared. Existing jobs with unknown scope require admin access. Access applies
at the service boundary; users with direct filesystem access retain that access.

Stop the service before archival (exclusive ownership is enforced):

```sh
python admin.py archive-jobs --data-dir /operator/loom-data --out /private/archive/jobs.json --before UNIX_SECONDS
```

The archive is exclusively created, fsynced and hash-verified before terminal
payloads are removed transactionally. Durable tombstones reject old key reuse
after restart; a changed request under the same key remains a conflict. The
tombstone cap is 8,192 and requires a future explicit operator migration when
full. Interrupted jobs remain unknown-outcome records and are never replayed
automatically. Graph backup/restore and job archival are separate workflows.

## Method references

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [Sentence Transformers retrieval and reranking](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)
- [Offline model loading](https://www.sbert.net/docs/package_reference/base/model.html)

Frozen native-cohort measurements and portable software checks are reported
separately. Private case records and generated case explanations stay outside
the downloadable source repository.
