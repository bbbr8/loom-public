# Loom improvement procedure

Loom 1.9.1 learns a growing regression curriculum from demonstrated failures. Each
retained case makes later checks stricter. Runtime patches remain explicit source
edits; this procedure does not generate code, train model weights, or run unattended.
It complements the existing `train/train.py` task curriculum and portable release
profiles. Native case integrations retain their separate validation boundary.

1. Pin the failing release before editing it. Keep its build allowlist and files.
2. Write a small JSON candidate with an independently stated `oracle_basis`, the
   original failing program, a distinct transfer example, and a valid control.
   Use `tests/improvement/float_dispatch.json` as the template. Expected answers
   must come from arithmetic, a specification, or an independent oracle.
3. Make the smallest source repair. Preserve unrelated work and the original receipt.
4. Run a bounded cycle. Both original and transfer must fail cleanly on the pinned
   baseline; the control must pass there. All three must pass the repaired release.
   Timeouts, crashes and execution-budget exhaustion cannot demonstrate a repair.
5. The cycle checks every retained case, the portable guard suites and selftest in
   frozen copies. Source drift, a missing guard, a skipped check or any failure holds
   the candidate. Passing cases are added to the curriculum automatically.
6. Run `check` on subsequent changes. Use the full release and NumPy fuzz profiles
   before shipping; a curriculum check alone does not certify a release.

```sh
loom-lang improve cycle --case tests/improvement/float_dispatch.json \
  --baseline /absolute/pinned/baseline --report work/improve-float.json
loom-lang improve status
loom-lang improve check --report work/retained-checks.json
loom-lang improve retire --case-id ID --reason "superseded by a stronger invariant"
```

Cycles default to a 300-second total execution budget, capped at 600 seconds, with
60-second child-process limits and 5-second/200,000-step program limits. Use the
validation queue for substantial cycles and release/fuzz runs in the user's workspace.
Stage only the portable source snapshot, candidate cases and baseline; fetch the
reports and curriculum state, verify their hashes and receipt chain, then apply
the returned state with exclusive write ownership. Never rerun an uncertain job
before reconciling its pinned job ID.

State defaults to `state/improvement/` and is excluded from portable builds. Cases
have content-derived IDs. Immutable numbered receipts form a hash chain recording
promotion, hold and retirement, source fingerprints, baseline outcomes, guard
outputs and the actual host. `--state` selects an isolated test/worker state.
Retirement changes the active curriculum while preserving its earlier receipts.
The limits are 64 active cases and 2,048 receipts per state directory; reaching a
limit requires an explicit archive or retirement, never silent history deletion.
The lock rejects concurrent writers. This is trusted local test execution, not an
untrusted-code sandbox or cryptographic attestation against a malicious writer.

The seeded cases and their transfer examples are same-agent synthetic checks.
They improve coverage, not measured real-world accuracy. They never promote
synthetic results to evidence or mutate native inputs, global memory or weights.

## Review fixes retained in 1.9.1

- Float arithmetic evaluates only the requested operation.
- Measurement dependencies survive aliases, function arguments and common mutable
  containers. Gate bodies and helper closures reject measured inputs. Ordinary
  observation remains exportable. Conservatively, capturing a classical measured
  value makes a program trace-only even if the alias later proves unused. The
  historical `used_if` flag now covers measurement-dependent execution generally.
- QASM and optimization reject measurement-dependent parameters and loops unless
  `--trace` explicitly accepts this run's resolved path and parameters.
- Numeric noise probabilities are checked before rounding, including an exact
  Pauli total. Floats use their exact binary rational value; there is no overshoot
  tolerance. Symbolic formulas retain their existing user-selected domain.
- QASM declares `sx`, `sxdg`, `crx` and `cry` using standard qelib1 gates. Round-trip
  tests compare full unitaries, including phase.

The archived Ket 0.2 interpreter remains a historical baseline.

## Feedback on ordinary use

The CLI now records one local metadata receipt for `run`, `check`, `qasm`, `opt`,
`draw`, and `search`: random use ID, time, command family, release, duration,
exit status, outcome and an optional SHA-256 of a program under 1 MiB. It retains
no program text, filename, query, argument list, stdout or stderr. `completed`
means execution completed successfully; correctness needs an independent oracle.
Direct interpreter/library calls are outside this CLI hook.

```sh
loom-lang usage review
loom-lang usage feedback --use-id ID --category wrong-result --note "Expected answer and task receipt locator"
```

Feedback categories are `correct`, `wrong-result`, `slow`, and `confusing`. Keep
notes short and procedural; they are explicitly supplied local text. The review
queue includes rejected/crashed/interrupted uses and negative feedback, plus ten
recent IDs for feedback on successful execution. A rejected program can be valid
input rejection; inspect it before proposing a runtime repair. Repeated feedback
on one ID replaces its current annotation.

Use `SURD_USAGE=0` to disable collection, or `SURD_USAGE_STATE=/absolute/path/usage.sqlite`
to isolate it. Portable test processes disable collection automatically. The default
is `state/usage.sqlite`, excluded from portable builds. SQLite serializes writers
with a 200 ms contention timeout. At 2,048 receipts collection warns and preserves
history; archive explicitly to resume. Receipt-write failure warns on stderr and
preserves the original command result or exception. No background monitor, model
training, source mutation, automatic benchmark or external transfer runs on use.

For each agent-assisted use, inspect the newest receipt and requested result.
Record observed friction with an independent expected behavior. When a repair is
warranted, pin the baseline first and use the existing `improve cycle`; record
successful unchanged behavior as retained. Read `AGENTS.md` for the standing loop.
