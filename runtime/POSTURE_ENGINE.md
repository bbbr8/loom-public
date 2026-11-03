# Build a stronger case posture from inspectable records

`search_posture` turns an explicit argument into a brief showing the strongest supported statement, the opposing account, a narrower record floor, distinct proof bridges, conditional concede/hold consequences and the next record that can change the assessment. It reuses Loom's quotation, scope, acyclic-argument and exact assumption-frontier engine.

```sh
python3 loom.py run examples/case_posture.surd
python3 loom.py search build examples/data/posture-documents.json -o posture-index.json
python3 loom.py search posture posture-index.json 'What does the credit record establish?' \
  --spec examples/data/posture-contract.json
```

Use `--json -o report.json` to retain the complete report. In a Loom program use `search_posture(index, question, specification)`, then `search_save`/`search_load`. All packaged examples and tests use invented records.

## Declare positions and their proof bridges

The specification contains `argument`, `issues`, optional `source_families` and optional `records`. `argument` uses the claims, exact citations, rules, scope filters and facets described in [REASONING_ENGINE.md](REASONING_ENGINE.md); select goals on each issue instead of inside `argument`.

An issue needs `id`, `title` and `goal`. Optional `floor` selects a narrower claim. An unresolved knowledge, authorization, application or injury bridge does not erase an established document fact. A broader claim must explicitly depend on the bridges it needs through argument rules; the engine cannot infer legal requirements from its wording.

```json
{
  "id": "credit",
  "title": "Recorded credit versus actual application",
  "goal": "credit_recorded",
  "floor": "credit_recorded",
  "materiality": "high",
  "bridges": [{"role":"actual_application", "claim":"credit_applied"}],
  "coordinates": {
    "actor":"Builder", "property":"Job A", "transaction":"Credit memo A",
    "stage":"issued versus applied", "event_date":null,
    "statement_date":null, "knowledge_as_of":null
  }
}
```

Coordinates preserve caller-supplied distinctions; the engine does not infer receipt, knowledge, assent or alignment. Materiality is a caller-selected high/medium/low research priority, not a strength score. `lead_order` puts unconditional supported statements first; `research_order` separately prioritizes material issues and declared adverse evidence. Declared opponent/third-party authorship can break an explanation-order tie only when that source participates in actual support for the goal.

`opponent` has `text` and optional exact `citations`. Without citations it is visibly hypothetical. Opposing quotations attached to argument claims remain in `adverse_evidence`; challenge-search hits remain retrieval leads until inspected and assigned. A quoted opposing account is not automatically true.

## Keep concession and refusal hypothetical

An issue can declare one or both forks with ids `concede` and `hold`. Each supplies `label`, `assume` and `goal`. The assumptions must be explicit assumption claims, and the paired branches must use disjoint assumption ids. They are removed from the baseline and activated only in that fork. Global assumptions remain visible.

`supported_under_stated_branch` requires a sufficient assumption set that depends on that branch and on no additional assumptions. A consequence already supported without the branch cannot manufacture a two-sided posture. `two_branches_supported` reports this declared-graph condition only: it does not establish an exhaustive dilemma, economic cost, legal consequence, actual concession or likely outcome. Write each consequence as a precise record-reconciliation point and verify the rule separately.

## Withdraw whole source families

`source_families` maps cited document ids to shared origin ids. Copies of one deposition, statement, email or ledger belong to one origin. Identical full text is merged even if given different origin labels. Unassigned origins share one conservative unknown group. Each group is removed in full and the argument reruns; both favorable and adverse status changes remain visible. Withdrawals cover the baseline goal and record floor, not every branch scenario.

Different text or different declared origins do not establish independence. The engine preserves `independence_verified: false` and supplies no corroboration count or probability of fraud. Near-duplicate, excerpt and paraphrase lineage needs an explicit origin assignment.

## Select a discriminating next record

Each `records` entry supplies `id`, linked `claims`, `record`, `custodian`, `identifier`, `if_confirms` and `if_weakens`. Both possible effects are mandatory. The report calculates affected issues from the argument dependencies and orders records by research priority. Open checks, blocked rules and unreviewed challenge facets remain visible even when no record task was supplied.

## Verification and bounds

Exact quotations are checked against scoped indexed snapshots, with locators and source hashes retained. Saved loading checks report integrity; it does not reopen native files. Rerun against the source index and inspect native parents, adjacent context and adverse records before case use. Authorship, materiality, origin assignments, evidence links and rule meaning remain declared. No legal-element or outcome grading is performed.

Limits: 16 issues, 16 bridges per issue, two branch scenarios per issue, eight assumptions per scenario, 64 cited documents including opponent accounts and 64 record tasks. Existing argument and exact assumption-frontier bounds still apply; posture audits share one additional frontier-work budget and fail visibly rather than truncating assumption alternatives. Substantial portable suites belong on the configured validation queue.

`tests/test_posture.py` exercises adversarial examples, invalid contracts, branch leakage, duplicate origins, exact quote checks, persistence and native-language/CLI integration. `tests/posture_oracle.py` independently checks seeded finite-state cases and whole-family removals. These establish implemented mechanics; they do not independently establish the truth or legal force of a case interpretation.

For explicit scenario superpositions and exact phase-oracle exploration, use [quantum reasoning](QUANTUM_REASONING.md). Its circuit measurements describe the declared model; the attached baseline preserves source support and open bridges.
