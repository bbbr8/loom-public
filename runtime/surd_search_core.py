"""Shared search values, validation and public reasoning port. Standard library only."""
import hashlib
import json
import math
import re
import unicodedata
from typing import Protocol, runtime_checkable


class SearchError(ValueError):
    def __init__(self, message):
        super().__init__(message)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as e:
        raise SearchError(f"value must be finite JSON: {e}") from e


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return re.findall(r"\w+(?:[.:/@-]\w+)*", text, re.UNICODE)


def integer(value, name, low=1, high=1000):
    if type(value) is not int or not low <= value <= high:
        raise SearchError(f"{name} must be an integer in {low}..{high}")
    return value


def number(value, name, low=0, high=100):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise SearchError(f"{name} must be finite in {low}..{high}")
    return float(value)


def options(value, allowed):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise SearchError(f"options must be a map using: {', '.join(sorted(allowed))}")
    return value


def validate_scope(where, ranges):
    if not isinstance(where, dict) or not isinstance(ranges, dict) or any(not isinstance(b, dict) or not b or set(b) - {"gte", "lte"} for b in ranges.values()):
        raise SearchError("where is an exact-match map; ranges maps fields to gte/lte bounds")
    scalar = lambda v: v is None or type(v) in (str, int, float, bool)
    if any(not isinstance(f, str) or not all(scalar(v) for v in (vs if isinstance(vs, list) else [vs])) for f, vs in where.items()):
        raise SearchError("where values must be JSON scalars or lists of scalars")
    for f, bounds in ranges.items():
        if not isinstance(f, str) or any(type(v) not in (str, int, float) for v in bounds.values()):
            raise SearchError("range bounds must be strings or finite numbers")
        if "gte" in bounds and "lte" in bounds:
            try:
                if bounds["gte"] > bounds["lte"]: raise SearchError("range floor exceeds ceiling")
            except TypeError as e:
                raise SearchError("range bounds have incompatible types") from e
    canonical([where, ranges])
    return where, ranges


@runtime_checkable
class ReasoningCorpus(Protocol):
    """Read-only source boundary used by argument auditing.

    Implementations return independent document snapshots. Structural runtime
    checking verifies method availability; adapters must honor these semantics.
    """
    def search(self, query, opts=None): ...
    def document(self, ident): ...
    def documents(self, where=None, ranges=None): ...
    def document_tokens(self, ident): ...
    def in_scope(self, ident, where=None, ranges=None): ...
    def manifest(self): ...
