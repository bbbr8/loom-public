# Loom 1.8 runtime upgrade

Version 1.8 adds reusable conditional meanings, returned-value exactness and optional execution budgets. It preserves existing `given` projection behavior and the default unlimited execution policy.

## Conditional meanings

`normalize(m)` returns a new meaning with unit norm while preserving relative phase. `condition(joint, "label")` returns a map with a normalized `meaning` and the selected branch's `probability`. This makes a conditional projection reusable on fresh quantum wires. `given(joint, "label")` continues returning an unnormalized projection.

Normalization uses exact arithmetic when its square root is supported. Otherwise only the new meaning moves to floating point, with a source-line note. A branch probability can remain exact. Zero and non-finite norms raise structured errors. Run `examples/conditioning.surd` for a synthetic six-expectation example.

## Exactness and complete expressions

`exactness(value)` describes numeric representation using `exact`, `approximate`, `mixed`, `not_applicable` or `unknown`. Lists and maps retain metadata for their members. Exact money, dates and collections now report correctly in `eval --json`; opaque values remain unknown. This metadata describes representation, not semantic correctness or source truth. Very large exact values remain serializable when a finite decimal approximation is unavailable.

Evaluation now consumes the entire expression, so `loom-lang eval '1 2'` produces an error. CLI evaluation still accepts setup statements on preceding lines. Session evaluation accepts one complete expression and validates request shapes, code types and reset seeds without terminating the session.

## Optional execution limits

```sh
python3 loom.py run examples/conditioning.surd --max-steps 10000 --timeout 10
python3 loom.py eval 'exactness([$1.25, float(1/3)])' --json
python3 loom.py serve --max-steps 100000 --timeout 10
```

Python callers can use `Interp(max_steps=..., timeout=...)` with `run` or `evaluate`. Exhaustion raises `S-E017`. Steps cover statements, evaluated expression nodes and explicit loop/shot iterations, including empty loops. Shots share one budget; each session exec/eval request receives a fresh budget. Session reset preserves the configured limits. Earlier mutations remain after an error.

These are cooperative checkpoints. Parsing, native operations, memory growth and external I/O need process-level bounds. A timeout cannot interrupt a long native call.

## Architecture and validation

The shared project also provides the public search/reasoning boundary and reproducible portable build route documented in [ARCHITECTURE.md](ARCHITECTURE.md). The release allowlist includes the new example and regression tests. `tests/test_runtime_upgrade.py` adds 23 regression tests; the workspace gate and portable release runner include them.

The upgrade task retains pinned before/after sources, remote job receipts, numerical and synthetic reasoning oracles, local native compatibility checks and a routing scan. See the task's `SURD_1_8_UPGRADE_REPORT.md` and `SURD_1_8_VALIDATION.json` for actual coverage and exclusions. Native-store integrations, training histories and the language-suite contract remain separate from portable release verification.
