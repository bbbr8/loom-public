"""Portable, independent synthetic oracles for the validation. No case data required."""
import argparse
import hashlib
import itertools
import json
import math
import random
import subprocess
import sys
import tempfile
import time
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from loom_qulap.model import solve
from loom_qulap.surd_bridge import load_surd, quantum_search, export_witness_program, replay_witnesses
from qulap_fixtures import build


def core_oracle(trials):
    rng = random.Random(20261011)
    cases, checks = 0, 0
    for _ in range(trials):
        n = rng.randint(2, 5)
        domains = [sorted(rng.sample(range(-4, 7), rng.randint(1, 5))) for _ in range(n)]
        weights = [rng.randint(-3, 3) for _ in range(n)]
        threshold = rng.randint(-15, 15)
        equality = rng.choice([False, True])
        expr = " + ".join("(" + str(w) + " * x" + str(i) + ")" for i, w in enumerate(weights))
        packet = {"schema": "qulap.model.v1", "source_tier": "synthetic",
                  "variables": [{"id": "x" + str(i), "domain": d, "unit": "integer"} for i, d in enumerate(domains)],
                  "constraints": [{"id": "linear", "expr": "(" + expr + ")" + (" == " if equality else " <= ") + str(threshold)}],
                  "metrics": [{"id": "total", "expr": " + ".join("x" + str(i) for i in range(n)), "unit": "integer"}],
                  "propositions": [{"id": "positive", "expr": "x0 > 0"}], "sources": []}
        # Direct arithmetic over itertools; does not use the QULAP expression evaluator.
        worlds = []
        for candidate in itertools.product(*domains):
            weighted = sum(a * b for a, b in zip(weights, candidate))
            if (weighted == threshold if equality else weighted <= threshold):
                worlds.append(candidate)
        r = solve(packet, seconds=3, retain_worlds=0)
        assert r["complete"] and r["feasible_states"] == len(worlds), (packet, r)
        checks += 2
        if worlds:
            assert r["metrics"]["total"]["min"] == min(map(sum, worlds))
            assert r["metrics"]["total"]["max"] == max(map(sum, worlds))
            values = {w[0] > 0 for w in worlds}
            expected = "POSSIBLE_IN_MODEL" if len(values) == 2 else "FORCED_IN_MODEL" if True in values else "IMPOSSIBLE_IN_MODEL"
            assert r["propositions"]["positive"]["status"] == expected
            checks += 3
        else:
            assert r["state"] == "INCONSISTENT_MODEL"
            assert r["propositions"]["positive"]["status"] == "INCONSISTENT_MODEL"
            checks += 2
        cases += 1
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_qulap.py")
    runner = unittest.TextTestRunner(verbosity=1)
    unit = runner.run(suite)
    assert unit.wasSuccessful(), "targeted regression failures"
    return {"model_trials": cases, "oracle_assertions": checks, "targeted_tests": unit.testsRun,
            "oracle": "independent itertools arithmetic enumeration; fixed reproducible seed"}


def quantum_oracle(surd_path, trials):
    surd, receipt = load_surd(surd_path)
    rng = random.Random(20261012)
    checks = 0
    for i in range(trials):
        n = rng.randint(1, 512)
        m = rng.randint(0, n)
        wires, k, probability, history = surd.grover_realm(n, range(m))
        d = 2 ** max(1, (n - 1).bit_length())
        expected = 0 if not m else math.sin((2 * k + 1) * math.asin(math.sqrt(m / d))) ** 2
        assert abs(float(probability) - expected) < 1e-10, (n, m, k, probability, expected)
        assert 0 <= float(probability) <= 1 + 1e-12
        assert wires == max(1, (n - 1).bit_length())
        checks += 3
        if d <= 64:
            # Full rational vector, independently from SURD's two-amplitude grouping.
            state = [Fraction(1) for _ in range(d)]
            for _ in range(k):
                state = [-a if j < m else a for j, a in enumerate(state)]
                mean = sum(state) / d
                state = [2 * mean - a for a in state]
            exact_p = sum((a * a for a in state[:m]), Fraction(0)) / d
            assert probability.q.n == (exact_p.numerator,) and probability.q.d == exact_p.denominator
            checks += 1
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root)
        packet = json.loads((root / "mechanism.model.json").read_text())
        result = quantum_search(packet, surd_path, "owner_funding_required")
        assert result["state"] == "SIMULATED" and result["marked_states"] == 4
        result2 = quantum_search(packet, surd_path, "knowing_use_hypothesis")
        assert result2["marked_states"] == 2
        limited = quantum_search(packet, surd_path, max_nodes=2)
        assert limited["state"] == "ABSTAIN_INCOMPLETE_MODEL_SEARCH"
        checks += 3
        r = solve(packet)
        source, worlds = export_witness_program(r)
        (root / "witnesses.surd").write_text(source)
        (root / "worlds.json").write_text(json.dumps(worlds))
        replay = replay_witnesses(root, surd_path)
        assert replay["state"] == "REPLAY_PASSED", replay
        checks += 1
    return {"quantum_trials": trials, "oracle_assertions": checks, "backend": receipt,
            "oracles": ["closed-form amplitude-amplification probability", "full rational state vector for <=64 basis states"],
            "adapter_checks": 4, "simulation_only": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["core", "quantum"])
    p.add_argument("--trials", type=int, default=2500)
    p.add_argument("--surd", default="surd.py")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    start = time.monotonic()
    try:
        result = core_oracle(a.trials) if a.mode == "core" else quantum_oracle(a.surd, a.trials)
        result.update({"passed": True, "seconds": time.monotonic() - start, "proof_effect": "NONE", "data": "synthetic only"})
        code = 0
    except Exception as exc:
        import traceback
        result = {"passed": False, "error": str(exc), "traceback": traceback.format_exc(), "proof_effect": "NONE"}
        code = 1
    result["ok"] = result["passed"]
    result["input_sha256"] = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                              for folder in (ROOT, ROOT / "tests", ROOT / "tests")
                              for f in sorted(folder.rglob("*.py"))}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("input_sha256", "traceback")}, sort_keys=True))
    sys.exit(code)
