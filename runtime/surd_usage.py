"""Bounded local use feedback. Stores metadata only; never edits runtime semantics."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

LIMIT = 2048
COMMANDS = {"run", "check", "qasm", "opt", "draw", "search"}


def location():
    return Path(os.environ.get("SURD_USAGE_STATE", str(Path(__file__).resolve().parent / "state/usage.sqlite")))


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=0.2)
    db.execute("CREATE TABLE IF NOT EXISTS uses (id TEXT PRIMARY KEY, created REAL, command TEXT, release TEXT, seconds REAL, exit_code INTEGER, outcome TEXT, source_sha256 TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS feedback (use_id TEXT PRIMARY KEY REFERENCES uses(id), category TEXT, note TEXT)")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def source_hash(argv):
    # Never retain program contents, query text, filenames, or command arguments.
    if len(argv) < 2 or argv[0] == "search" or argv[1].startswith("-"):
        return None
    try:
        with open(argv[1], "rb") as src:
            data = src.read(1024 * 1024 + 1)
        return hashlib.sha256(data).hexdigest() if len(data) <= 1024 * 1024 else None
    except OSError:
        return None


def record(argv, release, seconds, exit_code, outcome, source_sha256=None, path=None):
    if not argv or argv[0] not in COMMANDS:
        return None
    db = connect(path or location())
    try:
        with db:
            if db.execute("SELECT count(*) FROM uses").fetchone()[0] >= LIMIT:
                raise ValueError("use receipt limit reached; archive the database explicitly")
            key = uuid.uuid4().hex
            db.execute("INSERT INTO uses VALUES (?,?,?,?,?,?,?,?)", (key, time.time(), argv[0], release, seconds, exit_code, outcome, source_sha256))
        return key
    finally:
        db.close()


def review(path=None):
    db = connect(path or location())
    try:
        totals = dict(db.execute("SELECT outcome,count(*) FROM uses GROUP BY outcome"))
        rows = db.execute("SELECT u.id,u.command,u.release,u.outcome,u.seconds,f.category,f.note FROM uses u LEFT JOIN feedback f ON f.use_id=u.id WHERE u.outcome != 'completed' OR f.category IN ('wrong-result','slow','confusing') ORDER BY u.created DESC LIMIT 20").fetchall()
        return {"ok": True, "uses": sum(totals.values()), "outcomes": totals,
                "recent_uses": [dict(zip(("id","command","release","outcome","seconds"), row)) for row in db.execute("SELECT id,command,release,outcome,seconds FROM uses ORDER BY created DESC LIMIT 10")],
                "remaining": LIMIT-sum(totals.values()), "review_queue": [dict(zip(("id","command","release","outcome","seconds","category","note"), r)) for r in rows],
                "next_step": "Inspect a queued use; state an independent expected answer; pin its baseline; repair and run improve cycle with original, transfer and control.",
                "scope": "CLI use metadata; successful execution is unverified correctness; no automatic runtime edits or model training"}
    finally:
        db.close()


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["review", "feedback"])
    parser.add_argument("--state")
    parser.add_argument("--use-id")
    parser.add_argument("--category", choices=["correct","wrong-result","slow","confusing"])
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "feedback":
            if not args.use_id or not args.category or len(args.note) > 2000:
                raise ValueError("feedback needs --use-id, --category and a note of at most 2000 characters")
            db = connect(args.state or location())
            try:
                with db:
                    if not db.execute("SELECT 1 FROM uses WHERE id=?", (args.use_id,)).fetchone():
                        raise ValueError("unknown use ID")
                    db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?)", (args.use_id,args.category,args.note))
            finally:
                db.close()
        result = review(args.state)
    except (OSError, ValueError, sqlite3.Error) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1
