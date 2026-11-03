# Quantum reasoning over inspectable case scenarios

`search_quantum` explores explicit assumption combinations, attaches a baseline
source posture, and shows which requested records distinguish competing scenarios.
Loom executes exact phase-oracle and Grover circuits on a classical computer.
Circuit measurement shares are properties of the configured model. Factual
confidence, credibility, guilt, source truth and legal outcomes remain unassessed.

```sh
python3 loom.py run examples/quantum_reasoning.surd
python3 loom.py search build examples/data/quantum-documents.json -o quantum-index.json
python3 loom.py search quantum quantum-index.json 'Which record discriminates these explanations?' \
  --spec examples/data/quantum-contract.json --json -o quantum-report.json
```

In a program use `search_quantum(index, question, specification)`; the last argument
can be a map or a JSON contract path. `search_save` and `search_load` preserve and
validate the complete report. The packaged example uses invented credit records.

## Declare the hypothesis space

The contract contains:

- `posture`: a complete [posture contract](POSTURE_ENGINE.md), with its argument,
  exact citations, issues, adverse accounts, source origins and record requests.
- `variables`: 1..8 distinct explicit assumption claim ids, in bit order. A `1`
  activates that assumption; a `0` leaves it unasserted. Inactivity preserves an
  unresolved proposition rather than introducing factual opposition.
- `target`: the claim whose supported scenarios the phase oracle marks.
- `requirements`: optional `{claim,status}` constraints; statuses are `supported`,
  `opposed`, `contested` and `unresolved` within the declared graph.
- `exclusive_groups`: optional lists of variables; **exactly one** variable in
  each list must be active. These are caller-declared hypothesis constraints.
- `guards`: optional evidence-type, scope, date and money checks below.
- `records`: optional candidate records below.
- `iterations`: optional 0..16 Grover iterations; omit for the first
  non-increasing peak in the bounded exact search.

The baseline excludes enumerated assumptions and every posture-fork assumption.
Other global assumptions remain active and visible. Each world then activates only
its listed variables. A target is marked only when all requirements, exclusivity
constraints and requested guards pass and the target has support without opposition.
Contested targets remain unmarked. Rules and their meaning remain caller-declared.

## Check structured source annotations

Each guard needs an `id`, `kind` and `claims`:

| Kind | Additional field | Check against directly cited support-source metadata |
| --- | --- | --- |
| `fact_kind` | `expected` | One claim's sources declare the expected `metadata.fact_kind`, such as `credit_issued` or `credit_applied`. |
| `same_scope` | `field` | 2..8 claims agree on `actor`, `project`, `transaction`, `version` or `stage`. |
| `before` | `field` | Two ordered claims have strictly ordered ISO dates on `event_date`, `statement_date` or `received_at`. Same-day dates leave strict order blocked. |
| `money_equal` | none | Two claims agree on `metadata.amount`, supplied as finite exact-cent decimal strings. |

Missing annotations or removed source support leave a guard `open`; conflicting
annotations leave it `blocked`, including known money conflicts when another claim
has missing annotations. Guard reports retain diagnostic reasons and the observed
values by claim. A blocked `before` guard means strict calendar-date order is not
established; same-day annotations do not resolve the actual order within that day.
Open or blocked guards prevent scenario marking
while the attached baseline posture retains its supported source statements.
Unrequested guards are reported explicitly. Dates need `YYYY-MM-DD`; uncertain
dates remain missing/open. No date, receipt or application is invented.

These checks enforce annotation relationships. Human/native review determines
whether annotations and evidence-to-claim links faithfully describe the record.
Quotation validation uses the indexed snapshot. Native file reopening, source
authenticity, inferred semantic typing and automatic native revision watching
remain separate work.

## Inspect the quantum operations

For n variables there are D=2^n basis states. The prepared state has amplitude
1/sqrt(D) on each assignment. The phase oracle negates marked amplitudes, and
Grover diffusion reflects them about their mean. Rational amplitude coefficients
over sqrt(D) keep normalization and every measurement share exact.

The report retains the selected state, amplitudes, iteration history and exact
marked share. A separate `H^n → phase oracle → H^n` circuit supplies the full
Walsh interference spectrum and names the variables in each parity mode. The
spectrum describes the declared oracle's Boolean structure; it supplies an
inspectable analytical diagnostic rather than source corroboration.

In the invented three-variable example, one of eight states is marked; two Grover
iterations change its circuit measurement share from 1/8 to 121/128. This fraction
describes sampling that marked scenario under the simulated circuit. It assigns
no probability that the scenario occurred in the case.

Every source family is withdrawn in full and the classical oracle reruns. The
report retains lost and newly marked scenarios, including changes caused by removal
of adverse evidence. Exact copies share an origin through the posture engine.

The truth table is built by classical enumeration. The report counts those world
evaluations separately from simulated oracle applications; it claims no computational
quantum speedup or hardware execution. Automatic iteration selection stops after
the first non-increasing step, within 16 turns. `selection_simulated_steps` includes
that rejected look-ahead step; `oracle_applications` and `history` describe only
the selected Grover circuit. Fixed caller-selected turns are always executed,
including turns beyond the first peak. Integer Walsh butterflies compute the
interference spectrum in O(n × 2^n) work with exact amplitudes.

The text report previews at most 16 feasible scenarios, putting target-supporting
assignments first when truncation is needed; JSON retains every assignment. It also
shows up to four largest nonconstant oracle parity modes and guard changes caused
by source-family withdrawal. These diagnostics describe the declared model.

## Choose the next record

Each requested record supplies `id`, `claim`, `record`, `custodian`, `identifier`,
`if_confirms` and `if_weakens`. The engine partitions **feasible** scenarios by the
claim's four-valued status, preserving target-supporting and other scenarios.
It ranks the record by how many target-marked/unmarked scenario pairs it separates,
then by all scenario pairs separated, then by id. These are classical combinatorial
counts on the declared scenario set, without assigned record-outcome likelihoods,
monetary value or factual confidence. When all feasible scenarios agree on the
target, the target-pair score is zero.

## Validation and limits

Limits: 8 variables/256 worlds, 16 requirements, 16 exact-one groups, 32 guards,
32 record requests, 16 circuit turns, plus existing posture/argument limits.
One additional frontier-work budget covers all world and source-family reruns;
an exhausted budget fails visibly. Reports retain hashes and validated nested
posture reports. Loading verifies report integrity; rerunning the source index
performs current model evaluation.

`tests/test_quantum_reasoning.py` checks baseline isolation, four-valued conflict,
exact guards, family withdrawals, source duplicates, record partitions and both
entry points. `tests/quantum_reasoning_oracle.py` independently simulates H gates,
phase flips and the zero-state reflection using a floating state vector and checks
the exact circuit results through eight wires, including independently selected
iteration peaks, work counts, histories and interference amplitudes. These validate
implemented mechanics; native-case semantic performance requires reviewed tasks.

Algorithm references: [Grover's original search paper](https://arxiv.org/abs/quant-ph/9605043)
describes phase changes and inversion about the average. Source/derivation modeling
is consistent with the distinctions in [W3C PROV-O](https://www.w3.org/TR/prov-o/).
No RDF conformance or physical quantum implementation is claimed.
