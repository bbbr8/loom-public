# Typed normalization — Loom 1.19

Normalize values for comparison while keeping their exact raw value, source role,
profile, rule version and unresolved alternatives. Names and formatting remain
identity candidates; reviewed aliases retain their existing separate workflow.

Supported kinds: text, name, business_name, identifier, email, phone, date,
datetime, number, money, percentage, money_stage, formula, empty and address.

```
loom normalize value value.json
loom normalize record record.json
loom normalize graph --db case.sqlite selected-fields.json
loom normalize inspect --db case.sqlite SOURCE_NODE_ID
loom studio normalized-search --db case.sqlite '1234.56'
```

A value packet is `{ "kind":"money", "value":"$1,234.56",
"profile":{"dollar_currency":"USD"} }`. It produces canonical decimal text,
exact rational value and integer minor units. Currency precision conflicts stay
open; rounding and binary-float currency conversion are excluded. Bare dollar
symbols keep currency open unless explicitly resolved. Numeric defaults are dot
decimal/comma grouping; other locales require explicit decimal/group separators.

Dates retain ambiguity: `03/04/2018` supplies MDY/DMY alternatives; `02/07/18`
requires an explicit century. Profile keys `date_order` and `two_digit_century`
record those choices. Day-only input stays day-only. Naive times keep timezone
open; zoned datetimes carry both original offset and UTC forms.

Identifiers require source text, preserving leading zeros and namespaces. Only
an explicit `identifier_separators:true` profile removes spaces/dashes. OCR O/0
and I/1 lookalikes remain review flags; multiline identifiers remain open.
Phones require explicit country context for national forms and preserve extensions.
Numeric identifier cells with a native all-zero number format retain that declared
display width in a separate canonical view; raw numeric text and the format remain.
Mailbox local-part case stays intact unless a domain policy is explicitly supplied;
domain spelling is normalized according to [RFC 5321 section 2.4](https://www.rfc-editor.org/rfc/rfc5321#section-2.4).

Structured addresses preserve unit, direction and postal components. Free-text
addresses retain review state. Reimbursed, refunded, credited, requested and
applied remain distinct stages. “Paid back” keeps alternative meanings open.
Text views preserve negation and conditions; source quotations stay unchanged.

## Record and graph views

A record packet declares record_id, optional source_locator/profile and fields.
Each field supplies name, kind, value and optional path, role, namespace, scope,
profile or source_span. The output retains raw fields with states and alternatives.

A graph packet supplies batch_id, expected_revision, optional profile and 1..100
records. Each record selects a node_id and typed fields. A field selects either a
path such as `["data","cells","B4"]` (list indexes are explicit integers) or
start/end offsets into source text. The raw value is read from the current source;
caller-supplied replacement values are excluded. Source-linked research-space
views capture content/revision dependencies. Repeated identical normalization is
idempotent; source changes invalidate earlier views, which remain inspectable.

New spreadsheet intake attaches typed field views alongside raw cells. Formulas,
number formats, merged ranges, hidden rows and header/column positions survive.
Native percentage formats supply an explicit ratio-input profile; percentage
cells keep exact ratios and percentage display values alongside raw numbers.
Quoted or escaped percent signs stay literal. Header rows remain labels.
Currency and financial role are separate. Blank cells stay blank; formulas stay
unevaluated. Duplicate CSV headers reject by default; file option
`retain_duplicate_headers:true` preserves positional columns and their raw headers.
`normalization_profile` on each file controls declared assumptions.

## Retrieval and interfaces

The additive FTS normalization index updates transactionally. Normalized search is
opt-in and returns the original record plus normalization/profile match lineage.
Source-token hard clauses, scope filters, dependency eligibility and bounded
candidates still apply. Default indexed source ranking remains unchanged.

Language/API functions: normalize_value, normalize_record, graph_normalize,
graph_normalization, graph_normalized_search. Pure functions accept a packet;
graph normalization accepts a graph plus packet; inspection/search accept a graph
plus source ID/query. HTTP reader roles can normalize supplied values and read/search
views; persisted graph views require writer access. Local-file intake remains local.

Current limits: supported currencies USD/CAD/AUD/EUR/GBP/JPY/KWD; national phone
profiles US/CA; declared centuries 1800/1900/2000/2100. Unsupported conventions
retain raw data and require an explicit adapter/profile extension. No geocoding,
automatic identity merges, currency conversion, OCR character repair, formula
evaluation or financial-stage inference runs in normalization.
