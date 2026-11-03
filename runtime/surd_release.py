"""Bounded, side-effect-free validation of a frozen portable Surd release."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
from surd_build import BuildError, encoded, sha, unpack, verify_tree


def verdict(code, stdout, require_json=False):
    if code != 0:
        return "failed"
    if require_json:
        try:
            data = json.loads(stdout)
            return "passed" if isinstance(data, dict) and data.get("ok") is True else "failed"
        except ValueError:
            return "failed"
    if re.search(r"\bSKIP\b|\bskipped\b", stdout, re.I):
        return "skipped"
    return "passed"


def builtin_verdict(data):
    rows = data.get("builtins", []) if isinstance(data, dict) else []
    portable = [x for x in rows if x.get("scope") == "portable"]
    integrations = [x for x in rows if x.get("scope") == "native-case-integration"]
    ok = bool(portable) and len(portable) + len(integrations) == len(rows)
    ok = ok and all(x.get("verified") is True for x in portable)
    ok = ok and {x.get("name") for x in integrations} == {"docs", "anchor_check", "stages"}
    ok = ok and all(x.get("verified") is None and x.get("status") == "integration_required" for x in integrations)
    return {"state": "passed" if ok else "failed", "portable_examples": len(portable), "excluded_integrations": [x["name"] for x in integrations]}


def validate(archive, profile, report_path):
    archive, report_path = Path(archive).resolve(), Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": "surd.release.v1", "ok": False, "profile": profile, "archive_sha256": sha(archive.read_bytes()),
              "host": platform.node(), "python": sys.version, "checks": [],
              "scope": "portable runtime, packaged examples and synthetic test fixtures; native case stores, suite contract, training and optional external truth oracle excluded"}
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="surd-release-") as tmp:
            root = unpack(archive, Path(tmp)/"source")
            manifest_bytes = (root/"BUILD_MANIFEST.json").read_bytes()
            manifest = verify_tree(root)
            report.update(version=manifest["version"], manifest_sha256=sha(manifest_bytes))
            env = dict(os.environ, SURD_PORTALS="0", LOOM_RESEARCH_DB="", PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1", PYTHON=sys.executable, CASE_LANGUAGE_ROOT=str(Path(tmp)/"no-case"), SURD_PORTABLE_TEST="1")
            env.pop("SURD_NO_NUMPY", None)
            py = sys.executable
            checks = []
            if profile != "fuzz":
                scripts = ["test_architecture.py", "test_search.py", "test_reasoning.py", "test_upgrade.py"]
                if (root/"tests/test_ontology.py").is_file():
                    scripts.append("test_ontology.py")
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1,14,0):
                    scripts.append('test_scalability.py')
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1,15,0):
                    scripts.append('test_storage_architecture.py')
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1,16,0):
                    scripts.append('test_sqlite_bootstrap.py')
                if tuple(int(x) for x in manifest['version'].split('.')) >= (1,17,0):
                    scripts.append('test_studio.py')
                if tuple(int(x) for x in manifest['version'].split('.')) >= (1,18,0):
                    scripts.append('test_casework.py')
                if tuple(int(x) for x in manifest['version'].split('.')) >= (1,19,0):
                    scripts.append('test_normalization.py')
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 13, 0):
                    scripts.append("test_research.py")
                    checks.append(("persistent research and exact bundle oracle", [py,"tests/research_oracle.py","--trials","1000" if profile == "release" else "50","--events","250" if profile == "release" else "40","--out",str(Path(tmp)/"research-oracle.json")], True, {}))
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 12, 0):
                    scripts.extend(["test_integrations.py", "test_qulap_embedded.py"])
                    for launcher in ("loom", "loom-lang"):
                        checks.append(("extracted " + launcher + " launcher", [str(root/"bin"/launcher), "version"], False, {}))
                    checks.append(("embedded QULAP finite-state oracle", [py,"tests/qulap_oracle.py","core","--trials","2500" if profile == "release" else "100","--out",str(Path(tmp)/"qulap-core.json")], True, {}))
                    checks.append(("embedded QULAP quantum and replay oracle", [py,"tests/qulap_oracle.py","quantum","--trials","100" if profile == "release" else "10","--surd",str(root/"surd.py"),"--out",str(Path(tmp)/"qulap-quantum.json")], True, {}))
                if (root/"tests/test_assumption_frontiers.py").is_file():
                    scripts.append("test_assumption_frontiers.py")
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 8, 0):
                    scripts.append("test_runtime_upgrade.py")
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 9, 0):
                    scripts.append("test_posture.py")
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 9, 1):
                    scripts.extend(["test_review_regressions.py", "test_improve.py"])
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 10, 0):
                    scripts.append("test_collections.py")
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 11, 0):
                    scripts.append("test_quantum_reasoning.py")
                for script in scripts:
                    checks.append((script, [py, "tests/"+script], False, {}))
                checks.append(("builtin documentation", [py, "surd.py", "describe", "--json"], "builtins", {}))
                checks.append(("reasoning DAG oracle", [py, "tests/reasoning_oracle.py", "--cases", "2500" if profile == "release" else "100"], True, {}))
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 9, 0):
                    checks.append(("posture origin and branch oracle", [py, "tests/posture_oracle.py", "--cases", "1500" if profile == "release" else "100"], True, {}))
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 10, 0):
                    checks.append(("collection pipeline oracle", [py, "tests/collections_oracle.py", "--cases", "2500" if profile == "release" else "100"], True, {}))
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 11, 0):
                    checks.append(("quantum reasoning independent circuit oracle", [py,"tests/quantum_reasoning_oracle.py","--cases","1000" if profile == "release" else "50"], True, {}))
                for path in sorted((root/"examples").glob("*.surd")):
                    for seed in ((1, 2, 3) if profile == "release" else (1,)):
                        checks.append((path.name+" seed "+str(seed), [py,"surd.py","run",str(path.relative_to(root)),"--seed",str(seed)], False, {}))
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 12, 0):
                    for path in sorted((root/"examples").glob("*.loom")):
                        checks.append((path.name, [py,"loom.py","run",str(path.relative_to(root))], False, {}))
                if profile == "release":
                    checks.extend([("selftest", [py,"surd.py","selftest"], False, {}), ("selftest without numpy", [py,"surd.py","selftest"], False, {"SURD_NO_NUMPY":"1"})])
            else:
                if tuple(int(x) for x in manifest["version"].split(".")) >= (1, 9, 1):
                    checks.append(("fuzz harness watchdog and phase checks", [py, "tests/test_fuzz_harness.py"], False, {}))
                checks.append(("independent numpy oracle", [py,"tests/fuzz_surd.py"], True, {}))
            for label, command, require_json, overrides in checks:
                began = time.monotonic()
                try:
                    p = subprocess.run(command, cwd=root, env=dict(env, **overrides), capture_output=True, text=True, timeout=300)
                    state = verdict(p.returncode, p.stdout+ (p.stderr if not require_json else ""), require_json is True)
                    if require_json == "builtins" and p.returncode == 0:
                        data = json.loads(p.stdout)
                        builtin_result = builtin_verdict(data)
                        state = builtin_result["state"]
                        report["builtin_coverage"] = builtin_result
                    row = {"name": label, "state": state, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
                except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                    row = {"name": label, "state": "failed", "error": str(e)}
                row["seconds"] = round(time.monotonic()-began, 4)
                report["checks"].append(row)
                print(label+": "+row["state"], file=sys.stderr, flush=True)
            verify_tree(root)
            if (root/"BUILD_MANIFEST.json").read_bytes() != manifest_bytes:
                raise BuildError("manifest changed during validation")
            if sha(archive.read_bytes()) != report["archive_sha256"]:
                raise BuildError("archive changed during validation")
            report["ok"] = bool(checks) and len(report["checks"]) == len(checks) and all(c["state"] == "passed" for c in report["checks"])
    except (OSError, ValueError, KeyError) as e:
        report["error"] = str(e)
    report["counts"] = {state: sum(c["state"] == state for c in report["checks"]) for state in ("passed", "failed", "skipped")}
    report["seconds"] = round(time.monotonic()-started, 4)
    report_path.write_bytes(encoded(report))
    return report


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive"); parser.add_argument("--profile", choices=("quick","release","fuzz"), default="quick"); parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = validate(args.archive, args.profile, args.report)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(cli())
