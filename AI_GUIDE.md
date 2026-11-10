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
