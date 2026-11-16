# Loom — local analytical tools

Loom combines source retrieval, a persistent knowledge graph, exact finite-domain
models and classical circuit simulation in a local Python runtime. This public
distribution runs on your own computer. It includes no HTTP server, hosted
endpoint, access-key setup, cloud deployment or browser service.

## Start

Use Python 3.12 or newer for the shared portable validation environment. Core
execution uses the standard library. NumPy is optional for numerical fuzz checks.

```sh
git clone https://github.com/bbbr8/loom-public.git
cd loom-public
python runtime/loom.py version
python runtime/loom.py describe --json
python runtime/loom.py graph init --db research.sqlite
python runtime/loom.py research demo --directory my-synthetic-demo
```

Use `python3` where your Python executable has that name. The `loom` shell wrapper
provides the same local commands on systems with a POSIX shell.

## Capabilities

- Source retrieval, source locators, explicit relationships and immutable graph revisions.
- Exact quantities, declared finite alternatives, counterexamples and record planning.
- Local document intake, reviewed relationship proposals, typed normalization and SQLite.
- Read-only retrieval-recipe Pareto evaluation with separate development/audit families.
- Circuit simulation and exact supported arithmetic on the local CPU.

Inputs and generated graphs belong in directories you choose outside version
control. The included examples and test databases are synthetic. Optional offline
models and document readers are separate installations; no model service is bundled.

## Verify

Start in the repository root. The final command runs the full runtime suite,
including numerical tests that require NumPy in the selected Python environment.

```sh
python -m unittest discover -s tests -v
cd runtime
python surd.py selftest
python -m unittest discover -s tests -v
```

See [AI_GUIDE.md](AI_GUIDE.md), [local setup](QUICK_START.md),
[runtime reference](runtime/README.md), [research guide](runtime/RESEARCH_ENGINE.md),
[SQLite guide](runtime/SQLITE_BOOTSTRAP.md) and [public distribution policy](PUBLIC_DISTRIBUTION.md).
Finite test results describe supplied inputs and models; their bounds remain explicit.

Frontier packets can set `"aggregation": "family_macro"` to give each development
family equal weight when ranking Pareto-eligible recipes. For each metric Loom
averages its defined query values within each family, then averages those family
means. False hits use only explicitly unanswerable queries. The default
`query_micro` keeps the existing per-query means and total false hits. Each
recipe exposes its `ranking_metrics`; audit queries run after selection.

The [retrieval benchmark](bench/README.md) reports exact per-query average
precision at 10 and its mean across the evaluation set.
