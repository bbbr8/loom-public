"""Deterministic, allowlisted source releases and module-boundary checks (stdlib only)."""
import argparse
import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

SCHEMA = "surd.build.v1"
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 512


class BuildError(ValueError):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name:
        raise BuildError("unsafe release path: " + repr(name))
    return p


def architecture(root):
    root = Path(root)
    sources = {p.stem: p.read_text() for p in root.glob("surd*.py")}
    return _architecture(sources)


def _architecture(sources):
    graph = {}
    for name, source in sorted(sources.items()):
        try:
            tree = ast.parse(source, filename=name + ".py")
        except SyntaxError as e:
            raise BuildError(str(e)) from e
        deps = set()
        for node in ast.walk(tree):
            imports = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in imports:
                module = module.split(".")[0]
                if module == "surd" or module.startswith("surd_"):
                    if module not in sources:
                        raise BuildError(name + " imports missing module " + module)
                    deps.add(module)
            if name == "surd_reason" and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "index" and node.attr.startswith("_"):
                raise BuildError("reasoning uses private corpus member: " + node.attr)
        graph[name] = sorted(deps)
    for name, allowed in {"surd_search_core": set(), "surd_collections": set(), "surd_quantum_core": set(), "surd_reason": {"surd_search_core"}}.items():
        if name in graph and not set(graph[name]) <= allowed:
            raise BuildError(name + " violates its dependency boundary")
    active, done, order = [], set(), []
    def visit(name):
        if name in active:
            raise BuildError("module cycle: " + " -> ".join(active + [name]))
        if name in done:
            return
        active.append(name)
        for dep in graph[name]:
            visit(dep)
        active.pop(); done.add(name); order.append(name)
    for name in graph:
        visit(name)
    if not graph:
        raise BuildError("no Surd modules found")
    return {"ok": True, "dependencies": graph, "dependency_order": order}


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    config_path = root / "surd.build.json"
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    names = config.get("files")
    if config.get("schema") != SCHEMA or not isinstance(names, list) or not names or len(names) > MAX_FILES or len(set(names)) != len(names):
        raise BuildError("invalid build allowlist")
    payload = {}
    for name in sorted(names):
        safe_name(name)
        path = root / name
        if any((root.joinpath(*PurePosixPath(name).parts[:i])).is_symlink() for i in range(1, len(PurePosixPath(name).parts) + 1)) or not path.is_file():
            raise BuildError("missing or linked build input: " + name)
        payload[name] = path.read_bytes()
    if payload.get("surd.build.json") != config_bytes:
        raise BuildError("build config must be included unchanged")
    if sum(map(len, payload.values())) > MAX_BYTES:
        raise BuildError("release exceeds size budget")
    graph = _architecture({Path(n).stem: b.decode() for n, b in payload.items() if "/" not in n and n.startswith("surd") and n.endswith(".py")})
    version_match = re.search(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', payload["surd.py"].decode(), re.M)
    if not version_match:
        raise BuildError("missing semantic runtime version")
    version = version_match.group(1)
    records = {name: {"sha256": sha(data), "bytes": len(data), "mode": 0o755 if name in ("bin/surd", "bin/loom", "bin/loom-lang", "gate.sh") else 0o644} for name, data in sorted(payload.items())}
    manifest = {"schema": SCHEMA, "version": version, "python_minimum": "3.9", "files": records, "architecture": graph}
    manifest_bytes = encoded(manifest)
    payload["BUILD_MANIFEST.json"] = manifest_bytes
    try:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        if not 0 <= epoch <= 4294967295:
            raise ValueError()
    except ValueError:
        raise BuildError("SOURCE_DATE_EPOCH must be an integer in 0..4294967295")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".surd-build-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9) as gz:
                with tarfile.open(mode="w", fileobj=gz, format=tarfile.USTAR_FORMAT) as tar:
                    for name, data in sorted(payload.items()):
                        member = tarfile.TarInfo("surd-" + version + "/" + name)
                        member.size = len(data); member.mtime = epoch
                        member.mode = records.get(name, {}).get("mode", 0o644)
                        tar.addfile(member, io.BytesIO(data))
        for name in names:
            if (root / name).read_bytes() != payload[name]:
                raise BuildError("source changed during build: " + name)
        os.replace(temp, output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"ok": True, "archive": str(output), "archive_sha256": sha(output.read_bytes()), "manifest_sha256": sha(manifest_bytes), "manifest": manifest}


def verify_tree(root):
    root = Path(root)
    manifest = json.loads((root / "BUILD_MANIFEST.json").read_bytes())
    records = manifest.get("files", {})
    if manifest.get("schema") != SCHEMA or not records or len(records) > MAX_FILES:
        raise BuildError("invalid release manifest")
    for name, record in records.items():
        safe_name(name); path = root / name
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise BuildError("release integrity failed: " + name)
        if path.stat().st_mode & 0o777 != record["mode"]:
            raise BuildError("release mode changed: " + name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if actual != set(records) | {"BUILD_MANIFEST.json"}:
        raise BuildError("unmanifested or missing release files")
    return manifest


def unpack(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise BuildError("extraction destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".surd-unpack-", dir=destination.parent) as tmp:
        tmp = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            members, names, prefixes, size = [], set(), set(), 0
            for member in tar:
                path = safe_name(member.name)
                size += member.size
                if not member.isfile() or len(path.parts) < 2 or member.name in names or len(names) >= MAX_FILES + 1 or size > MAX_BYTES or member.size < 0:
                    raise BuildError("invalid or oversized release member: " + member.name)
                names.add(member.name); prefixes.add(path.parts[0]); members.append(member)
            if len(prefixes) != 1:
                raise BuildError("release must have one root")
            prefix = next(iter(prefixes))
            if not re.fullmatch(r"surd-[0-9]+\.[0-9]+\.[0-9]+", prefix):
                raise BuildError("invalid release root")
            for member in members:
                path = tmp / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(member.mode & 0o777)
        manifest = verify_tree(tmp / prefix)
        if prefix != "surd-" + manifest["version"]:
            raise BuildError("manifest version differs from release root")
        os.replace(tmp / prefix, destination)
    return destination


def cli(argv=None):
    parser = argparse.ArgumentParser(prog="surd build", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check", help="validate the local module graph")
    p = sub.add_parser("pack", help="build a reproducible portable source release")
    p.add_argument("--output", required=True)
    p = sub.add_parser("inspect", help="extract and verify a release manifest")
    p.add_argument("archive")
    p = sub.add_parser("verify", help="test a verified, isolated release")
    p.add_argument("archive"); p.add_argument("--profile", choices=("quick", "release", "fuzz"), default="quick")
    p.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    try:
        if args.action == "check":
            result = architecture(root)
        elif args.action == "pack":
            result = build(root, args.output)
        elif args.action == "inspect":
            with tempfile.TemporaryDirectory() as tmp:
                result = {"ok": True, "manifest": verify_tree(unpack(args.archive, Path(tmp)/"release"))}
        else:
            return subprocess.call([sys.executable, str(root/"surd_release.py"), args.archive, "--profile", args.profile, "--report", args.report])
        print(json.dumps(result, sort_keys=True)); return 0
    except (BuildError, OSError, ValueError, tarfile.TarError) as e:
        print(json.dumps({"ok": False, "error": str(e)})); return 1


if __name__ == "__main__":
    sys.exit(cli())
