#!/usr/bin/env python3
"""Release gate: propagate failures, bound subprocesses, validate fuzz's JSON verdict."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(label, args, env=None, timeout=600, verdict=False):
    print(label, flush=True)
    try:
        p = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"FAIL {label}: {e}", file=sys.stderr)
        return False
    ok = p.returncode == 0
    if verdict:
        try:
            data = json.loads(p.stdout)
            ok = ok and data.get("ok") is True
            print(json.dumps(data, sort_keys=True))
        except (ValueError, AttributeError):
            ok = False
            print(p.stdout)
    elif not ok:
        print(p.stdout)
    elif p.stdout.strip():
        print(p.stdout.strip().splitlines()[-1])
    if p.stderr:
        print(p.stderr, file=sys.stderr)
    if not ok:
        print(f"FAIL {label} (exit {p.returncode})", file=sys.stderr)
    return ok


def main():
    py = sys.executable
    checks = [run("selftest", [py, "surd.py", "selftest"])]
    checks.append(run("selftest without numpy", [py, "surd.py", "selftest"],
                      env=dict(os.environ, SURD_NO_NUMPY="1")))
    for path in sorted((ROOT / "examples").glob("*.surd")):
        for seed in (1, 2, 3):
            checks.append(run(f"{path.name} seed {seed}", [py, "surd.py", "run", str(path), "--seed", str(seed)],
                              env=dict(os.environ, SURD_PORTALS="0")))   # gate runs never write the portal learning log
    checks.append(run("upgrade regressions", [py, "tests/test_upgrade.py"]))
    checks.append(run("runtime and conditional-state regressions", [py, "tests/test_runtime_upgrade.py"]))
    checks.append(run("search and correctness regressions", [py, "tests/test_search.py"]))
    checks.append(run("quantum scenario reasoning regressions", [py, "tests/test_quantum_reasoning.py"]))
    checks.append(run("source-bound reasoning regressions", [py, "tests/test_reasoning.py"]))
    checks.append(run("alternative assumption path regressions", [py, "tests/test_assumption_frontiers.py"]))
    checks.append(run("collection pipeline regressions", [py, "tests/test_collections.py"]))
    checks.append(run("case posture regressions", [py, "tests/test_posture.py"]))
    checks.append(run("training round: natural-idiom tasks", [py, "train/train.py", "round", "--note", "gate"]))
    checks.append(run("held-out set 1 (blind)", [py, "train/train.py", "heldout", "--set", "heldout", "--note", "gate"]))
    checks.append(run("held-out set 2 (blind, never tuned on)", [py, "train/train.py", "heldout", "--set", "heldout2", "--note", "gate"]))
    if "--skip-fuzz" not in sys.argv[1:]:
        checks.append(run("numpy oracle fuzz", [py, "tests/fuzz_surd.py"], verdict=True))
    print(f"gate {sum(checks)}/{len(checks)} passed")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
