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

## Complex arithmetic and time

The runnable example `semantic_time.loom` starts with equal amplitudes on two labels and advances one
label by a quarter turn. Their probabilities stay equal. The overlap with the
original meaning becomes `1/2 + (1/2)i`, and the fidelity becomes `1/2`.

`elapsed_seconds(start, end)` returns an exact signed duration for timestamps
with a UTC offset. `shift_instant(start, seconds)` returns a UTC timestamp at
microsecond resolution. Pass the duration to `evolve_phase(meaning, label,
cycles_per_second, seconds)` to connect clock time to the phase of that label.

`poly_eval([1, 2, 3], z)` computes `1 + 2z + 3z²` at a real or complex
point. `poly_shift([0, 0, 0, 1], 2)` returns the four complex coefficients
of `(2 + h)³` in ascending degree order, with real parts `[8, 12, 6, 1]`.
The coefficient of `h` gives the first derivative at the chosen point.

Inputs and generated graphs belong in directories you choose outside version
control. The included examples and test databases are synthetic. Optional offline
models and document readers are separate installations; no model service is bundled.

## Verify

```sh
python -m unittest discover -s runtime/tests -p 'test_*.py' -v
python -m unittest discover -s tests -v
python runtime/surd.py selftest
```

See [AI_GUIDE.md](AI_GUIDE.md), [local setup](QUICK_START.md),
[runtime reference](runtime/README.md), [research guide](runtime/RESEARCH_ENGINE.md),
[SQLite guide](runtime/SQLITE_BOOTSTRAP.md) and [public distribution policy](PUBLIC_DISTRIBUTION.md).
Finite test results describe supplied inputs and models; their bounds remain explicit.
