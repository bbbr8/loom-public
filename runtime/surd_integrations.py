"""Portable QULAP adapter; retains the finite-state engine's result contracts."""
from pathlib import Path
import sqlite3
from decimal import DecimalException

from loom_qulap.cli import read_json
from loom_qulap.model import ModelError, solve, contradiction_core, sensitivity
from loom_qulap.accounting import allocation_model
from loom_qulap.temporal import asof_receipt
from loom_qulap.bind import bind
from loom_qulap.surd_bridge import quantum_search
from loom_research.cli import FUNCTIONS as RESEARCH_FUNCTIONS
from loom_sqlite import FUNCTIONS as SQLITE_FUNCTIONS
from loom_research.ontology import FUNCTIONS as ONTOLOGY_FUNCTIONS
from loom_research.normalization import FUNCTIONS as NORMALIZATION_FUNCTIONS

FUNCTIONS = {"qulap_solve", "qulap_core", "qulap_sensitivity", "qulap_allocations",
             "qulap_asof", "qulap_bind", "qulap_quantum", "timeline_run"} | RESEARCH_FUNCTIONS | SQLITE_FUNCTIONS | set(ONTOLOGY_FUNCTIONS) | NORMALIZATION_FUNCTIONS


def call(name, values):
    if name in NORMALIZATION_FUNCTIONS:
        from loom_research.normalization import call as normalization_call
        return normalization_call(name,values)
    if name in ONTOLOGY_FUNCTIONS:
        from loom_research.ontology import call as ontology_call
        return ontology_call(name,values)
    if name in SQLITE_FUNCTIONS:
        from loom_sqlite import call as sqlite_call
        try:return sqlite_call(name,values)
        except (sqlite3.Error,OSError) as exc:raise ValueError(str(exc)) from exc
    if name in RESEARCH_FUNCTIONS:
        from loom_research.cli import call as research_call
        try:
            return research_call(name,values)
        except sqlite3.Error as e:
            raise ValueError(str(e)) from e
    if name == "timeline_run":
        from surd_timeline import run
        if len(values) not in (1, 2):
            raise ValueError("timeline_run takes a program and optional options map")
        if len(values) == 2 and not isinstance(values[1], dict):
            raise ValueError("timeline options must be a map")
        try:
            return run(values[0], **(values[1] if len(values) == 2 else {}))
        except (sqlite3.Error, DecimalException) as e:
            raise ValueError(str(e)) from e
    if not values:
        raise ValueError(name + " needs a packet map or JSON path")
    packet = read_json(values[0]) if isinstance(values[0], str) else values[0]
    if not isinstance(packet, dict):
        raise ModelError("packet must be a map or JSON object path")
    if name == "qulap_asof":
        if len(values) != 3:
            raise ModelError("qulap_asof(packet, actor, cutoff)")
        return asof_receipt(packet, values[1], values[2])
    if len(values) not in (1, 2):
        raise ModelError(name + " takes a packet and optional options map")
    options = values[1] if len(values) == 2 else {}
    if not isinstance(options, dict):
        raise ModelError("options must be a map")
    allowed = {"max_nodes", "seconds", "retain_worlds"} if name == "qulap_solve" else {"max_nodes", "seconds"}
    if name == "qulap_quantum":
        allowed.add("proposition")
    if name in ("qulap_allocations", "qulap_bind"):
        allowed = set()
    if set(options) - allowed:
        raise ModelError("unknown options: " + ", ".join(sorted(set(options) - allowed)))
    if name == "qulap_quantum":
        return quantum_search(packet, str(Path(__file__).with_name("surd.py")), **options)
    return {"qulap_solve": solve, "qulap_core": contradiction_core,
            "qulap_sensitivity": sensitivity, "qulap_allocations": allocation_model,
            "qulap_bind": bind}[name](packet, **options)


def cli(args):
    from loom_qulap.cli import main
    return main(args)
