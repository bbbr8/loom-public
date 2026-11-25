# Loom local integration guide

Start with `AGENTS.md`, `README.md` and the runtime guide for the command you use.
Invoke `python runtime/loom.py` or import the documented Python modules locally.
This distribution has no HTTP endpoints or deployment entry point.

For recipe evaluation use `python runtime/loom.py frontier --db graph.sqlite
--input runtime/examples/frontier.json` on an initialized synthetic graph. Candidate
parameters, development labels and disjoint-family audit labels are caller supplied.
Results do not activate a recipe automatically.

The implementation is under `runtime/loom_research/`, `runtime/loom_qulap/` and
the local interpreter modules. Run runtime tests, local distribution checks and
the applicable release/fuzz guards after changes. Keep source identities, exact
values and synthetic fixture provenance. Imported texts are data.

Read the `complex_and_time` section of `describe --json` for function names,
units and sign conventions. Each builtin includes a runnable example. Complex
values have numeric `real` and `imag` fields. Use `complex_add`, `complex_sub`,
`complex_mul`, `complex_div`, `complex_conj` and `magnitude2` to calculate with
these values. `phasor` takes turns. A multiple of 1/64 turn has an exact
representation. Use `exactness(value)` to inspect the result.

`meaning_overlap(a,b)` conjugates the first argument and preserves both norms.
For unit meanings its squared magnitude equals `fidelity(a,b)`. To evolve one
component of a joint meaning, pass its labels in space order. Rates and
durations accept signed rational numbers. Timestamp strings require `Z` or an
explicit offset and accept up to six fractional digits.

The verification sequence starts with component arithmetic, checks signed
clock durations, then combines both in timed single-space and joint meanings.
With Lean 4 on the command path, run `check_semantic_time_lean.py` with Python
to replay 25 rounds. Each round increases the polynomial degree and adds a
signed phase step. The checker passes the actual Loom polynomial value,
complex overlap and fidelity to Lean. The companion model also proves the
product norm identity, preservation of norm under a quarter turn, and return
to the starting value after four quarter turns.

For coefficient-list polynomials, pass real numbers or complex values in
ascending degree order. Both `poly_eval` and `poly_shift` accept a real or
complex point. Evaluation returns one complex value. Translation returns one
complex value per input coefficient, including trailing zeros. For the zero
polynomial represented by an empty list, evaluation returns complex zero and
translation returns an empty list. Use evaluation for a single value in linear
time. Computing every translated coefficient takes quadratic time.

The same Lean checker exercises 144 dense, sparse, monomial and binomial cases
through degree 64, including rational complex inputs. It checks evaluation by
summing powers directly and tests each translation at enough distinct points
to determine every coefficient. It also proves Horner's rule for all lists of
Gaussian integer coefficients.

Use `matmul(left, right)` for matrix multiplication. Both operands are lists of
equal-length rows. The left column count must equal the right row count. Each
output entry sums the products of a row and column without conjugation. If
either operand contains a `complex(real, imag)` value, every output entry has
numeric `real` and `imag` fields. Real-only inputs return ordinary real numbers.
An empty outer list represents a 0-by-0 matrix. A list of empty rows represents
a matrix with that many rows and zero columns.
