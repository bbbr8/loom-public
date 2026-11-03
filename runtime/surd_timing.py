"""surd_timing — exact timing test: do statements land nearer money events than their own calendar would by chance?

hits(A, B, before, after): statements a in A with an event b in B at lag b − a in [−before, +after] days.
The null keeps both calendars and slides the statements around the span as a circle, one day at a time, through EVERY
offset (no sampling): p = share of offsets whose hit count is at least the observed one; expected = the mean. Exact
fractions. Pure module (stdlib only); surd_ext wraps it as the Surd built-in timing(statements, events, before, after).
"""
from bisect import bisect_left
from fractions import Fraction as F


def _hits(A, B, before, after):
    out = []
    for a in A:
        i = bisect_left(B, a - before)
        if i < len(B) and B[i] <= a + after:
            out.append((a, B[i]))
    return out


def test(A, B, before, after, lo=None, hi=None):
    """A, B: day ordinals. Returns observed, expected, p (Fractions), offsets, the hits and the per-offset counts."""
    if before < 0 or after < 0:
        raise ValueError("before and after are day counts (0 or more)")
    A, B = sorted(A), sorted(B)
    if not A or not B:
        raise ValueError("timing needs at least one statement and one event")
    lo = min(A[0], B[0]) if lo is None else lo
    hi = max(A[-1], B[-1]) if hi is None else hi
    L = hi - lo + 1
    obs = _hits(A, B, before, after)
    counts = []
    for k in range(L):
        shifted = sorted(lo + ((a - lo + k) % L) for a in A)
        counts.append(len(_hits(shifted, B, before, after)))
    n_obs = len(obs)
    p = F(sum(1 for c in counts if c >= n_obs), L)
    return {"observed": n_obs, "statements": len(A), "events": len(B), "expected": F(sum(counts), L), "p": p,
            "offsets": L, "hits": obs, "lo": lo, "hi": hi}
