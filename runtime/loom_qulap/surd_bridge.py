"""Pin and reuse installed SURD, without editing the interpreter."""
import hashlib
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from .model import ModelError, solve


def source_fingerprints(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ModelError("SURD source is unavailable: " + str(p))
    files, pending = {}, [p]
    while pending:
        current = pending.pop()
        if current.name in files:
            continue
        raw = current.read_bytes()
        files[current.name] = hashlib.sha256(raw).hexdigest()
        if len(files) > 32:
            raise ModelError("SURD local dependency inventory exceeds 32 modules")
        tree = ast.parse(raw, filename=str(current))
        for node in ast.walk(tree):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else []
            for name in names:
                dep = p.parent / (name + ".py")
                if dep.is_file() and dep.name not in files:
                    pending.append(dep)
    return files


def load_surd(path):
    p = Path(path).expanduser().resolve()
    before = source_fingerprints(p)
    for filename in before:
        name = Path(filename).stem
        loaded = sys.modules.get(name)
        if filename != p.name and loaded is not None and Path(getattr(loaded, "__file__", "")).resolve() != p.parent / filename:
            raise ModelError("SURD local dependency is already loaded from another runtime: " + name)
    spec = importlib.util.spec_from_file_location("qulap_pinned_surd", p)
    module = importlib.util.module_from_spec(spec)
    previous_path = sys.path[:]
    try:
        sys.path.insert(0, str(p.parent))
        # Compile inspected bytes directly rather than reuse a concurrently stale .pyc.
        exec(compile(p.read_bytes(), str(p), "exec"), module.__dict__)
    except ImportError as exc:
        raise ModelError("SURD dependency unavailable: " + str(exc)) from exc
    finally:
        sys.path[:] = previous_path
    after = source_fingerprints(p)
    if before != after:
        raise ModelError("SURD or a local dependency changed during import; pin a stable snapshot")
    if not hasattr(module, "grover_realm"):
        raise ModelError("SURD does not expose grover_realm")
    return module, {"path": str(p), "sha256": before[p.name], "local_source_sha256": before,
                    "version": getattr(module, "VERSION", "unknown")}


def quantum_search(packet, surd_path, proposition=None, max_nodes=1000000, seconds=10):
    # Actual installed SURD exact two-amplitude evolution, not a renamed SQL query.
    result = solve(packet, max_nodes, seconds, 0)
    if not result["complete"]:
        return {"state": "ABSTAIN_INCOMPLETE_MODEL_SEARCH", "solver": result, "proof_effect": "NONE"}
    n = result["candidate_states"]
    if n > 2 ** 20:
        raise ModelError("SURD amplification is bounded to 2^20 declared candidate states")
    if proposition is not None and proposition not in result["propositions"]:
        raise ModelError("unknown proposition: " + proposition)
    marked = result["feasible_states"] if proposition is None else result["propositions"][proposition]["true_count"]
    surd, receipt = load_surd(surd_path)
    wires, iterations, probability, history = surd.grover_realm(n, range(marked))
    return {"schema": "qulap.surd-search.v1", "state": "SIMULATED", "proof_effect": "NONE",
            "backend": receipt, "model_sha256": result["model_sha256"],
            "candidate_states": n, "padded_basis_states": 2 ** wires, "marked_states": marked,
            "predicate": proposition or "all feasible states", "wires": wires,
            "classical_visited_nodes": result["visited_nodes"],
            "classical_constraint_evaluations": result["classical_constraint_evaluations"],
            "classical_feasible_leaf_evaluations": result["evaluated_feasible_leaves"],
            "simulated_oracle_iterations": iterations,
            "measurement_probability_exact": surd.fmt_value(probability),
            "measurement_probability_numeric": float(probability),
            "iteration_probabilities_exact": [surd.fmt_value(p) for p in history],
            "boundary": "Classical constraint search supplies the marked-state count; SURD simulates amplitude amplification. This measurement probability is not a fraud probability, evidence weight, or hardware speedup.",
            "algorithm": "SURD grover_realm exact symmetric two-amplitude simulator"}


def export_witness_program(result, json_name="worlds.json"):
    if not result["complete"] or result["state"] == "INCONSISTENT_MODEL" or result["worlds_truncated"]:
        raise ModelError("SURD witness export requires a complete feasible model with all worlds retained")
    import json
    lines = ["# QULAP finite-state witnesses; synthetic/derived annotations, proof_effect NONE",
             "realm qstates = load(" + json.dumps(json_name) + ")",
             "expect len(qstates) == " + str(result["feasible_states"])]
    for i, world in enumerate(result["worlds"]):
        for name, value in world.items():
            if type(value) is bool:
                # SURD Record exposes bool as text; encode booleans as explicit 0/1 at the boundary.
                value = int(value)
            lines.append("expect field(qstates[" + str(i) + "], " + json.dumps(name) + ") == " + str(value))
    lines += ["print \"QULAP witness replay passed\"", ""]
    worlds = [{k: int(v) if type(v) is bool else v for k, v in w.items()} for w in result["worlds"]]
    return "\n".join(lines), worlds


def replay_witnesses(directory, surd_path, seconds=30):
    root = Path(directory).expanduser().resolve()
    src = root / "witnesses.surd"
    data = root / "worlds.json"
    if not src.is_file() or not data.is_file():
        raise ModelError("replay directory needs witnesses.surd and worlds.json")
    if src.stat().st_size + data.stat().st_size > 5000000:
        raise ModelError("witness replay exceeds 5 MB")
    surd = Path(surd_path).expanduser().resolve()
    before = source_fingerprints(surd)
    # SURD file loads resolve relative to process cwd, so replay owns that context.
    try:
        run = subprocess.run([sys.executable, str(surd), "run", "witnesses.surd"], cwd=root,
                             capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return {"state": "ERROR", "diagnostic": "SURD replay time limit", "proof_effect": "NONE"}
    stable = before == source_fingerprints(surd)
    return {"state": "REPLAY_PASSED" if run.returncode == 0 and stable else "ERROR", "exit_code": run.returncode,
            "stdout": run.stdout, "stderr": run.stderr, "backend_sha256": before[surd.name],
            "backend_local_source_sha256": before,
            "backend_stable": stable, "proof_effect": "NONE",
            "boundary": "Replays enumerated numeric/boolean witnesses; does not independently verify the model or native records."}
