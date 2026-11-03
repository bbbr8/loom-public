# Loom: AI entry point

Loom is a local analytical research engine with a browser workbench, authenticated JSON API, CLI and persistent source graph. This guide is for AI coding assistants and developers.

## Read in order

1. `AGENTS.md`: editing and data-handling rules.
2. `README.md`: current capabilities and runtime configuration.
3. `docs/TRY_LOOM.md`: installation and first run.
4. `docs/API.md`: actual request contracts; do not invent endpoints.
5. The relevant tests and module before changing behavior.

## Code map

| Path | Responsibility |
|---|---|
| `server.py` | HTTP API, authentication integration and workbench assets |
| `worker.py` | Bounded execution and operation dispatch |
| `jobs.py` | Retained jobs and request identity |
| `access.py`, `admin.py` | Access policy and administration |
| `web/` | Browser interface and synthetic examples |
| `runtime/loom_research/` | Retrieval, graphs, intake, evidence, normalization and research |
| `runtime/loom_qulap/` | Exact finite-domain modeling |
| `tests/` | API, access, queue and browser regressions |
| `runtime/tests/` | Engine behavior and release checks |
| `compose.yaml`, `Dockerfile` | Local Linux-container packaging |

## Commands

```sh
# Requires a local .env access key; see installation guide.
docker compose up --build -d
docker compose ps
node --check web/app.js
node --test tests/workbench.test.cjs
python -B -m unittest discover -s tests -v
python -B runtime/tests/test_research.py
python -B runtime/tests/test_integrations.py
```

These are documented commands, not a claim that all currently pass on every platform. The authoring Windows environment encountered symlink and byte-count test failures; Docker startup has not been verified there. Review failures against a baseline before assigning their cause.

## Working with results

Keep source facts, calculated results, model assumptions and candidate relationships distinct. Preserve exact arithmetic and source locators. A successful HTTP response does not certify the correctness of a research conclusion. Model-ranked relevance does not establish truth.

For semantic search, the optional package and local model directories are separate requirements. The default engine needs no hosted AI service.
