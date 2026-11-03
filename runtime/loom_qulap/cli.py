import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .accounting import allocation_model
from .bind import bind
from .bridges import evidence_matrix
from .cubes import query, rollup
from .model import ModelError, contradiction_core, sensitivity, solve
from .surd_bridge import export_witness_program, quantum_search, replay_witnesses
from .temporal import asof_receipt


def read_json(path):
    p = Path(path)
    if p.stat().st_size > 5000000:
        raise ModelError("JSON input exceeds 5 MB; narrow the analytical packet")
    def no_duplicates(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ModelError("duplicate JSON key: " + k)
            d[k] = v
        return d
    value = json.loads(p.read_text(), object_pairs_hook=no_duplicates,
                       parse_constant=lambda x: (_ for _ in ()).throw(ModelError("nonfinite JSON number")))
    if not isinstance(value, dict):
        raise ModelError("packet must be a JSON object")
    return value


def save(result, path=None):
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Results never overwrite an existing source or earlier analysis by accident.
        with p.open("x") as f:
            f.write(text)
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="QULAP: constrained alternative-state accounting and evidence analytics, with SURD simulation")
    parser.add_argument("--version", action="version", version="QULAP " + __version__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("solve", "core", "sensitivity", "quantum", "export-surd", "allocations", "rico", "asof", "bind"):
        p = subs.add_parser(name)
        p.add_argument("input")
        p.add_argument("--out")
        if name not in ("allocations", "rico", "asof", "bind"):
            p.add_argument("--max-nodes", type=int, default=1000000)
            p.add_argument("--seconds", type=float, default=10)
        if name in ("solve", "export-surd"):
            p.add_argument("--retain-worlds", type=int, default=128)
        if name == "quantum":
            p.add_argument("--proposition")
            p.add_argument("--surd", default=str(Path(__file__).resolve().parents[1] / "surd.py"))
        if name == "export-surd":
            p.add_argument("--directory", required=True)
        if name == "asof":
            p.add_argument("--actor", required=True)
            p.add_argument("--cutoff", required=True)
    p = subs.add_parser("cube")
    p.add_argument("--database", required=True)
    p.add_argument("--sql-file", required=True)
    p.add_argument("--parameters", default="[]")
    p.add_argument("--contract")
    p.add_argument("--max-rows", type=int, default=1000)
    p.add_argument("--seconds", type=float, default=3)
    p.add_argument("--out")
    p = subs.add_parser("replay-surd")
    p.add_argument("directory")
    p.add_argument("--surd", default=str(Path(__file__).resolve().parents[1] / "surd.py"))
    p.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.command == "replay-surd":
            result = replay_witnesses(args.directory, args.surd)
        elif args.command == "cube":
            result = query(args.database, Path(args.sql_file).read_text(), json.loads(args.parameters), args.max_rows, args.seconds)
            if args.contract:
                result["rollup"] = rollup(result, read_json(args.contract))
        else:
            packet = read_json(args.input)
            if args.command == "allocations":
                result = allocation_model(packet)
            elif args.command == "bind":
                result = bind(packet)
            elif args.command == "rico":
                result = evidence_matrix(packet)
            elif args.command == "asof":
                result = asof_receipt(packet, args.actor, args.cutoff)
            elif args.command == "quantum":
                result = quantum_search(packet, args.surd, args.proposition, args.max_nodes, args.seconds)
            elif args.command == "core":
                result = contradiction_core(packet, args.max_nodes, args.seconds)
            elif args.command == "sensitivity":
                result = sensitivity(packet, args.max_nodes, args.seconds)
            else:
                result = solve(packet, args.max_nodes, args.seconds, args.retain_worlds)
                if args.command == "export-surd":
                    src, worlds = export_witness_program(result)
                    d = Path(args.directory)
                    d.mkdir(parents=True, exist_ok=False)
                    (d / "witnesses.surd").write_text(src)
                    (d / "worlds.json").write_text(json.dumps(worlds, indent=2) + "\n")
                    result["surd_export"] = str(d / "witnesses.surd")
        save(result, args.out)
        if result.get("state") in ("ERROR", "UNAVAILABLE"):
            return 2
        if result.get("state") in ("INCOMPLETE", "ABSTAIN_INCOMPLETE_MODEL_SEARCH", "INCOMPLETE_CORE_REDUCTION"):
            return 3
        return 0
    except (ModelError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"state": "ERROR", "diagnostic": str(exc), "proof_effect": "NONE"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
