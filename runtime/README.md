# Loom

Formerly Surd. Use `loom` or `loom-lang` on this Mac, or `bin/loom` in a portable release.
The existing `surd` command, `.surd` files, Python modules and serialized schema IDs
remain compatible. The former Strata timeline dialect is integrated through
`loom timeline` and legacy `loom -e`/`loom FILE` invocation. Both dialects support `.loom` files.

Loom 1.16.0 combines an analytical language, classical quantum simulator, read-only source timelines, embedded QULAP and a persistent analytical research engine. Its [research graph](RESEARCH_ENGINE.md) preserves source/version history, grows source/concept/entity links, retrieves graph paths, compiles finite models, plans complementary record bundles and evolves tested research recipes and relation schemas. Python 3.9+ runs the core without third-party packages. NumPy optionally accelerates simulation and is required for the independent numerical fuzz profile. See [unified commands and program functions](INTEGRATIONS.md).

## Start with the question

Run these commands from the extracted release directory:

```sh
python3 loom.py version
python3 loom.py run examples/search_reasoning.surd
python3 loom.py run examples/case_posture.surd
python3 loom.py run examples/quantum_reasoning.surd
python3 loom.py run examples/collections.surd
python3 loom.py describe --json
```

| Goal | Guide | Language entry point |
| --- | --- | --- |
| Find source passages and evaluate retrieval | [Search](SEARCH_ENGINE.md) | `search` |
| Test declared premises, opposition and assumption dependencies | [Reasoning](REASONING_ENGINE.md) | `search_reason` |
| Preserve the record floor, test adverse accounts and choose the next record | [Case posture](POSTURE_ENGINE.md) | `search_posture` |
| Explore assumption combinations with exact phase-oracle circuits | [Quantum reasoning](QUANTUM_REASONING.md) | `search_quantum` |
| Group, scan, partition and transform structured data | [Collections](COLLECTIONS.md) | `group_by`, `scan`, `partition` |

The posture and quantum examples use invented records. Quantum measurement shares describe the configured circuit; they assign no probability to a factual or legal conclusion. Source meaning and evidence-to-claim links require reviewed annotations and inference rules.

Version 1.11.1 improves contract validation, preserves known money conflicts despite missing annotations, explains guard failures and bounds scenario previews. Exact circuit selection stops at its first peak; integer Walsh butterflies reduce interference computation without changing amplitudes. JSON reports retain every scenario, source reference and sensitivity result.

## Build and validate a portable release

```sh
python3 loom.py build check
python3 loom.py build pack --output dist/surd.tar.gz
python3 loom.py build verify dist/surd.tar.gz --profile quick --report dist/quick.json
python3 loom.py build verify dist/surd.tar.gz --profile release --report dist/release.json
python3 loom.py build verify dist/surd.tar.gz --profile fuzz --report dist/fuzz.json
```

Extract the source release into a new directory and use `bin/loom`, or run its `surd.py` with a selected Python interpreter. Build verification always extracts into a fresh directory and runs there. No pip installation, case stores, training history or background services are required for the portable profiles. Validation disables portal logging. Run substantial release/fuzz work through the configured validation queue when using the user's workspace.

[Architecture and build contracts](ARCHITECTURE.md), [search](SEARCH_ENGINE.md), and [reasoning](REASONING_ENGINE.md) describe the boundaries and limitations. Native-case integrations and the shared language-suite contract require separately configured local resources; the portable release does not certify those integrations.

The [bounded improvement procedure](SELF_IMPROVEMENT.md) retains proven regression cases; run `loom-lang improve status` or `loom-lang improve --help`.

SQLite setup: [bootstrap, inspection, backup and native program functions](SQLITE_BOOTSTRAP.md). Use `loom sqlite init --db project.sqlite --demo` for the synthetic starter graph.
