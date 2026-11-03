#!/usr/bin/env python3
"""Loom — a qubit programming language where wires receive gates and gates are exact values.

Quantum-circuit semantics on a classical simulator. Supported dyadic phases use exact cyclotomic
arithmetic through pi/32, including Clifford+T circuits. Probabilities print in exact closed form.
Unsupported angles such as rx(0.3) move the run to complex floating point with a visible note.

    loom graph init | ingest | ingest-files | search | paths | overview | asof | diff | verify ...
    loom research run | plan | compile | learn | methods | bind | unbind | demo ...
    loom timeline FILE | -e PROGRAM [--store DB] [--ledger DB] [--json]
    loom qulap solve | core | sensitivity | quantum | cube | bind | asof ...
    loom-lang run FILE [--seed S] [--shots N] [--float] [--mixed] [--json]
    loom-lang draw FILE           ascii circuit of one run
    loom-lang stats FILE          gate counts, T-count, depth
    loom-lang opt FILE [--trace] [--require-proof]  optimized source with verification status
    loom-lang qasm FILE [--trace] OpenQASM 2.0 export
    loom-lang check FILE          parse only
    loom-lang from-qasm FILE      OpenQASM 2.0 -> Loom source
    loom-lang explain FILE        plain-English walk through a program, with the qubits after each step
    loom-lang eval 'EXPR' [--json] one expression (earlier lines may set things up)
    execution limits: --max-steps N [--timeout SECONDS] on run, eval, test and serve
    loom-lang test FILE [--json]  run the program's test "…" { } blocks and expects
    loom-lang serve               JSON-lines session: exec eval reset describe status
    loom-lang search build | query | evaluate | compare | inspect | fuse | reason | posture | quantum
    loom-lang build check | pack | inspect | verify
    loom-lang improve cycle | check | status | retire  bounded regression curriculum
    loom-lang repl | selftest | describe [--json] [--integrations] | version

stdlib only. Simulation on a classical computer — no quantum hardware is touched.
"""
import cmath
import difflib
from collections import ChainMap
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from contextlib import contextmanager
from fractions import Fraction as F
from functools import lru_cache
import surd_search
import surd_collections
import surd_bank
import surd_ext

VERSION = "1.19.1"
sys.setrecursionlimit(max(sys.getrecursionlimit(), 12000))   # Loom functions recurse through the interpreter
SQRT2 = math.sqrt(2.0)
TOL = 1e-9
MAX_QUBITS = 24
MAX_EXACT = 14
MAX_MIXED = 10
MAX_MIXED_EXACT = 6
F0, F1 = F(0), F(1)


# ───────────────────────────── errors ─────────────────────────────
class SurdError(Exception):
    def __init__(self, code, msg, pos=None, hint=None):
        super().__init__(msg)
        self.code, self.msg, self.pos, self.hint = code, msg, pos, hint

    def render(self):
        where = f" line {self.pos[0]}:{self.pos[1]}" if self.pos else ""
        s = f"{self.code}{where}: {self.msg}"
        return s + (f"\n  hint: {self.hint}" if self.hint else "")

    def as_dict(self):
        return {"code": self.code, "message": self.msg,
                "line": self.pos[0] if self.pos else None, "col": self.pos[1] if self.pos else None,
                "hint": self.hint}


ERRORS = {
    "S-E001": "syntax error", "S-E002": "unknown name", "S-E003": "unknown gate",
    "S-E004": "wrong number of wires or parameters", "S-E005": "index out of range",
    "S-E006": "same wire used twice in one operation", "S-E007": "non-unitary statement inside a gate body",
    "S-E008": "expectation failed", "S-E009": "type error", "S-E010": "too many qubits",
    "S-E011": "redeclaration", "S-E012": "length mismatch (broadcast / measure)",
    "S-E013": "cannot export to OpenQASM", "S-E014": "gate recursion too deep", "S-E015": "division by zero", "S-E016": "invalid search or evaluation input",
    "S-E017": "execution budget exhausted",
}


class ExecutionBudget:
    """Cooperative, shared budget. Nested runs and shots share one outer execution."""
    def __init__(self, max_steps=None, timeout=None):
        if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
            raise SurdError("S-E004", "max_steps must be a positive whole number", None, "use --max-steps 100000")
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise SurdError("S-E004", "timeout must be finite and positive", None, "use --timeout 10")
        self.max_steps, self.timeout = max_steps, timeout
        self.steps, self.deadline, self.depth = 0, None, 0

    @contextmanager
    def execution(self):
        if self.depth == 0:
            self.steps = 0
            self.deadline = time.monotonic() + self.timeout if self.timeout is not None else None
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def checkpoint(self, pos=None):
        if not self.depth or (self.max_steps is None and self.deadline is None): return
        self.steps += 1
        if self.max_steps is not None and self.steps > self.max_steps:
            raise SurdError("S-E017", f"execution exceeded {self.max_steps} language steps", pos,
                            "reduce the work or raise --max-steps; earlier mutations remain in the session")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SurdError("S-E017", f"execution exceeded {self.timeout:g} seconds at a language checkpoint", pos,
                            "reduce the work or raise --timeout; native calls need an external process timeout")

    def info(self):
        return {"max_steps": self.max_steps, "timeout_seconds": self.timeout, "steps": self.steps,
                "policy": "cooperative-language-checkpoints-v1"}


# ───────────────────────────── exact numbers ─────────────────────────────
def fmt_frac(x):
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


MAX_LEVEL = 64          # largest cyclotomic order kept exact: phases k·π/32 (QFT phases to π/32)


def _reduce(n):
    """canonical form: drop to the smallest power-of-two level that holds the number"""
    while len(n) > 1 and not any(n[1::2]):
        n = n[0::2]
    return n


def _lift(n, k):
    m = len(n)
    if m == k: return n
    step = k // m
    out = [0] * k
    for i, x in enumerate(n): out[i * step] = x
    return tuple(out)


_gcd = math.gcd


def _mki(n, d):
    """build a QW from integer numerators n and a positive denominator d, normalized"""
    o = QW.__new__(QW)
    n = _reduce(n)
    if not any(n):
        o.n, o.d = (0,), 1
        return o
    g = _gcd(d, *n)
    if g != 1:
        n = tuple(x // g for x in n); d //= g
    o.n, o.d = n, d
    return o


def _mk(t):
    """build a QW from rationals (Fractions or ints): one common denominator, integer numerators"""
    t = tuple(x if type(x) is F else F(x) for x in t)
    d = 1
    for x in t:
        d = d * x.denominator // _gcd(d, x.denominator)
    return _mki(tuple(x.numerator * (d // x.denominator) for x in t), d)


class QW:
    """Exact element of the cyclotomic field Q(ζ_N), N = 2·len(n) a power of two:
    (n0 + n1·ζ + … + n_{k-1}·ζ^{k-1}) / d with ζ = e^{iπ/k}, ζ^k = −1, integer n, positive d, gcd 1.
    Kept at the smallest level that holds it, so Clifford+T numbers stay in Q(e^{iπ/4}) and only dyadic
    phases (π/8, π/16, …) raise the level. Integers instead of Fractions: one gcd per operation."""
    __slots__ = ("n", "d")

    def __init__(self, c0=0, c1=0, c2=0, c3=0):
        q = _mk((c0, c1, c2, c3))
        self.n, self.d = q.n, q.d

    @property
    def c(self):
        """the coefficients as Fractions (for readers outside the hot paths)"""
        d = self.d
        return tuple(F(x, d) for x in self.n)

    def __add__(self, o):
        if type(o) is not QW: return NotImplemented
        a, b, da, db = self.n, o.n, self.d, o.d
        if len(a) != len(b):
            k = max(len(a), len(b)); a, b = _lift(a, k), _lift(b, k)
        if da == db:
            return _mki(tuple(x + y for x, y in zip(a, b)), da)
        return _mki(tuple(x * db + y * da for x, y in zip(a, b)), da * db)

    def __sub__(self, o):
        if type(o) is not QW: return NotImplemented
        a, b, da, db = self.n, o.n, self.d, o.d
        if len(a) != len(b):
            k = max(len(a), len(b)); a, b = _lift(a, k), _lift(b, k)
        if da == db:
            return _mki(tuple(x - y for x, y in zip(a, b)), da)
        return _mki(tuple(x * db - y * da for x, y in zip(a, b)), da * db)

    def __neg__(self):
        o = QW.__new__(QW)
        o.n, o.d = tuple(-x for x in self.n), self.d
        return o

    def __mul__(self, o):
        if type(o) is not QW: return NotImplemented
        a, b = self.n, o.n
        d = self.d * o.d
        if len(b) == 1:
            b0 = b[0]
            return _mki(tuple(x * b0 for x in a), d) if b0 else QZ
        if len(a) == 1:
            a0 = a[0]
            return _mki(tuple(a0 * y for y in b), d) if a0 else QZ
        if len(a) == len(b) == 4:
            a0, a1, a2, a3 = a
            b0, b1, b2, b3 = b
            return _mki((a0 * b0 - a1 * b3 - a2 * b2 - a3 * b1,
                         a0 * b1 + a1 * b0 - a2 * b3 - a3 * b2,
                         a0 * b2 + a1 * b1 + a2 * b0 - a3 * b3,
                         a0 * b3 + a1 * b2 + a2 * b1 + a3 * b0), d)
        if len(a) != len(b):
            k = max(len(a), len(b)); a, b = _lift(a, k), _lift(b, k)
        k = len(a)
        out = [0] * k
        for i, x in enumerate(a):
            if not x: continue
            for j, y in enumerate(b):
                if not y: continue
                m = i + j
                if m < k: out[m] += x * y
                else: out[m - k] -= x * y
        return _mki(tuple(out), d)

    def conj(self):
        n = self.n
        k = len(n)
        if k == 1: return self
        o = QW.__new__(QW)
        o.n, o.d = (n[0],) + tuple(-n[k - j] for j in range(1, k)), self.d
        return o

    def galois(self, j):
        """σ_j: ζ_N -> ζ_N^j (j odd)"""
        n = self.n
        k = len(n)
        N = 2 * k
        out = [0] * k
        for i, x in enumerate(n):
            if not x: continue
            e = (i * j) % N
            if e < k: out[e] += x
            else: out[e - k] -= x
        return _mki(tuple(out), self.d)

    def is_zero(self):
        return not any(self.n)

    def norm2(self): return R2.of(self * self.conj())
    def re(self): return R2.of((self + self.conj()) * _HALF)
    def im(self): return R2.of((self - self.conj()) * _MHALF_I)

    def __complex__(self):
        n, d = self.n, self.d
        k = len(n)
        if k == 1: return complex(n[0] / d)
        if k == 4:
            return (n[0] + n[1] * _W + n[2] * 1j + n[3] * _W3) / d
        return sum((x * cmath.exp(1j * math.pi * j / k) for j, x in enumerate(n) if x), 0j) / d

    @staticmethod
    def from_r2(r):
        return r.q

    @staticmethod
    def omega(j, N=8):
        """ζ_N^j"""
        k = N // 2
        j %= N
        t = [0] * k
        if j < k: t[j] = 1
        else: t[j - k] = -1
        return _mki(tuple(t), 1)

    def __str__(self):
        re, im = self.re(), self.im()
        if im.is_zero():
            return str(re)

        def istr(v):
            s = str(v)
            if " + " in s or " - " in s:
                return f"({s})i"
            return "i" if s == "1" else f"{s}·i"
        neg = im.sign() < 0
        if re.is_zero():
            return ("-" if neg else "") + istr(-im if neg else im)
        rs = str(re)
        if " + " in rs or " - " in rs: rs = f"({rs})"
        return f"{rs} {'-' if neg else '+'} {istr(-im if neg else im)}"


_W = cmath.exp(1j * math.pi / 4)
_W3 = _W ** 3
QZ = QW.__new__(QW); QZ.n, QZ.d = (0,), 1
Q1 = _mki((1,), 1)
QI = _mki((0, 1), 1)                       # i = ζ_4
_HALF = _mki((1,), 2)
_MHALF_I = _mki((0, -1), 2)                # −i/2
QH = _mki((0, 1, 0, -1), 2)                # 1/√2 = (ζ8 − ζ8³)/2


def _term(r, s):
    n, d = abs(r).numerator, abs(r).denominator
    return ("" if n == 1 else str(n)) + s + ("" if d == 1 else f"/{d}")


_RAD16 = {1: "√(2+√2)", 2: "√2", 3: "√(2−√2)"}      # 2cos(jπ/8)


def _cos_dec(j, k, prec=80):
    """cos(jπ/k) to prec digits, k a power of two: half-angle from cos(π/2) = 0, then Chebyshev"""
    import decimal
    ctx = decimal.Context(prec=prec + 10)
    c, kk = ctx.create_decimal(0), 2
    while kk < k:
        c = ctx.sqrt(ctx.divide(ctx.add(1, c), 2)); kk *= 2
    if k == 1: c = ctx.create_decimal(-1)
    t0, t1 = ctx.create_decimal(1), c
    if j == 0: return t0
    for _ in range(j - 1):
        t0, t1 = t1, ctx.subtract(ctx.multiply(ctx.multiply(2, c), t1), t0)
    return t1


@lru_cache(maxsize=128)
def _real_cos_bounds(k, bits):
    """Certified integer intervals for cos(j*pi/k), scaled by 2**bits.

    Half-angle square roots use integer floor/ceiling bounds; interval
    Chebyshev recurrence preserves containment. No floating point decides a sign.
    """
    scale = 1 << bits
    lo = hi = 0
    kk = 2
    while kk < k:
        lo = math.isqrt(((scale + lo) * scale) // 2)
        hi = math.isqrt(((scale + hi) * scale + 1) // 2) + 1
        kk *= 2
    out = [(scale, scale), (lo, hi)]
    for j in range(2, k // 2):
        a, b = out[-1]
        products = (lo*a, lo*b, hi*a, hi*b)
        pl, ph = min(products) // scale, -(-max(products) // scale)
        prev_lo, prev_hi = out[-2]
        out.append((2*pl-prev_hi, 2*ph-prev_lo))
    return tuple(out)


class R2:
    """Exact real number in a cyclotomic field. R2(a, b) = a + b·√2 (the Clifford+T reals); dyadic phases make
    reals like √(2+√2) = 2cos(π/8). The name is historical: arithmetic is exact at any supported level."""
    __slots__ = ("q",)

    def __init__(self, a=0, b=0):
        a = a if type(a) is F else F(a)
        b = b if type(b) is F else F(b)
        self.q = _mk((a, b, F0, -b)) if b else _mk((a,))

    @staticmethod
    def of(q):
        o = R2.__new__(R2)
        o.q = q
        return o

    @property
    def a(self): return F(self.q.n[0], self.q.d)

    @property
    def b(self):
        n = self.q.n
        return F(n[1], self.q.d) if len(n) == 4 else F0

    def __add__(self, o): return R2.of(self.q + o.q) if type(o) is R2 else NotImplemented
    def __sub__(self, o): return R2.of(self.q - o.q) if type(o) is R2 else NotImplemented
    def __mul__(self, o): return R2.of(self.q * o.q) if type(o) is R2 else NotImplemented
    def __neg__(self): return R2.of(-self.q)

    def inv(self):
        if self.is_zero(): raise ZeroDivisionError
        c = self.q.c
        if len(c) == 1: return R2.of(_mk((1 / c[0],)))
        N = 2 * len(c)
        rest = Q1
        for j in range(3, N, 2):                  # product of the other Galois conjugates
            rest = rest * self.q.galois(j)
        nrm = (self.q * rest).c                   # the field norm: a rational
        return R2.of(rest * _mk((1 / nrm[0],)))

    def __truediv__(self, o): return self * o.inv() if type(o) is R2 else NotImplemented
    def is_zero(self): return not any(self.q.n)
    def is_rational(self): return len(self.q.n) == 1

    def __float__(self):
        n, d = self.q.n, self.q.d
        if len(n) == 1: return n[0] / d
        if len(n) == 4: return (n[0] + n[1] * SQRT2) / d
        k = len(n)
        return (n[0] + sum(2 * n[j] * math.cos(j * math.pi / k) for j in range(1, k // 2))) / d

    def __eq__(self, o): return isinstance(o, R2) and self.q.n == o.q.n and self.q.d == o.q.d
    def __hash__(self): return hash((self.q.n, self.q.d))

    def sign(self):
        if self.is_zero(): return 0
        n = self.q.n
        if len(n) == 1: return 1 if n[0] > 0 else -1
        if len(n) == 4:                            # a + b sqrt(2), by integer squares
            a, b = n[0], n[1]
            if not a: return 1 if b > 0 else -1
            if not b or (a > 0) == (b > 0): return 1 if a > 0 else -1
            a_dominates = a*a > 2*b*b
            return (1 if a > 0 else -1) if a_dominates else (1 if b > 0 else -1)
        bits = 64
        while True:                               # a nonzero algebraic real eventually excludes zero
            bounds = _real_cos_bounds(len(n), bits)
            lo = hi = n[0] * (1 << bits)
            for j in range(1, len(n)//2):
                a, b = bounds[j]
                x, y = 2*n[j]*a, 2*n[j]*b
                lo += min(x, y); hi += max(x, y)
            if lo > 0: return 1
            if hi < 0: return -1
            bits *= 2

    def __str__(self):
        c = self.q.c
        k = len(c)
        if k == 1: return fmt_frac(c[0])
        terms = []                                  # x = c0 + Σ c_j · 2cos(jπ/k)
        for j in range(1, k // 2):
            r = c[j]
            if not r: continue
            if k == 4: name = "√2"
            elif k == 8: name = _RAD16[j]
            else:                                   # write 2c·cos(jπ/k) as a cosine with its own coefficient
                g = math.gcd(j, k)
                name, r = f"cos({'' if j // g == 1 else j // g}π/{k // g})", 2 * r
            terms.append((r, name))
        if c[0] < 0 and terms and terms[0][0] > 0:          # √2/4 - 1/4 reads better than -1/4 + √2/4
            terms.append((c[0], None))
            out = ""
        else:
            out = fmt_frac(c[0]) if c[0] else ""
        for r, name in terms:
            t = _term(r, name) if name is not None else fmt_frac(abs(r))
            if not out: out = ("-" if r < 0 else "") + t
            else: out += f" {'-' if r < 0 else '+'} {t}"
        return out


_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


class Poly:
    """Exact polynomial in declared symbols (`symbol p`) with cyclotomic coefficients. Noise channels with a
    symbolic probability turn a density matrix into polynomials, so prob/purity/expval come out as formulas."""
    __slots__ = ("t",)

    def __init__(self, t=None):
        self.t = {m: c for m, c in (t or {}).items() if not c.is_zero()}

    @staticmethod
    def var(name): return Poly({((name, 1),): Q1})

    @staticmethod
    def coerce(o):
        if type(o) is Poly: return o
        if type(o) is R2: return Poly({(): o.q})
        if type(o) is QW: return Poly({(): o})
        if isinstance(o, (int, F)): return Poly({(): _mk((F(o),))})
        return None

    def __add__(self, o):
        o = Poly.coerce(o)
        if o is None: return NotImplemented
        t = dict(self.t)
        for m, c in o.t.items(): t[m] = t[m] + c if m in t else c
        return Poly(t)
    __radd__ = __add__

    def __neg__(self): return Poly({m: -c for m, c in self.t.items()})

    def __sub__(self, o):
        o = Poly.coerce(o)
        return NotImplemented if o is None else self + (-o)

    def __rsub__(self, o):
        o = Poly.coerce(o)
        return NotImplemented if o is None else o + (-self)

    @staticmethod
    def _mono(a, b):
        d = dict(a)
        for v, e in b: d[v] = d.get(v, 0) + e
        return tuple(sorted(d.items()))

    def __mul__(self, o):
        o = Poly.coerce(o)
        if o is None: return NotImplemented
        t = {}
        for ma, ca in self.t.items():
            for mb, cb in o.t.items():
                m = Poly._mono(ma, mb)
                x = ca * cb
                t[m] = t[m] + x if m in t else x
        return Poly(t)
    __rmul__ = __mul__

    def __truediv__(self, o):
        o = Poly.coerce(o)
        if o is None: return NotImplemented
        if not o.is_const() or o.is_zero():
            raise SurdError("S-E009", "a formula can only be divided by a nonzero number", None,
                            "measurement collapse would divide by a formula; ask prob(…) instead of measuring")
        inv = R2.of(o.const()).inv().q
        return Poly({m: c * inv for m, c in self.t.items()})

    def __pow__(self, n):
        r = Poly({(): Q1})
        for _ in range(n): r = r * self
        return r

    def is_zero(self): return not self.t
    def is_const(self): return all(m == () for m in self.t)
    def is_rational(self): return False
    def const(self): return self.t.get((), QZ)
    def conj(self): return Poly({m: c.conj() for m, c in self.t.items()})
    def re(self): return Poly({m: c.re().q for m, c in self.t.items()})
    def im(self): return Poly({m: c.im().q for m, c in self.t.items()})
    def norm2(self): return (self * self.conj()).re()

    def __eq__(self, o):
        o = Poly.coerce(o)
        return o is not None and (self - o).is_zero()

    def __hash__(self): return hash(tuple(sorted((m, c.n, c.d) for m, c in self.t.items())))

    def __float__(self):
        if self.is_const(): return float(R2.of(self.const()))
        raise SurdError("S-E009", f"the formula {self} has no single value", None, "evaluate it: at(P, p, 1/100)")

    def sign(self):
        if self.is_const(): return R2.of(self.const()).sign()
        raise SurdError("S-E009", "a formula has no sign until its symbols have values", None,
                        "compare at(P, p, 1/100), or test == between formulas")

    def at(self, vals):
        tot = QZ
        for m, c in self.t.items():
            term = c
            for v, e in m:
                if v not in vals: raise SurdError("S-E002", f"no value given for symbol '{v}'", None)
                for _ in range(e): term = term * vals[v].q
            tot = tot + term
        return tot

    def source(self):
        """re-parseable Loom text: 3*p^2 - 2*p^3 (rational coefficients; others in exact closed form)"""
        if not self.t: return "0"
        parts = []
        for m, c in sorted(self.t.items(), key=lambda kv: (sum(e for _, e in kv[0]), kv[0])):
            cr = R2.of(c)
            if not (c.im().is_zero() and cr.is_rational()):
                raise SurdError("S-E013", "this formula has irrational coefficients; it has no plain source form")
            mono = "*".join(v + (f"^{e}" if e > 1 else "") for v, e in m)
            a = cr.a
            coef = fmt_frac(abs(a))
            cf = coef if abs(a).denominator == 1 else f"({coef})"
            term = mono if (mono and abs(a) == 1) else (f"{cf}*{mono}" if mono else cf)
            parts.append(("-" if a < 0 else "+") + " " + term)
        out = " ".join(parts)
        return out[2:] if out.startswith("+ ") else "-" + out[2:]

    def deriv(self, name):
        t = {}
        for m, c in self.t.items():
            d = dict(m)
            e = d.get(name, 0)
            if not e: continue
            if e == 1: del d[name]
            else: d[name] = e - 1
            mm = tuple(sorted(d.items()))
            x = c * _mk((F(e),))
            t[mm] = t[mm] + x if mm in t else x
        return Poly(t)

    def __str__(self):
        if not self.t: return "0"

        def deg(m): return sum(e for _, e in m)
        out = ""
        for m in sorted(self.t, key=lambda m: (deg(m), m)):
            c = self.t[m]
            mono = "·".join(v + (str(e).translate(_SUP) if e > 1 else "") for v, e in m)
            if c.im().is_zero():
                cr = R2.of(c)
                neg = cr.sign() < 0
                cs = str(-cr if neg else cr)
            else:
                neg, cs = False, f"({c})"
            if mono:
                if cs == "1": term = mono
                elif " + " in cs or " - " in cs or "/" in cs: term = f"({cs}){mono}"
                else: term = f"{cs}{mono}"
            else:
                term = cs
            out = (("-" if neg else "") + term) if not out else out + f" {'-' if neg else '+'} {term}"
        return out


class PiVal:
    """An angle k*pi with k rational."""
    __slots__ = ("k",)

    def __init__(self, k): self.k = F(k)
    def __float__(self): return float(self.k) * math.pi
    def __eq__(self, o): return isinstance(o, PiVal) and o.k == self.k
    def __hash__(self): return hash(("pi", self.k))

    def __str__(self):
        k = self.k
        if k == 0: return "0"
        n, d = abs(k.numerator), k.denominator
        s = ("" if n == 1 else str(n)) + "π" + ("" if d == 1 else f"/{d}")
        return ("-" if k < 0 else "") + s


def fmt_float(x):
    if isinstance(x, complex):
        r, i = x.real, x.imag
        if abs(i) < 1e-12: return f"{r:.6f}"
        if abs(r) < 1e-12: return f"{i:.6f}i"
        return f"{r:.6f}{'+' if i >= 0 else '-'}{abs(i):.6f}i"
    return f"{x:.10g}"


def fmt_value(v):
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, R2):
        s = str(v)
        if len(s) > 40:                                   # exact, but too long to read: say so and show its size
            c = v.q.c
            digits = max(len(str(abs(x.numerator))) + len(str(x.denominator)) for x in c)
            approx = finite_approximation(v)
            return (f"≈ {approx:.9f}" if approx is not None else "finite decimal approximation unavailable") + f"  (exact; its fraction runs to {digits} digits)"
        if not v.is_rational() or v.a.denominator != 1:
            f = float(v)
            s += f"  ≈ {f:.6f}" if f == 0 or abs(f) >= 1e-4 else f"  ≈ {f:.3e}"
        return s
    if isinstance(v, PiVal): return str(v)
    if isinstance(v, Poly): return str(v)
    if isinstance(v, (Realm, Record)): return str(v)
    if isinstance(v, list): return "[" + ", ".join(fmt_short(x) for x in v) + "]"
    if isinstance(v, dict): return "{" + ", ".join(f"{fmt_short(k)}: {fmt_short(x)}" for k, x in v.items()) + "}"
    if v is NONE or isinstance(v, (FuncVal, Meaning, Space, Money, Date, Bound)): return str(v)
    if isinstance(v, float): return f"{fmt_float(v)}  (float)"
    return str(v)


def fmt_short(v):
    """a value inside a list or map: exact form without the decimal gloss; strings quoted"""
    if isinstance(v, str): return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, int): return str(v)
    if isinstance(v, (R2, PiVal, Poly, Money, Date, Bound)): return str(v)
    if isinstance(v, float): return fmt_float(v)
    return fmt_value(v)


def fmt_angle(v):
    if isinstance(v, (PiVal, Poly)): return str(v)
    if isinstance(v, R2): return str(v) if v.is_rational() else f"{float(v):.4g}"
    return f"{float(v):.4g}"


# ───────────────────────────── angles & matrices ─────────────────────────────
class NotExact(Exception):
    pass


def ang_float(v): return float(v)


def ang_neg(v):
    if isinstance(v, PiVal): return PiVal(-v.k)
    if isinstance(v, R2): return -v
    return -float(v)


def ang_half(v):
    if isinstance(v, PiVal): return PiVal(v.k / 2)
    if isinstance(v, R2): return v * R2(F(1, 2))
    return float(v) / 2


def ang_add(a, b):
    if isinstance(a, PiVal) and isinstance(b, PiVal): return PiVal(a.k + b.k)
    if isinstance(a, PiVal) and isinstance(b, R2) and b.is_zero(): return a
    if isinstance(b, PiVal) and isinstance(a, R2) and a.is_zero(): return b
    if isinstance(a, R2) and isinstance(b, R2): return a + b
    return float(a) + float(b)


def eighth(v):
    """angle = m*pi/4 exactly -> m, else None."""
    if isinstance(v, PiVal):
        m = v.k * 4
        return int(m) if m.denominator == 1 else None
    if isinstance(v, R2) and v.is_zero():
        return 0
    return None


class ExSys:
    one, zero, i, h = Q1, QZ, QI, QH
    half = _HALF
    mhalf_i = _MHALF_I

    @staticmethod
    def expi(v):
        """e^{iθ} exactly when θ = k·π with k dyadic and the level stays within MAX_LEVEL"""
        if isinstance(v, R2) and v.is_zero(): return Q1
        if not isinstance(v, PiVal): raise NotExact
        d = v.k.denominator
        if d & (d - 1): raise NotExact                # 3π/7: not dyadic
        N = max(2, 2 * d)
        if N > MAX_LEVEL: raise NotExact
        return QW.omega(int(v.k * N / 2), N)

    def cos(self, v): return (self.expi(v) + self.expi(ang_neg(v))) * self.half
    def sin(self, v): return (self.expi(v) - self.expi(ang_neg(v))) * self.mhalf_i
    def c(self, re=0, im=0): return QW(re, 0, im, 0)


class FlSys:
    one, zero, i, h = 1 + 0j, 0j, 1j, complex(1 / SQRT2)

    @staticmethod
    def expi(v): return cmath.exp(1j * ang_float(v))
    def cos(self, v): return complex(math.cos(ang_float(v)))
    def sin(self, v): return complex(math.sin(ang_float(v)))
    def c(self, re=0, im=0): return complex(re, im)


EX, FL = ExSys(), FlSys()

PRIM = {"id": 0, "h": 0, "x": 0, "y": 0, "z": 0, "s": 0, "sdg": 0, "t": 0, "tdg": 0, "sx": 0, "sxdg": 0,
        "rx": 1, "ry": 1, "rz": 1, "p": 1, "u": 3}
ALIAS = {"phase": "p", "cnot": "cx", "toffoli": "ccx", "fredkin": "cswap", "u3": "u", "u1": "p"}
ADJ = {"id": "id", "h": "h", "x": "x", "y": "y", "z": "z", "s": "sdg", "sdg": "s", "t": "tdg", "tdg": "t",
       "sx": "sxdg", "sxdg": "sx"}


def build(name, ps, S):
    one, zero, i = S.one, S.zero, S.i
    if name == "id": return (one, zero, zero, one)
    if name == "h": return (S.h, S.h, S.h, -S.h)
    if name == "x": return (zero, one, one, zero)
    if name == "y": return (zero, -i, i, zero)
    if name == "z": return (one, zero, zero, -one)
    if name == "s": return (one, zero, zero, i)
    if name == "sdg": return (one, zero, zero, -i)
    if name == "t": return (one, zero, zero, S.expi(PiVal(F(1, 4))))
    if name == "tdg": return (one, zero, zero, S.expi(PiVal(F(-1, 4))))
    if name in ("sx", "sxdg"):
        hh = S.c(F(1, 2)) if S is EX else 0.5 + 0j
        a, b = S.c(1, 1) * hh, S.c(1, -1) * hh
        return (a, b, b, a) if name == "sx" else (b, a, a, b)
    if name == "p": return (one, zero, zero, S.expi(ps[0]))
    if name == "rz":
        h = ang_half(ps[0])
        return (S.expi(ang_neg(h)), zero, zero, S.expi(h))
    if name == "rx":
        h = ang_half(ps[0]); c, s = S.cos(h), S.sin(h)
        return (c, -i * s, -i * s, c)
    if name == "ry":
        h = ang_half(ps[0]); c, s = S.cos(h), S.sin(h)
        return (c, -s, s, c)
    if name == "u":
        th, ph, la = ps
        h = ang_half(th); c, s = S.cos(h), S.sin(h)
        return (c, -S.expi(la) * s, S.expi(ph) * s, S.expi(ang_add(ph, la)) * c)
    raise KeyError(name)


class Mat:
    __slots__ = ("ex", "fl")

    def __init__(self, name, ps):
        try:
            self.ex = build(name, ps, EX)
        except NotExact:
            self.ex = None
        self.fl = build(name, ps, FL)


PAULI = {"X": Mat("x", ()), "Y": Mat("y", ()), "Z": Mat("z", ())}
CHANNELS = {"depolarize": 1, "dephase": 1, "bitflip": 1, "pauli": 3, "ampdamp": 1}


def adjoint(op):
    name, ps, t, ctrls = op
    if name in ADJ: return (ADJ[name], ps, t, ctrls)
    if name in ("rx", "ry", "rz", "p"): return (name, (ang_neg(ps[0]),), t, ctrls)
    if name == "u": return ("u", (ang_neg(ps[0]), ang_neg(ps[2]), ang_neg(ps[1])), t, ctrls)
    raise KeyError(name)


# ───────────────────────────── state ─────────────────────────────
class _Base:
    def zero(self): return QZ if self.exact else 0j
    def one(self): return Q1 if self.exact else 1 + 0j
    def isz(self, a): return a.is_zero() if self.exact else a == 0
    def n2(self, a): return a.norm2() if self.exact else (a.real * a.real + a.imag * a.imag)
    def zero_val(self): return (Poly() if getattr(self, "sym", False) else R2()) if self.exact else 0.0
    def cj(self, a): return a.conj() if self.exact else a.conjugate()
    def rl(self, a): return a.re() if self.exact else a.real
    def div(self, x, n): return x / n

    def scalar(self, v):
        """an exact real (R2), a formula (Poly) or float as an amplitude of this state's kind"""
        if type(v) is Poly: return v
        return QW.from_r2(v) if self.exact else complex(float(v))

    def bloch(self, q):
        rho = self.reduced([q])
        n = self.norm()
        if self.exact:
            two = R2(2)
            return ((two * rho[0][1].re()) / n, (-(two * rho[0][1].im())) / n, (rho[0][0] - rho[1][1]).re() / n)
        return (2 * rho[0][1].real / n, -2 * rho[0][1].imag / n, (rho[0][0] - rho[1][1]).real / n)


try:
    import numpy as _np
    if os.environ.get("SURD_NO_NUMPY"): _np = None
except ImportError:
    _np = None


class PureState(_Base):
    """amplitudes: a list of exact QW, or — in float mode with numpy present — a numpy array kept between gates
    and turned back into a list only when other code reads .v"""
    kind = "pure"

    def __init__(self, force_float=False):
        self.n = 0
        self.exact = not force_float
        self._v, self._a = [Q1 if self.exact else 1 + 0j], None

    @property
    def v(self):
        if self._a is not None:
            self._v, self._a = self._a.tolist(), None
        return self._v

    @v.setter
    def v(self, val):
        self._v, self._a = val, None

    def copy(self):
        o = PureState.__new__(PureState)
        o.n, o.exact, o._v, o._a = self.n, self.exact, list(self.v), None
        return o

    def _apply_np(self, m, t, ctrls):
        """one controlled 2×2 gate as a single vectorized update of two slices (axis of wire q = n−1−q)"""
        if self._a is None:
            self._a = _np.array(self._v, dtype=complex)
        n = self.n
        A = self._a.reshape([2] * n) if n else self._a
        idx = [slice(None)] * n
        for c in ctrls: idx[n - 1 - c] = 1
        i0, i1 = list(idx), list(idx)
        i0[n - 1 - t], i1[n - 1 - t] = 0, 1
        i0, i1 = tuple(i0), tuple(i1)
        m00, m01, m10, m11 = m
        x0, x1 = A[i0].copy(), A[i1]
        if m01 == 0 and m10 == 0:
            if m00 != 1: A[i0] = m00 * x0
            if m11 != 1: A[i1] = m11 * x1
        else:
            A[i0] = m00 * x0 + m01 * x1
            A[i1] = m10 * x0 + m11 * x1

    def expval(self, qs, paulis):
        w = self.copy()
        for q, P in zip(qs, paulis):
            if P != "I": w.apply(PAULI[P], q, ())
        tot = self.zero()
        for a, b in zip(self.v, w.v):
            if not (self.isz(a) or self.isz(b)): tot = tot + self.cj(a) * b
        return self.rl(tot) / self.norm()

    def add_qubits(self, k):
        if self.n + k > MAX_QUBITS:
            raise SurdError("S-E010", f"{self.n + k} qubits requested; the simulator holds {MAX_QUBITS}")
        for _ in range(k):
            self.v = self.v + [self.zero()] * len(self.v)
            self.n += 1

    def to_float(self):
        if self.exact:
            self.v = [complex(a) for a in self.v]
            self.exact = False
            nn = sum(abs(a) ** 2 for a in self.v)
            if nn > 0 and abs(nn - 1) > 1e-15:
                s = 1 / math.sqrt(nn)
                self.v = [a * s for a in self.v]

    def apply(self, mat, t, ctrls):
        if self.exact and mat.ex is None:
            self.to_float()
        if not self.exact and _np is not None and self.n >= 6:
            return self._apply_np(mat.fl, t, ctrls)
        m00, m01, m10, m11 = mat.ex if self.exact else mat.fl
        isz = self.isz
        diag = isz(m01) and isz(m10)
        mask = 0
        for c in ctrls: mask |= 1 << c
        tb = 1 << t
        v = self.v
        for i in range(len(v)):
            if i & tb or (i & mask) != mask: continue
            j = i | tb
            a, b = v[i], v[j]
            if diag:
                v[i] = m00 * a
                v[j] = m11 * b
            else:
                if isz(a) and isz(b): continue
                v[i] = m00 * a + m01 * b
                v[j] = m10 * a + m11 * b

    def norm(self):
        tot = self.zero_val()
        for a in self.v:
            if not self.isz(a): tot = tot + self.n2(a)
        return tot

    def distribution(self, qs):
        d, k = {}, len(qs)
        for i, a in enumerate(self.v):
            if self.isz(a): continue
            key = "".join("1" if i >> q & 1 else "0" for q in qs)
            d[key] = d.get(key, self.zero_val()) + self.n2(a)
        n = self.norm()
        return {key: self.div(p, n) for key, p in d.items()}

    def prob_bits(self, qs, bits):
        tot = self.zero_val()
        for i, a in enumerate(self.v):
            if self.isz(a): continue
            if all((i >> q & 1) == int(b) for q, b in zip(qs, bits)):
                tot = tot + self.n2(a)
        return self.div(tot, self.norm())

    def measure(self, t, rng):
        p1 = self.prob_bits([t], "1")
        pf = float(p1)
        if self.exact and p1.is_zero(): out = 0
        elif self.exact and (R2(1) - p1).is_zero(): out = 1
        else: out = 1 if rng.random() < pf else 0
        z = self.zero()
        for i in range(len(self.v)):
            if (i >> t & 1) != out: self.v[i] = z
        if not self.exact:
            p = pf if out else 1 - pf
            s = 1 / math.sqrt(p) if p > 0 else 1.0
            self.v = [a * s for a in self.v]
        return out

    def reduced(self, qs):
        k = len(qs)
        maskA = 0
        for q in qs: maskA |= 1 << q
        groups = {}
        for i, a in enumerate(self.v):
            if self.isz(a): continue
            r = 0
            for pos, q in enumerate(qs):
                if i >> q & 1: r |= 1 << (k - 1 - pos)
            groups.setdefault(i & ~maskA, []).append((r, a))
        d = 1 << k
        z = self.zero()
        rho = [[z] * d for _ in range(d)]
        for lst in groups.values():
            for r, a in lst:
                for c, b in lst:
                    rho[r][c] = rho[r][c] + a * (b.conj() if self.exact else b.conjugate())
        return rho

    def purity(self, qs):
        # a pure state's two halves share one purity: reduce over the smaller side
        rest = [q for q in range(self.n) if q not in set(qs)]
        if not rest:
            return R2(1) if self.exact else 1.0
        if len(rest) < len(qs):
            qs = rest
        rho = self.reduced(qs)
        tot = self.zero_val()
        for row in rho:
            for x in row:
                if not self.isz(x): tot = tot + self.n2(x)
        n = self.norm()
        return self.div(tot, n * n)


State = PureState


class MixedState(_Base):
    """Density matrix rho (unnormalised; trace N). Gates act rho -> U rho U+, channels are exact for rational p."""
    kind = "mixed"

    @property
    def r(self):
        if self._R is not None:
            self._r, self._R = self._R.tolist(), None
        return self._r

    @r.setter
    def r(self, val):
        self._r, self._R = val, None

    def _fast(self):
        return (not self.exact) and _np is not None and self.n >= 3 and not getattr(self, "sym", False)

    def _view(self):
        if self._R is None:
            self._R = _np.array(self._r, dtype=complex)
        return self._R.reshape([2] * (2 * self.n))

    def _np_gate(self, m, t, ctrls):
        """rho -> U rho U+ for a controlled 2×2 gate: row axes 0..n-1, column axes n..2n-1 (wire q at n−1−q)"""
        n = self.n
        T = self._view()
        for side, mm in ((0, m), (n, tuple(complex(x).conjugate() for x in m))):
            idx = [slice(None)] * (2 * n)
            for c in ctrls: idx[side + n - 1 - c] = 1
            i0, i1 = list(idx), list(idx)
            i0[side + n - 1 - t], i1[side + n - 1 - t] = 0, 1
            i0, i1 = tuple(i0), tuple(i1)
            x0, x1 = T[i0].copy(), T[i1].copy()
            T[i0] = mm[0] * x0 + mm[1] * x1
            T[i1] = mm[2] * x0 + mm[3] * x1

    def _blocks(self, t):
        n = self.n
        ra, ca = n - 1 - t, 2 * n - 1 - t
        def I(rb, cb):
            idx = [slice(None)] * (2 * n); idx[ra], idx[ca] = rb, cb
            return tuple(idx)
        return self._view(), I

    def __init__(self, pure):
        self._R = None
        self.n, self.exact = pure.n, pure.exact
        z = pure.zero()
        cv = [pure.cj(a) for a in pure.v]
        self.r = [[(z if pure.isz(a) or pure.isz(b) else a * b) for b in cv] for a in pure.v]

    def copy(self):
        o = MixedState.__new__(MixedState)
        o.n, o.exact, o._R = self.n, self.exact, None
        if getattr(self, "sym", False): o.sym = True
        if self._R is not None:
            o._r, o._R = None, self._R.copy()
        else:
            o._r = [list(row) for row in self._r]
        return o

    def add_qubits(self, k):
        if self.n + k > MAX_MIXED:
            raise SurdError("S-E010", f"a mixed (noisy) state holds {MAX_MIXED} qubits; {self.n + k} requested")
        z = self.zero()
        for _ in range(k):
            d = len(self.r)
            self.r = [row + [z] * d for row in self.r] + [[z] * (2 * d) for _ in range(d)]
            self.n += 1

    def to_float(self):
        if getattr(self, "sym", False):
            raise SurdError("S-E009", "a state with symbolic noise needs exact gates", None,
                            "keep angles dyadic (π/2^n) after `symbol`")
        if self.exact:
            self.r = [[complex(a) for a in row] for row in self.r]
            self.exact = False

    def to_symbolic(self):
        if not getattr(self, "sym", False):
            self.r = [[Poly.coerce(a) for a in row] for row in self.r]
            self.sym = True

    def _pairs(self, t, ctrls):
        mask = 0
        for c in ctrls: mask |= 1 << c
        tb = 1 << t
        return [(i, i | tb) for i in range(len(self.r)) if not i & tb and (i & mask) == mask]

    def apply_left(self, m, pairs):
        m00, m01, m10, m11 = m
        R = self.r
        isz = self.isz
        if isz(m01) and isz(m10):
            for i, j in pairs:
                R[i] = [m00 * a if not isz(a) else a for a in R[i]]
                R[j] = [m11 * b if not isz(b) else b for b in R[j]]
            return
        for i, j in pairs:
            ri, rj = R[i], R[j]
            R[i] = [m00 * a + m01 * b for a, b in zip(ri, rj)]
            R[j] = [m10 * a + m11 * b for a, b in zip(ri, rj)]

    def apply(self, mat, t, ctrls):
        if self.exact and mat.ex is None:
            self.to_float()
        if self._fast():
            return self._np_gate(mat.fl, t, ctrls)
        m = mat.ex if self.exact else mat.fl
        pairs = self._pairs(t, ctrls)
        self.apply_left(m, pairs)
        c00, c01, c10, c11 = [self.cj(x) for x in m]
        isz = self.isz
        for row in self.r:
            for i, j in pairs:
                a, b = row[i], row[j]
                if isz(a) and isz(b): continue
                row[i] = c00 * a + c01 * b
                row[j] = c10 * a + c11 * b

    def diag(self, i):
        x = self.r[i][i]
        return x.re() if type(x) is Poly else self.rl(x)

    def norm(self):
        tot = self.zero_val()
        for i in range(len(self.r)): tot = tot + self.diag(i)
        return tot

    def distribution(self, qs):
        d = {}
        for i in range(len(self.r)):
            p = self.diag(i)
            if (p.is_zero() if self.exact else p == 0): continue
            if type(p) is Poly and p.is_const(): p = R2.of(p.const())
            key = "".join("1" if i >> q & 1 else "0" for q in qs)
            d[key] = d.get(key, self.zero_val()) + p
        n = self.norm()
        return {key: p / n for key, p in d.items()}

    def prob_bits(self, qs, bits):
        tot = self.zero_val()
        for i in range(len(self.r)):
            if all((i >> q & 1) == int(b) for q, b in zip(qs, bits)):
                tot = tot + self.diag(i)
        return tot / self.norm()

    def measure(self, t, rng):
        p1 = self.prob_bits([t], "1")
        pf = float(p1)
        if self.exact and p1.is_zero(): out = 0
        elif self.exact and (R2(1) - p1).is_zero(): out = 1
        else: out = 1 if rng.random() < pf else 0
        z = self.zero()
        bad = [i for i in range(len(self.r)) if (i >> t & 1) != out]
        for i in bad: self.r[i] = [z] * len(self.r)
        for row in self.r:
            for i in bad: row[i] = z
        if not self.exact:
            p = pf if out else 1 - pf
            s = 1 / p if p > 0 else 1.0
            self.r = [[a * s for a in row] for row in self.r]
        return out

    def reset(self, t):
        """rho -> |0><0| (x) Tr_t(rho): both outcomes averaged, exactly"""
        if self._fast():
            T, I = self._blocks(t)
            T[I(0, 0)] = T[I(0, 0)] + T[I(1, 1)]
            T[I(1, 1)] = 0; T[I(0, 1)] = 0; T[I(1, 0)] = 0
            return
        tb = 1 << t
        R, z = self.r, self.zero()
        D = len(R)
        out = [[z] * D for _ in range(D)]
        for i in range(D):
            if i & tb: continue
            for j in range(D):
                if j & tb: continue
                a, b = R[i][j], R[i | tb][j | tb]
                out[i][j] = a if self.isz(b) else (b if self.isz(a) else a + b)
        self.r = out

    def reduced(self, qs):
        k = len(qs)
        maskA = 0
        for q in qs: maskA |= 1 << q
        D = len(self.r)
        A = []
        for i in range(D):
            a = 0
            for pos, q in enumerate(qs):
                if i >> q & 1: a |= 1 << (k - 1 - pos)
            A.append(a)
        groups = {}
        for i in range(D): groups.setdefault(i & ~maskA, []).append(i)
        z = self.zero()
        rho = [[z] * (1 << k) for _ in range(1 << k)]
        for g in groups.values():
            for i in g:
                ri = self.r[i]
                for j in g:
                    x = ri[j]
                    if not self.isz(x): rho[A[i]][A[j]] = rho[A[i]][A[j]] + x
        return rho

    def purity(self, qs):
        rho = self.reduced(qs)
        tot = self.zero_val()
        for row in rho:
            for x in row:
                if not self.isz(x): tot = tot + self.n2(x)
        n = self.norm()
        return tot / (n * n)

    def expval(self, qs, paulis):
        w = self.copy()
        for q, P in zip(qs, paulis):
            if P != "I":
                w.apply_left(PAULI[P].ex if w.exact else PAULI[P].fl, w._pairs(q, ()))
        tot = self.zero_val()
        for i in range(len(w.r)): tot = tot + w.diag(i)
        return tot / self.norm()

    def _combine(self, terms):
        """sum of coef * matrix over terms; coef exact R2 or float"""
        D = len(self.r)
        z = self.zero()
        out = [[z] * D for _ in range(D)]
        for coef, M in terms:
            if (coef.is_zero() if isinstance(coef, R2) else coef == 0): continue
            c = self.scalar(coef)
            for r in range(D):
                orow, mrow = out[r], M[r]
                for k in range(D):
                    x = mrow[k]
                    if not self.isz(x): orow[k] = orow[k] + c * x
        self.r = out

    def pauli_channel(self, t, px, py, pz):
        if self._fast():
            n = self.n
            T = self._view()
            ra, ca = n - 1 - t, 2 * n - 1 - t
            shp_r, shp_c = [1] * (2 * n), [1] * (2 * n)
            shp_r[ra] = 2; shp_c[ca] = 2
            sgn = _np.array([1.0, -1.0]).reshape(shp_r) * _np.array([1.0, -1.0]).reshape(shp_c)
            Z = T * sgn                                         # Z rho Z
            X = _np.flip(_np.flip(T, ra), ca)                   # X rho X
            Y = _np.flip(_np.flip(Z, ra), ca)                   # Y rho Y = X Z rho Z X
            px, py, pz = float(px), float(py), float(pz)
            new = (1 - px - py - pz) * T + px * X + py * Y + pz * Z
            self._R = new.reshape(1 << n, 1 << n)
            return
        one = R2(1) if self.exact else 1.0
        terms = [(one - px - py - pz, self.r)]
        for coef, P in ((px, "X"), (py, "Y"), (pz, "Z")):
            if (coef.is_zero() if isinstance(coef, R2) else coef == 0): continue
            c = self.copy()
            c.apply(PAULI[P], t, ())
            terms.append((coef, c.r))
        self._combine(terms)

    def ampdamp(self, t, g, s):
        """Kraus K0 = |0><0| + s|1><1| (s = sqrt(1-g)), K1 = sqrt(g)|0><1|"""
        if self._fast():
            T, I = self._blocks(t)
            g, s = float(g), float(s)
            a00, a11, a01, a10 = T[I(0, 0)].copy(), T[I(1, 1)].copy(), T[I(0, 1)].copy(), T[I(1, 0)].copy()
            T[I(0, 0)] = a00 + g * a11; T[I(1, 1)] = (1 - g) * a11
            T[I(0, 1)] = s * a01; T[I(1, 0)] = s * a10
            return
        tb = 1 << t
        R = self.r
        D = len(R)
        gs, ss, keep = self.scalar(g), self.scalar(s), self.scalar((R2(1) if self.exact else 1.0) - g)
        out = [list(row) for row in R]
        for i in range(D):
            for j in range(D):
                bi, bj = i >> t & 1, j >> t & 1
                if bi == 0 and bj == 0:
                    x = R[i | tb][j | tb]
                    if not self.isz(x): out[i][j] = R[i][j] + gs * x
                elif bi == 1 and bj == 1:
                    if not self.isz(R[i][j]): out[i][j] = keep * R[i][j]
                elif not self.isz(R[i][j]):
                    out[i][j] = ss * R[i][j]
        self.r = out


# ───────────────────────────── lexer ─────────────────────────────
KEYWORDS = {"wire", "wires", "bit", "bits", "let", "seed", "gate", "measure", "reset", "barrier", "if", "else",
            "repeat", "for", "in", "print", "expect", "sample", "ctl", "and", "or", "not", "pi", "true", "false",
            "noise", "symbol", "realm", "compare", "grover", "swaptest", "game", "solve",
            "fn", "return", "while", "break", "continue", "test", "none", "space"}
OPS2 = {"<-", "->", "=>", ">>", "=~", "..", "==", "!=", "<=", ">="}
OPS1 = set("[](){},;:+-*/^<>=&~%")


CONT_OPS = {"+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "=", "=>", ",", "&&", "||"}   # a line ending in one continues (1.7)


def lex(src):
    toks, i, line, col, depth = [], 0, 1, 1, 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\n":
            cont = toks and ((toks[-1][0] == "op" and toks[-1][1] in CONT_OPS) or (toks[-1][0] == "kw" and toks[-1][1] in ("and", "or", "not")))
            if depth == 0 and not cont: toks.append(("nl", "\n", line, col))
            i += 1; line += 1; col = 1; continue
        if ch in " \t\r":
            i += 1; col += 1; continue
        if ch == "#" or src.startswith("//", i):
            while i < n and src[i] != "\n": i += 1
            continue
        start = (line, col)
        if ch.isdigit():
            j = i
            while j < n and src[j].isdigit(): j += 1
            if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                j += 1
                while j < n and src[j].isdigit(): j += 1
            toks.append(("num", src[i:j], *start)); col += j - i; i = j; continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"): j += 1
            w = src[i:j]
            toks.append(("kw" if w in KEYWORDS else "id", w, *start)); col += j - i; i = j; continue
        if ch == "π":
            toks.append(("kw", "pi", *start)); i += 1; col += 1; continue
        if ch == "$" and i + 1 < n and src[i + 1].isdigit():
            j = i + 1
            while j < n and (src[j].isdigit() or (src[j] in ",." and j + 1 < n and src[j + 1].isdigit())):
                j += 1
            toks.append(("money", src[i + 1:j].replace(",", ""), *start)); col += j - i; i = j; continue
        if ch == "@" and src[i + 1:i + 11].count("-") == 2 and src[i + 1:i + 5].isdigit():
            toks.append(("date", src[i + 1:i + 11], *start)); col += 11; i += 11; continue
        if ch == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                if src[j] == "\\": j += 1
                j += 1
            if j >= n or src[j] != '"':
                raise SurdError("S-E001", "unterminated string", start, 'close it with "')
            try: value = json.loads(src[i:j+1])
            except ValueError:
                raise SurdError("S-E001", "invalid string escape", start, 'use JSON escapes such as \\" or \\\\')
            toks.append(("str", value, *start)); col += j + 1 - i; i = j + 1; continue
        two = src[i:i + 2]
        if two in OPS2:
            toks.append(("op", two, *start)); i += 2; col += 2; continue
        if ch in OPS1:
            if ch in "([": depth += 1
            elif ch in ")]": depth = max(0, depth - 1)
            toks.append(("op", ch, *start)); i += 1; col += 1; continue
        raise SurdError("S-E001", f"unexpected character {ch!r}", start)
    toks.append(("eof", "", line, col))
    return toks


class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0): return self.t[min(self.i + k, len(self.t) - 1)]
    def pos(self): t = self.peek(); return (t[2], t[3])

    def next(self):
        t = self.t[self.i]; self.i += 1; return t

    def at(self, kind, val=None):
        t = self.peek()
        return t[0] == kind and (val is None or t[1] == val)

    def accept(self, kind, val=None):
        if self.at(kind, val): return self.next()
        return None

    def expect(self, kind, val=None, what=None, hint=None):
        if self.at(kind, val): return self.next()
        t = self.peek()
        got = "end of input" if t[0] == "eof" else ("end of line" if t[0] == "nl" else repr(t[1]))
        if hint is None:
            hint = {"<-": "wires receive gates: q[0] <- h", ":": "wires q: 3 declares three wires",
                    "{": "a block is written { … } on the same line", "}": "every '{' needs a closing '}'",
                    ")": "check the parentheses", "]": "an index is written q[0] or q[1..3]"}.get(val)
            if hint is None and kind == "id": hint = "a name starts with a letter"
        raise SurdError("S-E001", f"expected {what or val or kind}, got {got}", (t[2], t[3]), hint)

    def ident(self, what="a name"):
        return self.expect("id", what=what)[1]

    def at_sep(self):
        t = self.peek()
        return t[0] in ("nl", "eof") or (t[0] == "op" and t[1] in (";", "}"))

    def program(self):
        return self.items(None)

    def items(self, end):
        out = []
        while True:
            while self.accept("nl") or self.accept("op", ";"): pass
            if self.at("eof"):
                if end: raise SurdError("S-E001", "missing '}'", self.pos(), "every '{' needs a closing '}'")
                return out
            if end and self.at("op", "}"): return out
            out.append(self.statement())
            if not self.at_sep():
                t = self.peek()
                raise SurdError("S-E001", f"expected end of statement, got {t[1]!r}", (t[2], t[3]),
                                "separate statements with a newline or ';'")

    def block(self):
        self.expect("op", "{")
        b = self.items("}")
        self.expect("op", "}")
        return b

    def qarg(self):
        p = self.pos()
        name = self.ident("a wire or register")
        lo = hi = None
        if self.accept("op", "["):
            lo = self.expr()
            if self.accept("op", ".."):
                hi = {"k": "end", "pos": self.pos()} if self.at("op", "]") else self.expr()
            self.expect("op", "]")
        return {"k": "ref", "name": name, "lo": lo, "hi": hi, "pos": p}

    def qargs(self):
        out = [self.qarg()]
        while self.accept("op", ","): out.append(self.qarg())
        return out

    def _index_assign_ahead(self):
        """after `name [`, does the matching `]` sit right before a single `=`?"""
        d, k = 0, 1
        while True:
            tk = self.peek(k)
            if tk[0] in ("eof", "nl"): return False
            if tk[0] == "op" and tk[1] == "[": d += 1
            elif tk[0] == "op" and tk[1] == "]":
                d -= 1
                if d == 0:
                    nx = self.peek(k + 1)
                    return nx[0] == "op" and nx[1] == "="
            k += 1

    def targets(self):
        if self.accept("op", "("):
            out = self.qargs()
            self.expect("op", ")")
            return out
        return self.qargs()

    def param_list(self):
        self.expect("op", "(", what="'(' and the parameter names")
        ps = []
        if not self.at("op", ")"):
            ps.append(self.ident("a parameter name"))
            while self.accept("op", ","): ps.append(self.ident("a parameter name"))
        self.expect("op", ")")
        if len(set(ps)) != len(ps): raise SurdError("S-E011", "parameter names must differ", self.pos())
        return ps

    def decls(self, plural):
        out = []
        while True:
            name = self.ident()
            size = None
            if plural:
                self.expect("op", ":", what="':' and a count (wires q: 3)")
                size = self.expr()
            out.append((name, size))
            if not self.accept("op", ","): break
        return out

    def statement(self):
        p = self.pos()
        t = self.peek()
        if t[0] == "kw":
            kw = t[1]
            if kw in ("wire", "wires", "bit", "bits"):
                self.next()
                return {"k": "wire" if kw.startswith("wire") else "bit", "decls": self.decls(kw.endswith("s")),
                        "pos": p}
            if kw == "let":
                self.next(); name = self.ident(); self.expect("op", "=")
                return {"k": "let", "name": name, "e": self.expr(), "pos": p}
            if kw == "seed":
                self.next(); return {"k": "seed", "e": self.expr(), "pos": p}
            if kw == "space":
                self.next(); name = self.ident("a space name"); self.expect("op", "{")
                labels = []
                while self.accept("nl"): pass
                while not self.at("op", "}"):
                    t_ = self.next()
                    if t_[0] not in ("id", "str", "kw"):
                        raise SurdError("S-E001", f"a sense is a name or a quoted label, got {t_[1]!r}", (t_[2], t_[3]))
                    labels.append(t_[1])
                    while self.accept("nl"): pass
                    if not self.accept("op", ","): break
                    while self.accept("nl"): pass
                while self.accept("nl"): pass
                self.expect("op", "}")
                return {"k": "space", "name": name, "labels": labels, "pos": p}
            if kw == "fn" and self.peek(1)[0] == "id":
                self.next(); name = self.ident("a function name")
                params = self.param_list()
                if self.accept("op", "=>"):                                   # fn name(x) => expr  (1.7)
                    return {"k": "fndef", "name": name, "params": params, "body": [{"k": "return", "e": self.expr(), "pos": p}], "pos": p}
                return {"k": "fndef", "name": name, "params": params, "body": self.block(), "pos": p}
            if kw == "return":
                self.next()
                return {"k": "return", "e": None if self.at_sep() else self.expr(), "pos": p}
            if kw == "while":
                self.next(); cond = self.expr()
                return {"k": "while", "cond": cond, "body": self.block(), "pos": p}
            if kw in ("break", "continue"):
                self.next(); return {"k": kw, "pos": p}
            if kw == "test":
                self.next()
                name = self.expect("str", what="a test name in quotes")[1]
                return {"k": "test", "name": name, "body": self.block(), "pos": p}
            if kw == "game":
                self.next()
                name = self.ident("a game name")
                question = self.next()[1] if self.at("str") else None
                self.expect("op", "{")
                players, promise, win = [], None, None
                while True:
                    while self.accept("nl") or self.accept("op", ";"): pass
                    if self.accept("op", "}"): break
                    t = self.peek()
                    if t[0] == "id" and t[1] == "players":
                        self.next()
                        while True:
                            pn = self.ident("a player name"); self.expect("op", "(")
                            xin = self.ident("the player's input name")
                            size = None
                            if self.accept("op", ":"): size = self.expr()
                            self.expect("op", "->", what="'->' and the player's answer name")
                            aout = self.ident("the player's answer name"); self.expect("op", ")")
                            players.append((pn, xin, size, aout))
                            if not self.accept("op", ","): break
                    elif t[0] == "id" and t[1] == "promise":
                        self.next(); promise = self.expr()
                    elif t[0] == "kw" and t[1] == "win" or t[0] == "id" and t[1] == "win":
                        self.next(); win = self.expr()
                    else:
                        raise SurdError("S-E001", f"a game body holds players, promise and win; got {t[1]!r}",
                                        (t[2], t[3]), "players alice(x -> a), bob(y -> b)  ·  win (a + b) % 2 == x * y")
                if not players or win is None:
                    raise SurdError("S-E001", "a game needs players and a win rule", p)
                return {"k": "game", "name": name, "question": question, "players": players, "promise": promise,
                        "win": win, "pos": p}
            if kw == "solve":
                self.next(); name = self.ident("a game name")
                restarts = None
                if self.at("id", "restarts"):
                    self.next(); restarts = self.expr()
                return {"k": "solve", "name": name, "restarts": restarts, "pos": p}
            if kw == "realm":
                self.next(); name = self.ident("a realm name"); self.expect("op", "=")
                return {"k": "realm", "name": name, "e": self.expr(), "pos": p}
            if kw == "compare":
                self.next()
                names = [self.ident("a realm name")]
                while self.accept("op", ","): names.append(self.ident("a realm name"))
                w = None
                if self.at("id", "within"):
                    self.next(); w = self.expr()
                    if self.at("id", "days") or self.at("id", "day"): self.next()
                return {"k": "compare", "names": names, "w": w, "pos": p}
            if kw == "grover":
                self.next(); name = self.ident("a realm name")
                if not self.at("id", "where"):
                    raise SurdError("S-E001", "expected 'where' and a condition", self.pos(),
                                    "grover bank where amount == 55")
                self.next()
                return {"k": "grover", "name": name, "cond": self.expr(), "pos": p}
            if kw == "swaptest":
                self.next(); a = self.expr(); self.expect("op", ",")
                return {"k": "swaptest", "a": a, "b": self.expr(), "pos": p}
            if kw == "symbol":
                self.next()
                names = [self.ident("a symbol name")]
                while self.accept("op", ","): names.append(self.ident())
                return {"k": "symbol", "names": names, "pos": p}
            if kw == "gate":
                self.next()
                name = self.ident("a gate name")
                params = []
                if self.accept("op", "("):
                    if not self.at("op", ")"):
                        params.append(self.ident())
                        while self.accept("op", ","): params.append(self.ident())
                    self.expect("op", ")")
                if self.accept("op", "="):
                    return {"k": "gate", "name": name, "params": params, "expr": self.expr(), "qs": [], "reg": [],
                            "pos": p}
                self.expect("op", ":", what="'=' and a gate expression, or ':' and wire names")
                qs, regs = [], []
                while True:
                    qs.append(self.ident("a wire name"))
                    isreg = bool(self.accept("op", "["))
                    if isreg: self.expect("op", "]", what="']' (a register argument is written r[])")
                    regs.append(isreg)
                    if not self.accept("op", ","): break
                return {"k": "gate", "name": name, "params": params, "expr": None, "qs": qs, "reg": regs,
                        "body": self.block(), "pos": p}
            if kw == "measure":
                self.next(); return {"k": "measure", "q": self.qargs(), "c": None, "pos": p}
            if kw == "barrier":
                self.next(); return {"k": "barrier", "q": None if self.at_sep() else self.qargs(), "pos": p}
            if kw == "if":
                self.next()
                cond = self.expr()
                body = self.block()
                els = None
                j = self.i
                while self.accept("nl"): pass
                if self.accept("kw", "else"):
                    els = [self.statement()] if self.at("kw", "if") else self.block()
                else:
                    self.i = j
                return {"k": "if", "cond": cond, "body": body, "else": els, "pos": p}
            if kw == "repeat":
                self.next(); n = self.expr()
                return {"k": "repeat", "n": n, "body": self.block(), "pos": p}
            if kw == "for":
                self.next(); var = self.ident(); self.expect("kw", "in")
                lo = self.expr()
                if self.accept("op", ".."):
                    hi = self.expr()
                    return {"k": "for", "var": var, "lo": lo, "hi": hi, "body": self.block(), "pos": p}
                return {"k": "foreach", "var": var, "iter": lo, "body": self.block(), "pos": p}
            if kw == "print":
                self.next()
                if self.at("id"):
                    w = self.peek()[1]
                    nxt = self.peek(1)
                    end = nxt[0] in ("nl", "eof") or (nxt[0] == "op" and nxt[1] in ";}")
                    if w in ("state", "circuit", "stats") and end:
                        self.next(); return {"k": "print_" + w, "pos": p}
                    if w == "probs":
                        self.next()
                        return {"k": "print_probs", "q": None if self.at_sep() else self.qargs(), "pos": p}
                    if w == "bloch" and nxt[0] == "id":
                        self.next(); return {"k": "print_bloch", "q": self.qargs(), "pos": p}
                    if w == "unitary" and not end:
                        self.next(); return {"k": "print_unitary", "g": self.expr(), "pos": p}
                    if w == "paths" and nxt[0] == "str":
                        self.next(); return {"k": "print_paths", "bits": self.next()[1], "pos": p}
                    if w == "amp" and nxt[0] == "str":
                        self.next(); return {"k": "print_amp", "bits": self.next()[1], "pos": p}
                label = None
                if self.at("str") and not (self.peek(1)[0] == "op" and self.peek(1)[1] in ("+", "==", "!=", "[")):
                    label = self.next()[1]
                    if not self.accept("op", ","):
                        return {"k": "print", "label": label, "e": None, "pos": p}
                es = [self.expr()]
                while self.accept("op", ","): es.append(self.expr())
                return {"k": "print", "label": label, "e": es[0], "more": es[1:], "pos": p}
            if kw == "expect":
                self.next(); return {"k": "expect", "e": self.expr(), "pos": p}
            if kw == "noise":
                self.next()
                if self.at("id", "off"):
                    self.next(); return {"k": "noise", "name": None, "params": [], "pos": p}
                name = self.ident("a noise channel or 'off'")
                params = []
                if self.accept("op", "("):
                    params.append(self.expr())
                    while self.accept("op", ","): params.append(self.expr())
                    self.expect("op", ")")
                return {"k": "noise", "name": name, "params": params, "pos": p}
            if kw == "sample":
                self.next(); n = self.expr()
                return {"k": "sample", "n": n, "q": None if self.at_sep() else self.qargs(), "pos": p}
            raise SurdError("S-E001", f"'{kw}' cannot start a statement", p,
                            "a gate goes on wires: q[0] <- h" if kw == "ctl" else None)
        if t[0] == "kw" and t[1] == "fn":
            return {"k": "exprstmt", "e": self.expr(), "pos": p}
        if t[0] == "id" and self.peek(1)[0] == "op" and self.peek(1)[1] == "(":
            return {"k": "exprstmt", "e": self.expr(), "pos": p}       # a call used as a statement
        if t[0] == "id" and self.peek(1)[0] == "op" and self.peek(1)[1] == "[" and self._index_assign_ahead():
            self.next(); self.next()                                            # name[key] = expr stores in place (1.7)
            key = self.expr(); self.expect("op", "]"); self.expect("op", "=")
            return {"k": "exprstmt", "e": {"k": "fn", "name": "set", "args": [{"k": "ref", "name": t[1], "lo": None, "hi": None, "pos": p}, key, self.expr()], "pos": p}, "pos": p}
        if t[0] == "id" and self.peek(1)[0] == "op" and self.peek(1)[1] == "=":    # n = expr rebinds an existing name (1.7)
            self.next(); self.next()
            return {"k": "let", "name": t[1], "e": self.expr(), "pos": p, "assign": True}
        if t[0] == "id" or (t[0] == "op" and t[1] == "("):
            targets = self.targets()
            self.expect("op", "<-", what="'<-' (wires receive a gate: q[0] <- h)")
            if self.accept("kw", "measure"):
                return {"k": "measure", "c": targets, "q": self.qargs(), "pos": p}
            if self.accept("kw", "reset"):
                return {"k": "reset", "q": targets, "pos": p}
            return {"k": "apply", "q": targets, "g": self.expr(), "pos": p}
        got = "end of input" if t[0] == "eof" else repr(t[1])
        raise SurdError("S-E001", f"a statement cannot start with {got}", p)

    # expressions
    def expr(self): return self.e_or()

    def e_or(self):
        a = self.e_and()
        while self.at("kw", "or"):
            p = self.pos(); self.next(); a = {"k": "bin", "op": "or", "a": a, "b": self.e_and(), "pos": p}
        return a

    def e_and(self):
        a = self.e_not()
        while self.at("kw", "and"):
            p = self.pos(); self.next(); a = {"k": "bin", "op": "and", "a": a, "b": self.e_not(), "pos": p}
        return a

    def e_not(self):
        if self.at("kw", "not"):
            p = self.pos(); self.next(); return {"k": "not", "a": self.e_not(), "pos": p}
        return self.e_cmp()

    def e_cmp(self):
        a = self.e_seq()
        t = self.peek()
        if t[0] == "op" and t[1] in ("==", "!=", "<", "<=", ">", ">=", "=~"):
            p = self.pos(); self.next()
            return {"k": "cmp", "op": t[1], "a": a, "b": self.e_seq(), "pos": p}
        return a

    def e_seq(self):
        a = self.e_add()
        while self.at("op", ">>"):
            p = self.pos(); self.next()
            a = {"k": "gseq", "a": a, "b": self.e_add(), "pos": p}
        return a

    def e_add(self):
        a = self.e_ten()
        while self.at("op", "+") or self.at("op", "-"):
            p = self.pos(); op = self.next()[1]
            a = {"k": "bin", "op": op, "a": a, "b": self.e_ten(), "pos": p}
        return a

    def e_ten(self):
        a = self.e_mul()
        while self.at("op", "&"):
            p = self.pos(); self.next()
            a = {"k": "gten", "a": a, "b": self.e_mul(), "pos": p}
        return a

    def e_mul(self):
        a = self.e_un()
        while self.at("op", "*") or self.at("op", "/") or self.at("op", "%"):
            p = self.pos(); op = self.next()[1]
            a = {"k": "bin", "op": op, "a": a, "b": self.e_un(), "pos": p}
        return a

    def e_un(self):
        p = self.pos()
        if self.accept("op", "-"): return {"k": "neg", "a": self.e_un(), "pos": p}
        if self.accept("op", "+"): return self.e_un()
        if self.accept("op", "~"): return {"k": "ginv", "a": self.e_un(), "pos": p}
        if self.accept("kw", "ctl"): return {"k": "gctl", "a": self.e_un(), "pos": p}
        a = self.e_prim()
        if self.at("op", "^"):
            p = self.pos(); self.next()
            return {"k": "bin", "op": "^", "a": a, "b": self.e_un(), "pos": p}
        return a

    def e_prim(self):
        node = self.e_atom()
        while self.at("op", "["):
            p = self.pos(); self.next()
            lo = self.expr(); hi = None
            if self.accept("op", ".."):
                hi = {"k": "end", "pos": self.pos()} if self.at("op", "]") else self.expr()
            self.expect("op", "]")
            node = {"k": "index", "a": node, "lo": lo, "hi": hi, "pos": p}
        return node

    def e_atom(self):
        p = self.pos()
        t = self.peek()
        if t[0] == "kw" and t[1] == "none":
            self.next(); return {"k": "none", "pos": p}
        if t[0] == "kw" and t[1] == "fn":
            self.next(); params = self.param_list()
            if self.accept("op", "=>"):
                return {"k": "lambda", "params": params, "expr": self.expr(), "body": None, "pos": p}
            return {"k": "lambda", "params": params, "expr": None, "body": self.block(), "pos": p}
        if t[0] == "op" and t[1] == "[":
            self.next(); items = []
            while self.accept("nl"): pass
            if not self.at("op", "]"):
                items.append(self.expr())
                while self.accept("op", ","):
                    while self.accept("nl"): pass
                    if self.at("op", "]"): break
                    items.append(self.expr())
            while self.accept("nl"): pass
            self.expect("op", "]")
            return {"k": "list", "items": items, "pos": p}
        if t[0] == "op" and t[1] == "{":
            self.next(); pairs = []
            while self.accept("nl"): pass
            if not self.at("op", "}"):
                while True:
                    k_ = self.expr(); self.expect("op", ":", what="':' between key and value"); v_ = self.expr()
                    pairs.append((k_, v_))
                    while self.accept("nl"): pass
                    if not self.accept("op", ","): break
                    while self.accept("nl"): pass
                    if self.at("op", "}"): break
            while self.accept("nl"): pass
            self.expect("op", "}")
            return {"k": "map", "pairs": pairs, "pos": p}
        if t[0] == "num":
            self.next(); return {"k": "num", "v": F(t[1]), "pos": p}
        if t[0] == "money":
            self.next(); return {"k": "money", "v": F(t[1]), "pos": p}
        if t[0] == "date":
            self.next(); return {"k": "date", "v": t[1], "pos": p}
        if t[0] == "str":
            self.next(); return {"k": "str", "v": t[1], "pos": p}
        if t[0] == "kw" and t[1] == "pi":
            self.next(); return {"k": "pi", "pos": p}
        if t[0] == "kw" and t[1] in ("true", "false"):
            self.next(); return {"k": "bool", "v": t[1] == "true", "pos": p}
        if self.accept("op", "("):
            e = self.expr(); self.expect("op", ")"); return e
        if t[0] == "id":
            if self.peek(1)[0] == "op" and self.peek(1)[1] == "(":
                name = self.next()[1]; self.next()
                args = []
                if not self.at("op", ")"):
                    args.append(self.expr())
                    while self.accept("op", ","): args.append(self.expr())
                self.expect("op", ")")
                return {"k": "fn", "name": name, "args": args, "pos": p}
            return self.qarg()
        got = "end of line" if t[0] in ("nl", "eof") else repr(t[1])
        raise SurdError("S-E001", f"expected a value, got {got}", p,
                        "an operator needs something after it: h >> t, ~h, ctl x" if t[0] in ("nl", "eof") else None)


def parse(src):
    return Parser(lex(src)).program()


PRELUDE = """
gate cx = ctl x
gate cy = ctl y
gate cz = ctl z
gate ch = ctl h
gate cp(t) = ctl p(t)
gate crx(t) = ctl rx(t)
gate cry(t) = ctl ry(t)
gate crz(t) = ctl rz(t)
gate ccx = ctl ctl x
gate ccz = ctl ctl z
gate swap: a, b { (a, b) <- cx; (b, a) <- cx; (a, b) <- cx }
gate cswap = ctl swap
"""
_PRELUDE_AST = parse(PRELUDE)
NONUNITARY = {"wire", "bit", "seed", "gate", "symbol", "fndef", "test", "space", "realm", "compare", "grover", "swaptest", "game", "solve", "measure", "reset", "print", "print_state", "print_probs", "print_paths",
              "print_bloch", "print_amp", "print_circuit", "print_stats", "print_unitary", "expect", "sample",
              "noise"}
LABEL = {"id": "I", "h": "H", "x": "X", "y": "Y", "z": "Z", "s": "S", "sdg": "S†", "t": "T", "tdg": "T†",
         "sx": "√X", "sxdg": "√X†", "rx": "RX", "ry": "RY", "rz": "RZ", "p": "P", "u": "U"}


class QB:
    __slots__ = ("i",)

    def __init__(self, i): self.i = i


class QR:
    """a register bound to a gate's r[] argument"""
    __slots__ = ("idx",)

    def __init__(self, idx): self.idx = list(idx)


class GateVal:
    """a gate as a value: prim | def | seq (>>) | ten (&) | ctl | inv (~) | pow (^)"""
    __slots__ = ("kind", "a", "b", "name", "params", "arity", "regs")

    def __init__(self, kind, a=None, b=None, name=None, params=(), arity=1, regs=None):
        self.kind, self.a, self.b, self.name, self.params, self.arity, self.regs = \
            kind, a, b, name, tuple(params), arity, regs

    def label(self):
        k = self.kind
        par = "(" + ", ".join(fmt_angle(p) for p in self.params) + ")" if self.params else ""
        if k in ("prim", "def"): return self.name + par
        if k == "seq": return f"{self.a.label()} >> {self.b.label()}"
        if k == "ten": return f"({self.a.label()} & {self.b.label()})"
        if k == "ctl": return f"ctl {self.a.label()}"
        if k == "inv": return f"~{self.a.label()}"
        if k == "word":
            if self.arity == 1:
                return " >> ".join(n + ("(" + ", ".join(fmt_angle(p) for p in ps) + ")" if ps else "")
                                   for (n, ps, t, cs) in self.a) or "id"
            w = [f"w{i}" for i in range(self.arity)]
            body = []
            for (n, ps, t, cs) in self.a:
                par = "(" + ", ".join(fmt_angle(p) for p in ps) + ")" if ps else ""
                lhs = w[t] if not cs else "(" + ", ".join([w[c] for c in cs] + [w[t]]) + ")"
                body.append(f"{lhs} <- {'ctl ' * len(cs)}{n}{par}")
            return "{ " + "; ".join(body) + " }" if body else "{ }"
        return f"{self.a.label()} ^ {self.b}"

    def __str__(self): return f"<gate {self.label()} on {self.arity if self.arity else 'a register'} wire(s)>"


class ChannelVal:
    __slots__ = ("name", "params")

    def __init__(self, name, params): self.name, self.params = name, list(params)
    def __str__(self): return f"<channel {self.name}({', '.join(fmt_angle(p) for p in self.params)})>"


class FuncVal:
    __slots__ = ("name", "params", "body", "expr", "closure")

    def __init__(self, name, params, body, expr, closure):
        self.name, self.params, self.body, self.expr, self.closure = name, params, body, expr, closure

    def __str__(self): return f"<function {self.name}({', '.join(self.params)})>"


class _None:
    def __str__(self): return "none"
    def __repr__(self): return "none"


NONE = _None()


class ReturnSignal(Exception):
    def __init__(self, value): self.value = value


class BreakSignal(Exception): pass
class ContinueSignal(Exception): pass


def map_key(v):
    if isinstance(v, R2):
        return int(v.a) if v.is_rational() and v.a.denominator == 1 else str(v)
    if isinstance(v, (Money, Date)): return str(v)
    if isinstance(v, (str, bool)): return v
    return str(v)


def type_name(v):
    if v is NONE: return "none"
    if isinstance(v, bool): return "bool"
    if isinstance(v, R2): return "number" if v.is_rational() else "exact real"
    if isinstance(v, PiVal): return "angle"
    if isinstance(v, float): return "float"
    if isinstance(v, str): return "string"
    if isinstance(v, list): return "list"
    if isinstance(v, dict): return "map"
    if isinstance(v, FuncVal): return "function"
    if isinstance(v, GateVal): return "gate"
    if isinstance(v, ChannelVal): return "channel"
    if isinstance(v, Poly): return "formula"
    if isinstance(v, Realm): return "realm"
    if isinstance(v, Record): return "record"
    if isinstance(v, (QB, QR)): return "wires"
    if isinstance(v, Meaning): return "meaning" if len(v.spaces) == 1 else "joint meaning"
    if isinstance(v, Money): return "money"
    if isinstance(v, Bound): return "bound"
    if isinstance(v, Date): return "date"
    if isinstance(v, Space): return "space"
    if isinstance(v, surd_search.SearchIndex): return "search index"
    return type(v).__name__


def value_exactness(v, seen=None, depth=0):
    """Describe numeric representation, separately from quantum-state exactness or source truth."""
    def result(exact, representation, status=None):
        return {"exact": exact, "status": status or ("exact" if exact is True else "approximate" if exact is False else "not_applicable"),
                "representation": representation}
    if isinstance(v, (R2, QW)): return result(True, "cyclotomic")
    if isinstance(v, PiVal): return result(True, "rational_multiple_of_pi")
    if isinstance(v, Poly): return result(True, "symbolic_polynomial")
    if isinstance(v, Money): return result(True, "rational_dollars")
    if isinstance(v, Date): return result(True, "calendar_day")
    if type(v) in (int, F): return result(True, "rational")
    if isinstance(v, (float, complex)): return result(False, "binary_floating_point")
    if isinstance(v, Meaning): return result(v.exact, "cyclotomic_amplitudes" if v.exact else "complex_floating_amplitudes")
    if isinstance(v, (list, dict)):
        seen = set() if seen is None else seen
        if id(v) in seen or depth >= 64: return result(None, "container", "unknown")
        seen.add(id(v))
        try:
            items = ({str(k): value_exactness(x, seen, depth + 1) for k, x in v.items()} if isinstance(v, dict)
                     else [value_exactness(x, seen, depth + 1) for x in v])
        finally:
            seen.remove(id(v))
        states = {x["status"] for x in (items.values() if isinstance(items, dict) else items)} - {"not_applicable"}
        status = ("unknown" if "unknown" in states else "mixed" if "mixed" in states or {"exact", "approximate"} <= states
                  else next(iter(states)) if states else "not_applicable")
        out = result(True if status == "exact" else False if status in ("mixed", "approximate") else None,
                     "map" if isinstance(v, dict) else "list", status)
        out["items"] = items
        return out
    if isinstance(v, (GateVal, ChannelVal, FuncVal, Realm, Record, Bound)):
        return result(None, type_name(v), "unknown")
    return result(None, type_name(v))


def language_metadata(v):
    if v is None: return NONE
    if isinstance(v, dict): return {k: language_metadata(x) for k, x in v.items()}
    if isinstance(v, list): return [language_metadata(x) for x in v]
    return v


def finite_approximation(v):
    if not isinstance(v, (R2, float)): return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (OverflowError, ValueError):
        return None


def parse_expression(src):
    """Consume the complete expression, including its end; never accept an ignored suffix."""
    if not isinstance(src, str):
        raise SurdError("S-E009", "code must be text", None, "send code as a JSON string")
    p = Parser(lex(src))
    while p.accept("nl"): pass
    node = p.expr()
    while p.accept("nl"): pass
    p.expect("eof", what="end of expression", hint="eval takes one expression; use exec for statements")
    return node


MONEY_FUNCS = {"set_bounds", "butfor", "whatif", "verdict", "lo", "hi", "width", "within", "bound", "money", "date", "events", "libr", "share_bounds", "trace", "spectrum", "post", "balances", "trial",
               "statement", "flows", "net", "paths", "roundtrips", "subset_sum", "window_sums", "grover_sum", "provenance"}
BANK_FUNCS = {"bank", "foot", "returns", "reconcile", "calibrate"}
SEARCH_FUNCS = {"search_index", "search_load", "search_save", "search_info", "search", "search_evaluate", "search_compare", "search_fuse", "search_reason", "search_posture", "search_quantum"}
INTEGRATION_FUNCS = {"qulap_solve", "qulap_core", "qulap_sensitivity", "qulap_allocations",
                     "qulap_asof", "qulap_bind", "qulap_quantum", "timeline_run"}
INTEGRATION_FUNCS |= {"graph_hybrid_search","graph_explain","graph_inspect_record","research_next","graph_propose_relations","graph_review_relation","graph_suggest_relations","graph_import_documents"}
INTEGRATION_FUNCS |= {"sqlite_bootstrap","sqlite_status","sqlite_backup"}
INTEGRATION_FUNCS |= {"ontology_check","ontology_validate","ontology_expand","ontology_project","ontology_diff"}
INTEGRATION_FUNCS |= {"normalize_value","normalize_record","graph_normalize","graph_normalization","graph_normalized_search"}
INTEGRATION_FUNCS |= {"graph_case_map","graph_case_episode","graph_case_lifecycle","graph_case_compare",
                      "graph_case_versions","graph_case_recover","graph_case_evaluate"}
INTEGRATION_FUNCS |= {"graph_init","graph_ingest","graph_snapshot","graph_diff","graph_withdraw","graph_verify",
                      "graph_search","graph_paths","graph_overview","graph_resolve","graph_analyze","graph_compile","graph_evolve",
                      "graph_import_timeline","graph_ingest_files","research_plan","research_run","research_learn","research_methods",
                      "graph_propose_predicate","graph_activate_predicate"}
SEM_FUNCS = {"phase", "sense", "blend", "read", "joint", "distribution", "gap", "entropy", "given", "normalize", "condition", "collapse", "reframe",
             "encode", "senses", "beliefs", "trust", "belnap", "jaccard"}
CORE_FUNCS = {"range", "push", "pop", "str", "join", "split", "keys", "values", "map", "filter", "reduce", "sum",
              "min", "max", "sorted", "reverse", "contains", "type", "int", "floor", "set", "upper", "lower", "replace", "startswith", "endswith",
              "count", "any", "all", "zip", "enumerate", "repr", "get", "exactness"} | surd_collections.FUNCTIONS


# ───────────────────────────── interpreter ─────────────────────────────
class Interp:
    def __init__(self, seed=None, rng=None, echo=True, force_float=False, shots_mode=False, force_mixed=False,
                 max_steps=None, timeout=None, budget=None):
        self.budget = budget if budget is not None else ExecutionBudget(max_steps, timeout)
        self.state = PureState(force_float)
        self.force_mixed = force_mixed
        self.noise = None
        self.gphase = R2()
        self.games = {}
        self.cur_sink = None
        self.tests = []
        self.qregs, self.qnames, self.cregs, self.cbits, self.cnames = {}, [], {}, [], []
        self.gates, self.globals = {}, {}
        self.rng = rng or random.Random(seed)
        self.echo, self.shots_mode = echo, shots_mode
        self.lines, self.expects, self.notes, self.trace = [], [], [], []
        self.used_if = False
        self.read_bits = False
        self.tainted = set()
        self.measure_reads = 0
        self.statement_kind = None
        self.dynamic_reasons = []
        self.tainted_objects = {}
        self.has_measured = False
        # Built-in declarations are setup, including when shots share an active budget.
        saved_budget, self.budget = self.budget, ExecutionBudget()
        self.exec_block(_PRELUDE_AST, self.globals, None)
        self.budget = saved_budget
        self.builtin_gates = set(self.gates)

    # output
    def emit(self, s):
        self.lines.append(s)
        if self.echo: print(s)

    def run(self, src):
        if not isinstance(src, str):
            raise SurdError("S-E009", "code must be text", None, "send code as a JSON string")
        try:
            with self.budget.execution():
                self.exec_block(parse(src), self.globals, None)
        except ReturnSignal:
            raise SurdError("S-E009", "return outside a function", None, "return belongs inside fn … { }")
        except (BreakSignal, ContinueSignal):
            raise SurdError("S-E009", "break or continue outside a loop", None)
        return self

    def evaluate(self, src, setup=False):
        if not isinstance(src, str):
            raise SurdError("S-E009", "code must be text", None, "send code as a JSON string")
        with self.budget.execution():
            src = src.rstrip()
            if setup and "\n" in src:
                prefix, src = src.rsplit("\n", 1)
                self.run(prefix)
            return self.ev(parse_expression(src), self.globals)

    def exec_block(self, stmts, env, sink):
        for st in stmts:
            self.exec_stmt(st, env, sink)

    def exec_stmt(self, st, env, sink):
        self.budget.checkpoint(st.get("pos"))
        k = st["k"]
        if sink is not None and k in NONUNITARY:
            raise SurdError("S-E007", f"'{k.split('_')[0]}' is not allowed inside a gate body", st["pos"],
                           "a gate is a unitary: only gate calls, let, for and repeat")
        prev, self.cur_sink = self.cur_sink, sink
        prev_kind, self.statement_kind = self.statement_kind, k
        try:
            getattr(self, "st_" + k)(st, env, sink)
        finally:
            self.cur_sink = prev
            self.statement_kind = prev_kind

    def _binding_scope(self, env, name):
        """Find the actual binding, including nested closure ChainMaps and shadowing."""
        scopes = env.maps if isinstance(env, ChainMap) else [env]
        for scope in scopes:
            if name in scope:
                return self._binding_scope(scope, name) if isinstance(scope, ChainMap) else scope
        return self.globals

    def _measurement_read(self, env, name, pos):
        if env.get("__gate__") or self.cur_sink is not None:
            raise SurdError("S-E007", f"'{name}' depends on measured bits; a gate body cannot use it", pos,
                            "use measurement-dependent control outside the gate")
        self.read_bits = True
        self.measure_reads += 1
        # Conservative: even an unused classical alias may later escape through a mutable value.
        # Plain observation remains exportable; nested function statements have their own kind.
        if self.statement_kind not in {"expect", "print", "print_probs", "print_state", "print_amp", "print_bloch"}:
            self.used_if = True          # legacy public flag now means any measurement-dependent execution
            reason = {"name": name, "line": pos[0] if pos else None, "statement": self.statement_kind}
            if reason not in self.dynamic_reasons:
                self.dynamic_reasons.append(reason)

    def _taint_binding(self, env, name, dependent):
        scope = self._binding_scope(env, name)
        names = self.tainted if scope is self.globals else scope.setdefault("__measurement_names__", set())
        (names.add if dependent else names.discard)(name)
        value = scope[name]
        if dependent and isinstance(value, (list, dict)):
            self.tainted_objects[id(value)] = value

    # declarations
    def _taken(self, name):
        return name in self.qregs or name in self.cregs or name in self.gates or name in PRIM or name in ALIAS

    def _taken_msg(self, name):
        if name in PRIM or name in ALIAS or name in self.gates:
            return f"'{name}' is a gate name; a register cannot share it"
        return f"'{name}' is already declared"

    def st_wire(self, st, env, sink):
        sh = sorted(getattr(self, "shadowed_gates", set()))
        if sh:
            raise SurdError("S-E011", f"this program uses gate name(s) {', '.join(sh)} as variables, so it cannot declare wires",
                            st["pos"], f"rename the variable(s): {', '.join(x + '_val' for x in sh)}")
        for name, size in st["decls"]:
            if self._taken(name):
                raise SurdError("S-E011", self._taken_msg(name), st["pos"], "pick a new name")
            n = 1 if size is None else self.int_of(size, env)
            if n < 1: raise SurdError("S-E005", "a register needs at least one wire", st["pos"])
            if self.noise and self.state.n + n > MAX_MIXED:
                raise SurdError("S-E010", f"noise is on; a density matrix holds {MAX_MIXED} qubits", st["pos"])
            base = self.state.n
            self.state.add_qubits(n)
            if self.state.exact and self.state.n > MAX_EXACT:
                self.state.to_float()
                self.notes.append(f"line {st['pos'][0]}: {self.state.n} qubits > {MAX_EXACT} — float mode")
            if self.state.kind == "mixed" and self.state.exact and self.state.n > MAX_MIXED_EXACT:
                self.state.to_float()
                self.notes.append(f"line {st['pos'][0]}: mixed state over {MAX_MIXED_EXACT} qubits — float mode")
            if self.force_mixed and self.state.kind == "pure":
                self.to_mixed(st["pos"], None)
            self.qregs[name] = list(range(base, base + n))
            self.qnames += [name if size is None else f"{name}[{j}]" for j in range(n)]

    def st_bit(self, st, env, sink):
        for name, size in st["decls"]:
            if self._taken(name):
                raise SurdError("S-E011", self._taken_msg(name), st["pos"])
            n = 1 if size is None else self.int_of(size, env)
            base = len(self.cbits)
            self.cbits += [0] * n
            self.cregs[name] = list(range(base, base + n))
            self.cnames += [name if size is None else f"{name}[{j}]" for j in range(n)]

    def st_let(self, st, env, sink):
        if st.get("assign") and st["name"] not in env and st["name"] not in self.globals:
            raise SurdError("S-E002", f"'{st['name']}' is not a variable yet", st["pos"], f"create it first: let {st['name']} = …")
        nm = st["name"]
        # 1.7: a program with no wires may name a variable after a built-in gate (p, s, t, x …); once wires exist the
        # names are the gates' again, and a wire declaration refuses a program that already shadowed one
        builtin_gate = (nm in PRIM or nm in ALIAS) and nm not in self.gates
        if builtin_gate and not self.qregs:
            self.shadowed_gates = getattr(self, "shadowed_gates", set()) | {nm}
        elif nm in PRIM or nm in ALIAS or nm in self.gates or nm in CHANNELS:
            raise SurdError("S-E011", f"'{nm}' is a gate name; a variable cannot share it in a program with wires", st["pos"],
                            f"try {nm}_val or {nm}1")
        before = self.measure_reads
        env[st["name"]] = self.ev(st["e"], env)
        self._taint_binding(env, st["name"], self.measure_reads != before)

    def st_symbol(self, st, env, sink):
        for name in st["names"]:
            # a symbol may share a name with a gate that always takes parameters (p, rx …): p(θ) stays the gate
            param_gate = (name in PRIM and PRIM[name] > 0) or (name in self.gates and self.gates[name]["params"])
            if (not param_gate and (name in PRIM or name in ALIAS or name in self.gates)) or name in CHANNELS \
                    or name in self.qregs or name in self.cregs:
                raise SurdError("S-E011", f"'{name}' is already a name", st["pos"], "pick another symbol name")
            self.globals[name] = Poly.var(name)

    def st_seed(self, st, env, sink):
        if not self.shots_mode:
            self.rng.seed(self.int_of(st["e"], env))

    def st_gate(self, st, env, sink):
        name = st["name"]
        if name in PRIM or name in self.gates or name in ALIAS or name in CHANNELS:
            if not (name in self.gates and name not in getattr(self, "builtin_gates", ())):
                raise SurdError("S-E011", f"gate '{name}' is built in", st["pos"], "choose another name")
            raise SurdError("S-E011", f"gate '{name}' is already defined", st["pos"])
        if name in self.qregs or name in self.cregs:
            raise SurdError("S-E011", f"'{name}' is already a register", st["pos"])
        if len(set(st["qs"])) != len(st["qs"]):
            raise SurdError("S-E006", "a gate's qubit arguments must have distinct names", st["pos"])
        st.setdefault("reg", [False] * len(st["qs"]))
        self.gates[name] = st

    # control flow
    def st_if(self, st, env, sink):
        self.read_bits = False
        cond = self.ev(st["cond"], env)
        if self.read_bits and sink is None:
            self.used_if = True
        if self.truthy(cond, st["pos"]):
            self.exec_block(st["body"], env, sink)
        elif st["else"]:
            self.exec_block(st["else"], env, sink)

    def st_repeat(self, st, env, sink):
        for _ in range(self.int_of(st["n"], env)):
            self.budget.checkpoint(st["pos"])
            if self._loop_body(st["body"], env, sink): break

    def st_for(self, st, env, sink):
        lo, hi = self.int_of(st["lo"], env), self.int_of(st["hi"], env)
        for j in range(lo, hi):
            self.budget.checkpoint(st["pos"])
            env[st["var"]] = R2(j)
            if self._loop_body(st["body"], env, sink): break

    # quantum ops
    def gate_value(self, name, params, pos):
        name = ALIAS.get(name, name)
        for v in params:
            if not isinstance(v, (R2, PiVal, float)):
                raise SurdError("S-E009", "gate parameters must be numbers or angles", pos)
        if name in PRIM:
            if len(params) != PRIM[name]:
                raise SurdError("S-E004", f"'{name}' takes {PRIM[name]} parameter(s), got {len(params)}", pos,
                                f"write {name}(pi/4)" if PRIM[name] else None)
            return GateVal("prim", name=name, params=params, arity=1)
        g = self.gates.get(name)
        if g is None:
            return None
        if len(params) != len(g["params"]):
            raise SurdError("S-E004", f"'{name}' takes {len(g['params'])} parameter(s), got {len(params)}", pos)
        if g["expr"] is not None:           # algebraic definition: evaluate its expression now
            self._alg_depth = getattr(self, "_alg_depth", 0) + 1
            try:
                if self._alg_depth > 64:
                    raise SurdError("S-E014", f"gate '{name}' is defined in terms of itself", pos,
                                    "an algebraic gate cannot refer to itself")
                env = {"__gate__": True, "__depth__": 0}
                env.update(zip(g["params"], params))
                v = self.ev(g["expr"], env)
            finally:
                self._alg_depth -= 1
            if not isinstance(v, GateVal):
                raise SurdError("S-E009", f"gate '{name}' = … must be a gate expression", g["pos"])
            return v
        regs = g["reg"]
        return GateVal("def", name=name, params=params, arity=None if any(regs) else len(g["qs"]), regs=regs)

    def gate_ops(self, g, wires, pos, depth):
        """primitive (name, params, target, controls) list for gate value g on the given wires"""
        if depth > 64:
            raise SurdError("S-E014", f"'{g.label()}' expands more than 64 levels deep", pos,
                            "a gate that calls itself never ends")
        k = g.kind
        if k == "prim":
            if isinstance(wires[0], list):
                raise SurdError("S-E012", f"'{g.name}' acts on one wire, got a register", pos)
            if g.name == "id": return []            # id is "nothing on this wire": no gate, no noise, no slot
            return [(g.name, g.params, wires[0], ())]
        if k == "def": return self.expand_def(g, wires, pos, depth + 1)
        if k == "seq": return self.gate_ops(g.a, wires, pos, depth) + self.gate_ops(g.b, wires, pos, depth)
        if k == "ten":
            na = g.a.arity
            return self.gate_ops(g.a, wires[:na], pos, depth) + self.gate_ops(g.b, wires[na:], pos, depth)
        if k == "ctl":
            c = wires[0]
            return [(n, ps, t, (c,) + cs) for (n, ps, t, cs) in self.gate_ops(g.a, wires[1:], pos, depth)]
        if k == "inv": return [adjoint(o) for o in reversed(self.gate_ops(g.a, wires, pos, depth))]
        if k == "word":     # synthesized: a list of (prim, params, local target, local controls)
            return [(n, ps, wires[t], tuple(wires[c] for c in cs)) for (n, ps, t, cs) in g.a]
        base = self.gate_ops(g.a, wires, pos, depth)
        if g.b < 0: base = [adjoint(o) for o in reversed(base)]
        return base * abs(g.b)

    def expand_def(self, g, args, pos, depth):
        d = self.gates[g.name]
        env = {"__gate__": True, "__depth__": depth}
        env.update(zip(d["params"], g.params))
        for q, a, isreg in zip(d["qs"], args, d["reg"]):
            if isreg != isinstance(a, list):
                raise SurdError("S-E012", f"'{q}' of '{g.name}' expects {'a register' if isreg else 'one wire'}",
                                pos)
            env[q] = QR(a) if isreg else QB(a)
        ops = []
        self.exec_block(d["body"], env, ops)
        return ops

    def layout(self, val, groups, pos):
        """how the wires on the left of <- feed the gate: one call, a broadcast, or a zip"""
        if val.arity is None:
            if len(groups) != len(val.regs):
                raise SurdError("S-E004", f"'{val.label()}' takes {len(val.regs)} argument(s), got {len(groups)}",
                                pos)
            args = []
            for g, isreg in zip(groups, val.regs):
                if isreg: args.append(list(g))
                elif len(g) == 1: args.append(g[0])
                else: raise SurdError("S-E012", f"'{val.label()}' wants one wire there; got {len(g)}", pos)
            return [args]
        k = val.arity
        if len(groups) == k:
            lens = {len(g) for g in groups if len(g) != 1}
            if len(lens) > 1:
                raise SurdError("S-E012", f"registers of lengths {sorted(lens)} cannot be zipped together", pos,
                                "zip needs registers of one length (single wires repeat)")
            L = lens.pop() if lens else 1
            return [[g[0] if len(g) == 1 else g[i] for g in groups] for i in range(L)]
        if k == 1:
            return [[w] for g in groups for w in g]
        if len(groups) == 1 and len(groups[0]) == k:
            return [list(groups[0])]
        n = sum(len(g) for g in groups)
        raise SurdError("S-E004", f"'{val.label()}' acts on {k} wires; got {n} wire(s) in {len(groups)} target(s)",
                        pos, f"write ({', '.join('w' + str(i) for i in range(k))}) <- {val.label()}")

    def st_apply(self, st, env, sink):
        pos = st["pos"]
        val = self.ev(st["g"], env)
        groups = [self.qref(t, env) for t in st["q"]]
        if isinstance(val, ChannelVal):
            if sink is not None:
                raise SurdError("S-E007", f"noise channel '{val.name}' is not allowed inside a gate body", pos,
                                "a gate is a unitary; apply the channel after the gate")
            self.check_channel(val.name, val.params, pos)
            self.to_mixed(pos, f"{val.name}({', '.join(fmt_angle(p) for p in val.params)})")
            for q in [w for g in groups for w in g]:
                self.channel(val.name, val.params, q, pos)
                self.trace.append(("n", val.name, tuple(val.params), q))
            return
        if not isinstance(val, GateVal):
            raise SurdError("S-E009", "the right side of <- must be a gate, a channel, measure or reset", pos,
                            "e.g. q <- h · (a, b) <- cx · c <- measure q · q <- dephase(1/10)")
        depth = env.get("__depth__", 0)
        for args in self.layout(val, groups, pos):
            flat = [x for a in args for x in (a if isinstance(a, list) else [a])]
            if len(set(flat)) != len(flat):
                raise SurdError("S-E006", f"'{val.label()}' got the same wire twice", pos,
                                "each wire can appear once on the left of <-")
            ops = self.gate_ops(val, args, pos, depth)
            for (_, _, t, c) in ops:
                if t in c or len(set(c)) != len(c):
                    raise SurdError("S-E006", "a control wire is also the target (or repeated)", pos)
            if sink is not None:
                sink.extend(ops)
            else:
                for op in ops:
                    self.apply(op, pos)

    def apply(self, op, pos):
        name, ps, t, ctrls = op
        try:
            mat = Mat(name, ps)
        except (OverflowError, ValueError):
            raise SurdError("S-E009", f"{name}: angle too large to compute", pos)
        if self.noise:
            self.to_mixed(pos, f"noise {self.noise[0]}")
        if name == "rz" and not ctrls and mat.ex is None and self.state.exact and eighth(ps[0]) is not None:
            mat = Mat("p", (ps[0],))                       # rz(θ) = e^{-iθ/2}·p(θ): the phase is unobservable
            self.gphase = ang_add(self.gphase, ang_neg(ang_half(ps[0])))
        if self.state.exact and mat.ex is None:
            self.notes.append(f"line {pos[0]}: {name}({', '.join(fmt_angle(p) for p in ps)}) is outside the exact "
                              f"set — float mode from here")
        self.state.apply(mat, t, ctrls)
        self.trace.append(("g", name, ps, t, ctrls))
        if self.noise:
            for q in (t,) + tuple(ctrls):
                self.channel(self.noise[0], self.noise[1], q, pos)
                self.trace.append(("n", self.noise[0], tuple(self.noise[1]), q))

    # noise
    def to_mixed(self, pos, why):
        if self.state.kind == "mixed": return
        if self.state.n > MAX_MIXED:
            raise SurdError("S-E010", f"noise needs a density matrix; {self.state.n} qubits is over {MAX_MIXED}", pos)
        if self.state.exact and self.state.n > MAX_MIXED_EXACT:
            self.state.to_float()
            self.notes.append(f"line {pos[0]}: mixed state over {MAX_MIXED_EXACT} qubits — float mode")
        self.state = MixedState(self.state)
        if why: self.notes.append(f"line {pos[0]}: {why} — density-matrix mode from here")

    def check_channel(self, name, params, pos):
        if len(params) != CHANNELS[name]:
            raise SurdError("S-E004", f"'{name}' takes {CHANNELS[name]} probability, got {len(params)}", pos)
        if any(type(v) is Poly for v in params):
            return                                      # a formula: its range is the reader's to choose
        exact = []
        for v in params:
            # Validate exact inputs before conversion: rounding can hide a negative residual.
            if not isinstance(v, (R2, float)) or isinstance(v, float) and not math.isfinite(v):
                raise SurdError("S-E009", f"'{name}' needs probabilities between 0 and 1", pos,
                                f"e.g. {name}(1/100)")
            p = v if isinstance(v, R2) else R2(F(v))
            if p.sign() < 0 or (R2(1) - p).sign() < 0:
                raise SurdError("S-E009", f"'{name}' needs probabilities between 0 and 1", pos)
            exact.append(p)
        if name == "pauli" and (R2(1) - sum(exact, R2())).sign() < 0:
            raise SurdError("S-E009", "pauli(px, py, pz) needs px + py + pz <= 1", pos)

    def channel(self, name, params, q, pos):
        self.to_mixed(pos, f"{name}({', '.join(fmt_angle(p) if type(p) is not Poly else str(p) for p in params)})")
        S = self.state
        ps = list(params)
        if any(type(v) is Poly for v in ps):
            if not S.exact:
                raise SurdError("S-E009", "symbolic noise needs an exact state", pos, "keep gates on the exact set")
            S.to_symbolic()
            one = R2(1)
            if name == "ampdamp":
                tb = 1 << q
                if any(not x.is_zero() for i, row in enumerate(S.r) for j, x in enumerate(row) if (i ^ j) & tb):
                    raise SurdError("S-E009", "ampdamp(γ) with a symbol scales coherences by √(1−γ), "
                                              "which is not a polynomial", pos, "use dephase/bitflip/depolarize, or a number")
                S.ampdamp(q, ps[0], Poly())
                return
            third = lambda v: v * R2(F(1, 3))
            z = Poly()
            px, py, pz = {"depolarize": lambda: (third(ps[0]),) * 3, "dephase": lambda: (z, z, ps[0]),
                          "bitflip": lambda: (ps[0], z, z), "pauli": lambda: tuple(ps)}[name]()
            S.pauli_channel(q, px, py, pz)
            return
        if S.exact and any(not (isinstance(v, R2) and v.is_rational()) for v in ps):
            S.to_float()
            self.notes.append(f"line {pos[0]}: {name} with a non-rational probability — float mode")
        if not S.exact: ps = [float(v) for v in ps]
        zero = R2() if S.exact else 0.0
        if name == "ampdamp":
            g = ps[0]
            s = fn1("sqrt", (R2(1) - g) if S.exact else 1.0 - g, pos)
            tb = 1 << q
            coherent = any(not S.isz(x) for i, row in enumerate(S.r) for j, x in enumerate(row)
                           if (i ^ j) & tb)
            if S.exact and not isinstance(s, R2) and not coherent:
                S.ampdamp(q, g, R2())          # √(1-γ) only scales coherences, and there are none
                return
            if S.exact and not isinstance(s, R2):
                S.to_float(); g = float(g)
                self.notes.append(f"line {pos[0]}: ampdamp √(1-γ) is irrational outside Q(√2) — float mode")
            S.ampdamp(q, g, s if S.exact else float(s))
            return
        third = (lambda v: v * R2(F(1, 3))) if S.exact else (lambda v: v / 3)
        px, py, pz = {"depolarize": lambda: (third(ps[0]),) * 3, "dephase": lambda: (zero, zero, ps[0]),
                      "bitflip": lambda: (ps[0], zero, zero), "pauli": lambda: tuple(ps)}[name]()
        S.pauli_channel(q, px, py, pz)

    def st_noise(self, st, env, sink):
        if st["name"] is None:
            self.noise = None; return
        name = st["name"]
        if name not in CHANNELS:
            raise SurdError("S-E003", f"unknown noise channel '{name}'", st["pos"],
                           "channels: " + ", ".join(sorted(CHANNELS)))
        params = [self.ev(e, env) for e in st["params"]]
        self.check_channel(name, params, st["pos"])
        if self.state.n > MAX_MIXED:
            raise SurdError("S-E010", f"noise needs a density matrix; {self.state.n} qubits is over {MAX_MIXED}",
                           st["pos"])
        self.noise = (name, params)

    def st_measure(self, st, env, sink):
        if getattr(self.state, "sym", False):
            raise SurdError("S-E009", "cannot measure a state whose probabilities are formulas", st["pos"],
                            "ask prob(…) for the formula; measurement would divide by it")
        qs = [i for a in st["q"] for i in self.qref(a, env)]
        self.has_measured = True
        if st["c"] is None:
            res = "".join(str(self.state.measure(q, self.rng)) for q in qs)
            for q in qs: self.trace.append(("m", q, None))
            self.emit(f"measure {', '.join(self.qnames[q] for q in qs)} -> {res}")
            return
        cs = [i for a in st["c"] for i in self.cref(a, env)]
        if len(cs) != len(qs):
            raise SurdError("S-E012", f"measuring {len(qs)} qubit(s) into {len(cs)} bit(s)", st["pos"])
        for q, c in zip(qs, cs):
            self.cbits[c] = self.state.measure(q, self.rng)
            self.trace.append(("m", q, c))

    def st_reset(self, st, env, sink):
        for a in st["q"]:
            for q in self.qref(a, env):
                if self.state.kind == "mixed":
                    self.state.reset(q)
                    self.trace.append(("r", q))
                    continue
                if self.state.measure(q, self.rng):
                    self.state.apply(Mat("x", ()), q, ())
                self.trace.append(("r", q))

    def st_barrier(self, st, env, sink):
        qs = list(range(self.state.n)) if st["q"] is None else [i for a in st["q"] for i in self.qref(a, env)]
        if sink is None: self.trace.append(("b", tuple(qs)))

    # output statements
    def st_print(self, st, env, sink):
        if st["e"] is None:
            self.emit(st["label"]); return
        vals = [fmt_value(self.ev(e, env)) for e in [st["e"]] + st.get("more", [])]
        self.emit((st["label"] + " " if st["label"] else "") + "   ".join(vals))

    def st_print_state(self, st, env, sink):
        s = self.state
        n = s.norm()
        if s.kind == "mixed":
            pur = s.purity(list(range(s.n)))
            self.emit(f"state  {s.n} qubit(s), mixed ({'exact' if s.exact else 'float'})   purity = "
                      f"{fmt_value(pur) if s.exact else fmt_float(pur)}   order: {' '.join(self.qnames)}")
            d = s.distribution(list(range(s.n)))
            for k in sorted(d)[:64]:
                p = d[k]
                self.emit(f"  |{k}>  population {fmt_value(p) if s.exact else fmt_float(p)}")
            coh = sum(1 for i, row in enumerate(s.r) for j, x in enumerate(row) if i != j and not s.isz(x))
            self.emit(f"  coherences (nonzero off-diagonal entries): {coh}")
            return
        mode = "exact" if s.exact else "float"
        self.emit(f"state  {s.n} qubit(s), {mode}   order: {' '.join(self.qnames)}")
        unnorm = s.exact and not (n - R2(1)).is_zero()
        gp = self.gphase
        if not (isinstance(gp, R2) and gp.is_zero()):
            self.emit(f"  (global phase e^(i·{fmt_angle(gp)}) set aside — it changes no measurement; amplitudes below "
                      f"omit it)")
        if unnorm:
            self.emit(f"  (after measurement — amplitudes shown unscaled; divide by √N, N = {n})")
        rows = [(i, a) for i, a in enumerate(s.v) if not s.isz(a)]
        shown = [("".join("1" if i >> q & 1 else "0" for q in range(s.n)), str(a) if s.exact else fmt_float(a),
                  s.div(s.n2(a), n)) for i, a in rows[:64]]
        w = max([len(x[1]) for x in shown] + [8])
        for bits, amp, p in shown:
            self.emit(f"  |{bits}>  {amp:<{w}}  p = {fmt_value(p) if s.exact else fmt_float(p)}")
        if len(rows) > 64: self.emit(f"  … {len(rows) - 64} more")

    def st_print_probs(self, st, env, sink):
        qs = list(range(self.state.n)) if st["q"] is None else [i for a in st["q"] for i in self.qref(a, env)]
        d = self.state.distribution(qs)
        self.emit(f"probs  {' '.join(self.qnames[q] for q in qs)}")
        for k in sorted(d):
            p = d[k]
            pf = float(p)
            self.emit(f"  {k}  {'█' * round(pf * 30):<30} {fmt_value(p) if isinstance(p, R2) else fmt_float(p)}")

    def st_print_bloch(self, st, env, sink):
        for a in st["q"]:
            for q in self.qref(a, env):
                x, y, z = self.state.bloch(q)
                f = (lambda v: str(v)) if self.state.exact else fmt_float
                self.emit(f"bloch {self.qnames[q]}  x = {f(x)}  y = {f(y)}  z = {f(z)}")

    def st_print_amp(self, st, env, sink):
        if self.state.kind == "mixed":
            raise SurdError("S-E009", "a mixed (noisy) state has no amplitudes", st["pos"],
                           "print state shows its populations; prob(...) still works")
        bits = st["bits"]
        if len(bits) != self.state.n or set(bits) - {"0", "1"}:
            raise SurdError("S-E009", f"amp needs a {self.state.n}-bit string", st["pos"])
        i = sum(1 << q for q, b in enumerate(bits) if b == "1")
        a = self.state.v[i]
        self.emit(f"amp |{bits}> = {a if self.state.exact else fmt_float(a)}")

    def st_print_circuit(self, st, env, sink):
        for line in draw(self.trace, self.qnames, self.cnames):
            self.emit(line)

    def st_expect(self, st, env, sink):
        v = self.ev(st["e"], env)
        ok = self.truthy(v, st["pos"])
        self.expects.append({"ok": ok, "line": st["pos"][0]})
        if not ok:
            detail = ""
            e = st["e"]
            if e["k"] == "cmp":
                detail = f"  (left = {fmt_value(self.ev(e['a'], env))}, right = {fmt_value(self.ev(e['b'], env))})"
            self.emit(f"✗ S-E008 line {st['pos'][0]}: expect failed{detail}")

    def st_sample(self, st, env, sink):
        n = self.int_of(st["n"], env)
        qs = list(range(self.state.n)) if st["q"] is None else [i for a in st["q"] for i in self.qref(a, env)]
        d = self.state.distribution(qs)
        keys = sorted(d)
        weights = [float(d[k]) for k in keys]
        hist = Counter(self.rng.choices(keys, weights, k=n)) if n > 0 else Counter()
        self.emit(f"sample {n}  {' '.join(self.qnames[q] for q in qs)}")
        for k in keys:
            c = hist.get(k, 0)
            self.emit(f"  {k}  {c:>6}  {'█' * round(30 * c / max(n, 1))}")

    # whole-gate algebra
    def unitary(self, val, pos):
        if not isinstance(val, GateVal):
            raise SurdError("S-E009", "expected a gate, like h or rz(pi/2) >> t", pos)
        if val.arity is None:
            raise SurdError("S-E009", f"'{val.label()}' takes a register; give it a fixed size to compare it", pos)
        if val.arity > 8:
            raise SurdError("S-E010", f"a {val.arity}-wire unitary is too large to compare", pos)
        ops = self.gate_ops(val, list(range(val.arity)), pos, 0)
        return ops_unitary(ops, val.arity)

    def st_print_unitary(self, st, env, sink):
        val = self.ev(st["g"], env)
        cols, exact = self.unitary(val, st["pos"])
        k = val.arity
        if k > 3: raise SurdError("S-E010", "print unitary shows up to 3 wires (8×8)", st["pos"])
        labels = ["".join("1" if j >> q & 1 else "0" for q in range(k)) for j in range(1 << k)]
        cells = [[(str(cols[c][r]) if exact else fmt_float(complex(cols[c][r]))) for c in range(1 << k)]
                 for r in range(1 << k)]
        w = max(len(x) for row in cells for x in row)
        self.emit(f"unitary {val.label()}  ({'exact' if exact else 'float'}; rows = output, columns = input)")
        self.emit("      " + "  ".join(f"|{lb}>".rjust(w) for lb in labels))
        for lb, row in zip(labels, cells):
            self.emit(f"  <{lb}| " + "  ".join(x.rjust(w) for x in row))

    # ═════════ ability: Feynman path sums — every path into an amplitude, and which ones cancel ═════════
    def st_print_paths(self, st, env, sink):
        bits = st["bits"]
        n = self.state.n
        if len(bits) != n or set(bits) - {"0", "1"}:
            raise SurdError("S-E009", f"paths needs a {n}-bit string", st["pos"], f'e.g. print paths "{"0" * n}"')
        if any(op[0] != "g" for op in self.trace):
            raise SurdError("S-E009", "paths needs a gate-only history (no measure, reset or noise yet)", st["pos"],
                            "print paths before the first measurement")
        target = sum(1 << q for q, b in enumerate(bits) if b == "1")
        mats = [(Mat(nm, ps), nm, ps, t, c) for (_, nm, ps, t, c) in self.trace]
        exact = all(m.ex is not None for m, *_ in mats)
        one = Q1 if exact else 1 + 0j
        lab = lambda i: "|" + "".join("1" if i >> q & 1 else "0" for q in range(n)) + ">"
        paths = [(0, one, ())]
        for gi, (m, nm, ps, t, c) in enumerate(mats):
            M = m.ex if exact else m.fl
            mask = 0
            for q in c: mask |= 1 << q
            branching = not (M[1] == 0 if not exact else M[1].is_zero()) and \
                not (M[0] == 0 if not exact else M[0].is_zero())
            new = []
            for idx, amp, hist in paths:
                if (idx & mask) != mask:
                    new.append((idx, amp, hist)); continue
                b = idx >> t & 1
                for b2 in (0, 1):
                    e = M[2 * b2 + b]                      # row b2, column b
                    if (e.is_zero() if exact else e == 0): continue
                    idx2 = (idx & ~(1 << t)) | (b2 << t)
                    step = ((gi, op_label(nm, ps, c), idx2),) if (branching or idx2 != idx) else ()
                    new.append((idx2, amp * e, hist + step))
            paths = new
            if len(paths) > 1 << 15:
                raise SurdError("S-E010", "more than 32768 paths; the path view is for small circuits", st["pos"])
        final = [(amp, hist) for idx, amp, hist in paths if idx == target]
        tot = Q1 * Q0 if False else None
        total = sum((a for a, _ in final), QZ if exact else 0j) if not exact else None
        if exact:
            total = QZ
            for a, _ in final: total = total + a
        zero = (lambda x: x.is_zero()) if exact else (lambda x: abs(x) < 1e-12)
        # cancellation: pair each contribution with an equal-and-opposite one
        used, pairs = set(), []
        for i, (a, _) in enumerate(final):
            if i in used: continue
            for j in range(i + 1, len(final)):
                if j not in used and zero(a + final[j][0]):
                    used |= {i, j}; pairs.append((i, j)); break
        fmt = (lambda x: str(x)) if exact else (lambda x: fmt_float(complex(x)))
        self.emit(f"paths into {lab(target)} after {len(mats)} gate(s): {len(final)} path(s), "
                  f"{2 * len(pairs)} cancel in {len(pairs)} pair(s) → amplitude {fmt(total)}")
        for i, (a, hist) in enumerate(final[:32]):
            route = lab(0) + "".join(f" -{g}→ {lab(j)}" for _, g, j in hist)
            mark = next((f"  ✕ cancels path {pj + 1 if pi == i else pi + 1}" for pi, pj in pairs if i in (pi, pj)), "")
            self.emit(f"  {i + 1:>3}. {fmt(a):>14}   {route}{mark}")
        if len(final) > 32: self.emit(f"  … {len(final) - 32} more")

    # ═════════ ability: stabilizer generators of a state (exact) ═════════
    def stabilizers(self, qs, pos):
        n = len(qs)
        if n > 6: raise SurdError("S-E010", "stabilizers looks at up to 6 wires (4096 Pauli strings)", pos)
        if len(set(qs)) != n: raise SurdError("S-E006", "stabilizers names a wire twice", pos)
        import itertools
        found = []
        for P in itertools.product("IXYZ", repeat=n):
            if all(x == "I" for x in P): continue
            v = self.state.expval(qs, "".join(P))
            if isinstance(v, R2):
                sgn = 1 if (v - R2(1)).is_zero() else (-1 if (v + R2(1)).is_zero() else 0)
            else:
                sgn = 1 if abs(v - 1) < 1e-9 else (-1 if abs(v + 1) < 1e-9 else 0)
            if sgn: found.append((sgn, "".join(P)))
        def vec(P):                                   # symplectic bits: x part then z part
            x = sum(1 << i for i, c in enumerate(P) if c in "XY")
            z = sum(1 << i for i, c in enumerate(P) if c in "ZY")
            return x | (z << n)
        basis, gens = {}, []
        for sgn, P in sorted(found, key=lambda t: (sum(c != "I" for c in t[1]), t[1].count("Y"), t[1])):
            v = vec(P)
            while v:
                top = v.bit_length() - 1
                if top not in basis:
                    basis[top] = v; gens.append(("+" if sgn > 0 else "-") + P); break
                v ^= basis[top]
        return gens, len(found) + 1 == 1 << n, len(found) + 1

    # ═════════ ability: optimal synthesis with proof ═════════
    def synth(self, g, pos):
        if g.arity is None or g.arity > 2:
            raise SurdError("S-E010", "synth builds 1- and 2-wire gates", pos)
        cols, exact = self.unitary(g, pos)
        if not exact:
            raise SurdError("S-E009", "synth needs an exact gate (Clifford+T angles)", pos,
                            "angles like 0.3 have no finite Clifford+T circuit")
        target = [[complex(cols[c][r]) for c in range(len(cols))] for r in range(len(cols))]
        word = synth_1q(target) if g.arity == 1 else synth_2q(target)
        if word is None:
            raise SurdError("S-E010", "no circuit within the search limit (T-count 12 / 14 gates)", pos)
        ops = [(n, (), t, cs) for (n, t, cs) in word]
        ops = optimize([("g",) + o for o in ops])
        val = GateVal("word", [o[1:] for o in ops], arity=g.arity)
        if not unitaries_match(self.unitary(val, pos), (cols, exact), up_to_phase=True):
            raise SurdError("S-E010", "internal: the synthesized circuit failed its exact proof", pos)
        return val

    # ═════════ ability: realms — agreements and collisions across data, in two incompatible bases ═════════
    # ═════════ general-purpose programming: functions, closures, loops, lists, maps, tests ═════════
    def st_fndef(self, st, env, sink):
        name = st["name"]
        if name in PRIM or name in ALIAS or name in self.gates or name in CHANNELS or name in FUNCS or \
                name in CORE_FUNCS or name in SEM_FUNCS or name in MONEY_FUNCS or name in SEARCH_FUNCS or name in INTEGRATION_FUNCS or name in BANK_FUNCS or name in surd_ext.FUNCS or name in self.qregs or name in self.cregs:
            raise SurdError("S-E011", f"'{name}' is already a name", st["pos"], "pick another function name")
        env[name] = FuncVal(name, st["params"], st["body"], None, env)

    def st_return(self, st, env, sink):
        raise ReturnSignal(NONE if st["e"] is None else self.ev(st["e"], env))

    def st_break(self, st, env, sink): raise BreakSignal()
    def st_continue(self, st, env, sink): raise ContinueSignal()

    def st_exprstmt(self, st, env, sink):
        self.ev(st["e"], env)

    def _loop_body(self, body, env, sink):
        """run a loop body; True means break"""
        try:
            self.exec_block(body, env, sink)
        except ContinueSignal:
            return False
        except BreakSignal:
            return True
        return False

    def st_while(self, st, env, sink):
        n = 0
        while self.truthy(self.ev(st["cond"], env), st["pos"]):
            n += 1
            if n > 10_000_000: raise SurdError("S-E014", "a while loop ran 10,000,000 times", st["pos"])
            if self._loop_body(st["body"], env, sink): break

    def st_foreach(self, st, env, sink):
        it = self.ev(st["iter"], env)
        if isinstance(it, Realm): seq = list(it.recs)
        elif isinstance(it, dict): seq = list(it.keys())
        elif isinstance(it, str): seq = list(it)
        elif isinstance(it, list): seq = list(it)
        else:
            raise SurdError("S-E009", f"cannot loop over {type_name(it)}", st["pos"],
                            "loop over a list, a map, a string, a realm, or a range lo..hi")
        for x in seq:
            self.budget.checkpoint(st["pos"])
            env[st["var"]] = x
            if self._loop_body(st["body"], env, sink): break

    def st_test(self, st, env, sink):
        before = len(self.expects)
        err = None
        try:
            self.exec_block(st["body"], env, None)
        except SurdError as e:
            if e.code == "S-E017": raise
            err = e
        mine = self.expects[before:]
        ok = err is None and all(e["ok"] for e in mine)
        self.tests.append({"name": st["name"], "ok": ok, "line": st["pos"][0], "checks": len(mine),
                           "error": err.as_dict() if err else None})
        self.emit(f"test {st['name']!r}: {'ok' if ok else 'FAILED'}  ({len(mine)} check(s))"
                  + (f" — {err.render()}" if err else ""))

    def call_value(self, fv, vals, pos, dependent=()):
        """call a function value with already-evaluated arguments"""
        if not isinstance(fv, FuncVal):
            raise SurdError("S-E009", f"{type_name(fv)} is not a function", pos)
        if len(vals) != len(fv.params):
            raise SurdError("S-E004", f"{fv.name} takes {len(fv.params)} argument(s), got {len(vals)}", pos)
        self._calls = getattr(self, "_calls", 0) + 1
        try:
            if self._calls > 400:
                raise SurdError("S-E014", f"{fv.name} recursed more than 400 levels", pos,
                                "check the base case of the recursion")
            local = dict(zip(fv.params, vals))
            local["__measurement_names__"] = {p for p, tainted in zip(fv.params, dependent) if tainted}
            env2 = ChainMap(local, fv.closure)
            try:
                if fv.expr is not None:
                    return self.ev(fv.expr, env2)
                self.exec_block(fv.body, env2, self.cur_sink)
            except ReturnSignal as r:
                return r.value
            except RecursionError:
                raise SurdError("S-E014", f"{fv.name} recursed too deeply", pos, "check the base case of the recursion")
            except (BreakSignal, ContinueSignal):
                raise SurdError("S-E009", "break/continue outside a loop", pos)
            return NONE
        finally:
            self._calls -= 1

    def arg_value(self, node, env):
        """a call argument: wires pass as wires (so functions can build circuits); anything else by value"""
        if node["k"] == "ref":
            nm = node["name"]
            if isinstance(env.get(nm), (QB, QR)) or (nm in self.qregs and not isinstance(env.get(nm), FuncVal)
                                                      and nm not in env):
                idx = self.qref(node, env)
                return QB(idx[0]) if len(idx) == 1 and (node["lo"] is not None and node["hi"] is None
                                                        or isinstance(env.get(nm), QB)) else QR(idx)
        return self.ev(node, env)

    def core_fn(self, name, v, pos):
        """the general-purpose library: exact where the values are exact"""
        def need(n, what):
            if len(v) not in (n if isinstance(n, tuple) else (n,)):
                raise SurdError("S-E004", f"{name}{what}", pos)
        if name == "exactness":
            need(1, "(value)")
            return language_metadata(value_exactness(v[0]))
        def num(x):
            if isinstance(x, R2) and x.is_rational() and x.a.denominator == 1: return int(x.a)
            raise SurdError("S-E009", f"{name} needs whole numbers", pos)
        def seq(x):
            if isinstance(x, list): return x
            if isinstance(x, (str, dict)): return list(x)
            if isinstance(x, Realm): return list(x.recs)
            raise SurdError("S-E009", f"{name} needs a list, got {type_name(x)}", pos)
        def less(a, b):
            if isinstance(a, str) and isinstance(b, str): return a < b
            return compare("<", a, b, pos)
        if name in surd_collections.FUNCTIONS:
            return self.collection_fn(name, v, pos)
        if name == "range":
            need((1, 2, 3), "(n) or range(lo, hi [, step])")
            lo, hi, st_ = (0, num(v[0]), 1) if len(v) == 1 else (num(v[0]), num(v[1]), num(v[2]) if len(v) == 3 else 1)
            if st_ == 0: raise SurdError("S-E009", "range step cannot be 0", pos)
            if abs(hi - lo) // abs(st_) > 1_000_000: raise SurdError("S-E010", "range longer than 1,000,000", pos)
            return [R2(i) for i in range(lo, hi, st_)]
        if name == "push":
            need(2, "(list, value)"); seq(v[0]).append(v[1]); return v[0]
        if name == "pop":
            need(1, "(list)"); xs = seq(v[0])
            if not xs: raise SurdError("S-E005", "pop from an empty list", pos)
            return xs.pop()
        if name == "set":
            need(3, "(list_or_map, key, value)")
            if isinstance(v[0], dict): v[0][map_key(v[1])] = v[2]; return v[0]
            xs, i = seq(v[0]), num(v[1])
            if not -len(xs) <= i < len(xs): raise SurdError("S-E005", f"index {i} is outside the list", pos)
            xs[i] = v[2]; return v[0]
        if name in ("str", "repr"):
            need(1, "(value)"); return v[0] if (name == "str" and isinstance(v[0], str)) else fmt_short(v[0])
        if name == "join":
            need((1, 2), "(list [, separator])")
            sep = v[1] if len(v) == 2 else ""
            return sep.join(x if isinstance(x, str) else fmt_short(x) for x in seq(v[0]))
        if name == "split":
            need((1, 2), "(text [, separator])")
            if not isinstance(v[0], str): raise SurdError("S-E009", "split needs text", pos)
            return v[0].split(v[1]) if len(v) == 2 else v[0].split()
        if name == "replace":
            need(3, "(text, old, new)")
            if not all(isinstance(x, str) for x in v): raise SurdError("S-E009", "replace works on text", pos)
            return v[0].replace(v[1], v[2])
        if name in ("startswith", "endswith"):
            need(2, "(text, part)")
            if not all(isinstance(x, str) for x in v): raise SurdError("S-E009", f"{name} works on text", pos)
            return v[0].startswith(v[1]) if name == "startswith" else v[0].endswith(v[1])
        if name in ("upper", "lower"):
            need(1, "(text)")
            if not isinstance(v[0], str): raise SurdError("S-E009", f"{name} needs text", pos)
            return v[0].upper() if name == "upper" else v[0].lower()
        if name == "get":                                                     # get(map, key, default) (1.7)
            need(3, "(map, key, default)")
            if not isinstance(v[0], dict): raise SurdError("S-E009", "get reads a map", pos, "get(m, key, default)")
            return v[0].get(map_key(v[1]), v[2])
        if name in ("keys", "values"):
            need(1, "(map)")
            if not isinstance(v[0], dict): raise SurdError("S-E009", f"{name} needs a map", pos)
            return [(R2(k) if isinstance(k, int) else k) for k in v[0]] if name == "keys" else list(v[0].values())
        if name in ("map", "filter"):
            need(2, "(function, list)")
            if not isinstance(v[0], FuncVal) and isinstance(v[1], FuncVal): v = [v[1], v[0]]   # (list, fn) reads the same (1.7)
            out = []
            for x in seq(v[1]):
                r = self.call_value(v[0], [x], pos)
                if name == "map": out.append(r)
                elif self.truthy(r, pos): out.append(x)
            return out
        if name == "reduce":
            need(3, "(function, list, start)")
            acc = v[2]
            for x in seq(v[1]): acc = self.call_value(v[0], [acc, x], pos)
            return acc
        if name == "sum":
            need(1, "(list)")
            tot = None
            for x in seq(v[0]): tot = x if tot is None else arith("+", tot, x, pos)   # a sum of money is money (1.7)
            return R2() if tot is None else tot
        if name in ("min", "max"):
            xs = seq(v[0]) if len(v) == 1 else list(v)
            if not xs: raise SurdError("S-E005", f"{name} of an empty list", pos)
            best = xs[0]
            for x in xs[1:]:
                if (less(x, best) if name == "min" else less(best, x)): best = x
            return best
        if name == "sorted":
            need((1, 2), "(list [, key function])")
            import functools
            key = v[1] if len(v) == 2 else None
            xs = [(self.call_value(key, [x], pos) if key else x, x) for x in seq(v[0])]
            xs.sort(key=functools.cmp_to_key(lambda a, b: -1 if less(a[0], b[0]) else (1 if less(b[0], a[0]) else 0)))
            return [x for _, x in xs]
        if name == "reverse":
            need(1, "(list)"); return list(reversed(seq(v[0])))
        if name == "contains":
            need(2, "(collection, value)")
            if isinstance(v[0], str) and isinstance(v[1], str): return v[1] in v[0]
            if isinstance(v[0], dict): return map_key(v[1]) in v[0]
            return any(values_equal(x, v[1]) for x in seq(v[0]))
        if name == "count":
            need(2, "(list, value)"); return R2(sum(1 for x in seq(v[0]) if values_equal(x, v[1])))
        if name in ("any", "all"):
            need(1, "(list)")
            vals = [self.truthy(x, pos) for x in seq(v[0])]
            return any(vals) if name == "any" else all(vals)
        if name == "zip":
            need(2, "(list, list)"); return [[a, b] for a, b in zip(seq(v[0]), seq(v[1]))]
        if name == "enumerate":
            need(1, "(list)"); return [[R2(i), x] for i, x in enumerate(seq(v[0]))]
        if name == "type":
            need(1, "(value)"); return type_name(v[0])
        if name in ("int", "floor"):
            need(1, "(number)")
            x = v[0]
            if isinstance(x, str):
                try: return R2(int(x.strip()))
                except ValueError: raise SurdError("S-E009", f"{x!r} is not a whole number", pos)
            if isinstance(x, (R2, float, PiVal)):
                f_ = F(x.a) if isinstance(x, R2) and x.is_rational() else F(float(x))
                return R2(math.floor(f_) if name == "floor" else int(f_))
            raise SurdError("S-E009", f"{name} needs a number", pos)
        raise SurdError("S-E002", f"unknown function '{name}'", pos)

    def collection_fn(self, name, vals, pos):
        """Adapt exact language values, callbacks and budgets to collection kernels."""
        callback = name in {"group_by", "unique_by", "partition", "scan", "flat_map"}
        arity = 3 if name == "scan" else 2 if callback or name == "chunk" else 1
        if len(vals) != arity:
            raise SurdError("S-E004", f"{name} takes {arity} argument(s)", pos)
        function = vals[0] if callback else None
        values = vals[1] if callback else vals[0]
        if callback and not isinstance(function, FuncVal) and isinstance(values, FuncVal) and name != "scan":
            function, values = values, function
        if callback:
            if not isinstance(function, FuncVal):
                raise SurdError("S-E009", f"{name} needs a function", pos)
            parameters = 2 if name == "scan" else 1
            if len(function.params) != parameters:
                raise SurdError("S-E004", f"{name} callback takes {parameters} argument(s)", pos)
        if isinstance(values, Realm): values = values.recs
        if not isinstance(values, list):
            raise SurdError("S-E009", f"{name} needs a list or realm", pos)

        def scalar(value):
            if value is NONE: return ("none",)
            if type(value) is bool: return ("bool", value)
            if type(value) is str: return ("text", value)
            if isinstance(value, R2): return ("exact_number", value.q.n, value.q.d)
            if isinstance(value, Money): return ("money", value.d)
            if isinstance(value, Date): return ("date", value.n)
            if isinstance(value, PiVal): return ("angle", value.k)
            if type(value) is int: return ("map_integer_key", value)
            if type(value) is float and math.isfinite(value): return ("float", value)
            raise surd_collections.CollectionError(f"unsupported grouping key: {type_name(value)}")

        size = None
        if name == "chunk":
            value = vals[1]
            if not isinstance(value, R2) or not value.is_rational() or value.a.denominator != 1 or value.a <= 0:
                raise SurdError("S-E009", "chunk size must be a positive whole number", pos)
            size = int(value.a)
        try:
            return surd_collections.apply(name, values,
                invoke=(lambda *items: self.call_value(function, list(items), pos)) if callback else None,
                scalar=scalar, truth=lambda x: self.truthy(x, pos),
                checkpoint=lambda: self.budget.checkpoint(pos),
                start=vals[2] if name == "scan" else None, size=size)
        except surd_collections.CollectionError as error:
            raise SurdError("S-E010" if error.limit else "S-E009", str(error), pos,
                            "see COLLECTIONS.md for collection contracts and limits") from error

    def ev_index(self, base, node, env):
        lo = self.ev(node["lo"], env)
        if isinstance(base, Record):                                         # record["column"] == field(record, "column") (1.7)
            if not isinstance(lo, str) or node["hi"] is not None:
                raise SurdError("S-E009", "a record is indexed by a column name", node["pos"], 'r["amount"] or field(r, "amount")')
            return record_value(base, lo)
        if isinstance(base, dict):
            if node["hi"] is not None: raise SurdError("S-E009", "a map has no slices", node["pos"])
            key = map_key(lo)
            if key not in base:
                near = difflib.get_close_matches(str(key), [str(k) for k in base], 2)
                raise SurdError("S-E005", f"no key {fmt_value(lo)} in the map", node["pos"],
                                f"did you mean {', '.join(near)}?" if near else "keys(m) lists the keys")
            return base[key]
        if isinstance(base, Realm): base = base.recs
        if not isinstance(base, (list, str)):
            raise SurdError("S-E009", f"{type_name(base)} cannot be indexed", node["pos"])
        n = len(base)
        def as_int(v):
            if isinstance(v, R2) and v.is_rational() and v.a.denominator == 1: return int(v.a)
            raise SurdError("S-E009", "an index must be a whole number", node["pos"])
        i = as_int(lo)
        if node["hi"] is None:
            if i < 0: i += n
            if not 0 <= i < n:
                raise SurdError("S-E005", f"index {i} is outside 0..{n - 1}", node["pos"])
            return base[i]
        j = n if node["hi"]["k"] == "end" else as_int(self.ev(node["hi"], env))
        return base[max(0, i):max(0, j)]


    # ═════════ ability: accounting — tracing, books, relationships, exact sums, money on qubits ═════════
    def money_fn(self, name, args, env, pos):
        v = [self.ev(a, env) for a in args]
        def need(n, sig):
            if len(v) not in (n if isinstance(n, tuple) else (n,)): raise SurdError("S-E004", f"{name}{sig}", pos)
        def evlist(x):
            if isinstance(x, Realm): x = list(x.recs)
            if not isinstance(x, list): raise SurdError("S-E009", f"{name} needs a list of events or a realm", pos)
            return norm_events(x, pos)
        def classes(x):
            if isinstance(x, list): return {str(c) for c in x}
            if isinstance(x, str): return {x}
            raise SurdError("S-E009", "a class is a name (or a list of names)", pos)
        def order_of(x):
            o = x if isinstance(x, str) else "deposits_first"
            if o not in ("deposits_first", "withdrawals_first"):
                raise SurdError("S-E009", "order is \"deposits_first\" or \"withdrawals_first\"", pos)
            return o
        M = Money
        if name == "bound":
            need(2, "(low, high)")
            if isinstance(v[0], Money) or isinstance(v[1], Money): return Bound(money_of(v[0], pos), money_of(v[1], pos))
            return Bound(v[0].a, v[1].a, money=False)
        if name in ("lo", "hi", "width"):
            need(1, "(bound)")
            b = v[0]
            if not isinstance(b, Bound): raise SurdError("S-E009", f"{name} needs a bound (floor .. ceiling)", pos)
            return b.wrap(b.lo if name == "lo" else (b.hi if name == "hi" else b.hi - b.lo))
        if name == "within":
            need(2, "(value, bound)")
            b = v[1]
            if not isinstance(b, Bound): raise SurdError("S-E009", "within(value, bound)", pos)
            x = money_of(v[0], pos) if b.money else v[0].a
            return b.lo <= x <= b.hi
        if name == "money":
            need(1, "(value)"); return M(money_of(v[0], pos))
        if name == "date":
            need(1, "(\"YYYY-MM-DD\")"); return Date(date_of(v[0], pos))
        if name == "events":
            need((1, 2), "(realm [, \"class column\"])")
            col = v[1] if len(v) == 2 else None
            out = []
            for e in evlist(v[0]):
                rec = v[0].recs[e[0]] if isinstance(v[0], Realm) else None
                cls = (rec.row.get(col) if (rec is not None and col) else e[3]) or ""
                out.append({"date": Date(e[1]), "amount": M(e[2]), "cls": str(cls), "page": e[4]})
            return out
        if name == "libr":
            need((2, 3), "(events, class [, order])")
            evs, cs = evlist(v[0]), classes(v[1])
            _, _, traj = two_pool(evs, order_of(v[2] if len(v) == 3 else None), cs, "O")
            rows = []
            for day, C, O in traj:
                row = {"date": Date(day), "class_left": M(C), "balance": M(C + O)}
                if rows and rows[-1]["date"].n == day: rows[-1] = row
                else: rows.append(row)
            return rows
        if name == "share_bounds":
            need(4, "(events, date, outflow, class)")
            evs = evlist(v[0])
            amt = money_of(v[2], pos)
            if amt >= 0: raise SurdError("S-E009", "the outflow is negative money, e.g. -$12.34", pos)
            b = share_bounds(evs, date_of(v[1], pos), amt, classes(v[3]))
            if b is None: raise SurdError("S-E005", f"no outflow of {M(amt)} on {Date(date_of(v[1], pos))}", pos)
            return {"floor": M(b[0]), "ceiling": M(b[1]), "range": Bound(b[0], b[1])}
        if name == "trace":
            need((2, 3), "(events, \"fifo\" | \"lifo\" | \"prorata\" [, order])")
            doc = v[1]
            if doc not in ("fifo", "lifo", "prorata"):
                raise SurdError("S-E009", "trace doctrines: fifo, lifo, prorata (libr and share_bounds bound a class)", pos)
            evs = evlist(v[0])
            res = tranche_trace(evs, order_of(v[2] if len(v) == 3 else None), doc)
            return [{"date": Date(day), "amount": M(-sum(comp.values(), F0)), "from": {k: M(x) for k, x in comp.items()}}
                    for i, day, amt, comp in res]
        if name == "spectrum":
            need(4, "(events, date, outflow, class)")
            evs = evlist(v[0])
            day, amt, cs = date_of(v[1], pos), money_of(v[2], pos), classes(v[3])
            idx = [e[0] for e in evs if e[1] == day and e[2] == amt]
            if not idx: raise SurdError("S-E005", f"no outflow of {M(amt)} on {Date(day)}", pos)
            out = {}
            for order in ("deposits_first", "withdrawals_first"):
                for doc in ("fifo", "lifo", "prorata"):
                    comp = next(c for i, d_, a_, c in tranche_trace(evs, order, doc) if i == idx[0])
                    out[f"{doc}/{order.split('_')[0]}"] = M(sum((x for k, x in comp.items() if k in cs), F0))
            b = share_bounds(evs, day, amt, cs)
            out["floor"], out["ceiling"] = M(b[0]), M(b[1])
            return out
        if name == "post":
            need((5, 6), "(book, date, debit_account, credit_account, amount [, memo])")
            book = v[0]
            if not isinstance(book, list): raise SurdError("S-E009", "a book is a list (let book = [])", pos)
            amt = money_of(v[4], pos)
            if amt <= 0: raise SurdError("S-E009", "post a positive amount; swap the accounts to reverse", pos)
            book.append({"date": Date(date_of(v[1], pos)), "debit": str(v[2]), "credit": str(v[3]), "amount": M(amt),
                         "memo": str(v[5]) if len(v) == 6 else ""})
            return book
        def postings(x):
            if not isinstance(x, list): raise SurdError("S-E009", f"{name} needs a book (a list of postings)", pos)
            for p_ in x:
                if not (isinstance(p_, dict) and {"debit", "credit", "amount"} <= set(p_)):
                    raise SurdError("S-E009", "a posting is {date, debit, credit, amount}", pos)
            return x
        if name == "balances":
            need((1, 2), "(book [, as_of date])")
            cut = date_of(v[1], pos) if len(v) == 2 else None
            bal = {}
            for p_ in postings(v[0]):
                if cut is not None and isinstance(p_.get("date"), Date) and p_["date"].n > cut: continue
                a = money_of(p_["amount"], pos)
                bal[p_["debit"]] = bal.get(p_["debit"], F0) + a
                bal[p_["credit"]] = bal.get(p_["credit"], F0) - a
            return {k: M(x) for k, x in sorted(bal.items())}
        if name == "trial":
            need(1, "(book)")
            dr = sum((money_of(p_["amount"], pos) for p_ in postings(v[0])), F0)
            return {"debits": M(dr), "credits": M(dr), "balanced": True, "postings": R2(len(v[0]))}
        if name == "statement":
            need(2, "(book, account)")
            run, rows = F0, []
            for p_ in sorted(postings(v[0]), key=lambda p_: p_["date"].n if isinstance(p_.get("date"), Date) else 0):
                a = money_of(p_["amount"], pos)
                if p_["debit"] == v[1]: run += a; rows.append({"date": p_["date"], "in": M(a), "running": M(run), "memo": p_["memo"]})
                elif p_["credit"] == v[1]: run -= a; rows.append({"date": p_["date"], "out": M(a), "running": M(run), "memo": p_["memo"]})
            return rows
        if name == "flows":
            need(1, "(book)")
            agg = {}
            for p_ in postings(v[0]):
                key = (p_["credit"], p_["debit"])          # money moves from the credited account to the debited one
                tot, n = agg.get(key, (F0, 0))
                agg[key] = (tot + money_of(p_["amount"], pos), n + 1)
            return [{"from": a, "to": b, "total": M(t), "count": R2(n)} for (a, b), (t, n) in sorted(agg.items())]
        if name == "net":
            need(3, "(book, a, b)")
            t = F0
            for p_ in postings(v[0]):
                a = money_of(p_["amount"], pos)
                if p_["credit"] == v[1] and p_["debit"] == v[2]: t += a
                elif p_["credit"] == v[2] and p_["debit"] == v[1]: t -= a
            return M(t)
        if name == "paths":
            need((3, 4), "(book, from, to [, max hops])")
            hops = int(v[3].a) if len(v) == 4 else 6
            edges = {}
            for p_ in postings(v[0]):
                edges.setdefault(p_["credit"], {}).setdefault(p_["debit"], F0)
                edges[p_["credit"]][p_["debit"]] += money_of(p_["amount"], pos)
            found = []
            def walk(node, path, cap):
                if len(found) > 200 or len(path) > hops + 1: return
                if node == v[2] and len(path) > 1:
                    found.append({"path": list(path), "most": M(cap)}); return
                for nxt, amt in edges.get(node, {}).items():
                    if nxt in path: continue
                    walk(nxt, path + [nxt], min(cap, amt))
            walk(v[1], [v[1]], F(10) ** 18)
            return sorted(found, key=lambda r: (-r["most"].d, len(r["path"])))
        if name == "roundtrips":
            need((1, 2, 3), "(events [, minimum, days])")
            lo = money_of(v[1], pos) if len(v) >= 2 else F(1000)
            win = int(v[2].a) if len(v) == 3 else 14
            evs = sorted(evlist(v[0]), key=lambda e: (e[1], e[0]))
            ins = [e for e in evs if e[2] >= lo]
            outs = [e for e in evs if -e[2] >= lo]
            out = []
            for a in ins:
                for b in outs:
                    if b[2] == -a[2] and 0 <= b[1] - a[1] <= win:
                        out.append({"in": Date(a[1]), "out": Date(b[1]), "amount": M(a[2]), "days": R2(b[1] - a[1]),
                                    "pages": f"{a[4]} → {b[4]}"})
                    elif a[2] == -b[2] and 0 <= a[1] - b[1] <= win:
                        out.append({"out": Date(b[1]), "in": Date(a[1]), "amount": M(a[2]), "days": R2(a[1] - b[1]),
                                    "pages": f"{b[4]} → {a[4]}"})
            return out
        if name == "subset_sum":
            need((2, 3), "(amounts, target [, max items])")
            items = [money_of(x, pos) for x in (v[0] if isinstance(v[0], list) else [])]
            if len(items) > 400: raise SurdError("S-E010", "subset_sum takes at most 400 amounts", pos)
            k = int(v[2].a) if len(v) == 3 else 4
            return [[R2(i) for i in s] for s in subset_sums(items, money_of(v[1], pos), k)]
        if name == "window_sums":
            need(2, "(amounts, target)")
            items = [money_of(x, pos) for x in v[0]]
            t = money_of(v[1], pos)
            out = []
            for i in range(len(items)):
                acc = F0
                for j in range(i, len(items)):
                    acc += items[j]
                    if acc == t: out.append({"start": R2(i), "end": R2(j), "count": R2(j - i + 1)})
            return out
        if name == "grover_sum":
            need(2, "(amounts, target)")
            items = [money_of(x, pos) for x in v[0]]
            if not 1 <= len(items) <= 20: raise SurdError("S-E010", "grover_sum searches 1 to 20 amounts (2^20 subsets)", pos)
            t = money_of(v[1], pos)
            marked = [m for m in range(1 << len(items)) if sum((items[i] for i in range(len(items)) if m >> i & 1), F0) == t]
            n, k, p, hist = grover_realm(1 << len(items), marked)
            sols = [[R2(i) for i in range(len(items)) if m >> i & 1] for m in marked]
            return {"qubits": R2(n), "subsets": R2(1 << len(items)), "solutions": sols, "oracle_calls": R2(k),
                    "p_success": p, "classical_expected": R2(F((1 << len(items)) + 1, len(marked) + 1)) if marked else NONE}
        if name == "provenance":
            need((2, 3), "(events, date [, order])")
            evs, day = evlist(v[0]), date_of(v[1], pos)
            pool, deficit = {}, F0
            for e in by_day_order(evs, order_of(v[2] if len(v) == 3 else None)):
                if e[1] > day: break
                amt = e[2]
                if amt > 0:
                    cover = min(deficit, amt); deficit -= cover; amt -= cover
                    if amt: pool[e[3] or "(unclassed)"] = pool.get(e[3] or "(unclassed)", F0) + amt
                else:
                    need_, tot = -amt, sum(pool.values(), F0)
                    take = min(tot, need_)
                    if tot:
                        for k_ in pool: pool[k_] -= pool[k_] * take / tot
                    deficit += need_ - take
            pool = {k_: x for k_, x in pool.items() if x > 0}
            if not pool: return NONE
            sp = Space("PROVENANCE", sorted(pool))
            return self._weights_meaning((sp,), pool, pos)
        if name in ("set_bounds", "butfor", "whatif", "verdict"):
            return self.money_leap(name, args, v, env, pos)
        raise SurdError("S-E002", f"unknown function '{name}'", pos)

    def money_leap(self, name, args, v, env, pos):
        M = Money
        def evs_of(x):
            if isinstance(x, Realm): x = list(x.recs)
            if not isinstance(x, list): raise SurdError("S-E009", f"{name} needs events (a list) or a realm", pos)
            return x, norm_events(x, pos)
        def cls_of(x):
            if isinstance(x, list): return {str(c) for c in x}
            if isinstance(x, str): return {x}
            raise SurdError("S-E009", "a class is a name or a list of names", pos)
        def pick(fn_, raw, evs, want_out):
            flags = {}
            for (i, d, a, c, pg) in evs:
                if (a < 0) != want_out: continue
                r = self.call_value(fn_, [raw[i]], pos) if isinstance(fn_, FuncVal) else None
                if r is None: raise SurdError("S-E009", f"{name} picks with a function, e.g. fn(e) => e[\"amount\"] < $0", pos)
                if self.truthy(r, pos): flags[i] = True
            return flags
        if name == "set_bounds":
            if len(v) != 3: raise SurdError("S-E004", "set_bounds(events, fn(e) => picks outflows, class)", pos)
            raw, evs = evs_of(v[0])
            flags = pick(v[1], raw, evs, True)
            cs = cls_of(v[2])
            lo = hi = None
            total = F0
            for order in ("deposits_first", "withdrawals_first"):
                k_lo, total = set_replay(evs, order, cs, flags, True)
                k_hi, _ = set_replay(evs, order, cs, flags, False)
                lo = k_lo if lo is None else min(lo, k_lo)
                hi = k_hi if hi is None else max(hi, k_hi)
            return {"outflows": R2(len(flags)), "total": M(total), "floor": M(lo), "ceiling": M(hi), "range": Bound(lo, hi)}
        if name in ("butfor", "whatif"):
            if len(v) not in (2, 3): raise SurdError("S-E004", f"{name}(events, {'class' if name == 'butfor' else 'fn(e) => picks deposits'} [, order])", pos)
            raw, evs = evs_of(v[0])
            order = v[2] if len(v) == 3 else "deposits_first"
            if name == "butfor":
                cs = cls_of(v[1])
                removed = {i for (i, d, a, c, pg) in evs if a > 0 and c in cs}
            else:
                removed = set(pick(v[1], raw, evs, False))
            pairs, _ = surd_bank.pair_returns(evs, {i: _raw_field(raw[i], "memo") for (i, *_r) in evs},
                                              {i: _raw_field(raw[i], "type") for (i, *_r) in evs})
            returned_with = [r for d_, r in pairs if d_ in removed]
            removed |= set(returned_with)
            actual, _ = butfor_replay(evs, order, set())
            low_actual = butfor_replay.low
            without, final = butfor_replay(evs, order, removed)
            low_without = butfor_replay.low
            act = {r[0]: r[3] for r in actual}
            rows = []
            for i, day, amt, unc, bal, pg in without:
                extra = unc - act.get(i, F0)
                if extra > 0:
                    rows.append({"date": Date(day), "amount": M(amt), "uncovered": M(extra), "balance_without": M(bal),
                                 "page": pg})
            removed_total = sum((a for (i, d, a, c, pg) in evs if i in removed), F0)
            return {"removed_deposits": R2(len(removed) - len(returned_with)), "removed_total": M(removed_total),
                    "returns_removed_with_them": R2(len(returned_with)),
                    "deepest_shortfall": M(-low_without[0]), "deepest_on": Date(low_without[1]) if low_without[1] else NONE,
                    "actual_lowest": M(low_actual[0]),
                    "outflows_not_covered": R2(len(rows)), "first": rows[0]["date"] if rows else NONE,
                    "paid_on_missing_money": M(sum((r["uncovered"].d for r in rows), F0)),
                    "outflows": rows, "final_balance_without": M(final)}
        if name == "verdict":
            if len(v) != 6: raise SurdError("S-E004", 'verdict(events, date, outflow, class, ">=" | "<=" | "==", amount)', pos)
            raw, evs = evs_of(v[0])
            day, amt, cs, op, x = date_of(v[1], pos), money_of(v[2], pos), cls_of(v[3]), v[4], money_of(v[5], pos)
            if op not in (">=", "<=", "=="): raise SurdError("S-E009", 'verdict compares with ">=", "<=" or "=="', pos)
            b = share_bounds(evs, day, amt, cs)
            if b is None: raise SurdError("S-E005", f"no outflow of {M(amt)} on {Date(day)}", pos)
            lo, hi = b
            idx = next(e[0] for e in evs if e[1] == day and e[2] == amt)
            holds, fails = [], []
            for order in ("deposits_first", "withdrawals_first"):
                for doc in ("fifo", "lifo", "prorata"):
                    comp = next(c for i, d_, a_, c in tranche_trace(evs, order, doc) if i == idx)
                    share = sum((y for k, y in comp.items() if k in cs), F0)
                    ok = share >= x if op == ">=" else (share <= x if op == "<=" else share == x)
                    (holds if ok else fails).append(f"{doc}/{order.split('_')[0]}")
            if op == ">=": val = "T" if lo >= x else ("F" if hi < x else "B")
            elif op == "<=": val = "T" if hi <= x else ("F" if lo > x else "B")
            else: val = "T" if lo == hi == x else ("F" if not lo <= x <= hi else "B")
            words = {"T": "holds under every tracing convention", "F": "fails under every tracing convention",
                     "B": "depends on the tracing convention"}[val]
            return {"verdict": val, "means": words, "range": Bound(lo, hi), "holds_under": holds, "fails_under": fails}
        raise SurdError("S-E002", f"unknown function '{name}'", pos)

    # ═════════ ability: semantics — meanings as qubit states ═════════
    def st_space(self, st, env, sink):
        name = st["name"]
        if name in PRIM or name in self.gates or name in CHANNELS or self._taken(name) or name in self.globals:
            raise SurdError("S-E011", f"'{name}' is already a name", st["pos"], "pick another space name")
        if len(st["labels"]) < 2 or len(set(st["labels"])) != len(st["labels"]):
            raise SurdError("S-E009", "a space needs two or more different senses", st["pos"])
        if len(st["labels"]) > 64: raise SurdError("S-E010", "a space holds up to 64 senses", st["pos"])
        self.globals[name] = Space(name, st["labels"])

    def _space(self, node, env):
        v = self.ev(node, env)
        if not isinstance(v, Space):
            raise SurdError("S-E009", f"expected a space, got {type_name(v)}", node.get("pos"),
                            "declare one: space PURPOSE { construction, furniture }")
        return v

    def _weights_meaning(self, spaces, weights, pos):
        """α_k = √(w_k / Σw): exact when the root lies in the field"""
        tot = sum((w for w in weights.values()), F0)
        if tot <= 0: raise SurdError("S-E009", "weights must be positive with a positive total", pos)
        amps = {}
        for k, w in weights.items():
            if w < 0: raise SurdError("S-E009", "a weight cannot be negative", pos)
            if w == 0: continue
            r = fn1("sqrt", R2(w / tot), pos)
            amps[k] = r.q if isinstance(r, R2) else complex(math.sqrt(float(w / tot)))
        return Meaning(spaces, amps)

    # ═════════ ability: native statements — load, foot, returned items, reconcile, calibrate ═════════
    def bank_fn(self, name, args, env, pos):
        v = [self.ev(a, env) for a in args]
        M = Money
        def need(n, sig):
            if len(v) not in (n if isinstance(n, tuple) else (n,)): raise SurdError("S-E004", f"{name}{sig}", pos)
        def rows_of(x):
            if isinstance(x, Realm): x = list(x.recs)
            if not isinstance(x, list): raise SurdError("S-E009", f"{name} needs events (a list) or a realm", pos)
            out = []
            for e in x:
                if isinstance(e, Record):
                    row = e.row
                    if e.day is None or e.cents is None: continue
                    out.append({"day": e.day, "amount": F(e.cents), "memo": str(row.get("memo") or row.get("description") or ""),
                                "type": str(row.get("type") or ""), "month": str(row.get("statement_month") or row.get("month") or ""),
                                "page": e.loc, "begin": None, "end": None})
                elif isinstance(e, dict):
                    def opt(k):
                        return money_of(e[k], pos) if k in e and e[k] is not NONE else None
                    out.append({"day": date_of(e.get("date"), pos), "amount": money_of(e.get("amount"), pos),
                                "memo": str(e.get("memo") or ""), "type": str(e.get("type") or ""), "month": str(e.get("month") or ""),
                                "page": str(e.get("page") or ""), "begin": opt("begin"), "end": opt("end")})
                else:
                    raise SurdError("S-E009", f"an event is a map {{date, amount, …}}, got {type_name(e)}", pos)
            return out
        def dd(n): return Date(n) if n is not None else NONE
        try:
            if name == "bank":
                need(1, "(\"statement.csv\" | [paths])")
                paths = v[0] if isinstance(v[0], list) else [v[0]]
                out = []
                for pth in paths:
                    for r in surd_bank.load_statement(str(pth)):
                        out.append({"date": Date(r["day"]), "amount": M(r["amount"]), "cls": "", "memo": r["memo"],
                                    "check": r["check"], "type": r["type"], "month": r["month"], "page": r["page"],
                                    "begin": M(r["begin"]) if r["begin"] is not None else NONE,
                                    "end": M(r["end"]) if r["end"] is not None else NONE})
                return out
            if name == "foot":
                need(1, "(events with month, begin, end)")
                return [{"month": m["month"], "begin": M(m["begin"]), "credits": M(m["credits"]), "debits": M(m["debits"]),
                         "computed_end": M(m["computed_end"]), "statement_end": M(m["statement_end"]), "gap": M(m["gap"]),
                         "ties": m["ties"], "rows": R2(m["rows"]), "pages": m["pages"]} for m in surd_bank.foot(rows_of(v[0]))]
            if name == "returns":
                need(1, "(events)")
                rows = rows_of(v[0])
                evs = [(i, r["day"], r["amount"], "", r["page"]) for i, r in enumerate(rows)]
                pairs, orphans = surd_bank.pair_returns(evs, {i: r["memo"] for i, r in enumerate(rows)},
                                                        {i: r["type"] for i, r in enumerate(rows)})
                out = []
                for dep, ret in pairs:
                    d0, d1 = rows[dep]["day"], rows[ret]["day"]
                    marked = [(i, dy, a, "__provisional__" if i == dep else "", pg) for (i, dy, a, c, pg) in evs]
                    flags = {i: True for (i, dy, a, c, pg) in evs if a < 0 and d0 <= dy < d1
                             and not surd_bank.is_return(rows[i]["memo"], rows[i]["type"])}
                    lo = hi = None
                    for order in ("deposits_first", "withdrawals_first"):
                        k_lo, tot = set_replay(marked, order, {"__provisional__"}, flags, True)
                        k_hi, _ = set_replay(marked, order, {"__provisional__"}, flags, False)
                        lo = k_lo if lo is None else min(lo, k_lo); hi = k_hi if hi is None else max(hi, k_hi)
                    out.append({"deposit_date": Date(d0), "return_date": Date(d1), "amount": M(rows[dep]["amount"]),
                                "days_provisional": R2(d1 - d0), "deposit_page": rows[dep]["page"], "return_page": rows[ret]["page"],
                                "return_memo": rows[ret]["memo"], "outflows_while_provisional": R2(len(flags)),
                                "paid_while_provisional": M(sum((-rows[i]["amount"] for i in flags), F0)),
                                "spent_floor": M(lo), "spent_ceiling": M(hi), "spent": Bound(lo, hi)})
                for i in orphans:
                    out.append({"return_date": Date(rows[i]["day"]), "amount": M(-rows[i]["amount"]), "return_page": rows[i]["page"],
                                "return_memo": rows[i]["memo"], "deposit_date": NONE})
                return out
            if name == "reconcile":
                need((2, 3), "(events_a, events_b [, days])")
                days = int(v[2].a) if len(v) == 3 else 5
                a = [(r["day"], r["amount"], r["memo"], r["page"]) for r in rows_of(v[0])]
                b = [(r["day"], r["amount"], r["memo"], r["page"]) for r in rows_of(v[1])]
                rc = surd_bank.reconcile(a, b, days)
                return {"matched": R2(rc["matched"]), "explained": rc["explained"],
                        "netted": [{"in": n["in"], "deposit_date": Date(n["deposit_day"]), "return_date": Date(n["return_day"]),
                                    "amount": M(n["amount"]), "return_memo": n["return"][0], "return_page": n["return"][1]} for n in rc["netted"]],
                        "split": [{"whole_in": x["whole_in"], "date": Date(x["day"]), "amount": M(x["amount"]),
                                   "parts": [M(p_) for p_ in x["parts"]]} for x in rc["split"]],
                        "shifted": [{"amount": M(x["amount"]), "a_date": Date(x["a_day"]), "b_date": Date(x["b_day"]),
                                     "days": R2(x["shift"])} for x in rc["shifted"]],
                        "zero": [{"in": z["in"], "date": Date(z["day"]), "memo": z["row"][0], "page": z["row"][1]} for z in rc["zero"]],
                        "only_a": [{"date": Date(d_), "amount": M(x)} for d_, x in rc["only_a"]],
                        "only_b": [{"date": Date(d_), "amount": M(x)} for d_, x in rc["only_b"]],
                        "first_divergence": dd(rc["first_divergence"]), "max_divergence": M(rc["max_divergence"]),
                        "max_divergence_on": dd(rc["max_divergence_on"]), "divergent_days": R2(rc["divergent_days"]),
                        "final_gap": M(rc["final_gap"])}
            if name == "calibrate":
                need(1, "(events with the bank's overdraft notices)")
                c = surd_bank.calibrate(rows_of(v[0]))
                def side(x):
                    return {"consistent": x["consistent"],
                            "explained": [{"notice": e["notice"], "page": e["page"], "items": R2(e["items"]),
                                           "replayed_items": R2(e["replayed_items"]), "date": Date(e["day"]), "low": M(e["low"])}
                                          for e in x["explained"]],
                            "missed": [{"notice": e["notice"], "page": e["page"]} for e in x["missed"]],
                            "invented": [{"date": Date(e["day"]), "low": M(e["low"]), "items": R2(e["items"])} for e in x["invented"]]}
                out = {"notices": R2(c["notices"]), "deposits_first": side(c["deposits_first"]),
                       "withdrawals_first": side(c["withdrawals_first"])}
                cal = side(c["calibrated"])
                cal["withdrawals_first_on"] = [Date(x) for x in c["calibrated"]["withdrawals_first_on"]]
                out["calibrated"] = cal
                return out
        except surd_bank.BankError as e:
            raise SurdError("S-E009", str(e), pos)
        except OSError as e:
            raise SurdError("S-E009", f"cannot read the statement: {e}", pos)
        raise SurdError("S-E002", f"unknown function '{name}'", pos)

    def search_fn(self, name, args, env, pos):
        vals = [self.ev(a, env) for a in args]
        def native(v):
            if v is NONE: return None
            if isinstance(v, R2):
                if not v.is_rational(): raise surd_search.SearchError("search data needs rational numbers or text")
                return int(v.a) if v.a.denominator == 1 else float(v.a)
            if isinstance(v, (Money, Date)): return str(v)
            if isinstance(v, list): return [native(x) for x in v]
            if isinstance(v, dict): return {k: native(x) for k, x in v.items()}
            if isinstance(v, (str, bool, int, float, surd_search.SearchIndex)): return v
            raise surd_search.SearchError(f"unsupported search value: {type_name(v)}")
        def language(v):
            if v is None: return NONE
            if type(v) is int: return R2(v)
            if isinstance(v, list): return [language(x) for x in v]
            if isinstance(v, dict): return {k: language(x) for k, x in v.items()}
            return v
        def need(counts):
            if len(vals) not in counts: raise SurdError("S-E004", f"{name} takes {' or '.join(map(str, counts))} argument(s)", pos)
        try:
            if name in INTEGRATION_FUNCS:
                import surd_integrations
                result = surd_integrations.call(name, [native(v) for v in vals])
            elif name == "search_index":
                need((1, 2))
                if isinstance(vals[0], Realm):
                    rows = []
                    for rec in vals[0].recs:
                        row = native(rec.row)
                        row.setdefault("id", f"{vals[0].name}:{rec.i}")
                        row.setdefault("locator", rec.loc)
                        rows.append(row)
                elif isinstance(vals[0], str): rows = surd_search.load_rows(vals[0])
                else: rows = native(vals[0])
                result = surd_search.SearchIndex(rows, native(vals[1]) if len(vals) == 2 else None)
            elif name == "search_load":
                need((1,)); result = surd_search.load(native(vals[0]))
            elif name == "search_save":
                need((2,)); result = surd_search.save(native(vals[0]), native(vals[1]))
            elif name == "search_info":
                need((1,))
                if not isinstance(vals[0], surd_search.SearchIndex): raise surd_search.SearchError("search_info needs an index")
                result = vals[0].manifest()
            elif name == "search":
                need((2, 3))
                if not isinstance(vals[0], surd_search.SearchIndex): raise surd_search.SearchError("search needs an index")
                result = vals[0].search(native(vals[1]), native(vals[2]) if len(vals) == 3 else None)
                from loom_research.capture import record_search
                record_search(vals[0],str(native(vals[1])),result)
                surd_ext.auto_portal(self, _ext_context(), str(native(vals[1])), "search")
            elif name == "search_evaluate":
                need((2, 3)); result = surd_search.evaluate(vals[0], native(vals[1]), native(vals[2]) if len(vals) == 3 else None)
            elif name == "search_reason":
                need((2, 3)); result = surd_search.reason(vals[0], native(vals[1]), native(vals[2]) if len(vals) == 3 else None)
            elif name == "search_posture":
                need((3,)); result = surd_search.posture(vals[0], native(vals[1]), native(vals[2]))
            elif name == "search_quantum":
                need((3,)); result = surd_search.quantum(vals[0], native(vals[1]), native(vals[2]))
            elif name == "search_compare":
                need((2, 3)); result = surd_search.compare(native(vals[0]), native(vals[1]), native(vals[2]) if len(vals) == 3 else None)
            else:
                need((1, 2)); result = surd_search.fuse(native(vals[0]), native(vals[1]) if len(vals) == 2 else None)
            return language(result)
        except (ValueError, surd_search.SearchError, OSError, TypeError, OverflowError) as e:
            raise SurdError("S-E016", str(e), pos, "see SEARCH_ENGINE.md for corpus, query and evaluation schemas") from e

    def sem_fn(self, name, args, env, pos):
        def ev(i): return self.ev(args[i], env)
        def need(n, sig):
            if len(args) not in (n if isinstance(n, tuple) else (n,)): raise SurdError("S-E004", f"{name}{sig}", pos)
        def meaning(i):
            v = ev(i)
            if not isinstance(v, Meaning): raise SurdError("S-E009", f"{name} needs a meaning, got {type_name(v)}", pos)
            return v
        def frac(v):
            if isinstance(v, R2) and v.is_rational(): return v.a
            raise SurdError("S-E009", f"{name} needs exact rational weights", pos)
        if name == "sense":
            need(2, "(space, \"label\")")
            sp, lab = self._space(args[0], env), ev(1)
            sp.index(lab, pos)
            return Meaning((sp,), {lab: Q1})
        if name == "blend":
            need(2, "(space, {\"label\": weight, …})")
            sp, w = self._space(args[0], env), ev(1)
            if not isinstance(w, dict): raise SurdError("S-E009", "blend takes a map of weights", pos)
            for k in w: sp.index(k, pos)
            return self._weights_meaning((sp,), {k: frac(v) for k, v in w.items()}, pos)
        if name == "read":
            need(3, "(space, text, {\"cue word\": \"sense\", …})")
            sp, text, cues = self._space(args[0], env), ev(1), ev(2)
            if not isinstance(text, str) or not isinstance(cues, dict):
                raise SurdError("S-E009", "read takes a space, a text and a map of cue words to senses", pos)
            toks = re.findall(r"[a-z0-9']+", text.lower())
            counts = {}
            for cue, lab in cues.items():
                sp.index(lab, pos)
                cw = str(cue).lower().split()          # a cue fires at a word start: "overage" in "overages", never "coverage"
                n = sum(1 for i in range(len(toks) - len(cw) + 1)
                        if all(toks[i + j].startswith(cw[j]) if j == len(cw) - 1 else toks[i + j] == cw[j]
                               for j in range(len(cw))))
                if n: counts[lab] = counts.get(lab, 0) + n
            if not counts: return NONE                       # no cue fired: unread, never a guess
            return self._weights_meaning((sp,), {k: F(v) for k, v in counts.items()}, pos)
        if name == "joint":
            need(3, "(spaceA, spaceB, [[\"a\", \"b\", weight], …])")
            A, B, rows = self._space(args[0], env), self._space(args[1], env), ev(2)
            if not isinstance(rows, list): raise SurdError("S-E009", "joint takes a list of [a, b, weight] rows", pos)
            w = {}
            for r in rows:
                if not (isinstance(r, list) and len(r) == 3): raise SurdError("S-E009", "each row is [a, b, weight]", pos)
                A.index(r[0], pos); B.index(r[1], pos)
                w[(r[0], r[1])] = w.get((r[0], r[1]), F0) + frac(r[2])
            return self._weights_meaning((A, B), w, pos)
        if name == "distribution":
            need(1, "(meaning)"); m = meaning(0)
            return {m.label(k): p for k, p in m.probs().items()}
        if name == "gap":
            need(2, "(meaning, meaning)")
            f_ = meaning_fidelity(meaning(0), meaning(1), pos)
            return (R2(1) - f_) if isinstance(f_, R2) else 1 - f_
        if name == "entropy":
            need(1, "(meaning)"); m = meaning(0)
            if len(m.spaces) == 2:
                rho, labs = meaning_reduced(m, 0); n = float(m.norm())
                return entropy_bits([float(complex(rho[(l, l)]).real) / n for l in labs if (l, l) in rho])
            return entropy_bits(m.probs().values())
        if name == "normalize":
            need(1, "(meaning)")
            m = meaning(0)
            out = normalize_meaning(m, pos)
            if m.exact and not out.exact:
                self.notes.append(f"line {pos[0]}: normalize needs a square root outside the supported exact root set — float meaning")
            return out
        if name in ("given", "condition"):
            need(2, "(joint, \"label of the second space\")")
            m, lab = meaning(0), ev(1)
            if len(m.spaces) != 2: raise SurdError("S-E009", "given conditions a joint meaning", pos)
            m.spaces[1].index(lab, pos)
            amps = {a: v for (a, b), v in m.amps.items() if b == lab}
            if not amps: raise SurdError("S-E009", f"the joint meaning never pairs anything with {lab!r}", pos)
            selected = Meaning((m.spaces[0],), amps)
            if name == "given": return selected
            out = normalize_meaning(selected, pos)
            if selected.exact and not out.exact:
                self.notes.append(f"line {pos[0]}: condition needs a square root outside the supported exact root set — float meaning")
            return {"meaning": out, "probability": selected.norm() / m.norm()}
        if name == "collapse":
            need(2, "(meaning, \"label\")"); m, lab = meaning(0), ev(1)
            if lab not in m.amps: raise SurdError("S-E009", f"the meaning has no weight on {lab!r}", pos)
            return Meaning(m.spaces, {lab: Q1})
        if name == "phase":
            need(3, "(meaning, \"label\", angle)")
            m, lab, th = meaning(0), ev(1), ev(2)
            m.spaces[0].index(lab, pos) if len(m.spaces) == 1 else None
            try:
                e_ = EX.expi(th)
            except NotExact:
                e_ = cmath.exp(1j * float(th))
            return Meaning(m.spaces, {k: (_mul(e_, v) if (k == lab) else v) for k, v in m.amps.items()})
        if name == "reframe":
            need(4, "(meaning, \"from\", \"to\", angle)")
            m, a, b, th = meaning(0), ev(1), ev(2), ev(3)
            if len(m.spaces) != 1: raise SurdError("S-E009", "reframe acts on a single-space meaning", pos)
            m.spaces[0].index(a, pos); m.spaces[0].index(b, pos)
            try:
                c, s_ = EX.cos(th), EX.sin(th)
            except NotExact:
                c, s_ = complex(math.cos(float(th))), complex(math.sin(float(th)))
            va, vb = m.amps.get(a, QZ), m.amps.get(b, QZ)
            amps = dict(m.amps)
            amps[a] = _mix(_mul(c, va), _mul(_mul(s_, QW.from_r2(R2(-1))) if isinstance(s_, QW) else -s_, vb))
            amps[b] = _mix(_mul(s_, va), _mul(c, vb))
            return Meaning(m.spaces, amps)
        if name == "encode":
            need((2, 3), "(meaning, wires [, wires for the second space])")
            m = meaning(0)
            regs = [self.qref(args[i], env) for i in range(1, len(args))]
            if len(regs) != len(m.spaces): raise SurdError("S-E004", "one register per space of the meaning", pos)
            for sp, r in zip(m.spaces, regs):
                if len(r) < sp.nbits: raise SurdError("S-E012", f"space {sp.name} needs {sp.nbits} wires", pos)
            flat = [q for r in regs for q in r]
            if len(set(flat)) != len(flat):
                raise SurdError("S-E006", "encode registers overlap", pos, "use a distinct register for each meaning space")
            if self.state.kind != "pure": raise SurdError("S-E009", "encode needs a pure (noiseless) state", pos)
            if self.state.prob_bits(flat, "0" * len(flat)) != (R2(1) if self.state.exact else 1.0) and \
                    not (not self.state.exact and abs(float(self.state.prob_bits(flat, "0" * len(flat))) - 1) < 1e-12):
                raise SurdError("S-E009", "encode needs its wires at 0 (fresh or reset)", pos)
            nrm = m.norm()
            if not ((nrm - R2(1)).is_zero() if isinstance(nrm, R2) else abs(float(nrm) - 1) < 1e-12):
                raise SurdError("S-E009", "encode needs a normalized meaning (blend, sense, joint and read are)", pos)
            if not m.exact and self.state.exact: self.state.to_float()
            S = self.state
            old = S.v
            new = [S.zero()] * len(old)
            def bits(k):
                keys = (k,) if len(m.spaces) == 1 else k
                idx = 0
                for sp, r, lab in zip(m.spaces, regs, keys):
                    code = sp.index(lab, pos)
                    for j in range(sp.nbits):
                        if code >> (sp.nbits - 1 - j) & 1: idx |= 1 << r[j]
                return idx
            for i, a in enumerate(old):
                if S.isz(a): continue
                for k, v in m.amps.items():
                    amp = v if S.exact else complex(v)
                    new[i | bits(k)] = new[i | bits(k)] + a * amp
            S.v = new
            self.trace.append(("b", tuple(flat)))
            return NONE
        if name == "senses":
            need(2, "(wires, space)")
            qs, sp = self.qref(args[0], env), self._space(args[1], env)
            if len(qs) < sp.nbits: raise SurdError("S-E012", f"space {sp.name} needs {sp.nbits} wires", pos)
            d = self.state.distribution(qs[:sp.nbits])
            out = {}
            for b, p in d.items():
                code = int(b, 2)
                lab = sp.labels[code] if code < len(sp.labels) else f"(unused {b})"
                out[lab] = p
            return out
        if name in ("trust", "beliefs"):
            need((1, 2) if name == "trust" else 2, "(claims [, \"quantity\"])" if name == "trust" else "(claims, \"quantity\")")
            claims = ev(0)
            if not (isinstance(claims, list) and all(isinstance(c, list) and len(c) == 3 for c in claims)):
                raise SurdError("S-E009", f"{name} takes a list of [family, quantity, value] claims", pos)
            cl = [(str(c[0]), str(c[1]), fmt_short(c[2]) if not isinstance(c[2], str) else c[2]) for c in claims]
            t, conf = truth_discovery(cl)
            if name == "trust":
                return {f: round(v, 6) for f, v in t.items()}
            q = ev(1)
            opts = {v: c for (qq, v), c in conf.items() if qq == str(q)}
            if not opts: raise SurdError("S-E009", f"no claims about {q!r}", pos)
            sp = Space(str(q), sorted(opts))
            tot = sum(opts.values())
            return Meaning((sp,), {v: complex(math.sqrt(c / tot)) for v, c in opts.items()})
        if name == "belnap":
            need(2, "(witnessed, contradicted)")
            return belnap(self.truthy(ev(0), pos), self.truthy(ev(1), pos))
        if name == "jaccard":
            need(2, "(text, text)")
            a, b = ev(0), ev(1)
            if not (isinstance(a, str) and isinstance(b, str)): raise SurdError("S-E009", "jaccard compares two texts", pos)
            A, B = words(a), words(b)
            if not A and not B: return R2()
            return R2(1) - R2(F(len(A & B), len(A | B)))
        raise SurdError("S-E002", f"unknown function '{name}'", pos)


    # ═════════ ability: explain — a plain-English walk through a program ═════════
    GATE_WORDS = {
        "h": "puts {w} into an equal mix of 0 and 1", "x": "flips {w} (0 ↔ 1)",
        "y": "flips {w} and gives it a quarter-turn phase", "z": "flips the sign of {w}'s 1 part (seen only through interference)",
        "s": "turns {w}'s 1 part a quarter turn", "sdg": "turns {w}'s 1 part back a quarter turn",
        "t": "turns {w}'s 1 part an eighth turn", "tdg": "turns {w}'s 1 part back an eighth turn",
        "sx": "does half of a flip on {w}", "sxdg": "undoes half of a flip on {w}", "id": "leaves {w} alone",
        "rx": "rotates {w} by {a} about the X axis", "ry": "rotates {w} by {a} about the Y axis",
        "rz": "rotates {w} by {a} about the Z axis", "p": "turns {w}'s 1 part by {a}",
        "u": "applies a general rotation u({a}) to {w}"}
    NAMED = {"cx": "flips {t} whenever {c} is 1", "cz": "flips the sign when {c} and {t} are both 1",
             "cy": "applies y to {t} whenever {c} is 1", "ch": "applies h to {t} whenever {c} is 1",
             "swap": "swaps {c} and {t}", "ccx": "flips {t} whenever {c} are both 1 (a Toffoli)",
             "ccz": "flips the sign when all three are 1", "cswap": "swaps the last two wires whenever {c} is 1",
             "cp": "turns the 1·1 part by {a}", "crz": "rotates {t} about Z by {a} whenever {c} is 1",
             "crx": "rotates {t} about X by {a} whenever {c} is 1", "cry": "rotates {t} about Y by {a} whenever {c} is 1"}

    def describe_gate(self, g, names):
        """plain words for a gate value acting on the named wires"""
        k = g.kind
        ang = ", ".join(fmt_angle(p) for p in g.params) if g.params else ""
        if k == "prim":
            return self.GATE_WORDS.get(g.name, f"applies {g.name}").format(w=names[0], a=ang)
        if k == "def":
            d = self.gates.get(g.name, {})
            if g.name in self.NAMED and len(names) >= 2:
                return self.NAMED[g.name].format(c=" and ".join(names[:-1]), t=names[-1], a=ang)
            return f"runs the gate {g.name}{'(' + ang + ')' if ang else ''} on {', '.join(names)}" + \
                (" (defined in the program)" if g.name not in getattr(self, "builtin_gates", ()) else "")
        if k == "seq":
            return f"{self.describe_gate(g.a, names)}; then {self.describe_gate(g.b, names)}"
        if k == "ten":
            na = g.a.arity
            return f"{self.describe_gate(g.a, names[:na])}, while {self.describe_gate(g.b, names[na:])}"
        if k == "ctl":
            return f"only when {names[0]} is 1: {self.describe_gate(g.a, names[1:])}"
        if k == "inv":
            return f"undoes this: {self.describe_gate(g.a, names)}"
        if k == "pow":
            return f"does this {g.b} time(s): {self.describe_gate(g.a, names)}"
        if k == "word":
            return f"runs a {len(g.a)}-gate circuit (synthesized or imported) on {', '.join(names)}"
        return f"applies {g.label()}"

    def describe_stmt(self, st, env):
        k = st["k"]
        refname = lambda r: r["name"] + ("" if r["lo"] is None else "[…]")
        if k == "wire":
            parts = [f"{n}" + (f" ({self.int_of(sz, env)} wires)" if sz is not None else "") for n, sz in st["decls"]]
            return f"creates {' and '.join(parts)}; every new wire starts at 0"
        if k == "bit": return "creates classical bits to hold measurement results"
        if k == "let": return f"remembers the value of {st['name']}"
        if k == "symbol": return f"makes {', '.join(st['names'])} a symbol: noise using it turns answers into formulas"
        if k == "gate":
            return f"defines a new gate called {st['name']}" + (" as a formula of other gates" if st["expr"] else
                                                                " step by step")
        if k == "apply":
            try:
                val = self.ev(st["g"], env)
                groups = [self.qref(t, env) for t in st["q"]]
            except SurdError:
                return "applies a gate"
            if isinstance(val, ChannelVal):
                ps = ", ".join(fmt_angle(p) for p in val.params)
                what = {"depolarize": "scrambles it completely", "dephase": "erases its phase",
                        "bitflip": "flips it", "ampdamp": "lets its 1 decay to 0", "pauli": "hits it with X, Y or Z"}
                return f"adds noise: with probability {ps}, it {what.get(val.name, 'disturbs it')} " \
                       f"({', '.join(self.qnames[w] for g_ in groups for w in g_)})"
            if isinstance(val, GateVal):
                calls = self.layout(val, groups, st["pos"]) if val.arity is not None else [[w for g_ in groups for w in g_]]
                texts = []
                for args in calls[:3]:
                    nm = [self.qnames[a] if not isinstance(a, list) else ", ".join(self.qnames[x] for x in a) for a in args]
                    texts.append(self.describe_gate(val, nm))
                more = f" (and the same on {len(calls) - 3} more)" if len(calls) > 3 else ""
                return "; ".join(texts) + more
            return "applies a gate"
        if k == "measure":
            try:
                ws = [self.qnames[q] for r in st["q"] for q in self.qref(r, env)]
            except SurdError:
                ws = [refname(r) for r in st["q"]]
            return "reads " + ", ".join(ws) + " — the qubits collapse to the outcome seen"
        if k == "reset": return "sets " + ", ".join(refname(r) for r in st["q"]) + " back to 0"
        if k == "noise":
            return "turns noise off" if st["name"] is None else \
                f"from here on, every gate is followed by {st['name']} noise"
        if k == "if": return "checks a condition and runs its block only if it holds"
        if k == "for": return f"repeats its block for {st['var']} = each value in the range"
        if k == "repeat": return "repeats its block"
        if k == "expect": return "checks a claim"
        if k.startswith("print"): return "shows a result"
        if k == "realm": return f"loads a table of records as the realm {st['name']}"
        if k == "compare": return "compares realms record by record: same money, same or different meaning"
        if k == "grover": return "scans the realm classically, then simulates Grover amplitude amplification"
        if k == "swaptest": return "measures how alike two records' meanings are with the swap-test circuit"
        if k == "game": return f"declares the game {st['name']}" + (f": “{st['question']}”" if st["question"] else "")
        if k == "solve": return f"solves the game {st['name']}: best classical play vs best play with shared qubits"
        if k == "seed": return "fixes the random seed, so measurements repeat"
        if k == "space": return f"declares the meaning space {st['name']} with senses {', '.join(st['labels'])}"
        if k == "fndef": return f"defines the function {st['name']}({', '.join(st['params'])})"
        if k == "barrier": return "marks a boundary in the circuit drawing"
        return k

    def state_words(self):
        S = self.state
        if S.n == 0: return "no wires yet"
        d = S.distribution(list(range(S.n)))
        def weight(kv):
            v = kv[1]
            if isinstance(v, Poly) and not v.is_const(): return (1, kv[0])     # formulas: after numbers, by outcome
            return (0, -float(v))
        items = sorted(d.items(), key=weight)
        shown = " · ".join(f"|{b}> {fmt_value(p).split('  ≈')[0] if isinstance(p, R2) else (str(p) if isinstance(p, Poly) else f'{float(p):.4f}')}"
                           for b, p in items[:4])
        more = f" · +{len(items) - 4} more" if len(items) > 4 else ""
        notes = []
        if S.kind == "mixed":
            notes.append("noisy")
        elif S.n >= 2 and S.n <= 10:
            ent = []
            for q in range(S.n):
                e = S.purity([q])
                if (isinstance(e, R2) and not (e - R2(1)).is_zero()) or (not isinstance(e, R2) and abs(float(e) - 1) > 1e-9):
                    ent.append(self.qnames[q])
            if ent: notes.append("entangled: " + ", ".join(ent))
        return shown + more + (f"   ({'; '.join(notes)})" if notes else "")

    def explain(self, src):
        prog = parse(src)
        lines = src.splitlines()
        out = []
        for st in prog:
            ln = st["pos"][0]
            text = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
            if text.startswith("#") or not text: continue
            words = self.describe_stmt(st, self.globals)
            if st["k"] == "if":
                try:
                    held = self.truthy(self.ev(st["cond"], self.globals), st["pos"])
                    words += " — it held, so the block ran" if held else " — it did not hold, so the block was skipped"
                except SurdError:
                    pass
            if st["k"] == "expect":
                words = "checks that " + text[len("expect"):].split("#")[0].strip()
            before = len(self.lines)
            changes = st["k"] in ("apply", "measure", "reset", "wire")
            try:
                self.exec_stmt(st, self.globals, None)
                err = None
            except SurdError as e:
                err = e
            shown = self.lines[before:]
            out.append(f"line {ln:>3} │ {text}")
            out.append(f"         │   what it does: {words}")
            if st["k"] == "measure" and not err and st["c"] is not None:
                seen = []
                for qa, ca in zip([q for r in st["q"] for q in self.qref(r, self.globals)],
                                  [c for r in st["c"] for c in self.cref(r, self.globals)]):
                    seen.append(f"{self.qnames[qa]} → {self.cbits[ca]}")
                out.append(f"         │   saw: {', '.join(seen)}")
            if st["k"] == "expect":
                ok = self.expects[-1]["ok"] if self.expects else None
                out.append(f"         │   result: {'passed ✓' if ok else 'FAILED ✗'}")
            for o in shown[:6]:
                if st["k"] != "expect": out.append(f"         │   output: {o}")
            if err:
                out.append(f"         │   error: {err.render()}"); break
            if changes and self.state.n:
                out.append(f"         │   qubits now: {self.state_words()}")
        return out

    # ═════════ ability: games — questions answered by qubit strategies ═════════
    def st_game(self, st, env, sink):
        name = st["name"]
        if name in self.games or name in self.globals or name in PRIM or name in self.gates:
            raise SurdError("S-E011", f"'{name}' is already a name", st["pos"])
        sizes, names_in, names_out = [], [], []
        for pn, xin, size, aout in st["players"]:
            n = 2 if size is None else self.int_of(size, env)
            if not 1 <= n <= 4: raise SurdError("S-E009", "a player's input takes 1 to 4 values", st["pos"])
            sizes.append(n); names_in.append(xin); names_out.append(aout)
        if len(sizes) > 3: raise SurdError("S-E010", "games have up to 3 players (one qubit each)", st["pos"])
        if len(set(names_in + names_out)) != 2 * len(sizes):
            raise SurdError("S-E011", "every input and answer name must be different", st["pos"])

        def make(expr):
            def f(inp, out):
                e2 = {"__depth__": 0}
                e2.update({n: R2(v) for n, v in zip(names_in, inp)})
                e2.update({n: R2(v) for n, v in zip(names_out, out)})
                return self.truthy(self.ev(expr, e2), st["pos"])
            return f
        self.games[name] = {"name": name, "question": st["question"], "players": [x[0] for x in st["players"]],
                            "ins": names_in, "outs": names_out, "sizes": sizes,
                            "win_fn": make(st["win"]),
                            "promise_fn": (lambda inp: make(st["promise"])(inp, (0,) * len(sizes)))
                            if st["promise"] is not None else (lambda inp: True),
                            "solved": None}

    def solve_game(self, name, pos, restarts=24):
        g = self.games.get(name)
        if g is None: raise SurdError("S-E002", f"no game '{name}'", pos)
        if g["solved"] is None or g["solved"][5] != restarts:
            allowed, win = game_tables(g)
            if not allowed: raise SurdError("S-E009", "the promise allows no inputs", pos)
            cv, carg, ccount = game_classical(g, allowed, win)
            qv, qarg, evals, rs = game_quantum(g, allowed, win, self.rng, carg, restarts)
            g["solved"] = (R2(cv), qv, carg, qarg, (allowed, ccount, evals), restarts)
        return g["solved"]

    def st_solve(self, st, env, sink):
        restarts = 24 if st["restarts"] is None else self.int_of(st["restarts"], env)
        cv, qv, carg, qarg, (allowed, ccount, evals), _ = self.solve_game(st["name"], st["pos"], restarts)
        g = self.games[st["name"]]
        self.emit(f"game {g['name']}" + (f" — “{g['question']}”" if g["question"] else ""))
        self.emit(f"  classical value {fmt_value(cv)}   (every deterministic strategy: {ccount}; "
                  f"{len(allowed)} input case(s))")
        self.emit(f"  quantum value ≥ {fmt_value(qv)}   (achieved exactly by the strategy below; best of {restarts} AI "
                  f"searches, {evals} strategies scored)")
        d = qv - cv
        if d.sign() > 0:
            self.emit(f"  answer: YES — sharing entangled qubits raises the win rate by {fmt_value(d)}")
        else:
            self.emit("  answer: NO advantage found — the best quantum strategy found does no better than "
                      "the best classical one")
        self.emit("  quantum strategy (each player's move per input; the shared state is "
                  f"{'a Bell pair' if len(g['sizes']) == 2 else 'a GHZ state' if len(g['sizes']) == 3 else 'one qubit'}):")
        for p, pn in enumerate(g["players"]):
            moves = "   ".join(f"{g['ins'][p]}={i}: {_GAME_CANDS[qarg[p][i]][2]}" for i in range(g["sizes"][p]))
            self.emit(f"    {pn:8} {moves}")
        self.emit("  classical strategy: " + "   ".join(
            f"{pn}: " + ",".join(f"{g['ins'][p]}={i}→{carg[p][i]}" for i in range(g["sizes"][p]))
            for p, pn in enumerate(g["players"])))

    def st_realm(self, st, env, sink):
        name = st["name"]
        if name in PRIM or name in ALIAS or name in self.gates or name in CHANNELS or self._taken(name):
            raise SurdError("S-E011", f"'{name}' is already a name", st["pos"], "pick another realm name")
        v = self.ev(st["e"], env)
        if not isinstance(v, Realm):
            raise SurdError("S-E009", "a realm is made by load(…)", st["pos"], 'realm r = load("data.csv")')
        v.name = name
        self.globals[name] = v
        bad = sum(1 for r in v.recs if r.cents is None or r.day is None)
        self.emit(f"realm {name}: {len(v.recs)} records from {v.source}" +
                  (f"  ({bad} without an amount or date stay out of the anchor basis)" if bad else ""))

    def _realm(self, name, pos):
        v = self.globals.get(name)
        if not isinstance(v, Realm):
            raise SurdError("S-E002", f"'{name}' is not a realm", pos, f'realm {name} = load("…")')
        return v

    def st_compare(self, st, env, sink):
        import datetime as _dt
        realms = [self._realm(n, st["pos"]) for n in st["names"]]
        if len(realms) < 2: raise SurdError("S-E004", "compare needs two or more realms", st["pos"])
        w = 3 if st["w"] is None else self.int_of(st["w"], env)
        if w < 0: raise SurdError("S-E009", "date-window radius must be nonnegative", st["pos"])
        self.emit("  meaning scores use lexical token overlap; review transaction roles and source context")
        iso = lambda r: _dt.date.fromordinal(r.day).isoformat() if r.day else "?"
        txt = lambda r, k=34: (" ".join(str(r.row.get("meaning") or "").split()) or "—")[:k]
        for i in range(len(realms)):
            for j in range(i + 1, len(realms)):
                A, B = realms[i], realms[j]
                pairs, lone_a, lone_b = realm_compare(A, B, w)
                by = {k: [p for p in pairs if p[0] == k] for k in ("AGREE", "PARTIAL", "COLLIDE", "UNLABELED")}
                src = [p for p in pairs if p[5] == "DIFFER"]
                self.emit(f"compare {A.name} ⟷ {B.name}  (anchor: signed amounts, date-window radius {w} day(s), separation <= {2 * w}; "
                          f"meaning: word states)")
                self.emit(f"  anchor-coupled pairs {len(pairs)} · AGREE {len(by['AGREE'])} · PARTIAL "
                          f"{len(by['PARTIAL'])} · COLLIDE {len(by['COLLIDE'])} · UNLABELED {len(by['UNLABELED'])} · "
                          f"SOURCE-DIFFER {len(src)} · only in {A.name} {len(lone_a)} · only in {B.name} {len(lone_b)}")
                head = (f"    {'date A':10}  {'date B':10}  {'amount':>14}  {'F_anchor':>8}  {'F_mean':>7}  "
                        f"{(A.name + ' says'):34}  {(B.name + ' says'):34}  locators")
                for kind, title in (("COLLIDE", "same signed amount, no shared tokens"),
                                    ("PARTIAL", "same signed amount, partial token overlap"),
                                    ("AGREE", "same signed amount, token overlap meets threshold")):
                    rows = by[kind]
                    if not rows: continue
                    self.emit(f"  {kind} — {title} ({len(rows)})")
                    self.emit(head)
                    for _, fa, fm, a, b, sd in sorted(rows, key=lambda t: (t[3].day or 0))[:25]:
                        self.emit(f"    {iso(a):10}  {iso(b):10}  {_display_amount(a.cents):>14}  "
                                  f"{fmt_frac(fa):>8}  {fmt_frac(fm):>7}  {txt(a):34}  {txt(b):34}  "
                                  f"{a.loc or '?'} | {b.loc or '?'}")
                    if len(rows) > 25: self.emit(f"    … {len(rows) - 25} more")
                if src:
                    self.emit(f"  SOURCE-DIFFER — same money, the realms cite different records ({len(src)})")
                    for kind, fa, fm, a, b, sd in sorted(src, key=lambda t: (t[3].day or 0))[:25]:
                        self.emit(f"    {iso(a):10}  {_display_amount(a.cents):>14}  {A.name}: {a.loc:16}  "
                                  f"{B.name}: {b.loc:16}  [{kind.lower()}]")
                    if len(src) > 25: self.emit(f"    … {len(src) - 25} more")

    def st_grover(self, st, env, sink):
        R = self._realm(st["name"], st["pos"])
        marked = []
        for r in R.recs:
            renv = {"__depth__": 0}
            for k, v in r.row.items():
                if isinstance(k, str) and k.isidentifier() and k not in KEYWORDS and k not in PRIM:
                    renv[k] = record_value(r, k)
            renv["amount"] = R2(r.cents) if r.cents is not None else NONE
            renv["meaning"] = str(r.row.get("meaning") or "")
            renv["locator"] = r.loc
            if self.truthy(self.ev(st["cond"], renv), st["pos"]): marked.append(r)
        n, k, p, hist = grover_realm(len(R.recs), marked)
        N = len(R.recs)
        self.emit(f"grover over realm {R.name}: {N} records on {n} wires (2^{n} = {1 << n} basis states), "
                  f"{len(marked)} marked")
        self.emit(f"  classical predicate evaluations: {N}; amplitude amplification is simulated")
        if not marked:
            self.emit("  nothing satisfies the condition; Grover amplifies only what is in the data"); return
        self.emit(f"  {k} simulated oracle call(s) → P(measure a marked record) = {fmt_value(p)}")
        if hist:
            self.emit("  rise per iteration: " + " → ".join(f"{float(h):.4f}" for h in hist))
        for r in marked[:20]:
            self.emit(f"  marked  {r}")
        if len(marked) > 20: self.emit(f"  … {len(marked) - 20} more")

    def st_swaptest(self, st, env, sink):
        a, b = self.ev(st["a"], env), self.ev(st["b"], env)
        if not (isinstance(a, Record) and isinstance(b, Record)):
            raise SurdError("S-E009", "swaptest compares two records", st["pos"], "swaptest bank[3], ledger[0]")
        out = swap_test(a, b)
        if out is None:
            self.emit("swaptest: neither record carries informative words"); return
        p0, wires, exact = out
        fid = meaning_fid(a, b)
        fstr = "unlabeled" if fid is None else fmt_frac(fid)
        if isinstance(p0, R2):
            est = p0 * R2(2) - R2(1)
            self.emit(f"swaptest on {wires} wires (exact): P(ancilla 0) = {fmt_value(p0)} → F = 2P−1 = "
                      f"{fmt_value(est)}   formula |A∩B|²/(|A||B|) = {fstr}")
        else:
            self.emit(f"swaptest on {wires} wires (float: 1/√k amplitudes): P(ancilla 0) = {p0:.10f} → F = "
                      f"{2 * p0 - 1:.10f}   formula = {fstr}")

    def st_print_stats(self, st, env, sink):
        for line in fmt_stats(circuit_stats(self.trace, self.state.n)):
            self.emit(line)

    # references
    def qref(self, node, env):
        if node.get("k") != "ref":
            raise SurdError("S-E009", "expected a qubit or register here", node.get("pos"))
        name = node["name"]
        if isinstance(env.get(name), QR):
            return self._slice(env[name].idx, node, env)
        if isinstance(env.get(name), QB):
            if node["lo"] is not None:
                raise SurdError("S-E005", f"'{name}' is a single qubit inside this gate; it cannot be indexed",
                               node["pos"])
            return [env[name].i]
        if env.get("__gate__"):
            raise SurdError("S-E002", f"'{name}' is not an argument of this gate", node["pos"],
                           "gate bodies only see their own qubit arguments")
        if name not in self.qregs:
            near = difflib.get_close_matches(name, list(self.qregs), 2)
            raise SurdError("S-E002", f"unknown qubit or register '{name}'", node["pos"],
                           f"did you mean {', '.join(near)}?" if near else f"declare it: wire {name}")
        return self._slice(self.qregs[name], node, env)

    def cref(self, node, env):
        name = node["name"]
        if name not in self.cregs:
            raise SurdError("S-E002", f"unknown bit register '{name}'", node["pos"], f"declare it: bits {name}: 1")
        return self._slice(self.cregs[name], node, env)

    def _slice(self, reg, node, env):
        if node["lo"] is None: return list(reg)
        lo = self.int_of(node["lo"], env)
        h = node["hi"]
        hi = lo + 1 if h is None else (len(reg) if h["k"] == "end" else self.int_of(h, env))
        if not (0 <= lo < hi <= len(reg)):
            raise SurdError("S-E005", f"'{node['name']}' has {len(reg)} element(s); index {lo}"
                                     f"{'' if node['hi'] is None else '..' + str(hi)} is out of range", node["pos"],
                            f"valid indices are 0..{len(reg) - 1}")
        return reg[lo:hi]

    # expressions
    def int_of(self, e, env):
        v = self.ev(e, env)
        if isinstance(v, R2) and v.is_rational() and v.a.denominator == 1:
            return int(v.a)
        raise SurdError("S-E009", f"expected a whole number, got {fmt_value(v)}", e.get("pos"))

    def truthy(self, v, pos):
        if isinstance(v, bool): return v
        if v is NONE: return False
        if isinstance(v, (list, dict, str)): return len(v) > 0
        if isinstance(v, Poly): raise SurdError("S-E009", "a formula is not true or false", pos)
        if isinstance(v, R2): return not v.is_zero()
        raise SurdError("S-E009", "a condition must be true/false or a number", pos)

    def ev(self, e, env):
        self.budget.checkpoint(e.get("pos"))
        k = e["k"]
        if k == "num": return R2(e["v"])
        if k == "none": return NONE
        if k == "money": return Money(e["v"])
        if k == "date": return Date.parse(e["v"], e["pos"])
        if k == "list": return [self.ev(x, env) for x in e["items"]]
        if k == "map": return {map_key(self.ev(a, env)): self.ev(b, env) for a, b in e["pairs"]}
        if k == "lambda": return FuncVal("fn", e["params"], e["body"], e["expr"], env)
        if k == "index": return self.ev_index(self.ev(e["a"], env), e, env)
        if k == "str": return e["v"]
        if k == "bool": return e["v"]
        if k == "pi": return PiVal(1)
        if k == "neg":
            v = self.ev(e["a"], env)
            if isinstance(v, Money): return Money(-v.d)
            if isinstance(v, GateVal):
                raise SurdError("S-E009", "a gate cannot be negated", e["pos"], "its inverse is ~g")
            if isinstance(v, (R2, PiVal, Poly)): return ang_neg(v) if isinstance(v, PiVal) else -v
            if isinstance(v, float): return -v
            raise SurdError("S-E009", "cannot negate that", e["pos"])
        if k == "not": return not self.truthy(self.ev(e["a"], env), e["pos"])
        if k == "ref": return self.ev_ref(e, env)
        if k == "bin":
            if e["op"] in ("and", "or"):
                a = self.truthy(self.ev(e["a"], env), e["pos"])
                if e["op"] == "and": return a and self.truthy(self.ev(e["b"], env), e["pos"])
                return a or self.truthy(self.ev(e["b"], env), e["pos"])
            a, b = self.ev(e["a"], env), self.ev(e["b"], env)
            if isinstance(a, GateVal) or isinstance(b, GateVal):
                if e["op"] == "^" and isinstance(a, GateVal) and isinstance(b, R2) and b.is_rational() \
                        and b.a.denominator == 1:
                    if abs(b.a) > 4096: raise SurdError("S-E009", "gate power too large", e["pos"])
                    return GateVal("pow", a, int(b.a), arity=a.arity, regs=a.regs)
                raise SurdError("S-E009", f"'{e['op']}' does not apply to gates; use >> & ~ ctl ^n", e["pos"])
            return arith(e["op"], a, b, e["pos"])
        if k == "cmp":
            a, b = self.ev(e["a"], env), self.ev(e["b"], env)
            if isinstance(a, GateVal) or isinstance(b, GateVal):
                return self.gate_compare(e["op"], a, b, e["pos"], env)
            if e["op"] == "=~":
                raise SurdError("S-E009", "=~ compares gates up to a global phase", e["pos"])
            return compare(e["op"], a, b, e["pos"])
        if k == "fn": return self.ev_fn(e, env)
        if k in ("gseq", "gten"):
            a, b = self.gv(e["a"], env), self.gv(e["b"], env)
            if a.arity is None or b.arity is None:
                raise SurdError("S-E009", "a register-taking gate cannot be composed; apply it on its own", e["pos"])
            if k == "gseq":
                if a.arity != b.arity:
                    raise SurdError("S-E004", f"'>>' joins gates on the same wires: {a.label()} has {a.arity}, "
                                              f"{b.label()} has {b.arity}", e["pos"], "widen one with & id")
                return GateVal("seq", a, b, arity=a.arity)
            return GateVal("ten", a, b, arity=a.arity + b.arity)
        if k in ("ginv", "gctl"):
            a = self.gv(e["a"], env)
            if k == "ginv": return GateVal("inv", a, arity=a.arity, regs=a.regs)
            if a.arity is None:
                raise SurdError("S-E009", "a register-taking gate cannot take a control", e["pos"])
            return GateVal("ctl", a, arity=a.arity + 1)
        raise SurdError("S-E009", f"cannot evaluate {k}", e.get("pos"))

    def gv(self, node, env):
        v = self.ev(node, env)
        if not isinstance(v, GateVal):
            raise SurdError("S-E009", f"expected a gate, got {fmt_value(v)}", node.get("pos"))
        return v

    def gate_compare(self, op, a, b, pos, env):
        if env.get("__gate__"):
            raise SurdError("S-E007", "a gate body cannot compare gates", pos)
        if op not in ("==", "!=", "=~"):
            raise SurdError("S-E009", "gates compare with ==, != or =~", pos)
        A, B = self.unitary(a, pos), self.unitary(b, pos)
        if len(A[0]) != len(B[0]):
            raise SurdError("S-E004", f"cannot compare a {a.arity}-wire gate with a {b.arity}-wire gate", pos)
        if op == "=~": return unitaries_match(A, B, up_to_phase=True)
        same = unitaries_match(A, B, up_to_phase=False)
        return same if op == "==" else not same

    def ev_ref(self, e, env):
        name = e["name"]
        scope = self._binding_scope(env, name)
        if name in scope:
            names = self.tainted if scope is self.globals else scope.get("__measurement_names__", ())
            if name in names or id(scope[name]) in self.tainted_objects:
                self._measurement_read(env, name, e["pos"])
        if name in env and isinstance(env[name], (list, str, dict, Realm, Record)) and e["lo"] is not None:
            return self.ev_index(env[name], e, env)
        if name in env and not isinstance(env[name], (QB, QR)) and not (isinstance(env[name], Realm) and e["lo"] is not None):
            if e["lo"] is not None: raise SurdError("S-E009", f"'{name}' is a {type_name(env[name])}; it has no index", e["pos"])
            return env[name]
        if isinstance(self.globals.get(name), (list, str, dict)) and e["lo"] is not None:
            return self.ev_index(self.globals[name], e, env)
        if isinstance(self.globals.get(name), Realm) and e["lo"] is not None:
            R = self.globals[name]
            i = self.int_of(e["lo"], env)
            if not 0 <= i < len(R.recs):
                raise SurdError("S-E005", f"realm {name} has {len(R.recs)} records", e["pos"],
                                f"valid indices are 0..{len(R.recs) - 1}")
            return R.recs[i]
        if name in self.globals and not isinstance(self.globals[name], (QB, QR)) and not name.startswith("__"):
            return self.globals[name]
        if name in self.cregs:
            self._measurement_read(env, name, e["pos"])
            bits = [self.cbits[i] for i in self.cref(e, env)]
            if e["lo"] is not None and e["hi"] is None: return R2(bits[0])
            return "".join(map(str, bits))
        if e["lo"] is None and (name in PRIM or name in ALIAS or name in self.gates):
            return self.gate_value(name, [], e["pos"])
        if e["lo"] is None and name in CHANNELS:
            raise SurdError("S-E004", f"channel '{name}' needs its probability", e["pos"], f"write {name}(1/100)")
        if name in self.qregs or isinstance(env.get(name), (QB, QR)):
            raise SurdError("S-E009", f"'{name}' is a wire, not a value", e["pos"],
                           f"ask about it: prob({name}, \"…\"), bloch_z({name}), purity({name})")
        gnear = difflib.get_close_matches(name, sorted(set(PRIM) | set(self.gates) | set(ALIAS) | set(CHANNELS)), 3)
        if gnear:
            raise SurdError("S-E003", f"unknown gate or name '{name}'", e["pos"], f"did you mean {', '.join(gnear)}?")
        raise SurdError("S-E002", f"unknown name '{name}'", e["pos"], f"set it first: let {name} = …")

    FN_EARLY = {'prob': '_fn_prob', 'purity': '_fn_purity', 'entanglement': '_fn_purity', 'linear_entropy': '_fn_purity', 'bloch_x': '_fn_bloch', 'bloch_y': '_fn_bloch', 'bloch_z': '_fn_bloch', 'norm': '_fn_norm', 'xor': '_fn_xor', 'classical': '_fn_games', 'quantum': '_fn_games', 'qasm': '_fn_qasm', 'load': '_fn_load', 'fidelity': '_fn_realm', 'anchor': '_fn_realm', 'agreements': '_fn_realm', 'collisions': '_fn_realm', 'field': '_fn_realm', 'has': '_fn_realm', 'at': '_fn_formula', 'deriv': '_fn_formula', 'synth': '_fn_synth', 'tcount': '_fn_synth', 'gcount': '_fn_synth', 'stabilizers': '_fn_stabilizers', 'is_stabilizer': '_fn_stabilizers'}
    FN_LATE = {'len': '_fn_len', 'expval': '_fn_expval'}

    def _qs_of(self, nodes, env, name, pos):
        qs = [i for a in nodes for i in self.qref(a, env)]
        if len(set(qs)) != len(qs):
            raise SurdError("S-E006", f"{name} names the same qubit twice", pos)
        return qs

    def ev_fn(self, e, env):
        """a function call: user functions first, then built-ins (one small method per family), then gates"""
        name, args, pos = e["name"], e["args"], e["pos"]
        fv = env.get(name) if name in env else self.globals.get(name)
        if isinstance(fv, FuncVal):
            vals, dependent = [], []
            for a in args:
                before = self.measure_reads
                vals.append(self.arg_value(a, env))
                dependent.append(self.measure_reads != before)
            return self.call_value(fv, vals, pos, dependent)
        if name in CORE_FUNCS:
            before = self.measure_reads
            vals = [self.ev(a, env) for a in args]
            result = self.core_fn(name, vals, pos)
            if self.measure_reads != before:
                for value in vals + [result]:
                    if isinstance(value, (list, dict)):
                        self.tainted_objects[id(value)] = value
            return result
        if name in SEM_FUNCS:
            return self.sem_fn(name, args, env, pos)
        if name in MONEY_FUNCS:
            return self.money_fn(name, args, env, pos)
        if name in SEARCH_FUNCS or name in INTEGRATION_FUNCS:
            return self.search_fn(name, args, env, pos)
        if name in BANK_FUNCS:
            return self.bank_fn(name, args, env, pos)
        if name in surd_ext.FUNCS:
            return surd_ext.call(self, _ext_context(), name, [self.ev(a, env) for a in args], pos)
        if name == "fidelity" and len(args) != 2:
            raise SurdError("S-E004", "fidelity takes two arguments", pos, "fidelity(meaning_a, meaning_b)")
        if name in ("fidelity", "purity", "entanglement", "linear_entropy") and len(args) >= 1:
            first = self.ev(args[0], env) if not (args[0]["k"] == "ref" and (args[0]["name"] in self.qregs or
                                                isinstance(env.get(args[0]["name"]), (QB, QR)))) else None
            if isinstance(first, Meaning):
                if name == "fidelity":
                    return meaning_fidelity(first, self.ev(args[1], env), pos)
                if len(args) != 1: raise SurdError("S-E004", f"{name}(meaning) takes one argument", pos)
                pu = meaning_purity(first)
                return pu if name == "purity" else ((R2(1) - pu) if isinstance(pu, R2) else 1 - pu)
        if (env.get("__gate__") or self.cur_sink is not None) and name in READS_STATE:
            raise SurdError("S-E007", f"a gate body cannot call {name}()", pos,
                           "it reads the quantum state (or another gate's unitary); a gate must not depend on it")
        if self.has_measured and name in READS_STATE:
            self._measurement_read(env, name+'()', pos)
        if name in self.FN_EARLY:
            return getattr(self, self.FN_EARLY[name])(name, args, env, pos)
        if name in PRIM or name in ALIAS or name in self.gates:
            return self.gate_value(name, [self.ev(a, env) for a in args], pos)
        if name in CHANNELS:
            ps = [self.ev(a, env) for a in args]
            self.check_channel(name, ps, pos)
            return ChannelVal(name, ps)
        if name in self.FN_LATE:
            return getattr(self, self.FN_LATE[name])(name, args, env, pos)
        if name in ("equal", "equiv"):
            raise SurdError("S-E002", f"there is no {name}() function", pos,
                            "compare gates with operators: g1 == g2 (exactly) or g1 =~ g2 (up to a global phase)")
        vals = [self.ev(a, env) for a in args]
        if len(vals) != 1:
            raise SurdError("S-E004" if name in FUNCS1 else "S-E002",
                           f"{name} takes one argument" if name in FUNCS1 else f"unknown function '{name}'", pos,
                           None if name in FUNCS1 else "functions: " + ", ".join(sorted(FUNCS)))
        return fn1(name, vals[0], pos)

    def _fn_prob(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) < 2: raise SurdError("S-E004", 'prob(qubits…, "bits")', pos)
        qs = qs_of(args[:-1])
        bits = self.ev(args[-1], env)
        if isinstance(bits, R2): bits = int_bits(bits, len(qs), pos)
        if not isinstance(bits, str) or len(bits) != len(qs) or set(bits) - {"0", "1"}:
            raise SurdError("S-E009", f"prob needs a {len(qs)}-character bit string for {len(qs)} wire(s)", pos,
                            f'e.g. prob(…, "{"0" * len(qs)}")')
        return self.state.prob_bits(qs, bits)

    def _fn_purity(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if not args: raise SurdError("S-E004", f"{name}(qubits…)", pos)
        p = self.state.purity(qs_of(args))
        if name == "purity": return p
        if name == "entanglement" and self.state.kind != "pure":
            global_p = self.state.purity(list(range(self.state.n)))
            pure = global_p == R2(1) if isinstance(global_p, R2) else abs(global_p-1) < 1e-10
            if not pure:
                raise SurdError("S-E009", "entanglement's linear entropy requires a globally pure state", pos,
                                "use linear_entropy(wires) for mixedness; it is not a mixed-state entanglement measure")
        return (R2(1) - p) if isinstance(p, R2) else 1 - p

    def _fn_bloch(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        qs = qs_of(args)
        if len(qs) != 1: raise SurdError("S-E004", f"{name} takes exactly one qubit", pos)
        return self.state.bloch(qs[0])["xyz".index(name[-1])]

    def _fn_norm(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if name == "norm": return self.state.norm()

    def _fn_xor(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        vals = [self.ev(a, env) for a in args]
        if not vals or not all(isinstance(v, R2) and v.is_rational() and v.a.denominator == 1 for v in vals):
            raise SurdError("S-E009", "xor takes whole numbers", pos)
        r = 0
        for v in vals: r ^= int(v.a)
        return R2(r)

    def _fn_games(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) != 1 or args[0]["k"] != "ref" or args[0]["name"] not in self.games:
            raise SurdError("S-E004", f"{name}(game)", pos, "declare it first: game g { … }")
        return self.solve_game(args[0]["name"], pos)[0 if name == "classical" else 1]

    def _fn_qasm(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        vals = [self.ev(a, env) for a in args]
        if len(vals) != 1 or not isinstance(vals[0], str):
            raise SurdError("S-E004", 'qasm("circuit.qasm")', pos)
        path = os.path.expanduser(vals[0])
        if not os.path.exists(path): raise SurdError("S-E002", f"no file at {vals[0]}", pos)
        with open(path) as source:
            n, ops, meas, _ = qasm_parse(source.read(), self, pos)
        if meas:
            raise SurdError("S-E009", "a circuit used as a gate cannot measure or reset", pos,
                            "use `surd from-qasm` for the whole program, or drop the measurements")
        if n == 0: raise SurdError("S-E009", "the OpenQASM file declares no qubits", pos)
        return GateVal("word", ops, arity=n)

    def _fn_load(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        vals = [self.ev(a, env) for a in args]
        if not vals or not all(isinstance(v, str) for v in vals) or len(vals) > 2:
            raise SurdError("S-E004", 'load("path" [, "query"])', pos)
        return Realm("(unnamed)", load_rows(vals[0], vals[1] if len(vals) > 1 else None, pos), vals[0])

    def _fn_realm(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        vals = [self.ev(a, env) for a in args]
        if name == "has":
            if len(vals) != 2 or not all(isinstance(v, str) for v in vals):
                raise SurdError("S-E004", 'has(text, "word")', pos)
            return vals[1].lower() in words(vals[0]) or vals[1].lower() in vals[0].lower().split()
        if name == "field":
            if len(vals) != 2 or not isinstance(vals[0], Record) or not isinstance(vals[1], str):
                raise SurdError("S-E004", 'field(record, "column")', pos)
            return record_value(vals[0], vals[1])
        if name == "fidelity":
            if len(vals) != 2 or not all(isinstance(v, Record) for v in vals):
                raise SurdError("S-E004", "fidelity(record, record)", pos)
            f = meaning_fid(vals[0], vals[1])
            if f is None:
                raise SurdError("S-E009", "one record carries no informative words (unlabeled)", pos,
                                "compare its anchor instead: anchor(a, b, 3)")
            return R2(f)
        if name == "anchor":
            if len(vals) != 3 or not all(isinstance(v, Record) for v in vals[:2]):
                raise SurdError("S-E004", "anchor(record, record, days)", pos)
            return R2(anchor_fid(vals[0], vals[1], self.int_of(args[2], env)))
        if len(vals) != 3 or not all(isinstance(v, Realm) for v in vals[:2]):
            raise SurdError("S-E004", f"{name}(realm, realm, days)", pos)
        pairs, _, _ = realm_compare(vals[0], vals[1], self.int_of(args[2], env))
        return R2(sum(1 for pr in pairs if pr[0] == ("AGREE" if name == "agreements" else "COLLIDE")))

    def _fn_formula(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) < 2: raise SurdError("S-E004", f"{name}(formula, p{', value' if name == 'at' else ''} …)", pos)
        P = self.ev(args[0], env)
        P = Poly.coerce(P)
        if P is None: raise SurdError("S-E009", f"{name} takes a formula", pos)
        def symname(node):
            if node["k"] != "ref" or node["lo"] is not None or type(self.globals.get(node["name"])) is not Poly:
                raise SurdError("S-E009", "expected a symbol declared with `symbol`", node.get("pos"))
            return node["name"]
        if name == "deriv":
            if len(args) != 2: raise SurdError("S-E004", "deriv(formula, p)", pos)
            return P.deriv(symname(args[1]))
        if len(args) % 2 != 1: raise SurdError("S-E004", "at(formula, p, value, q, value …)", pos)
        vals = {}
        for i in range(1, len(args), 2):
            v = self.ev(args[i + 1], env)
            if type(v) is not R2: raise SurdError("S-E009", "at() substitutes exact numbers", pos)
            vals[symname(args[i])] = v
        q = P.at(vals)
        if not q.im().is_zero(): raise SurdError("S-E009", "the formula is not real at that point", pos)
        return R2.of(q)

    def _fn_synth(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) != 1: raise SurdError("S-E004", f"{name}(gate)", pos)
        g = self.gv(args[0], env)
        if name == "synth": return self.synth(g, pos)
        if g.arity is None: raise SurdError("S-E009", f"{name} needs a fixed-size gate", pos)
        ops = self.gate_ops(g, list(range(g.arity)), pos, 0)
        if name == "gcount": return R2(len(ops))
        return R2(sum(1 for (n, ps, t, c) in ops if clifford_class(n, ps, c) == "T"))

    def _fn_stabilizers(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        qs = qs_of(args) if args else list(range(self.state.n))
        gens, full, count = self.stabilizers(qs, pos)
        if name == "is_stabilizer": return full
        text = " ".join(gens) if gens else "(none)"
        return text if full else f"{text}  (partial: {count} of {1 << len(qs)} Paulis stabilize)"

    def _fn_len(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) == 1 and not (args[0]["k"] == "ref" and args[0]["lo"] is None and (
                args[0]["name"] in self.qregs or args[0]["name"] in self.cregs or
                isinstance(env.get(args[0]["name"]), (QB, QR)))):
            v = self.ev(args[0], env)
            if isinstance(v, (list, str, dict)): return R2(len(v))
            if isinstance(v, Realm): return R2(len(v.recs))
            if isinstance(v, QR): return R2(len(v.idx))
            raise SurdError("S-E009", f"len needs a list, text, map, realm or register, got {type_name(v)}", pos)
        if len(args) != 1 or args[0]["k"] != "ref" or args[0]["lo"] is not None:
            raise SurdError("S-E004", "len(register)", pos)
        nm = args[0]["name"]
        if isinstance(self.globals.get(nm), Realm): return R2(len(self.globals[nm].recs))
        if isinstance(env.get(nm), QR): return R2(len(env[nm].idx))
        if isinstance(env.get(nm), QB): return R2(1)
        if nm in self.qregs and not env.get("__gate__"): return R2(len(self.qregs[nm]))
        if nm in self.cregs: return R2(len(self.cregs[nm]))
        raise SurdError("S-E002", f"unknown register '{nm}'", pos)

    def _fn_expval(self, name, args, env, pos):
        qs_of = lambda nodes: self._qs_of(nodes, env, name, pos)
        if len(args) < 2: raise SurdError("S-E004", 'expval(qubits…, "ZZ")', pos)
        qs = qs_of(args[:-1])
        ps = self.ev(args[-1], env)
        if not isinstance(ps, str) or len(ps) != len(qs) or set(ps) - set("IXYZ"):
            raise SurdError("S-E009", f"expval needs a {len(qs)}-letter Pauli string of I X Y Z", pos)
        return self.state.expval(qs, ps)



FUNCS1 = {"sqrt", "cos", "sin", "abs", "float"}
READS_STATE = {"prob", "purity", "entanglement", "linear_entropy", "bloch_x", "bloch_y", "bloch_z", "norm", "expval"}
FUNCS = FUNCS1 | {"qasm", "xor", "classical", "quantum", "load", "fidelity", "anchor", "agreements", "collisions", "field", "has", "at", "deriv", "synth", "tcount", "gcount", "stabilizers", "is_stabilizer",
                  "prob", "purity", "entanglement", "linear_entropy", "bloch_x", "bloch_y", "bloch_z", "norm", "len", "expval",
                  }


# ───────────────────────────── synthesis search ─────────────────────────────
_GM = {"h": ((1 / SQRT2, 1 / SQRT2), (1 / SQRT2, -1 / SQRT2)), "x": ((0, 1), (1, 0)), "y": ((0, -1j), (1j, 0)),
       "z": ((1, 0), (0, -1)), "s": ((1, 0), (0, 1j)), "sdg": ((1, 0), (0, -1j)),
       "t": ((1, 0), (0, cmath.exp(1j * math.pi / 4))), "tdg": ((1, 0), (0, cmath.exp(-1j * math.pi / 4))),
       "sx": ((0.5 + 0.5j, 0.5 - 0.5j), (0.5 - 0.5j, 0.5 + 0.5j)),
       "sxdg": ((0.5 - 0.5j, 0.5 + 0.5j), (0.5 + 0.5j, 0.5 - 0.5j))}


def _key(M):
    """matrix up to global phase, rounded: the search's hash (every hit is re-proved exactly)"""
    flat = [x for row in M for x in row]
    ref = next(x for x in flat if abs(x) > 1e-9)
    ph = abs(ref) / ref
    return tuple((round((x * ph).real, 9) + 0.0, round((x * ph).imag, 9) + 0.0) for x in flat)


def _mm(A, B):
    n = len(A)
    return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(n)) for j in range(n)) for i in range(n))


def synth_1q(target, max_t=12):
    """T-optimal single-wire Clifford+T synthesis: BFS by T-count over C·T·…·C, Cliffords by shortest name"""
    I = ((1, 0), (0, 1))
    cliffs, frontier = {_key(I): (I, [])}, [(I, [])]
    while frontier:                                 # all 24 single-wire Cliffords, shortest words
        nxt = []
        for M, w in frontier:
            for gname in ("h", "s", "sdg", "z", "x", "y", "sx", "sxdg"):
                M2 = _mm(_GM[gname], M)
                k = _key(M2)
                if k not in cliffs:
                    cliffs[k] = (M2, w + [gname]); nxt.append((M2, w + [gname]))
        frontier = nxt
    tk = _key(target)
    if tk in cliffs: return [(g, 0, ()) for g in cliffs[tk][1]]
    seen = dict(cliffs)
    layer = list(cliffs.values())
    T = _GM["t"]
    C = list(cliffs.values())
    for tcount in range(1, max_t + 1):
        new = []
        for M, w in layer:
            MT = _mm(T, M)
            for CM, cw in C:
                M2 = _mm(CM, MT)
                k = _key(M2)
                if k in seen: continue
                word = w + ["t"] + cw
                seen[k] = (M2, word); new.append((M2, word))
                if k == tk: return [(g, 0, ()) for g in word]
        layer = new
        if len(seen) > 3_000_000: return None
    return None


def _gens2():
    """two-wire generators: 1-wire gates on either wire, and CNOT both ways (as 4×4, wire 0 = low bit)"""
    out = []
    for name in ("h", "s", "sdg", "t", "tdg", "x", "z"):
        g = _GM[name]
        for w in (0, 1):
            M = [[0] * 4 for _ in range(4)]
            for i in range(4):
                for j in range(4):
                    if (i >> (1 - w) & 1) == (j >> (1 - w) & 1):
                        M[i][j] = g[i >> w & 1][j >> w & 1]
            out.append(((name, w, ()), tuple(tuple(r) for r in M)))
    for c, t in ((0, 1), (1, 0)):
        M = [[0] * 4 for _ in range(4)]
        for j in range(4):
            i = j ^ (1 << t) if j >> c & 1 else j
            M[i][j] = 1
        out.append((("x", t, (c,)), tuple(tuple(r) for r in M)))
    return out


def _inv(M):
    return tuple(tuple(M[j][i].conjugate() if isinstance(M[j][i], complex) else M[j][i] for j in range(len(M)))
                 for i in range(len(M)))


def synth_2q(target, fwd_depth=5, back_depth=9, cap=400_000):
    """shortest two-wire circuit (gate count) by meet-in-the-middle: forward words to depth 5, backward to 9"""
    G = _gens2()
    I = tuple(tuple(1 if i == j else 0 for j in range(4)) for i in range(4))
    fwd = {_key(I): []}
    layer = [(I, [])]
    for _ in range(fwd_depth):
        nxt = []
        for M, w in layer:
            for g, GM in G:
                M2 = _mm(GM, M)
                k = _key(M2)
                if k not in fwd:
                    fwd[k] = w + [g]; nxt.append((M2, w + [g]))
        layer = nxt
        if len(fwd) > cap: break
    T = tuple(tuple(complex(x) for x in row) for row in target)
    best = None
    seen_b = {_key(T)}
    blayer = [(T, [])]
    for j in range(back_depth + 1):
        for M, bw in blayer:
            k = _key(M)
            if k in fwd:
                cand = fwd[k] + list(reversed(bw))
                if best is None or len(cand) < len(best): best = cand
        if best is not None and j >= len(best): break
        nxt = []
        for M, bw in blayer:
            for g, GM in G:
                M2 = _mm(_inv(GM), M)
                k = _key(M2)
                if k not in seen_b:
                    seen_b.add(k); nxt.append((M2, bw + [g]))
        blayer = nxt
        if len(seen_b) > cap: break
    return best


# ───────────────────────────── realms: data as quantum states ─────────────────────────────
# A realm is a table of records loaded read-only. Each record is read in two incompatible bases:
#   anchor basis  — exact cents ⊗ a date spread over a window of days (a uniform superposition of 2w+1 days)
#   meaning basis — a uniform superposition over the record's informative words
# Fidelities |⟨a|b⟩|² are exact rationals. Records that are equal in the anchor basis and orthogonal in the
# meaning basis are COLLISIONS (the same money told two ways); equal in both are AGREEMENTS.

GENERIC_WORDS = {
    "the", "and", "for", "from", "with", "into", "onto", "per", "via", "this", "that", "was", "are", "not", "but",
    "deposit", "deposits", "check", "checks", "debit", "credit", "wire", "wires", "internet", "transfer", "online",
    "file", "number", "printed", "statement", "counter", "face", "acct", "account", "llc", "inc", "co", "corp",
    "payment", "paid", "pmt", "txn", "transaction", "ref", "memo", "none", "null", "nan", "unknown", "misc",
    "item", "items", "amount", "total", "fee", "fees"}


def words(text):
    out, cur = [], []
    for ch in (text or "").lower() + " ":
        if ch.isalpha(): cur.append(ch)
        else:
            if len(cur) >= 3:
                w = "".join(cur)
                if w not in GENERIC_WORDS: out.append(w)
            cur = []
    return frozenset(out)


def _cents(v):
    if v is None or v == "": return None
    # Preserve direction and avoid converting integer/decimal inputs through float.
    # Accounting parentheses represent a negative amount.
    t = str(v).replace(",", "").replace("$", "").strip()
    if t.startswith("(") and t.endswith(")"): t = "-" + t[1:-1].strip()
    try: return F(t)
    except (ValueError, ZeroDivisionError): return None


def _day(v):
    import datetime as _dt
    if not v: return None
    t = str(v).strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%m/%d/%Y", 10), ("%m/%d/%y", 8)):
        try: return _dt.datetime.strptime(t[:n], fmt).date().toordinal()
        except ValueError: pass
    return None


def _display_amount(value):
    """Exact half-even rounding for presentation; works before Fraction.__format__."""
    if value is None:
        return "?"
    cents = round(abs(value) * 100)
    return f"${'-' if value < 0 else ''}{cents // 100:,}.{cents % 100:02d}"


class Record:
    __slots__ = ("realm", "i", "row", "cents", "day", "mean", "loc")

    def __init__(self, realm, i, row):
        self.realm, self.i, self.row = realm, i, row
        self.cents = _cents(row.get("amount"))
        self.day = _day(row.get("date"))
        self.mean = words(str(row.get("meaning") or ""))
        self.loc = str(row.get("locator") or "").strip()

    def __str__(self):
        import datetime as _dt
        d = _dt.date.fromordinal(self.day).isoformat() if self.day else "?"
        a = _display_amount(self.cents)
        return f"<{self.realm.name}[{self.i}] {d} {a} “{str(self.row.get('meaning') or '').strip()[:60]}” {self.loc}>"


class Realm:
    def __init__(self, name, rows, source):
        self.name, self.source = name, source
        self.recs = [Record(self, i, r) for i, r in enumerate(rows)]

    def __str__(self): return f"<realm {self.name}: {len(self.recs)} records from {self.source}>"


def load_rows(path, query, pos):
    import csv as _csv, sqlite3 as _sql
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        raise SurdError("S-E002", f"no file at {path}", pos, "give a path to a .sqlite, .csv or .json file")
    if p.endswith((".sqlite", ".db", ".sqlite3")):
        if not query: raise SurdError("S-E004", "a SQLite realm needs a query", pos,
                                      'load("x.sqlite", "select date, amount, … as meaning, … as locator from t")')
        con = _sql.connect(f"file:{p}?mode=ro", uri=True)          # read-only: a realm never writes
        try:
            cur = con.execute(query)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        except _sql.Error as e:
            raise SurdError("S-E009", f"the query failed: {e}", pos)
        finally:
            con.close()
        return rows
    if p.endswith(".csv"):
        with open(p, newline="") as f: return list(_csv.DictReader(f))
    if p.endswith(".json"):
        with open(p) as f: data = json.load(f)
        if isinstance(data, dict) and query:                     # load("file.json", "key"): a named list
            if not isinstance(data.get(query), list):
                raise SurdError("S-E005", f"{path} has no list named {query!r}", pos,
                                "lists: " + ", ".join(k for k, v in data.items() if isinstance(v, list)))
            data = data[query]
        elif isinstance(data, dict): data = next((v for v in data.values() if isinstance(v, list)), [])
        return [r for r in data if isinstance(r, dict)]
    raise SurdError("S-E009", "a realm loads .sqlite, .csv or .json", pos)


def record_value(r, col):
    v = r.row.get(col)
    if col == "date" and r.day: return Date(r.day)                          # a date, so windows compare (1.7)
    if col == "amount": return Money(r.cents) if r.cents is not None else NONE   # money, so $ literals and sums work (1.7)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try: return R2(F(str(v)))
        except (ValueError, ZeroDivisionError): return str(v)
    return "" if v is None else str(v)


def anchor_fid(a, b, w):
    """|⟨anchor a|anchor b⟩|²: exact cents must match; dates are uniform windows of 2w+1 days"""
    if a.cents is None or b.cents is None or a.day is None or b.day is None or a.cents != b.cents: return F0
    d = abs(a.day - b.day)
    if d > 2 * w: return F0
    return F(2 * w + 1 - d, 2 * w + 1) ** 2


def meaning_fid(a, b):
    """|⟨meaning a|meaning b⟩|² for uniform word superpositions: |A∩B|² / (|A|·|B|)"""
    if not a.mean or not b.mean: return None          # one side carries no informative words: unlabeled
    k = len(a.mean & b.mean)
    return F(k * k, len(a.mean) * len(b.mean))


def realm_compare(A, B, w, agree_at=F(1, 4)):
    """every anchor-coupled pair across two realms, classified in the meaning basis and by locator"""
    index = {}
    for r in B.recs:
        if r.cents is not None and r.day is not None: index.setdefault(r.cents, []).append(r)
    pairs = []
    for a in A.recs:
        for b in index.get(a.cents, ()):
            fa = anchor_fid(a, b, w)
            if not fa: continue
            fm = meaning_fid(a, b)
            if fm is None: kind = "UNLABELED"
            elif fm >= agree_at: kind = "AGREE"
            elif fm == 0: kind = "COLLIDE"
            else: kind = "PARTIAL"
            src = "SAME" if a.loc and b.loc and a.loc == b.loc else ("DIFFER" if a.loc and b.loc else "?")
            pairs.append((kind, fa, fm, a, b, src))
    matched_a = {id(p[3]) for p in pairs}
    matched_b = {id(p[4]) for p in pairs}
    lone_a = [r for r in A.recs if id(r) not in matched_a and r.cents is not None]
    lone_b = [r for r in B.recs if id(r) not in matched_b and r.cents is not None]
    return pairs, lone_a, lone_b


def grover_realm(N, marked):
    """Grover search over N records (padded to 2^n) with M marked. The state stays in the span of
    |marked⟩ and |unmarked⟩, so two exact amplitudes carry the whole 2^n-dimensional evolution."""
    n = max(1, (N - 1).bit_length())
    D = 1 << n
    M = len(marked)
    if M == 0: return n, 0, R2(), []
    a0 = R2(F(1, 1 << (n // 2))) * (R2(0, F(1, 2)) if n % 2 else R2(1))   # 2^{-n/2} exactly
    am, au = a0, a0
    theta = math.asin(math.sqrt(M / D))
    k = max(0, int(math.floor(math.pi / (4 * theta))))
    history = []
    two, Dr, Mr = R2(2), R2(D), R2(M)
    for step in range(k):
        am = -am                                            # oracle: phase-flip the marked records
        mean = (Mr * am + (Dr - Mr) * au) / Dr              # diffusion: invert about the mean
        am, au = two * mean - am, two * mean - au
        history.append(Mr * am * am)
    return n, k, Mr * am * am, history


def swap_test(A, B):
    """the swap-test circuit on two records' meaning states: P(ancilla = 0) = 1/2 + F/2"""
    vocab = sorted(A.mean | B.mean)
    if not vocab: return None
    n = max(1, (len(vocab) - 1).bit_length())
    if 2 * n + 1 > 13: raise SurdError("S-E010", f"swaptest needs {2 * n + 1} wires; 13 is the limit", None,
                                       "the two records share too large a vocabulary")
    pos_of = {w: i for i, w in enumerate(vocab)}
    S = PureState()
    S.add_qubits(2 * n + 1)

    def inv_sqrt(k):                                        # 1/√k exactly when k = m² or 2m²
        m = math.isqrt(k)
        if m * m == k: return QW.from_r2(R2(F(1, m)))
        m = math.isqrt(k // 2)
        if k % 2 == 0 and 2 * m * m == k: return QW.from_r2(R2(0, F(1, 2 * m)))
        return None
    amp = inv_sqrt(len(A.mean) * len(B.mean))               # the product state's one amplitude
    if amp is None:
        S.to_float()
        amp = complex(1 / math.sqrt(len(A.mean) * len(B.mean)))
    z = S.zero()
    S.v = [z] * (1 << (2 * n + 1))
    for wa in A.mean:
        for wb in B.mean:
            S.v[(pos_of[wa] << 1) | (pos_of[wb] << (n + 1))] = amp
    hm, xm = Mat("h", ()), Mat("x", ())
    S.apply(hm, 0, ())
    for j in range(n):                                      # controlled swap of qubit j of A with qubit j of B
        qa, qb = 1 + j, n + 1 + j
        S.apply(xm, qa, (qb,)); S.apply(xm, qb, (0, qa)); S.apply(xm, qa, (qb,))
    S.apply(hm, 0, ())
    return S.prob_bits([0], "0"), 2 * n + 1, S.exact


# ───────────────────────────── games: questions answered by qubit strategies ─────────────────────────────
# A nonlocal game: players who cannot talk each get an input and give a one-bit answer; a referee checks a rule.
# Classical value: every deterministic strategy, exhaustively. Quantum value: the players share a GHZ state
# (a Bell pair for two) and each picks, per input, a measurement rotation from 128 exact candidates
# rz(jπ/4) >> ry(kπ/8) — or a fixed answer. An AI search (coordinate ascent with random restarts, seeded with the
# best classical strategy) finds the strategy; its value is then re-derived in exact cyclotomic arithmetic.

def _game_candidates():
    """exact and float 2×2 matrices of the measurement rotations, plus the two fixed answers"""
    cands = [("fix", 0, "answer 0"), ("fix", 1, "answer 1")]
    for j in range(8):
        for k in range(16):
            ex = Mat("ry", (PiVal(F(k, 8)),)).ex
            rz = Mat("rz", (PiVal(F(j, 4)),)).ex
            # M = ry · rz (rz first): measuring Z after M measures the rotated basis
            a, b, c, d = ry_m = ex
            p, q, r, s_ = rz
            M = (a * p + b * r, a * q + b * s_, c * p + d * r, c * q + d * s_)
            lab = ("" if j == 0 else f"rz({fmt_angle(PiVal(F(j, 4)))}) >> ") + f"ry({fmt_angle(PiVal(F(k, 8)))})"
            if j == 0 and k == 0: lab = "measure Z"
            cands.append(("rot", (M, tuple(complex(x) for x in M)), lab, (j, k)))
    return cands


_GAME_CANDS = None


def game_tables(g):
    """allowed input tuples and the win table: win[(inputs, outputs)] -> bool"""
    import itertools
    ranges = [range(n) for n in g["sizes"]]
    allowed, win = [], {}
    k = len(g["sizes"])
    for inp in itertools.product(*ranges):
        if not g["promise_fn"](inp): continue
        allowed.append(inp)
        for out in itertools.product((0, 1), repeat=k):
            win[(inp, out)] = g["win_fn"](inp, out)
    return allowed, win


def game_classical(g, allowed, win):
    import itertools
    k = len(g["sizes"])
    per_player = [list(itertools.product((0, 1), repeat=n)) for n in g["sizes"]]   # input -> answer maps
    best, arg = F(-1), None
    for strat in itertools.product(*per_player):
        v = sum(1 for inp in allowed if win[(inp, tuple(strat[p][inp[p]] for p in range(k)))])
        if F(v, len(allowed)) > best: best, arg = F(v, len(allowed)), strat
    count = 1
    for pp in per_player: count *= len(pp)
    return best, arg, count


def game_eval(g, allowed, win, strat, exact):
    """win probability of a quantum strategy on the shared GHZ state, exact (R2) or float"""
    import itertools
    k = len(g["sizes"])
    cands = _GAME_CANDS
    h = QH if exact else 1 / SQRT2
    total = R2() if exact else 0.0
    one, zero = (Q1, QZ) if exact else (1 + 0j, 0j)
    for inp in allowed:
        moves = [cands[strat[p][inp[p]]] for p in range(k)]
        for raw in itertools.product((0, 1), repeat=k):
            t0, t1 = one, one                               # ⟨raw| (⊗U) |0…0⟩ and ⟨raw| (⊗U) |1…1⟩
            for p, mv in enumerate(moves):
                if mv[0] == "fix":
                    u0, u1 = (one, zero) if raw[p] == 0 else (zero, one)   # identity: measure, then ignore
                else:
                    M = mv[1][0] if exact else mv[1][1]
                    u0, u1 = M[2 * raw[p]], M[2 * raw[p] + 1]
                t0, t1 = t0 * u0, t1 * u1
            amp = (t0 + t1) * h
            pr = amp.norm2() if exact else (amp.real ** 2 + amp.imag ** 2)
            out = tuple(moves[p][1] if moves[p][0] == "fix" else raw[p] for p in range(k))
            if win[(inp, out)]: total = total + pr
    return total / R2(len(allowed)) if exact else total / len(allowed)


def _coord_scores(g, allowed, win, s, p, i, tabs):
    """win probability of every candidate move at coordinate (player p, input i), the rest of s held fixed.
    Only inputs where player p sees i change; the others add a constant. All candidates are scored at once."""
    import itertools
    k = len(g["sizes"])
    M0, M1, FIX, FB, np_ = tabs
    cands = _GAME_CANDS
    nc = len(cands)
    const = 0.0
    acc = np_.zeros(nc) if np_ else [0.0] * nc
    for inp in allowed:
        moves = [cands[s[q][inp[q]]] for q in range(k)]
        if inp[p] != i:
            const += _eval_one(moves, inp, win, k); continue
        others = [q for q in range(k) if q != p]
        for rr in itertools.product((0, 1), repeat=k - 1):
            a0, a1 = 1 + 0j, 1 + 0j
            outs = [0] * k
            for q, rq in zip(others, rr):
                mv = moves[q]
                if mv[0] == "fix":
                    u0, u1 = (1, 0) if rq == 0 else (0, 1); outs[q] = mv[1]
                else:
                    M = mv[1][1]; u0, u1 = M[2 * rq], M[2 * rq + 1]; outs[q] = rq
                a0 *= u0; a1 *= u1
            for rp in (0, 1):
                outs[p] = rp
                w_rot = win[(inp, tuple(outs))]
                outs[p] = 0; w_f0 = win[(inp, tuple(outs))]
                outs[p] = 1; w_f1 = win[(inp, tuple(outs))]
                if np_:
                    amp = a0 * M0[:, rp] + a1 * M1[:, rp]
                    pr = (amp.real ** 2 + amp.imag ** 2) * 0.5
                    wv = np_.where(FIX, np_.where(FB == 0, w_f0, w_f1), w_rot)
                    acc += pr * wv
                else:
                    for c in range(nc):
                        amp = a0 * M0[c][rp] + a1 * M1[c][rp]
                        w = (w_f0 if FB[c] == 0 else w_f1) if FIX[c] else w_rot
                        if w: acc[c] += (amp.real ** 2 + amp.imag ** 2) * 0.5
    n = len(allowed)
    return [(const + x) / n for x in (acc.tolist() if np_ else acc)]


def _eval_one(moves, inp, win, k):
    import itertools
    tot = 0.0
    for raw in itertools.product((0, 1), repeat=k):
        t0, t1 = 1 + 0j, 1 + 0j
        for q, mv in enumerate(moves):
            if mv[0] == "fix":
                u0, u1 = (1, 0) if raw[q] == 0 else (0, 1)
            else:
                M = mv[1][1]; u0, u1 = M[2 * raw[q]], M[2 * raw[q] + 1]
            t0 *= u0; t1 *= u1
        out = tuple(moves[q][1] if moves[q][0] == "fix" else raw[q] for q in range(k))
        if win[(inp, out)]:
            a = t0 + t1
            tot += (a.real ** 2 + a.imag ** 2) * 0.5
    return tot


def _game_tabs():
    """candidate tables: amplitude columns M0[c][r] = M_c[r][0], M1[c][r] = M_c[r][1]; fixed answers act as identity"""
    try:
        import numpy as np_
    except ImportError:
        np_ = None
    rows0, rows1, fix, fb = [], [], [], []
    for c in _GAME_CANDS:
        if c[0] == "fix":
            rows0.append((1, 0)); rows1.append((0, 1)); fix.append(True); fb.append(c[1])
        else:
            M = c[1][1]; rows0.append((M[0], M[2])); rows1.append((M[1], M[3])); fix.append(False); fb.append(0)
    if np_ and not os.environ.get("SURD_NO_NUMPY"):
        return (np_.array(rows0, dtype=complex), np_.array(rows1, dtype=complex), np_.array(fix), np_.array(fb), np_)
    return (rows0, rows1, fix, fb, None)


def game_quantum(g, allowed, win, rng, classical_arg, restarts=24):
    global _GAME_CANDS
    if _GAME_CANDS is None: _GAME_CANDS = _game_candidates()
    k = len(g["sizes"])
    nc = len(_GAME_CANDS)
    tabs = _game_tabs()
    coords = [(p, i) for p in range(k) for i in range(g["sizes"][p])]
    starts = [[[classical_arg[p][i] for i in range(g["sizes"][p])] for p in range(k)]]   # fixed answers 0/1
    for _ in range(restarts - 1):
        starts.append([[rng.randrange(nc) for _ in range(g["sizes"][p])] for p in range(k)])
    best_v, best_s, evals = -1.0, None, 0
    game_quantum.finals = []                                # every restart's final score (for variance plots)
    for s in starts:
        s = [list(r) for r in s]
        v = game_eval(g, allowed, win, s, False); evals += 1
        improved = True
        while improved:
            improved = False
            for p, i in coords:
                sc = _coord_scores(g, allowed, win, s, p, i, tabs); evals += nc - 1
                keep = s[p][i]
                for c in range(nc):                         # first strictly better candidate at the maximum
                    if c != keep and sc[c] > v + 1e-12 and sc[c] >= max(sc) - 1e-12:
                        v, keep, improved = sc[c], c, True
                        break
                s[p][i] = keep
        game_quantum.finals.append(v)
        if v > best_v + 1e-12: best_v, best_s = v, [list(r) for r in s]
    exact_v = game_eval(g, allowed, win, best_s, True)
    return exact_v, best_s, evals, restarts


def ops_unitary(ops, k):
    """columns of the exact (or float) unitary of a primitive op list on k qubits"""
    mats = [(Mat(n, ps), t, c) for (n, ps, t, c) in ops]
    exact = all(m.ex is not None for m, _, _ in mats)
    cols = []
    for j in range(1 << k):
        st = PureState(force_float=not exact)
        st.add_qubits(k)
        st.v = [st.zero()] * (1 << k)
        st.v[j] = st.one()
        for m, t, c in mats: st.apply(m, t, c)
        cols.append(st.v)
    return cols, exact


def unitaries_match(A, B, up_to_phase):
    (ca, ea), (cb, eb) = A, B
    exact = ea and eb
    a = [x for col in ca for x in col]
    b = [x for col in cb for x in col]
    if not exact:
        a = [complex(x) for x in a]; b = [complex(x) for x in b]
        zero = lambda x: abs(x) < 1e-9
    else:
        zero = lambda x: x.is_zero()
    if not up_to_phase:
        return all(zero(x - y) for x, y in zip(a, b))
    i0 = next((i for i, x in enumerate(a) if not zero(x)), None)
    if i0 is None or zero(b[i0]): return False
    return all(zero(y * a[i0] - x * b[i0]) for x, y in zip(a, b))


def fn1(name, v, pos):
    if name == "float":
        return float(v) if isinstance(v, (R2, PiVal, float)) else _terr(pos, name)
    if name in ("cos", "sin"):
        try:
            q = EX.cos(v) if name == "cos" else EX.sin(v)
            return q.re()
        except NotExact:
            pass
        if isinstance(v, (R2, PiVal, float)):
            return (math.cos if name == "cos" else math.sin)(float(v))
        return _terr(pos, name)
    if name == "abs":
        if isinstance(v, R2): return -v if v.sign() < 0 else v
        if isinstance(v, float): return abs(v)
        return _terr(pos, name)
    if name == "sqrt":
        if isinstance(v, R2) and v.is_rational() and v.a >= 0:
            a = v.a
            for mult, mk in ((1, lambda r: R2(r)), (2, lambda r: R2(0, r))):
                x = a / mult
                n, d = math.isqrt(x.numerator), math.isqrt(x.denominator)
                if n * n == x.numerator and d * d == x.denominator:
                    return mk(F(n, d))
        if isinstance(v, (R2, float)) and float(v) >= 0:
            return math.sqrt(float(v))
        return _terr(pos, name)
    return _terr(pos, name)


def _terr(pos, name):
    raise SurdError("S-E009", f"{name} cannot take that value", pos)


def poly_arith(op, a, b, pos):
    A, B = Poly.coerce(a), Poly.coerce(b)
    if A is None or B is None:
        raise SurdError("S-E009", f"'{op}' mixes a formula with something that is not a number", pos)
    if op == "+": return A + B
    if op == "-": return A - B
    if op == "*": return A * B
    if op == "/": return A / B
    if op == "^":
        if not (B.is_const() and R2.of(B.const()).is_rational() and R2.of(B.const()).a.denominator == 1
                and 0 <= R2.of(B.const()).a <= 64):
            raise SurdError("S-E009", "a formula's power must be a whole number 0..64", pos)
        return A ** int(R2.of(B.const()).a)


def money_arith(op, a, b, pos):
    if isinstance(a, Date) or isinstance(b, Date):
        if op == "-" and isinstance(a, Date) and isinstance(b, Date): return R2(a.n - b.n)
        if op in "+-" and isinstance(a, Date) and isinstance(b, R2) and b.is_rational() and b.a.denominator == 1:
            return Date(a.n + int(b.a) if op == "+" else a.n - int(b.a))
        raise SurdError("S-E009", f"'{op}' does not apply to {type_name(a)} and {type_name(b)}", pos,
                        "dates subtract to days; add or subtract whole days")
    if isinstance(a, Money) and isinstance(b, Money):
        if op == "+": return Money(a.d + b.d)
        if op == "-": return Money(a.d - b.d)
        if op == "/":
            if b.d == 0: raise SurdError("S-E015", "division by $0.00", pos)
            return R2(a.d / b.d)
        raise SurdError("S-E009", f"money {op} money is not defined", pos, "money adds, subtracts, and divides to a ratio")
    m, x = (a, b) if isinstance(a, Money) else (b, a)
    if isinstance(x, R2) and x.is_rational():
        if op == "*": return Money(m.d * x.a)
        if op == "/" and m is a:
            if x.a == 0: raise SurdError("S-E015", "division by zero", pos)
            return Money(m.d / x.a)
    raise SurdError("S-E009", f"'{op}' does not apply to {type_name(a)} and {type_name(b)}", pos,
                    "money times or divided by a number; money plus money")


def arith(op, a, b, pos):
    if isinstance(a, Bound) or isinstance(b, Bound):
        if isinstance(a, Bound) and isinstance(b, Bound) and op in "+-" and a.money == b.money:
            return Bound(a.lo + b.lo, a.hi + b.hi, a.money) if op == "+" else Bound(a.lo - b.hi, a.hi - b.lo, a.money)
        bb, x = (a, b) if isinstance(a, Bound) else (b, a)
        if op in "+-" and isinstance(x, Money) and bb.money:
            if op == "+": return Bound(bb.lo + x.d, bb.hi + x.d)
            if bb is a: return Bound(bb.lo - x.d, bb.hi - x.d)
            return Bound(x.d - bb.hi, x.d - bb.lo)
        if op == "*" and isinstance(x, R2) and x.is_rational():
            return Bound(bb.lo * x.a, bb.hi * x.a, bb.money)
        raise SurdError("S-E009", f"'{op}' does not apply to a bound and {type_name(x)}", pos,
                        "bounds add, subtract, shift by money and scale; for joint bounds over many outflows use set_bounds")
    if isinstance(a, (Money, Date)) or isinstance(b, (Money, Date)):
        if op == "+" and (isinstance(a, str) or isinstance(b, str)):
            return (a if isinstance(a, str) else fmt_short(a)) + (b if isinstance(b, str) else fmt_short(b))
        return money_arith(op, a, b, pos)
    if isinstance(a, Meaning) or isinstance(b, Meaning):
        if op in ("+", "-") and isinstance(a, Meaning) and isinstance(b, Meaning):
            if a.spaces != b.spaces: raise SurdError("S-E009", "only meanings over the same space add", pos)
            amps = dict(a.amps)
            for k, v in b.amps.items():
                v2 = v if op == "+" else (_mul(v, QW.from_r2(R2(-1))) if isinstance(v, QW) else -complex(v))
                amps[k] = _mix(amps[k], v2) if k in amps else v2
            return Meaning(a.spaces, amps)
        if op == "*" and isinstance(b, Meaning) and isinstance(a, (R2, float)):
            return Meaning(b.spaces, {k: _mul(_amp(a), v) for k, v in b.amps.items()})
        raise SurdError("S-E009", f"'{op}' does not apply to meanings that way", pos, "meanings add (+) and scale (k * m)")
    if op == "+" and isinstance(a, list) and isinstance(b, list): return a + b
    if op == "+" and (isinstance(a, str) or isinstance(b, str)):
        return (a if isinstance(a, str) else fmt_short(a)) + (b if isinstance(b, str) else fmt_short(b))
    if op == "*" and isinstance(a, list) and isinstance(b, R2) and b.is_rational() and b.a.denominator == 1:
        return a * int(b.a)
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)) or a is NONE or b is NONE:
        raise SurdError("S-E009", f"'{op}' does not apply to {type_name(a)} and {type_name(b)}", pos)
    if type(a) is Poly or type(b) is Poly:
        return poly_arith(op, a, b, pos)
    if isinstance(a, (str, bool)) or isinstance(b, (str, bool)):
        raise SurdError("S-E009", f"cannot use '{op}' on text or true/false", pos)
    try:
        if isinstance(a, R2) and isinstance(b, R2):
            if op == "+": return a + b
            if op == "-": return a - b
            if op == "*": return a * b
            if op == "/": return a / b
            if op == "%":
                if not (a.is_rational() and b.is_rational() and not b.is_zero()):
                    raise SurdError("S-E009", "% takes two rational numbers, the second nonzero", pos)
                return R2(a.a - b.a * (a.a / b.a).__floor__())
            if op == "^":
                if b.is_rational() and b.a.denominator == 1:
                    n, r = int(b.a), R2(1)
                    base = a if n >= 0 else a.inv()
                    for _ in range(abs(n)): r = r * base
                    return r
                r = float(a) ** float(b)
                if isinstance(r, complex):
                    raise SurdError("S-E009", f"{a} ^ {b} is not a real number", pos)
                return r
        if isinstance(a, PiVal) or isinstance(b, PiVal):
            ra = isinstance(a, R2) and a.is_rational()
            rb = isinstance(b, R2) and b.is_rational()
            if op in "+-" and isinstance(a, PiVal) and isinstance(b, PiVal):
                return PiVal(a.k + b.k if op == "+" else a.k - b.k)
            if op == "*" and isinstance(a, PiVal) and rb: return PiVal(a.k * b.a)
            if op == "*" and isinstance(b, PiVal) and ra: return PiVal(b.k * a.a)
            if op == "/" and isinstance(a, PiVal) and rb: return PiVal(a.k / b.a)
            if op == "/" and isinstance(a, PiVal) and isinstance(b, PiVal): return R2(a.k / b.k)
        x, y = float(a), float(b)
        if op == "+": r = x + y
        elif op == "-": r = x - y
        elif op == "*": r = x * y
        elif op == "/": r = x / y
        else: r = x ** y
        if isinstance(r, complex):
            raise SurdError("S-E009", f"{x:g} ^ {y:g} is not a real number", pos)
        if math.isinf(r) or math.isnan(r):
            raise SurdError("S-E009", "number too large", pos)
        return r
    except ZeroDivisionError:
        raise SurdError("S-E015", "division by zero", pos, "check the divisor")
    except OverflowError:
        raise SurdError("S-E009", "number too large", pos)


def values_equal(a, b):
    if isinstance(a, (Money, Date, Bound)) or isinstance(b, (Money, Date, Bound)): return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    if isinstance(a, R2) and isinstance(b, R2): return (a - b).is_zero()
    return a is b if (a is NONE or b is NONE) else a == b


def int_bits(v, width, pos):
    if not (v.is_rational() and v.a.denominator == 1 and 0 <= v.a < (1 << width)):
        raise SurdError("S-E009", f"{v} is not a {width}-bit whole number", pos, 'or write the bits: "0101"')
    return format(int(v.a), f"0{width}b")


def _iso(t):
    return bool(re.fullmatch(r"\d{4}-\d\d-\d\d", t.strip()[:10])) and len(t.strip()) in (10, 19, 20, 25)


def compare(op, a, b, pos):
    if isinstance(a, str) and isinstance(b, str) and op not in ("==", "!=") and _iso(a) and _iso(b):
        a, b = Date.parse(a, pos), Date.parse(b, pos)                         # ISO dates written as text order as dates (1.7)
    if isinstance(a, Bound) or isinstance(b, Bound):
        if op in ("==", "!="): return (a == b) if op == "==" else not (a == b)
        raise SurdError("S-E009", "a bound has no single order", pos, "use within(x, b), lo(b) or hi(b)")
    if isinstance(a, (Money, Date)) or isinstance(b, (Money, Date)):
        if isinstance(a, Money) and isinstance(b, Money): x, y = a.d, b.d
        elif isinstance(a, Date) and isinstance(b, Date): x, y = a.n, b.n
        elif isinstance(a, Money) and isinstance(b, R2) and b.is_zero(): x, y = a.d, 0
        elif isinstance(b, Money) and isinstance(a, R2) and a.is_zero(): x, y = 0, b.d
        elif isinstance(a, Money) and isinstance(b, R2) and b.is_rational(): x, y = a.d, b.a   # compare by value; + and - stay strict (1.7)
        elif isinstance(b, Money) and isinstance(a, R2) and a.is_rational(): x, y = a.a, b.d
        elif isinstance(a, Date) and isinstance(b, str) and _iso(b): x, y = a.n, Date.parse(b, pos).n
        elif isinstance(b, Date) and isinstance(a, str) and _iso(a): x, y = Date.parse(a, pos).n, b.n
        else:
            if op in ("==", "!="): return op == "!="
            raise SurdError("S-E009", f"cannot order {type_name(a)} against {type_name(b)}", pos)
        return {"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
    if isinstance(a, Meaning) and isinstance(b, Meaning):
        if op not in ("==", "!="): raise SurdError("S-E009", "meanings compare with == and !=", pos)
        f_ = meaning_fidelity(a, b, pos)
        same = (f_ - R2(1)).is_zero() if isinstance(f_, R2) else abs(f_ - 1) < 1e-12
        return same if op == "==" else not same
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)) or a is NONE or b is NONE:
        if op not in ("==", "!="): raise SurdError("S-E009", f"{type_name(a)} values compare only with == and !=", pos)
        same = values_equal(a, b)
        return same if op == "==" else not same
    if type(a) is Poly or type(b) is Poly:
        A, B = Poly.coerce(a), Poly.coerce(b)
        if A is None or B is None: raise SurdError("S-E009", "a formula compares only with numbers or formulas", pos)
        if op in ("==", "!="): return (A == B) if op == "==" else not (A == B)
        d = A - B
        if d.is_const(): s_ = R2.of(d.const()).sign(); return {"<": s_ < 0, "<=": s_ <= 0, ">": s_ > 0, ">=": s_ >= 0}[op]
        raise SurdError("S-E009", "a formula has no order until its symbols have values", pos,
                        "compare at(P, p, 1/100) < …, or test == between formulas")
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(a, R2) and isinstance(b, str): a = int_bits(a, len(b), pos)
        if isinstance(b, R2) and isinstance(a, str): b = int_bits(b, len(a), pos)
        if not (isinstance(a, str) and isinstance(b, str)) or op not in ("==", "!="):
            raise SurdError("S-E009", "bit strings compare only with == and !=", pos)
        return (a == b) if op == "==" else (a != b)
    if isinstance(a, bool) or isinstance(b, bool):
        if op not in ("==", "!="): raise SurdError("S-E009", "true/false compare only with == and !=", pos)
        return (a == b) if op == "==" else (a != b)
    if isinstance(a, PiVal) and isinstance(b, PiVal):
        a, b = R2(a.k), R2(b.k)
    if isinstance(a, R2) and isinstance(b, R2):
        s = (a - b).sign()
    else:
        d = float(a) - float(b)
        s = 0 if abs(d) <= TOL * max(1.0, abs(float(a)), abs(float(b))) else (1 if d > 0 else -1)
    return {"==": s == 0, "!=": s != 0, "<": s < 0, "<=": s <= 0, ">": s > 0, ">=": s >= 0}[op]


# ───────────────────────────── circuit drawing ─────────────────────────────
def op_label(name, ps, ctrls):
    if name == "x" and ctrls: return "⊕"
    lab = LABEL.get(name, name.upper())
    if ps: lab += "(" + ",".join(fmt_angle(p) for p in ps) + ")"
    return lab


def draw(trace, qnames, cnames):
    n = len(qnames)
    if n == 0: return ["(no qubits)"]
    nxt = [0] * n
    cols = []
    for op in trace:
        cells = {}
        if op[0] == "g":
            _, name, ps, t, ctrls = op
            cells[t] = op_label(name, ps, ctrls)
            for c in ctrls: cells[c] = "●"
        elif op[0] == "m":
            cells[op[1]] = "M" + ("" if op[2] is None else "→" + cnames[op[2]])
        elif op[0] == "r":
            cells[op[1]] = "|0>"
        elif op[0] == "n":
            cells[op[3]] = "~" + {"depolarize": "D", "dephase": "Φ", "bitflip": "B", "pauli": "P",
                                  "ampdamp": "A"}[op[1]] + "(" + ",".join(fmt_angle(p) for p in op[2]) + ")"
        elif op[0] == "b":
            for q in op[1]: cells[q] = "░"
        rows = list(cells)
        lo, hi = min(rows), max(rows)
        vertical = op[0] == "g" and len(rows) > 1
        span = range(lo, hi + 1) if vertical else rows
        col = max(nxt[r] for r in span)
        for r in span: nxt[r] = col + 1
        while len(cols) <= col: cols.append({})
        for r in span:
            cols[col][r] = cells.get(r, "┼")
    width = max(len(s) for s in qnames)
    out = []
    for r in range(n):
        line = qnames[r].rjust(width) + ": ─"
        for c in cols:
            w = max([len(s) for s in c.values()] + [1])
            s = c.get(r)
            if s is None:
                line += "─" * (w + 2)
            else:
                left = (w - len(s)) // 2
                line += "─" * (left + 1) + s + "─" * (w - len(s) - left + 1)
        out.append(line + "─")
    return out


# ───────────────────────────── stats & optimizer ─────────────────────────────
def circuit_stats(trace, n):
    counts, level = Counter(), [0] * n
    tcount = multi = meas = noise = other = 0
    for op in trace:
        if op[0] == "g":
            _, name, ps, t, c = op
            counts["c" * len(c) + name] += 1
            kind = clifford_class(name, ps, c)
            if kind == "T": tcount += 1
            elif kind == "other": other += 1
            if c: multi += 1
            qs = (t,) + tuple(c)
        elif op[0] == "m": meas += 1; qs = (op[1],)
        elif op[0] == "r": qs = (op[1],)
        elif op[0] == "n": noise += 1; qs = (op[3],)
        else:
            if op[1]:
                top = max(level[q] for q in op[1])
                for q in op[1]: level[q] = top
            continue
        L = max(level[q] for q in qs) + 1
        for q in qs: level[q] = L
    return {"qubits": n, "gates": sum(counts.values()), "multi_qubit": multi, "t_count": tcount,
            "non_clifford_other": other,
            "depth": max(level, default=0), "measurements": meas, "noise_ops": noise,
            "by_gate": dict(sorted(counts.items()))}


def clifford_class(name, ps, ctrls):
    """'clifford', 'T' (one uncontrolled odd-π/4 phase or rotation) or 'other' non-Clifford"""
    if name in ("id", "h", "x", "y", "z", "s", "sdg", "sx", "sxdg"):
        if not ctrls: return "clifford"
        return "clifford" if len(ctrls) == 1 and name in ("x", "y", "z") else "other"   # cx cy cz; ch, ccx are not
    if name in ("t", "tdg"): ang = PHASE_ANGLE[name]
    elif name in ("p", "rz", "rx", "ry"): ang = ps[0]
    else: return "other"
    m = eighth(ang)
    if m is None: return "other"
    if not ctrls: return "clifford" if m % 2 == 0 else "T"
    return "clifford" if len(ctrls) == 1 and name == "p" and m % 4 == 0 else "other"   # cp(π) = cz


def fmt_stats(st):
    out = [f"stats  {st['qubits']} qubit(s) · {st['gates']} gates · depth {st['depth']} · T-count {st['t_count']}"
           f" · other non-Clifford {st['non_clifford_other']}"
           f" · multi-qubit {st['multi_qubit']} · measurements {st['measurements']}"
           + (f" · noise {st['noise_ops']}" if st["noise_ops"] else "")]
    out.append("  " + "  ".join(f"{k}×{v}" for k, v in st["by_gate"].items()) if st["by_gate"] else "  (no gates)")
    return out


PHASE_ANGLE = {"z": PiVal(1), "s": PiVal(F(1, 2)), "sdg": PiVal(F(-1, 2)), "t": PiVal(F(1, 4)),
               "tdg": PiVal(F(-1, 4))}
PHASE_NAME = {1: "t", 2: "s", 4: "z", 6: "sdg", 7: "tdg"}


def vals_equal(a, b):
    return all(type(x) is type(y) and x == y for x, y in zip(a, b)) and len(a) == len(b)


def _period_zero(v, periods):
    """True when angle v is an exact multiple of periods*pi (the gate is exactly the identity)"""
    if isinstance(v, R2): return v.is_zero()
    if isinstance(v, PiVal): return (v.k / periods).denominator == 1
    return False


def combine(a, b):
    """merge two adjacent gates on the same target and controls: None (no rule), [] (cancel) or [op]"""
    _, n1, p1, t, c = a
    _, n2, p2, _, _ = b
    adj = adjoint((n1, p1, t, c))
    if adj[0] == n2 and vals_equal(adj[1], p2):
        return []
    if n1 in PHASE_ANGLE or n1 == "p":
        if n2 in PHASE_ANGLE or n2 == "p":
            ang = ang_add(p1[0] if n1 == "p" else PHASE_ANGLE[n1], p2[0] if n2 == "p" else PHASE_ANGLE[n2])
            m = eighth(ang)
            if m is not None:
                m %= 8
                if m == 0: return []
                if m in PHASE_NAME: return [("g", PHASE_NAME[m], (), t, c)]
                return [("g", "p", (PiVal(F(m, 4)),), t, c)]
            return [("g", "p", (ang,), t, c)]
    if n1 == n2 and n1 in ("rx", "ry", "rz"):
        ang = ang_add(p1[0], p2[0])
        if _period_zero(ang, 4): return []
        return [("g", n1, (ang,), t, c)]
    return None


def op_qubits(op):
    if op[0] == "g": return {op[3], *op[4]}
    if op[0] in ("m", "r"): return {op[1]}
    if op[0] == "n": return {op[3]}
    return set(op[1])


def optimize(trace):
    ops = [op for op in trace if not (op[0] == "g" and op[1] == "id")]
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(ops):
            op = ops[i]
            if op[0] != "g":
                i += 1; continue
            qs = op_qubits(op)
            j = next((j for j in range(i + 1, len(ops)) if op_qubits(ops[j]) & qs), None)
            nx = ops[j] if j is not None else None
            if nx and nx[0] == "g" and nx[3] == op[3] and set(nx[4]) == set(op[4]) and len(nx[4]) == len(op[4]):
                merged = combine(op, nx)
                if merged is not None:
                    del ops[j]
                    ops[i:i + 1] = merged
                    changed = True
                    continue
            i += 1
    return ops


def prove_same(before, after, n):
    """exact proof (float with tolerance off the exact set) that two gate-only traces are the same unitary"""
    kinds = {op[0] for op in before + after} - {"g"}
    if kinds:
        why = " and ".join(w for k, w in (("n", "has noise"), ("m", "measures"), ("r", "resets"), ("b", "has barriers"))
                           if k in kinds)
        return f"not checked — the circuit {why}; every rewrite is local to adjacent gates"
    if n > 8:
        return f"not checked — {n} qubits is over the 8-qubit proof limit"
    A = ops_unitary([op[1:] for op in before], n)
    B = ops_unitary([op[1:] for op in after], n)
    ok = unitaries_match(A, B, up_to_phase=False)
    exact = A[1] and B[1]
    kind = "exact" if exact else "float, 1e-9"
    status = ("PROVED equal" if exact else "NUMERICALLY VERIFIED equal") if ok else "FAILED — not equal"
    return f"{status} ({kind}, all {1 << n} basis inputs)"


def surd_angle(v):
    if isinstance(v, Poly): return v.source()
    if isinstance(v, PiVal): return qasm_angle(v)
    if isinstance(v, R2) and v.is_rational(): return fmt_frac(v.a)
    return f"float({fmt_frac(F(float(v)))})"           # the float's exact binary value; re-parses bit-for-bit


def emit_surd(it, ops):
    out = []
    syms = sorted(n for n, v in it.globals.items() if type(v) is Poly and len(v.t) == 1
                  and list(v.t) == [((n, 1),)])
    if syms: out.append("symbol " + ", ".join(syms))
    for name, idx in it.qregs.items():
        out.append(f"wire {name}" if it.qnames[idx[0]] == name else f"wires {name}: {len(idx)}")
    for name, idx in it.cregs.items():
        out.append(f"bit {name}" if it.cnames[idx[0]] == name else f"bits {name}: {len(idx)}")
    qn, cn = it.qnames, it.cnames
    for op in ops:
        if op[0] == "g":
            _, name, ps, t, c = op
            par = "(" + ", ".join(surd_angle(p) for p in ps) + ")" if ps else ""
            wires = [qn[q] for q in tuple(c) + (t,)]
            lhs = wires[0] if len(wires) == 1 else "(" + ", ".join(wires) + ")"
            out.append(f"{lhs} <- {'ctl ' * len(c)}{name}{par}")
        elif op[0] == "m":
            out.append(f"measure {qn[op[1]]}" if op[2] is None else f"{cn[op[2]]} <- measure {qn[op[1]]}")
        elif op[0] == "r":
            out.append(f"{qn[op[1]]} <- reset")
        elif op[0] == "n":
            out.append(f"{qn[op[3]]} <- {op[1]}({', '.join(surd_angle(p) for p in op[2])})")
        elif op[0] == "b":
            out.append("barrier " + ", ".join(qn[q] for q in op[1]))
    return out


# ───────────────────────────── OpenQASM export ─────────────────────────────
CTRL1 = {"x": "cx", "y": "cy", "z": "cz", "h": "ch", "p": "cu1", "rz": "crz", "rx": "crx", "ry": "cry", "u": "cu3"}
PHASE_EQ = {"s": PiVal(F(1, 2)), "sdg": PiVal(F(-1, 2)), "t": PiVal(F(1, 4)), "tdg": PiVal(F(-1, 4))}


def qasm_angle(v):
    if isinstance(v, PiVal):
        k = v.k
        if k == 0: return "0"
        n, d = k.numerator, k.denominator
        s = ("-" if n < 0 else "") + ("pi" if abs(n) == 1 else f"{abs(n)}*pi") + ("" if d == 1 else f"/{d}")
        return s
    return repr(float(v))


def to_qasm(it, allow_trace=False):
    if it.used_if and not allow_trace:
        raise SurdError("S-E013", "the program uses measurement-dependent classical execution", None,
                       "--trace exports only the path and parameters of this one run")
    loc = {}
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";', f"// exported by surd {VERSION}"]
    if it.used_if: lines.append("// unrolled trace of one run (measurement-dependent execution already resolved)")
    definitions = {
        "sx": "gate sx a { h a; s a; h a; }",
        "sxdg": "gate sxdg a { h a; sdg a; h a; }",
        "crx": "gate crx(theta) c,t { h t; crz(theta) c,t; h t; }",
        "cry": "gate cry(theta) c,t { ry(theta/2) t; cx c,t; ry(-theta/2) t; cx c,t; }",
    }
    needed = {CTRL1.get(op[1], op[1]) if len(op[4]) == 1 else op[1]
              for op in it.trace if op[0] == "g"}
    lines.extend(definitions[name] for name in definitions if name in needed)
    for name, idx in it.qregs.items():
        lines.append(f"qreg {name}[{len(idx)}];")
        for j, i in enumerate(idx): loc[i] = f"{name}[{j}]"
    cloc = {}
    for name, idx in it.cregs.items():
        lines.append(f"creg {name}[{len(idx)}];")
        for j, i in enumerate(idx): cloc[i] = f"{name}[{j}]"
    for op in it.trace:
        if op[0] == "g":
            _, name, ps, t, ctrls = op
            if ctrls and name in PHASE_EQ:
                name, ps = "p", (PHASE_EQ[name],)
            if not ctrls:
                base = {"p": "u1", "u": "u3"}.get(name, name)
            elif len(ctrls) == 1 and name in CTRL1:
                base = CTRL1[name]
            elif len(ctrls) == 2 and name == "x":
                base = "ccx"
            else:
                raise SurdError("S-E013", f"{len(ctrls)}-controlled {name} has no qelib1 gate", None,
                               "decompose it into cx / ccx first")
            args = ",".join(loc[q] for q in tuple(ctrls) + (t,))
            par = "(" + ",".join(qasm_angle(p) for p in ps) + ")" if ps else ""
            lines.append(f"{base}{par} {args};")
        elif op[0] == "m":
            if op[2] is None:
                raise SurdError("S-E013", "measure without a target bit", None, "write c <- measure q")
            lines.append(f"measure {loc[op[1]]} -> {cloc[op[2]]};")
        elif op[0] == "r":
            lines.append(f"reset {loc[op[1]]};")
        elif op[0] == "n":
            raise SurdError("S-E013", f"noise channel {op[1]} has no OpenQASM 2 form", None,
                           "export the noiseless program; noise belongs to the backend")
        elif op[0] == "b":
            lines.append(f"barrier {','.join(loc[q] for q in op[1])};")
    return "\n".join(lines)


# ───────────────────────────── money: exact cents, dates, tracing, books, relationships ─────────────────────────────
# Money is exact dollars (a Fraction: pro-rata shares keep their fractional cents). Dates are calendar days.
# Tracing follows the conventions the case's other engines established (Tally tally_math.py, Spectra):
# two or more pools, an overdraft is a DEBT repaid by the next deposits before any pool is credited, same-day order
# deposits-first by default ("withdrawals_first" also counts intraday dips), LIBR = everyone else's money spent first.

class Money:
    __slots__ = ("d",)

    def __init__(self, d): self.d = d if type(d) is F else F(d)

    def __str__(self):
        d = self.d
        neg = d < 0
        a = abs(d)
        cents = a * 100
        whole = cents.numerator // cents.denominator
        frac = cents - whole
        s = f"${whole // 100:,}.{whole % 100:02d}"
        if frac: s += f" + {fmt_frac(frac)}¢"
        return ("-" if neg else "") + s

    def __eq__(self, o): return isinstance(o, Money) and o.d == self.d
    def __hash__(self): return hash(("$", self.d))


class Date:
    __slots__ = ("n",)

    def __init__(self, n): self.n = n

    @staticmethod
    def parse(text, pos=None):
        import datetime as _dt
        try:
            return Date(_dt.date.fromisoformat(str(text)[:10]).toordinal())
        except ValueError:
            raise SurdError("S-E009", f"{text!r} is not a date", pos, "dates are written @2040-01-01")

    def __str__(self):
        import datetime as _dt
        return _dt.date.fromordinal(self.n).isoformat()

    def __eq__(self, o): return isinstance(o, Date) and o.n == self.n
    def __hash__(self): return hash(("@", self.n))


def money_of(v, pos):
    if isinstance(v, Money): return v.d
    if isinstance(v, R2) and v.is_rational(): return v.a
    if isinstance(v, str):
        t = v.replace("$", "").replace(",", "").strip()
        neg = t.startswith("(") and t.endswith(")")
        try:
            x = F(t.strip("()"))
            return -x if neg else x
        except (ValueError, ZeroDivisionError):
            pass
    raise SurdError("S-E009", f"expected money, got {type_name(v)}", pos, "write money as $1,234.56")


def date_of(v, pos):
    if isinstance(v, Date): return v.n
    if isinstance(v, str): return Date.parse(v, pos).n
    if isinstance(v, Record) and v.day: return v.day
    raise SurdError("S-E009", f"expected a date, got {type_name(v)}", pos, "write dates as @2040-01-01")


def norm_events(evs, pos):
    """events: maps {date, amount, cls[, page]} (or realm records) -> [(i, day, amount F, cls, page)]"""
    out = []
    for i, e in enumerate(evs):
        if isinstance(e, Record):
            row = e.row
            d, a, c, pg = e.day, F(e.cents) if e.cents is not None else None, row.get("cls") or row.get("owner") or "", e.loc
        elif isinstance(e, dict):
            d = date_of(e.get("date"), pos); a = money_of(e.get("amount"), pos)
            c = e.get("cls") or e.get("owner") or ""; pg = e.get("page") or e.get("locator") or ""
        else:
            raise SurdError("S-E009", f"an event is a map {{date, amount, cls}}, got {type_name(e)}", pos)
        if d is None or a is None: continue
        out.append((i, d, a, str(c), str(pg)))
    return out


def by_day_order(evs, order):
    days = {}
    for e in evs: days.setdefault(e[1], []).append(e)
    out = []
    for d in sorted(days):
        ins = [x for x in days[d] if x[2] > 0]
        outs = [x for x in days[d] if x[2] < 0]
        out += (ins + outs) if order == "deposits_first" else (outs + ins)
    return out


def two_pool(evs, order, cls, charge_first, stop=None):
    """replay with a class pool C and an others pool O; withdrawals charged to `charge_first` first"""
    C = O = deficit = F0
    traj = []
    for e in by_day_order(evs, order):
        i, day, amt, c, pg = e
        if stop is not None and i == stop: return C, O, traj
        if amt > 0:
            cover = min(deficit, amt); deficit -= cover; amt -= cover
            if c in cls: C += amt
            else: O += amt
        else:
            need = -amt
            for pool in (("C", "O") if charge_first == "C" else ("O", "C")):
                have = C if pool == "C" else O
                take = min(have, need)
                if pool == "C": C -= take
                else: O -= take
                need -= take
            deficit += need
        traj.append((day, C, O))
    return C, O, traj


def tranche_trace(evs, order, doctrine):
    """multi-class tracing: every outflow's composition {cls: amount}. fifo/lifo spend deposit lots oldest/newest
    first; prorata spends every class in proportion to the pool. Overdraft is a debt repaid by the next deposits."""
    lots = []                       # [cls, remaining] in arrival order
    pool = {}                       # prorata: cls -> amount
    deficit = F0
    out = []
    for e in by_day_order(evs, order):
        i, day, amt, c, pg = e
        if amt > 0:
            cover = min(deficit, amt); deficit -= cover; amt -= cover
            if amt > 0:
                if doctrine == "prorata": pool[c] = pool.get(c, F0) + amt
                else: lots.append([c, amt])
            continue
        need, comp = -amt, {}
        if doctrine == "prorata":
            tot = sum(pool.values(), F0)
            take_all = min(tot, need)
            if tot > 0:
                for k in list(pool):
                    share = pool[k] * take_all / tot
                    pool[k] -= share
                    if share: comp[k] = comp.get(k, F0) + share
            need -= take_all
        else:
            seq = lots if doctrine == "fifo" else list(reversed(lots))
            for lot in seq:
                if need <= 0: break
                take = min(lot[1], need)
                if take:
                    lot[1] -= take; need -= take
                    comp[lot[0]] = comp.get(lot[0], F0) + take
            lots[:] = [l for l in lots if l[1] > 0]
        if need > 0:
            comp["(overdraft)"] = need
            deficit += need
        out.append((i, day, amt, comp))
    return out


def share_bounds(evs, day, amount, cls):
    idx = [e[0] for e in evs if e[1] == day and e[2] == amount]
    if not idx: return None
    t = -amount
    floors, ceils = [], []
    for order in ("deposits_first", "withdrawals_first"):
        C1, O1, _ = two_pool(evs, order, cls, "C", idx[0])
        C2, O2, _ = two_pool(evs, order, cls, "O", idx[0])
        floors.append(min(max(F0, t - O1), C1))
        ceils.append(min(t, C2))
    return min(floors), max(ceils)


def subset_sums(items, target, k, limit=500):
    """every set of at most k items (by position) whose sum is exactly target: meet in the middle"""
    import itertools as _it
    n = len(items)
    half = k // 2
    small = {}
    for r in range(0, half + 1):
        for combo in _it.combinations(range(n), r):
            small.setdefault(sum((items[i] for i in combo), F0), []).append(combo)
    sols = set()
    for r in range(0, k - half + 1):
        for combo in _it.combinations(range(n), r):
            need = target - sum((items[i] for i in combo), F0)
            for other in small.get(need, ()):
                if set(other) & set(combo): continue
                s = tuple(sorted(set(other) | set(combo)))
                if 0 < len(s) <= k: sols.add(s)
        if len(sols) > limit: break
    return sorted(sols, key=lambda s: (len(s), s))


class Bound:
    """a closed interval of money or numbers: what holds under every convention (floor .. ceiling)"""
    __slots__ = ("lo", "hi", "money")

    def __init__(self, lo, hi, money=True):
        if lo > hi: lo, hi = hi, lo
        self.lo, self.hi, self.money = lo, hi, money

    def wrap(self, x): return Money(x) if self.money else R2(x)
    def __str__(self): return f"{self.wrap(self.lo)} .. {self.wrap(self.hi)}"
    def __eq__(self, o): return isinstance(o, Bound) and (o.lo, o.hi) == (self.lo, self.hi)
    def __hash__(self): return hash(("[]", self.lo, self.hi))


def set_replay(evs, order, cls, flags, least):
    """the class money that can reach ALL flagged outflows together: least=True spends the class first on everything
    else (and others first on the targets); least=False spends the class first on the targets (Tally set_bounds)"""
    K = O = deficit = F0
    k_in = total = F0
    for e in by_day_order(evs, order):
        i, day, amt, c, pg = e
        if amt > 0:
            cover = min(deficit, amt); deficit -= cover; amt -= cover
            if c in cls: K += amt
            else: O += amt
            continue
        need = -amt
        target = flags.get(i, False)
        if target: total += need
        k_first = (not target) if least else target
        for pool in (("K", "O") if k_first else ("O", "K")):
            have = K if pool == "K" else O
            take = min(have, need)
            if pool == "K":
                K -= take
                if target: k_in += take
            else:
                O -= take
            need -= take
        deficit += need
    return k_in, total


def _ext_context():
    """the types surd_ext needs, handed over so it never imports this module"""
    import types as _types
    return _types.SimpleNamespace(SurdError=SurdError, Realm=Realm, Record=Record, Money=Money, Date=Date, R2=R2, NONE=NONE, Bound=Bound,
                                  Space=Space, money_of=money_of, date_of=date_of, type_name=type_name,
                                  two_pool=two_pool, norm_events=norm_events,
                                  fmt_value=lambda x: x if isinstance(x, str) else str(x))


def _raw_field(e, k):
    if isinstance(e, dict): return str(e.get(k) or "")
    if isinstance(e, Record): return str(e.row.get(k) or (e.row.get("description") if k == "memo" else "") or "")
    return ""


def butfor_replay(evs, order, removed):
    """replay the account with the `removed` deposits taken out: every outflow the remaining money cannot cover,
    the uncovered part, and the running balance (the bank's overdraft becomes visible)"""
    bal = F0
    rows = []
    butfor_replay.low = (F0, None)
    for e in by_day_order(evs, order):
        i, day, amt, c, pg = e
        if i in removed: continue
        before = bal
        bal += amt
        if bal < butfor_replay.low[0]: butfor_replay.low = (bal, day)
        if amt < 0 and bal < 0:
            uncovered = min(-amt, -bal) if before >= 0 else -amt
            rows.append((i, day, amt, uncovered, bal, pg))
    return rows, bal


# ───────────────────────────── semantics: meanings as qubit states ─────────────────────────────
# A space is a set of labelled senses, laid on basis states of ⌈log2 k⌉ wires. A meaning is a state over a space
# (or over a product of two spaces: a joint meaning, e.g. word ⊗ audience). Amplitudes come from weights,
# α_v = √(w_v / Σw), exact whenever the square roots lie in Loom's fields; probabilities, fidelity and purity are
# then exact. The semantic layer is a precise notation for meaning — no claim is made that meaning is physical —
# but `encode` puts a meaning on real wires, where gates, noise and measurement act on it like any other state.

class Space:
    __slots__ = ("name", "labels", "nbits")

    def __init__(self, name, labels):
        self.name, self.labels = name, list(labels)
        self.nbits = max(1, (len(self.labels) - 1).bit_length())

    def index(self, label, pos=None):
        if label not in self.labels:
            near = difflib.get_close_matches(str(label), self.labels, 2)
            raise SurdError("S-E002", f"space {self.name} has no sense {label!r}", pos,
                            f"did you mean {', '.join(near)}?" if near else "senses: " + ", ".join(self.labels))
        return self.labels.index(label)

    def __str__(self): return f"<space {self.name} {{{', '.join(self.labels)}}}>"


def _amp_from_weight(w):
    """√w exactly when w (a rational) is m² or 2m² times a square ratio; else a float"""
    r = fn1("sqrt", R2(w), None) if w >= 0 else None
    return r


class Meaning:
    """amplitudes over the senses of one space, or over pairs (a, b) of two spaces (a joint meaning)"""
    __slots__ = ("spaces", "amps")

    def __init__(self, spaces, amps):
        self.spaces = tuple(spaces)
        amps = {k: v for k, v in amps.items() if not _is_zero_amp(v)}
        if not all(isinstance(v, QW) for v in amps.values()):     # one kind per meaning: exact or float
            amps = {k: complex(v) for k, v in amps.items()}
        self.amps = amps

    @property
    def exact(self): return all(isinstance(v, QW) for v in self.amps.values())

    def norm(self):
        tot = R2() if self.exact else 0.0
        for v in self.amps.values():
            tot = tot + (v.norm2() if isinstance(v, QW) else abs(complex(v)) ** 2)
        return tot

    def probs(self):
        n = self.norm()
        out = {}
        for k, v in self.amps.items():
            p = v.norm2() if isinstance(v, QW) else abs(complex(v)) ** 2
            out[k] = (p / n) if isinstance(p, R2) and isinstance(n, R2) else float(p) / float(n)
        return out

    def label(self, k):
        return k if len(self.spaces) == 1 else f"{k[0]}⊗{k[1]}"

    def __str__(self):
        if not self.amps: return "<meaning 0>"
        parts = []
        for k, v in self.amps.items():
            a = str(v) if isinstance(v, QW) else fmt_float(complex(v))
            parts.append(f"({a})|{self.label(k)}>" if (" " in a) else f"{a}|{self.label(k)}>")
        return " + ".join(parts) + f"   in {' ⊗ '.join(s.name for s in self.spaces)}"


def _is_zero_amp(v):
    return v.is_zero() if isinstance(v, QW) else abs(complex(v)) < 1e-15


def _amp(v):
    """an exact real or float as an amplitude"""
    if isinstance(v, QW): return v
    if isinstance(v, R2): return v.q
    return complex(float(v))


def _mix(a, b):
    """add two amplitudes of possibly different kinds"""
    if isinstance(a, QW) and isinstance(b, QW): return a + b
    return complex(a) + complex(b)


def _mul(a, b):
    if isinstance(a, QW) and isinstance(b, QW): return a * b
    return complex(a) * complex(b)


def _conj(a): return a.conj() if isinstance(a, QW) else complex(a).conjugate()


def normalize_meaning(m, pos=None):
    """Return a new unit vector, preserving relative phase and leaving the input untouched."""
    n = m.norm()
    if isinstance(n, R2):
        if n.sign() <= 0:
            raise SurdError("S-E009", "cannot normalize a zero meaning", pos, "choose a condition with positive probability")
        root = fn1("sqrt", n, pos)
        if isinstance(root, R2):
            inverse = root.inv().q
            return Meaning(m.spaces, {k: v * inverse for k, v in m.amps.items()})
    else:
        root = math.sqrt(n) if math.isfinite(n) and n > 0 else None
    if root is None or not math.isfinite(float(root)) or float(root) <= 0:
        raise SurdError("S-E009", "meaning norm must be finite and positive", pos,
                        "rescale amplitudes or choose a condition with positive probability")
    return Meaning(m.spaces, {k: complex(v) / float(root) for k, v in m.amps.items()})


def meaning_fidelity(m1, m2, pos):
    """|⟨m1|m2⟩|² / (⟨m1|m1⟩⟨m2|m2⟩): 1 = the same meaning, 0 = orthogonal"""
    for m in (m1, m2):
        if not isinstance(m, Meaning):
            raise SurdError("S-E009", f"fidelity compares two meanings; got {type_name(m)}", pos,
                            "read(…) gives none when no cue fires — check for none first" if m is NONE else None)
    if m1.spaces != m2.spaces:
        raise SurdError("S-E009", "fidelity compares meanings over the same space(s)", pos,
                        f"{' ⊗ '.join(s.name for s in m1.spaces)} vs {' ⊗ '.join(s.name for s in m2.spaces)}")
    ip = QZ if (m1.exact and m2.exact) else 0j
    for k, v in m1.amps.items():
        if k in m2.amps: ip = _mix(ip, _mul(_conj(v), m2.amps[k]))
    num = ip.norm2() if isinstance(ip, QW) else abs(ip) ** 2
    den = m1.norm() * m2.norm() if isinstance(m1.norm(), R2) and isinstance(m2.norm(), R2) else \
        float(m1.norm()) * float(m2.norm())
    if isinstance(num, R2) and isinstance(den, R2): return num / den
    return float(num) / float(den)


def meaning_reduced(m, side):
    """reduced density matrix of a joint meaning on one side (0 = first space, 1 = second)"""
    sp = m.spaces[side]
    labs = sp.labels
    rho = {}
    others = {}
    for (a, b), v in m.amps.items():
        mine, other = (a, b) if side == 0 else (b, a)
        others.setdefault(other, []).append((mine, v))
    for lst in others.values():
        for x, vx in lst:
            for y, vy in lst:
                rho[(x, y)] = _mix(rho.get((x, y), QZ if isinstance(vx, QW) else 0j), _mul(vx, _conj(vy)))
    return rho, labs


def meaning_purity(m):
    if len(m.spaces) == 1: return R2(1) if m.exact else 1.0
    rho, labs = meaning_reduced(m, 0)
    n = m.norm()
    tot = R2() if all(isinstance(v, QW) for v in rho.values()) else 0.0
    for v in rho.values():
        tot = tot + (v.norm2() if isinstance(v, QW) else abs(complex(v)) ** 2)
    if isinstance(tot, R2) and isinstance(n, R2): return tot / (n * n)
    return float(tot) / float(n) ** 2


def entropy_bits(probs):
    return -sum(float(p) * math.log2(float(p)) for p in probs if float(p) > 0)


def truth_discovery(claims, iters=50):
    """claims: (family, quantity, value). The numeric_spine rule (quantum_logic.py):
    c(v) = 1 − Π_{f ⊢ v}(1 − t_f); competing values of one quantity sharpen, c ← c²/Σc;
    t_f = mean c over the values f witnesses, clamped to [0.05, 0.99]; start 0.8; iterate to a fixed point.
    Trust is earned agreement, never a verdict: a family whose only witness is contradicted falls toward 0.05."""
    W = {}
    for f, q, v in claims: W.setdefault((q, v), set()).add(f)
    fams = sorted({f for s_ in W.values() for f in s_})
    t = {f: 0.8 for f in fams}
    c = {}
    for _ in range(iters):
        c = {key: 1 - math.prod(1 - t[f] for f in fs) for key, fs in W.items()}
        byq = {}
        for (q, v), x in c.items(): byq.setdefault(q, []).append(((q, v), x))
        for q, items in byq.items():
            if len(items) > 1:
                tot = sum(x for _, x in items)
                for key, x in items: c[key] = x * x / tot
        new_t = {f: max(0.05, min(0.99, sum(c[key] for key, fs in W.items() if f in fs) /
                                  sum(1 for key, fs in W.items() if f in fs))) for f in fams}
        if max(abs(new_t[f] - t[f]) for f in fams) < 1e-9:
            t = new_t
            break
        t = new_t
    return t, c


def belnap(witnessed, contradicted):
    return {(True, False): "T", (False, True): "F", (True, True): "B", (False, False): "N"}[(bool(witnessed), bool(contradicted))]


# ───────────────────────────── OpenQASM 2 import ─────────────────────────────
# OpenQASM 2.0 text -> a list of primitive ops on wires 0..n-1 (registers laid end to end, in declaration order),
# plus the measurements. Angles are re-read by Loom's own expression parser, so pi/4 stays exactly π/4.

QASM_1Q = {"id": ("id", 0), "x": ("x", 0), "y": ("y", 0), "z": ("z", 0), "h": ("h", 0), "s": ("s", 0),
           "sdg": ("sdg", 0), "t": ("t", 0), "tdg": ("tdg", 0), "sx": ("sx", 0), "sxdg": ("sxdg", 0),
           "rx": ("rx", 1), "ry": ("ry", 1), "rz": ("rz", 1), "p": ("p", 1), "u1": ("p", 1), "phase": ("p", 1),
           "u3": ("u", 3), "u": ("u", 3), "U": ("u", 3), "u2": ("u2", 2)}
QASM_CTL = {"cx": ("x", 1, 0), "CX": ("x", 1, 0), "cy": ("y", 1, 0), "cz": ("z", 1, 0), "ch": ("h", 1, 0),
            "crx": ("rx", 1, 1), "cry": ("ry", 1, 1), "crz": ("rz", 1, 1), "cu1": ("p", 1, 1), "cp": ("p", 1, 1),
            "cphase": ("p", 1, 1), "cu3": ("u", 1, 3), "ccx": ("x", 2, 0), "ccz": ("z", 2, 0)}


def _qasm_angle(text, pos, interp):
    t = text.strip().replace("π", "pi")
    if not t: raise SurdError("S-E001", "empty angle in OpenQASM", pos)
    node = Parser(lex(t)).expr()
    v = interp.ev(node, {"__depth__": 0})
    if not isinstance(v, (R2, PiVal, float)):
        raise SurdError("S-E009", f"OpenQASM angle {text!r} is not a number", pos)
    return v


def _split_top(s, sep=","):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip(): out.append(cur)
    return [x.strip() for x in out]


def qasm_parse(text, interp, pos=None):
    """returns (n_wires, ops, measures, regs): ops are (name, params, target, controls) on global wires"""
    import re as _re
    body = _re.sub(r"//[^\n]*", "", text)
    stmts = [x.strip() for x in body.replace("\n", " ").replace("}", "};").split(";")]
    qregs, cregs, n, nc = {}, {}, 0, 0
    gates = {}
    ops, measures = [], []
    i = 0
    flat = []
    # gate definitions contain { … } with inner ';' — re-join them
    buf = ""
    for st in stmts:
        if buf:
            buf += ";" + st
            if buf.count("{") == buf.count("}"): flat.append(buf); buf = ""
            continue
        if st.startswith("gate ") and st.count("{") != st.count("}"):
            buf = st; continue
        flat.append(st)
    if buf: raise SurdError("S-E001", "unclosed gate definition in OpenQASM", pos)

    def wires_of(arg):
        m = _re.fullmatch(r"([A-Za-z_]\w*)(?:\[(\d+)\])?", arg)
        if not m: raise SurdError("S-E001", f"OpenQASM argument {arg!r} is not a register or qubit", pos)
        name, idx = m.group(1), m.group(2)
        if name not in qregs: raise SurdError("S-E002", f"OpenQASM register {name!r} is not declared", pos)
        base, size = qregs[name]
        if idx is None: return list(range(base, base + size))
        if int(idx) >= size: raise SurdError("S-E005", f"OpenQASM index {arg} is out of range", pos)
        return [base + int(idx)]

    def emit(name, params, args, depth=0):
        """one gate application on resolved single wires"""
        if depth > 32: raise SurdError("S-E014", f"OpenQASM gate {name!r} nests too deeply", pos)
        if name in gates:
            fparams, fargs, gbody = gates[name]
            if len(params) != len(fparams) or len(args) != len(fargs):
                raise SurdError("S-E004", f"OpenQASM gate {name!r} called with the wrong arguments", pos)
            for st in [x.strip() for x in gbody.split(";") if x.strip()]:
                m = _re.fullmatch(r"([A-Za-z_]\w*)\s*(?:\((.*)\))?\s+(.+)", st)
                if not m: raise SurdError("S-E001", f"cannot read {st!r} in gate {name!r}", pos)
                sub = m.group(1)
                ptxt = _split_top(m.group(2)) if m.group(2) else []
                env = dict(zip(fparams, params))
                pv = []
                for ptx in ptxt:
                    for fp, val in env.items():       # substitute formal parameters by their exact values
                        ptx = _re.sub(rf"\b{fp}\b", f"({_qasm_value_text(val)})", ptx)
                    pv.append(_qasm_angle(ptx, pos, interp))
                amap = dict(zip(fargs, args))
                sub_args = []
                for a in [x.strip() for x in m.group(3).split(",")]:
                    if a not in amap: raise SurdError("S-E002", f"{a!r} is not an argument of gate {name!r}", pos)
                    sub_args.append(amap[a])
                emit(sub, pv, sub_args, depth + 1)
            return
        if name in ("swap",):
            a, b = args; ops.extend([("x", (), b, (a,)), ("x", (), a, (b,)), ("x", (), b, (a,))]); return
        if name in ("cswap",):
            c, a, b = args
            ops.extend([("x", (), a, (b,)), ("x", (), b, (c, a)), ("x", (), a, (b,))]); return
        if name in QASM_1Q:
            prim, k = QASM_1Q[name]
            if len(params) != k or len(args) != 1:
                raise SurdError("S-E004", f"OpenQASM {name} takes {k} angle(s) and 1 qubit", pos)
            if prim == "u2":
                prim, params = "u", [PiVal(F(1, 2))] + list(params)
            if prim != "id": ops.append((prim, tuple(params), args[0], ()))
            return
        if name in QASM_CTL:
            prim, nctl, k = QASM_CTL[name]
            if len(params) != k or len(args) != nctl + 1:
                raise SurdError("S-E004", f"OpenQASM {name} takes {k} angle(s) and {nctl + 1} qubits", pos)
            ops.append((prim, tuple(params), args[-1], tuple(args[:-1]))); return
        raise SurdError("S-E003", f"OpenQASM gate {name!r} is not in qelib1 and not defined in the file", pos)

    for st in flat:
        if not st: continue
        head = st.split(None, 1)[0]
        if head in ("OPENQASM", "include"): continue
        if head in ("qreg", "creg"):
            m = _re.fullmatch(r"(qreg|creg)\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]", st)
            if not m: raise SurdError("S-E001", f"cannot read {st!r}", pos)
            if m.group(1) == "qreg":
                qregs[m.group(2)] = (n, int(m.group(3))); n += int(m.group(3))
            else:
                cregs[m.group(2)] = (nc, int(m.group(3))); nc += int(m.group(3))
            continue
        if head == "gate":
            m = _re.fullmatch(r"gate\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s+([^{]+)\{(.*)\}", st, _re.S)
            if not m: raise SurdError("S-E001", f"cannot read gate definition {st[:40]!r}", pos)
            fparams = [x.strip() for x in m.group(2).split(",")] if m.group(2) else []
            gates[m.group(1)] = (fparams, [x.strip() for x in m.group(3).split(",")], m.group(4))
            continue
        if head == "barrier": continue
        if head == "measure":
            m = _re.fullmatch(r"measure\s+(\S+)\s*->\s*(\S+)", st)
            if not m: raise SurdError("S-E001", f"cannot read {st!r}", pos)
            qs = wires_of(m.group(1))
            cm = _re.fullmatch(r"([A-Za-z_]\w*)(?:\[(\d+)\])?", m.group(2))
            cb, cs = cregs[cm.group(1)]
            cbits = list(range(cb, cb + cs)) if cm.group(2) is None else [cb + int(cm.group(2))]
            measures.extend(zip(qs, cbits)); continue
        if head == "reset":
            for q in wires_of(st.split(None, 1)[1].strip()): measures.append((q, None))
            continue
        if head in ("if", "opaque"):
            raise SurdError("S-E013", f"OpenQASM '{head}' is not imported", pos, "import the gate part of the circuit")
        m = _re.fullmatch(r"([A-Za-z_]\w*)\s*(?:\((.*)\))?\s+(.+)", st)
        if not m: raise SurdError("S-E001", f"cannot read OpenQASM statement {st[:40]!r}", pos)
        name = m.group(1)
        params = [_qasm_angle(x, pos, interp) for x in _split_top(m.group(2))] if m.group(2) else []
        groups = [wires_of(a.strip()) for a in m.group(3).split(",")]
        L = max(len(g_) for g_ in groups)
        for k_ in range(L):                                 # registers broadcast as in OpenQASM
            emit(name, params, [g_[0] if len(g_) == 1 else g_[k_] for g_ in groups])
    return n, ops, measures, (qregs, cregs)


def _qasm_value_text(v):
    if isinstance(v, PiVal):
        k = v.k
        return f"{k.numerator}*pi/{k.denominator}"
    if isinstance(v, R2) and v.is_rational(): return f"{v.a.numerator}/{v.a.denominator}"
    return repr(float(v))


# ───────────────────────────── runners ─────────────────────────────
def creg_key(it):
    if not it.cregs: return ""
    parts = [(n, "".join(str(it.cbits[i]) for i in idx)) for n, idx in it.cregs.items()]
    return parts[0][1] if len(parts) == 1 else " ".join(f"{n}={b}" for n, b in parts)


def run_shots(src, shots, seed, force_float, force_mixed=False, budget=None):
    prog = parse(src)
    rng = random.Random(seed)
    hist, failed = Counter(), 0
    budget = budget if budget is not None else ExecutionBudget()
    with budget.execution():
        for _ in range(shots):
            budget.checkpoint()
            it = Interp(rng=rng, echo=False, force_float=force_float, shots_mode=True, force_mixed=force_mixed, budget=budget)
            it.exec_block(prog, it.globals, None)
            if not it.cregs:
                raise SurdError("S-E009", "--shots needs a bit register to count", None,
                               "declare bits c: n and write c <- measure q")
            hist[creg_key(it)] += 1
            failed += sum(1 for e in it.expects if not e["ok"])
    return hist, failed


def _fails_clean(it):
    """noise past the density limit must fail before touching the state"""
    try:
        it.run("wires big: 8\nnoise depolarize(1/10)\nq[1] <- h")
        return False
    except SurdError as e:
        if e.code != "S-E010": return False
    return it.state.kind == "pure" and it.state.prob_bits([it.qregs["q"][1]], "0") == R2(1)


def selftest(verbose=False):
    here = os.path.dirname(os.path.realpath(__file__))
    cases = []

    def case(name, src, check):
        cases.append((name, src, check))

    def ok_expects(it): return it.expects and all(e["ok"] for e in it.expects)

    # core
    case("bell exact 1/2", 'wires q: 2\nq[0] <- h\n(q[0], q[1]) <- cx\nexpect prob(q, "00") == 1/2\n'
         'expect prob(q, "11") == 1/2\nexpect prob(q, "01") == 0', ok_expects)
    case("H T H = 1/2 + √2/4, exact", 'wire q\nq <- h >> t >> h\nexpect prob(q, "0") == 1/2 + sqrt(2)/4',
         lambda it: ok_expects(it) and it.state.exact)
    case("bell linear entropy 1/2", 'wires q: 2\nq <- h & id >> cx\nexpect entanglement(q[0]) == 1/2\n'
         'expect purity(q) == 1', ok_expects)
    case("product state unentangled", 'wires q: 2\nq <- h\nexpect entanglement(q[0]) == 0', ok_expects)
    case("~ undoes a gate stack", 'gate w: a, b { a <- h; b <- t; (a, b) <- cx; a <- s; b <- sx; (a, b) <- cp(pi/4) }\n'
         'wires q: 2\nq <- w\nq <- ~w\nexpect prob(q, "00") == 1', ok_expects)
    case("ry(pi/2) exact, rx(0.3) floats", 'wire a, b\na <- ry(pi/2)\nexpect prob(a, "1") == 1/2\nb <- rx(0.3)\n'
         'expect prob(b, "1") == sin(0.15)^2', lambda it: ok_expects(it) and not it.state.exact and it.notes)
    case("bloch of |+>", 'wire q\nq <- h\nexpect bloch_x(q) == 1\nexpect bloch_z(q) == 0', ok_expects)
    case("zip a control over a register", 'wires q: 3\nq[0] <- x\n(q[0], q[1..3]) <- cx\nexpect prob(q, "111") == 1',
         ok_expects)
    case("for + classical if", 'wires q: 3\nbits c: 3\nfor i in 0..3 { q[i] <- x }\nc <- measure q\n'
         'if c == "111" { q[0] <- x }\nexpect c == 7\nexpect prob(q[0], "0") == 1', ok_expects)
    case("u(pi/2,0,pi) acts like h", 'wire a, b\na <- u(pi/2, 0, pi)\nb <- h\nexpect bloch_x(a) == bloch_x(b)\n'
         'expect prob(a, "0") == 1/2', ok_expects)
    case("swap", 'wires q: 2\nq[0] <- x\nq <- swap\nexpect prob(q, "01") == 1', ok_expects)
    case("cswap = ctl swap", 'wires q: 3\nq[0..2] <- x\nq <- cswap\nexpect prob(q, "101") == 1', ok_expects)
    # gate algebra (the Loom core)
    case("algebra identities", 'expect h >> z >> h == x\nexpect h >> x >> h == z\nexpect t ^ 8 == id\n'
         'expect t ^ 2 == s\nexpect sx ^ 2 == x\nexpect ~t == tdg\nexpect ~(h >> t) == tdg >> h\n'
         'expect (h & h) >> cz >> (h & h) =~ (h & h) >> cz >> (h & h)\nexpect id & h >> cx >> id & h == cz\n'
         'expect rz(pi) =~ z\nexpect not (rz(pi) == z)\nexpect rz(pi) != z\nexpect ctl ctl x == ccx\n'
         'expect cx >> (x & x) >> cx == x & id\nexpect ~cswap == cswap\nexpect u(pi/2, 0, pi) =~ h\n'
         'expect ctl (h >> t) == ctl h >> ctl t', ok_expects)
    case("algebraic gate definitions", 'gate bell = h & id >> cx\ngate rot(a) = ry(a) >> rz(a)\nwires q: 2\n'
         'q <- bell\nexpect prob(q, "11") == 1/2\nwire r\nr <- rot(pi/2) >> ~rot(pi/2)\nexpect prob(r, "0") == 1',
         ok_expects)
    case("tensor and broadcast agree", 'wires a: 2, b: 2\na <- h & x\nb[0] <- h\nb[1] <- x\n'
         'expect prob(a, "01") == prob(b, "01")', ok_expects)
    # noise
    case("dephase(1/2) kills coherence exactly", 'wire q\nq <- h\nq <- dephase(1/2)\nexpect bloch_x(q) == 0\n'
         'expect purity(q) == 1/2\nexpect prob(q, "0") == 1/2', ok_expects)
    case("depolarize(3/4) -> fully mixed", 'wire q\nq <- depolarize(3/4)\nexpect purity(q) == 1/2\n'
         'expect bloch_z(q) == 0', ok_expects)
    case("depolarize(1/10) shrinks bloch by 1-4p/3", 'wire q\nq <- depolarize(1/10)\nexpect bloch_z(q) == 13/15',
         ok_expects)
    case("bitflip(1/4)", 'wire q\nq <- bitflip(1/4)\nexpect prob(q, "1") == 1/4', ok_expects)
    case("ampdamp(1/2) exact (√(1/2) in Q(√2))", 'wire q\nq <- x\nq <- ampdamp(1/2)\nexpect prob(q, "0") == 1/2\n'
         'wire r\nr <- h\nr <- ampdamp(1/2)\nexpect bloch_x(r) == sqrt(2)/2', lambda it: ok_expects(it) and it.state.exact)
    case("noise model degrades a Bell pair", 'noise depolarize(1/100)\nwires q: 2\nq <- h & id >> cx\n'
         'expect prob(q, "00") < 1/2\nexpect prob(q, "00") > 49/100\nexpect purity(q) < 1\nnoise off\nq[0] <- x\n'
         'expect prob(q, "10") == prob(q[0], "1") - prob(q, "11")', ok_expects)
    case("noiseless mixed == pure", 'wires q: 2\nq[0] <- h >> t\nq <- cx\nq[1] <- bitflip(0)\n'
         'expect purity(q) == 1\nexpect bloch_y(q[0]) == 0\nexpect expval(q, "XX") == sqrt(2)/2',
         lambda it: ok_expects(it) and it.state.kind == "mixed")
    case("measure on a mixed state", 'wires q: 2\nbits c: 2\nq <- h & id >> cx\nq[0] <- dephase(1/3)\n'
         'c <- measure q\nexpect c == "00" or c == "11"', ok_expects)
    case("expval on a Bell pair", 'wires q: 2\nq <- h & id >> cx\nexpect expval(q, "ZZ") == 1\n'
         'expect expval(q, "XX") == 1\nexpect expval(q, "YY") == -1\nexpect expval(q[0], "Z") == 0', ok_expects)
    case("register gate + len + open slice", 'gate qft: r[] {\n  r[0] <- h\n  for k in 1..len(r) { (r[k], r[0]) <- cp(pi/2^k) }\n'
         '  if len(r) > 1 { r[1..] <- qft }\n}\nwires q: 3\nq[0] <- x\nq[2] <- x\nq <- qft\n'
         'gate qft3: a, b, c { a <- h; (b, a) <- cp(pi/2); (c, a) <- cp(pi/4); b <- h; (c, b) <- cp(pi/2); c <- h }\n'
         'q <- ~qft3\nexpect prob(q, "101") == 1', ok_expects)
    case("ghz via register gate", 'gate ghz: r[] { r[0] <- h; for i in 1..len(r) { (r[0], r[i]) <- cx } }\nwires q: 5\n'
         'q <- ghz\nexpect prob(q, "11111") == 1/2', ok_expects)

    def opt_check(it):
        after = optimize(it.trace)
        st = circuit_stats(after, it.state.n)
        return prove_same(it.trace, after, it.state.n).startswith("PROVED") and st["gates"] == 2
    case("optimizer cancels and merges, then proves", 'wires q: 2\nq[0] <- h >> h\nq[1] <- t >> t >> s\n'
         'q <- cx >> cx\nq[0] <- rz(pi/4) >> rz(-pi/4)\nq[1] <- x\nq[0] <- sdg >> s', opt_check)
    case("stats", 'wires q: 2\nq[0] <- h >> t\nq <- cx\nq[1] <- t',
         lambda it: circuit_stats(it.trace, 2)["t_count"] == 2 and circuit_stats(it.trace, 2)["depth"] == 4)

    def noise_traced(it):
        after = optimize(it.trace)
        st = circuit_stats(it.trace, it.state.n)
        return st["noise_ops"] == 1 and not prove_same(it.trace, after, 1).startswith("PROVED") and \
            any(op[0] == "n" for op in after)
    case("noise model is in the trace (opt/stats/qasm see it)", 'wire q\nnoise bitflip(1)\nq <- x\n'
         'expect prob(q, "1") == 0', lambda it: ok_expects(it) and noise_traced(it))
    case("T-count counts p(3π/4), rz(π/4), ctl t", 'wires q: 2\nq[0] <- p(3*pi/4)\nq[1] <- rz(pi/4)\nq <- ctl t\n'
         'q[0] <- s\nq[1] <- p(pi/2)', lambda it: circuit_stats(it.trace, 2)["t_count"] == 2
         and circuit_stats(it.trace, 2)["non_clifford_other"] == 1)
    case("opt keeps a T when s·t merges", 'wire q\nq <- s >> t',
         lambda it: circuit_stats(optimize(it.trace), 1)["t_count"] == 1)

    def emit_roundtrip(it):
        src = "\n".join(emit_surd(it, optimize(it.trace)))
        it2 = Interp(seed=1, echo=False).run(src)
        return [op[2] for op in it2.trace] == [op[2] for op in it.trace]
    case("emitted Loom re-parses exactly", 'wire q\nq <- rx(float(1/100000)) >> rz(-2.5) >> ry(float(1/3))',
         emit_roundtrip)

    def emit_roundtrip_ctl(it):
        src = "\n".join(emit_surd(it, it.trace))
        it2 = Interp(seed=1, echo=False).run(src)
        return it2.trace == it.trace
    case("emitted Loom keeps controls and measures", 'wires q: 3\nbits c: 3\nq <- h & id & id >> ccx\n'
         'q[2] <- reset\nc <- measure q', emit_roundtrip_ctl)
    case("noise failure leaves the state untouched", 'wires q: 3\nq[0] <- h', lambda it: _fails_clean(it))
    case("if inside a gate does not block opt/qasm", 'gate f: a { if 1 == 1 { a <- h } }\nwire q\nq <- f',
         lambda it: not it.used_if)
    case("density reset averages both outcomes", 'wires q: 2\nq <- h & id >> cx\nq[0] <- dephase(0)\nq[0] <- reset\n'
         'expect purity(q[1]) == 1/2\nexpect prob(q[0], "0") == 1', ok_expects)
    # probe round 1: rz global phase, and paths the coverage probe found untested
    case("rz(π/4) stays exact (global phase set aside)", 'wire q\nq <- h >> rz(pi/4) >> h\n'
         'expect prob(q, "0") == 1/2 + sqrt(2)/4', lambda it: ok_expects(it) and it.state.exact)
    # probe round 3: dyadic phases stay exact
    case("controlled rz(π/4) is exact (Q(ζ16))", 'wires q: 2\nq <- h\nq <- crz(pi/4)\n'
         'expect prob(q, "11") == 1/4', lambda it: ok_expects(it) and it.state.exact)
    case("π/8 amplitude: P = cos²(π/8) printed exactly", 'wire q\nq <- ry(pi/4)\n'
         'expect prob(q, "0") == cos(pi/8)^2\nexpect prob(q, "0") == 1/2 + sqrt(2)/4',
         lambda it: ok_expects(it) and it.state.exact)
    case("π/16 interference stays exact", 'wire q\nq <- h >> p(pi/16) >> h\nexpect prob(q, "0") == cos(pi/32)^2',
         lambda it: ok_expects(it) and it.state.exact)
    case("division in Q(ζ32)", 'expect (cos(pi/16) / cos(pi/16)) == 1\nexpect 1 / cos(pi/8) * cos(pi/8) == 1\n'
         'expect cos(pi/8)^2 + sin(pi/8)^2 == 1', ok_expects)
    case("sign of a number 1.6e-12 from zero, decided at 80 digits", 'expect sqrt(2) < 665857/470832\n'
         'expect sqrt(2) > 665857/470832 - 1/10^11\nexpect cos(pi/8) > 0\nexpect cos(pi/8) - 1307/1415 < 1/1000',
         ok_expects)
    case("past π/32 the run says float", 'wire q\nq <- h >> p(pi/64)', lambda it: not it.state.exact and it.notes)
    case("4-wire QFT is exact end to end", 'gate qft: r[] { r[0] <- h; for k in 1..len(r) { (r[k], r[0]) <- cp(pi/2^k) }; '
         'if len(r) > 1 { r[1..] <- qft } }\nwires q: 5\nq[1] <- x\nq <- qft\nq <- ~qft\nexpect prob(q, "01000") == 1',
         lambda it: ok_expects(it) and it.state.exact)

    def outputs(it):
        text = "\n".join(it.lines)
        return all(k in text for k in ("probs", "bloch q[0]", "amp |00>", "stats", "sample 64", "q[0]: ", "unitary"))
    case("print forms", 'wires q: 2\nq <- h & id >> cx\nprint probs\nprint bloch q[0]\nprint amp "00"\nprint stats\n'
         'sample 64 q\nprint circuit\nprint unitary ctl s\nprint state\nmeasure q', outputs)
    case("number functions", 'expect cos(pi/4) == sqrt(2)/2\nexpect sin(pi/2) == 1\nexpect abs(0 - 3) == 3\n'
         'expect sqrt(9/4) == 3/2\nexpect sqrt(8) == 2*sqrt(2)\nexpect float(1/4) == 0.25\n'
         'expect cos(1) > 0.54\nexpect 2^-2 == 1/4\nexpect pi/2 + pi/2 == pi\nexpect (pi/4)/(pi/8) == 2\n'
         'expect sqrt(3) > 1.73\nexpect not (1 > 2) and (1 < 2 or false)', ok_expects)

    def cli_ok(_):
        import tempfile, contextlib, io
        d = tempfile.mkdtemp().replace(chr(92), '/')
        f = os.path.join(d, "t.surd")
        open(f, "w").write('wires q: 2\nbits c: 2\nq <- h & id >> cx\nc <- measure q\nexpect c == "00" or c == "11"\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            codes = [main(["run", f, "--shots", "50", "--seed", "3"]), main(["run", f, "--json"]),
                     main(["qasm", f]), main(["stats", f, "--json"]), main(["opt", f]), main(["draw", f]),
                     main(["check", f]), main(["describe", "--json"]), main(["version"])]
        out = buf.getvalue()
        return codes == [0] * 9 and "OPENQASM 2.0" in out and "50 shots" in out and '"t_count"' in out
    case("command line", "wire q", cli_ok)
    # abilities: path sums, stabilizers, optimal synthesis, symbolic noise
    def paths_ok(it):
        t = "\n".join(it.lines)
        return "2 path(s), 2 cancel in 1 pair(s) → amplitude 0" in t and "→ amplitude 1" in t
    case("path sum: h >> h interference", 'wire q\nq <- h >> h\nprint paths "1"\nprint paths "0"', paths_ok)
    case("stabilizers", 'wires b: 2\nb <- h & id >> cx\nexpect stabilizers(b) == "+XX +ZZ"\nwires g: 3\ng[0] <- h\n'
         '(g[0], g[1..]) <- cx\nexpect is_stabilizer(g)\nwire m\nm <- h >> t\nexpect not is_stabilizer(m)\n'
         'expect stabilizers(b[0]) != "+XX"', ok_expects)
    case("synth: T-optimal, proved", 'let g = h >> t >> h >> t >> h >> s >> t >> t >> h\n'
         'expect tcount(g) == 4\nexpect tcount(synth(g)) == 2\nexpect synth(g) =~ g\n'
         'expect gcount(synth(t >> t)) == 1\nexpect synth(h >> h) =~ id', ok_expects)
    case("synth: two wires, shortest", 'expect gcount(synth(cz)) == 3\nexpect gcount(synth(swap)) == 3\n'
         'expect synth(cz) =~ cz\nexpect synth(cx >> (h & h) >> cx) =~ cx >> (h & h) >> cx', ok_expects)
    case("symbolic noise: repetition code fails as 3p² − 2p³", 'symbol p\nwires q: 3\nq <- bitflip(p)\n'
         '(q[0], q[1]) <- cx\n(q[0], q[2]) <- cx\n(q[2], q[1], q[0]) <- ccx\nlet f = prob(q[0], "1")\n'
         'expect f == 3*p^2 - 2*p^3\nexpect at(f, p, 1/100) == 149/500000\nexpect deriv(f, p) == 6*p - 6*p^2\n'
         'expect at(f - p, p, 1/2) == 0', ok_expects)
    case("symbolic noise: Bell under depolarize(p)", 'symbol p\nwires b: 2\nb <- h & id >> cx\nb[0] <- depolarize(p)\n'
         'expect purity(b) == 1 - 2*p + 4/3*p^2\nexpect expval(b, "ZZ") == 1 - 4/3*p\n'
         'expect prob(b, "01") == p/3', ok_expects)
    case("two symbols", 'symbol a, g\nwire q\nq <- h\nq <- dephase(a)\nq <- bitflip(g)\n'
         'expect bloch_x(q) == 1 - 2*a\nexpect at(bloch_x(q), a, 1/4, g, 1/3) == 1/2', ok_expects)
    def sym_roundtrip(it):
        src = "\n".join(emit_surd(it, optimize(it.trace)))
        it2 = Interp(seed=1, echo=False).run(src)
        a, b = it.state.distribution([0]), it2.state.distribution([0])
        return "symbol p" in src and all(Poly.coerce(a[k]) == Poly.coerce(b[k]) for k in a) and set(a) == set(b)
    def realm_case(_):
        import tempfile, sqlite3 as _sq, contextlib, io
        d = tempfile.mkdtemp().replace(chr(92), '/')
        open(os.path.join(d, "a.csv"), "w").write(
            "date,amount,meaning,locator\n2020-01-05,100.00,roof framing lumber,A1\n2020-01-09,250.50,deposit,A2\n"
            "2020-02-01,75.25,kitchen cabinets,A3\n2020-03-03,40.00,concrete pour,A4\n")
        json.dump([{"date": "2020-01-06", "amount": 100, "meaning": "lumber for the roof framing", "locator": "B1"},
                   {"date": "2020-01-09", "amount": "$250.50", "meaning": "owner advance", "locator": "A2"},
                   {"date": "2020-02-02", "amount": 75.25, "meaning": "furniture allowance", "locator": "B3"},
                   {"date": "2020-05-01", "amount": 999, "meaning": "unmatched", "locator": "B4"}],
                  open(os.path.join(d, "b.json"), "w"))
        con = _sq.connect(os.path.join(d, "c.sqlite"))
        con.execute("create table t(d text, amt real, what text, src text)")
        con.executemany("insert into t values (?,?,?,?)", [(f"2020-01-0{i}", float(i), "x", f"C{i}") for i in range(1, 9)])
        con.commit(); con.close()
        src = (f'realm a = load("{d}/a.csv")\nrealm b = load("{d}/b.json")\n'
               f'realm c = load("{d}/c.sqlite", "select d as date, amt as amount, what as meaning, src as locator from t")\n'
               'expect agreements(a, b, 3) == 1\nexpect collisions(a, b, 3) == 1\nexpect len(c) == 8\n'
               'expect fidelity(a[0], b[0]) == 1\nexpect anchor(a[0], b[0], 3) == 36/49\n'
               'expect fidelity(a[2], b[2]) == 0\nexpect field(a[1], "locator") == "A2"\n'
               'compare a, b within 3\ngrover c where amount == 3\nswaptest a[0], b[0]')
        it = Interp(seed=1, echo=False).run(src)
        out = "\n".join(it.lines)
        return ok_expects(it) and "COLLIDE — same signed amount, no shared tokens (1)" in out and \
            "P(measure a marked record) = 121/128" in out and "(exact): P(ancilla 0) = 1 → F = 2P−1 = 1" in out \
            and "UNLABELED 1" in out
    case("realms: load csv/json/sqlite, compare, grover, swaptest", "wire q", realm_case)
    case("games: CHSH reaches Tsirelson exactly; no signalling", 'seed 3\ngame chsh {\n  players a1(x -> a), b1(y -> b)\n'
         '  win (a + b) % 2 == x * y\n}\ngame tele {\n  players a1(x -> a), b1(y -> b)\n  win a == y\n}\n'
         'expect classical(chsh) == 3/4\nexpect quantum(chsh) == 1/2 + sqrt(2)/4\n'
         'expect quantum(tele) == 1/2\nexpect 7 % 3 == 1\nexpect xor(1, 1, 1) == 1', ok_expects)
    def qasm_case(_):
        import tempfile
        d = tempfile.mkdtemp()
        open(os.path.join(d, "a.qasm"), "w").write('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n')
        open(os.path.join(d, "b.qasm"), "w").write('OPENQASM 2.0;\ninclude "qelib1.inc";\ngate bell a, b { u2(0,pi) a; cx a, b; }\n'
                                                   'qreg r[2];\nbell r[0], r[1];\nu1(pi/4) r[1];\nu1(-pi/4) r[1];\n')
        it = Interp(seed=1, echo=False).run(f'let a = qasm("{d}/a.qasm")\nlet b = qasm("{d}/b.qasm")\nexpect a =~ b\n'
                                            f'expect a == b\nwires q: 2\nq <- a\nexpect prob(q, "11") == 1/2')
        return ok_expects(it)
    case("OpenQASM import: custom gates, u2/u1, exact equivalence", "wire q", qasm_case)
    def explain_ok(_):
        it = Interp(seed=1, echo=False)
        txt = "\n".join(it.explain('wires q: 2\nbits c: 2\nq <- h & id >> cx\nexpect prob(q, "11") == 1/2\nc <- measure q'))
        return "entangled: q[0], q[1]" in txt and "passed ✓" in txt and "only when q[0] is 1" in txt and "saw: q[0] →" in txt
    case("explain narrates gates, entanglement, checks and measurements", "wire q", explain_ok)
    # general-purpose programming
    case("functions, recursion, closures", 'fn fact(n) { if n <= 1 { return 1 }\n return n * fact(n - 1) }\n'
         'expect fact(20) == 2432902008176640000\nfn adder(k) { return fn(x) => x + k }\nlet a5 = adder(5)\n'
         'expect a5(10) == 15\nfn fib(n) { if n < 2 { return n }\n return fib(n - 1) + fib(n - 2) }\n'
         'expect map(fib, range(10)) == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]', ok_expects)
    case("lists, maps, strings, slices", 'let xs = [5, 3, 8, 1]\nexpect sorted(xs) == [1, 3, 5, 8]\nexpect xs[1..3] == [3, 8]\n'
         'expect xs[0 - 1] == 1\nexpect sum(xs) == 17\nlet m = {"a": 1}\nset(m, "b", 2)\nexpect keys(m) == ["a", "b"]\n'
         'expect m["b"] == 2\nexpect join(split("a,b,c", ","), "+") == "a+b+c"\nexpect "q" + str(3) == "q3"\n'
         'expect len("hello") == 5\nexpect contains(xs, 8)\nexpect zip([1, 2], [3, 4]) == [[1, 3], [2, 4]]', ok_expects)
    case("while, break, continue, for-in", 'let tot = 0\nlet i = 0\nwhile true {\n let i = i + 1\n if i % 2 == 0 { continue }\n'
         ' if i > 9 { break }\n let tot = tot + i\n}\nexpect tot == 25\nlet acc = []\nfor x in [1, 2, 3] { push(acc, x * x) }\n'
         'expect acc == [1, 4, 9]\nfor k in 0..10 { if k == 3 { break } }\nexpect k == 3', ok_expects)
    case("exact library values", 'expect sum([1/3, 1/3, 1/3]) == 1\nexpect min([sqrt(2), 3/2, 7/5]) == 7/5\n'
         'expect max(cos(pi/8), 9/10) == cos(pi/8)\nexpect reduce(fn(a, b) => a * b, range(1, 6), 1) == 120', ok_expects)
    case("functions build circuits, even inside gates", 'fn bell(a, b) { (a, b) <- h & id >> cx }\nwires q: 4\n'
         'bell(q[0], q[1])\nbell(q[2], q[3])\nexpect prob(q, "1111") == 1/4\ngate g2: a, b { bell(a, b) }\n'
         'expect g2 == h & id >> cx', ok_expects)
    case("test blocks record pass and fail", 'test "good" { expect 1 + 1 == 2 }\ntest "bad" { expect 1 == 2 }',
         lambda it: [t_["ok"] for t_ in it.tests] == [True, False])
    case("describe: every portable built-in example verifies", "wire q",
         lambda _: all(b["verified"] for b in describe_builtins() if b["scope"] == "portable"))
    case("semantics: meanings, joint meanings, encode on wires, trust, belnap, jaccard",
         'space P { construction, furniture, overage, other }\nspace A { lender, owner }\n'
         'let m1 = read(P, "synthetic construction overage", {"overage": "overage", "house": "construction"})\n'
         'let m2 = read(P, "towards the furniture", {"furniture": "furniture"})\n'
         'expect distribution(m1) == {"overage": 1/2, "construction": 1/2}\nexpect fidelity(m1, m2) == 0\n'
         'expect read(P, "coverage of the lot", {"overage": "overage"}) == none\n'
         'let j = joint(P, A, [["overage", "lender", 1], ["furniture", "owner", 1]])\n'
         'expect entanglement(j) == 1/2\nexpect given(j, "owner") == sense(P, "furniture")\n'
         'let b = blend(P, {"construction": 1, "furniture": 3})\nexpect fidelity(b, sense(P, "furniture")) == 3/4\n'
         'expect reframe(sense(P, "construction"), "construction", "furniture", pi/2) == sense(P, "furniture")\n'
         'let pp = phase(blend(P, {"construction": 1, "furniture": 1}), "furniture", pi/2)\n'
         'expect fidelity(pp, blend(P, {"construction": 1, "furniture": 1})) == 1/2\n'
         'expect entropy(blend(P, {"construction": 1, "furniture": 1, "overage": 1, "other": 1})) == 2\n'
         'wires w: 2\nwires a: 1\nencode(j, w, a)\nexpect senses(a, A) == {"lender": 1/2, "owner": 1/2}\n'
         'expect entanglement(a) == 1/2\nexpect belnap(true, true) == "B"\nexpect jaccard("rough plumbing", "plumbing rough") == 0\n'
         'let tr = trust([["X", "q", 1], ["Y", "q", 1], ["Z", "q", 2]])\nexpect tr["Z"] == float(1/20)', ok_expects)
    case("money and dates", 'expect $1,234.56 + $0.44 == $1,235.00\nexpect str($100 / 3) == "$33.33 + 1/3¢"\n'
         'expect $100 / $25 == 4\nexpect @2040-03-04 - @2040-01-01 == 63\nexpect @2040-01-01 + 30 == @2040-01-31\n'
         'expect -$5 < $0\nexpect money("($1,234.50)") == -$1,234.50', ok_expects)
    case("books: double entry, balances, statement, flows, net, paths",
         'let book = []\npost(book, @2020-01-01, "cash", "owner", $1,000)\npost(book, @2020-01-05, "lumber", "cash", $400)\n'
         'post(book, @2020-01-09, "builder", "cash", $250)\npost(book, @2020-01-10, "cash", "builder", $50)\n'
         'let b = balances(book)\nexpect b["cash"] == $400\nexpect b["owner"] == -$1,000\n'
         'expect sum(map(fn(k) => b[k] / $1, keys(b))) == 0\nexpect len(statement(book, "cash")) == 4\n'
         'expect net(book, "cash", "builder") == $200\nexpect paths(book, "owner", "builder")[0]["most"] == $250\n'
         'expect balances(book, @2020-01-05)["cash"] == $600', ok_expects)
    case("tracing: fifo, lifo, prorata, libr, bounds, round trips",
         'let ev = [{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-02, "amount": $300, "cls": "B"},\n'
         '  {"date": @2020-01-03, "amount": -$200, "cls": ""}, {"date": @2020-01-04, "amount": $50, "cls": "A"},\n'
         '  {"date": @2020-01-05, "amount": -$50, "cls": ""}]\n'
         'expect trace(ev, "fifo")[0]["from"] == {"A": $100, "B": $100}\nexpect trace(ev, "lifo")[0]["from"] == {"B": $200}\n'
         'expect trace(ev, "prorata")[0]["from"] == {"A": $50, "B": $150}\n'
         'expect libr(ev, "A")[2]["class_left"] == $100\nexpect share_bounds(ev, @2020-01-03, -$200, "A")["range"] == bound($0, $100)\n'
         'expect len(roundtrips(ev, $50, 3)) == 1\nlet sp = spectrum(ev, @2020-01-03, -$200, "A")\n'
         'expect sp["fifo/deposits"] == $100 and sp["floor"] == $0', ok_expects)
    case("exact sums and quantum search", 'let xs = [$10, $20, $30, $40, $50]\nexpect len(subset_sum(xs, $60, 3)) == 3\n'
         'expect window_sums(xs, $90)[0]["start"] == 1\nlet g = grover_sum(xs, $60)\nexpect len(g["solutions"]) == 3\n'
         'expect g["p_success"] > 0.9\nexpect type(provenance([{"date": @2020-01-01, "amount": $1, "cls": "A"}, '
         '{"date": @2020-01-01, "amount": $1, "cls": "B"}], @2020-01-01)) == "meaning"', ok_expects)
    case("residence time (Little's law): floor..ceiling of how long a class's money stayed",
         'let ev = [{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"},\n'
         '  {"date": @2020-01-11, "amount": -$100, "cls": ""}, {"date": @2020-01-21, "amount": -$100, "cls": ""}]\n'
         'let rs = residence(ev, "A")\nexpect rs["floor_days"] == 10\nexpect rs["ceiling_days"] == 20\n'
         'expect rs["depleted_spent_first"] == @2020-01-11\nexpect rs["depleted_spent_last"] == @2020-01-21', ok_expects)
    case("physics: Rayleigh spectroscopy, Zeno shift test, detailed balance",
         'expect rayleigh([0, 7, 14, 21, 28], 7) > 9.999\nexpect rayleigh([0, 3.5], 7) < 0.000001\n'
         'let ev = map(fn(k) => {"date": @2020-01-01 + 30 * k, "amount": -$500, "account": "A"}, [0, 1, 2, 3, 4, 5])\n'
         'expect spectral_lines(ev)[0]["period"] == 30\n'
         'let zt = zeno_test([{"date": @2020-01-01, "amount": $1}, {"date": @2020-01-08, "amount": $1}], [@2020-01-01, @2020-01-08], @2020-01-01, @2020-01-20)\n'
         'expect zt["p_near"] == 1/20\n'
         'let g = accounts({"A": [{"date": @2020-01-01, "amount": -$5, "memo": "TRANSFER TO B"}, {"date": @2020-01-02, "amount": $5, "memo": "TRANSFER FROM B"}],\n'
         '  "B": [{"date": @2020-01-01, "amount": $5, "memo": "TRANSFER FROM A"}, {"date": @2020-01-02, "amount": -$5, "memo": "TRANSFER TO A"}]})\n'
         'let db = detailed_balance(g)\nexpect db["reciprocal"] == $5\nexpect db["entropy_production"] == 0', ok_expects)
    case("multi-bank ledger, observers, language auditor, portals, collections",
         'let A = [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "ONLINE TRANSFER TO B"},\n'
         '  {"date": @2020-01-05, "amount": $10, "memo": "XFER FROM C"}]\n'
         'let B = [{"date": @2020-01-02, "amount": $60, "memo": "ONLINE TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"},\n'
         '  {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO C"}]\n'
         'let C = [{"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}, {"date": @2020-01-05, "amount": -$10, "memo": "XFER TO A"}]\n'
         'let g = accounts({"A": A, "B": B, "C": C})\nexpect len(transfers(g)) == 3\nexpect consolidate(g)["group"] == $50\n'
         'expect cycles(g)[0]["bottleneck"] == $10\nexpect len(floats(g)) == 0\n'
         'expect filter(fn(o) => o["memo"] == "pay", carry(g, "fifo")["outflows"])[0]["from"] == {"X": $50}\n'
         'let f = [{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}]\n'
         'expect pooled(f, ["bank", "lender"], "q", "yes", @2020-01-13)["only_when_pooled"]\n'
         'expect knew(f, "q")["lender"]["date"] == @2020-01-12\n'
         'expect len(every_perspective(f, ["bank", "lender"], "q", "yes", [@2020-01-11, @2020-01-13])) == 4\n'
         'expect perspectives(f, ["bank", "lender"], "q", "yes", [@2020-01-13], 50, 2)["overall"]["T"] + perspectives(f, ["bank", "lender"], "q", "yes", [@2020-01-13], 50, 2)["overall"]["F"] == 1\n'
         'expect audit_language("don\'t worry, it will come back in the draw")["functions"][0]["weight"] == 1/2\n'
         'expect len(audit_thread([{"speaker": "J", "audience": "o", "text": "don\'t worry"}, {"speaker": "J", "audience": "l", "text": "paid $5.00"}])["audience_split"]) == 1\n'
         'expect portal("$60.00 on 2020-01-02", {"A": A, "B": B})["tunnels"][0]["realms"] == ["A", "B"]\n'
         'expect collections([{"id": "inv", "amount": $40}], [{"id": "r1", "amount": $40, "draw_no": 1}, {"id": "r2", "amount": $20, "draw_no": 2}], "draw_no", [1, 1/2])[0]["groups"] == ["1", "2"]\n'
         'expect len(kinds()) == 0', ok_expects)
    case("native statements: foot, returned items, reconcile, but-for drops the return",
         'let st = [{"date": @2020-01-01, "amount": $100, "month": "2020-01", "begin": $0, "end": $40, "memo": "dep", "type": ""},\n {"date": @2020-01-02, "amount": $50, "month": "2020-01", "memo": "ck 77", "type": ""},\n {"date": @2020-01-03, "amount": -$80, "month": "2020-01", "memo": "check 1", "type": ""},\n {"date": @2020-01-04, "amount": -$50, "month": "2020-01", "memo": "RTRN CK 77", "type": "returned"},\n {"date": @2020-01-05, "amount": $20, "month": "2020-01", "memo": "dep", "type": ""}]\n'
         'expect foot(st)[0]["ties"]\nexpect foot(st)[0]["computed_end"] == $40\n'
         'let rt = returns(st)\nexpect len(rt) == 1\nexpect rt[0]["deposit_date"] == @2020-01-02\n'
         'expect rt[0]["paid_while_provisional"] == $80\nexpect rt[0]["spent"] == bound($0, $50)\n'
         'let rc = reconcile(st, filter(fn(e) => e["amount"] != $50 and e["amount"] != -$50, st))\n'
         'expect rc["explained"]\nexpect len(rc["netted"]) == 1\nexpect rc["max_divergence"] == $50\n'
         'fn cl(e) {\n if e["memo"] == "ck 77" { return "B" }\n return ""\n}\nlet bf = butfor(map(fn(e) => {"date": e["date"], "amount": e["amount"], "memo": e["memo"], "type": e["type"], "cls": cl(e)}, st), "B")\n'
         'expect bf["returns_removed_with_them"] == 1\nexpect bf["final_balance_without"] == $40', ok_expects)
    case("bounds, joint bounds, but-for, verdicts",
         'let ev = [{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"},\n'
         '  {"date": @2020-01-02, "amount": -$120, "cls": "", "memo": "land"}, {"date": @2020-01-03, "amount": -$60, "cls": "", "memo": "land"}]\n'
         'let jb = set_bounds(ev, fn(e) => e["amount"] < $0, "A")\nexpect jb["range"] == bound($80, $100)\n'
         'expect within($90, jb["range"])\nexpect lo(jb["range"]) + $10 == $90\nexpect bound($1, $2) + bound($3, $4) == bound($4, $6)\n'
         'let bf = butfor(ev, "A")\nexpect bf["deepest_shortfall"] == $80\nexpect bf["first"] == @2020-01-02\n'
         'let wi = whatif(ev, fn(e) => e["cls"] == "B")\nexpect wi["deepest_shortfall"] == $80\n'
         'expect verdict(ev, @2020-01-02, -$120, "A", ">=", $20)["verdict"] == "T"\n'
         'expect verdict(ev, @2020-01-02, -$120, "A", ">=", $110)["verdict"] == "F"\n'
         'expect verdict(ev, @2020-01-02, -$120, "A", ">=", $60)["verdict"] == "B"', ok_expects)
    case("a huge exact value prints readably", 'print (1/3)^60 + 1', lambda it: "exact; its fraction runs to" in it.lines[0]
         and len(it.lines[0]) < 90)
    case("optimized symbolic program re-runs to the same formulas", 'symbol p\nwire q\nq <- h >> h\n'
         'q <- bitflip(3*p^2 - 2*p^3/3)\nq <- dephase(p)', sym_roundtrip)
    errs = [("S-E009", "symbol p\nwire q\nq <- bitflip(p)\nmeasure q"),
            ("S-E009", "symbol p\nwire q\nq <- bitflip(p)\nq <- rx(0.3)"),
            ("S-E009", "symbol p\nwire q\nq <- bitflip(p)\nexpect prob(q, \"1\") < 1/2"),
            ("S-E009", "symbol p\nwire q\nq <- h\nq <- ampdamp(p)"),
            ("S-E011", "symbol h"), ("S-E009", "wire q\nq <- h\nq <- dephase(1/2)\nprint paths \"0\""),
            ("S-E009", "let v = synth(rx(0.3))"), ("S-E010", "let v = synth(ccx)"),
            ("S-E001", "game g { players a(x -> b) }"), ("S-E011", "game g { players a(x -> x)\n win x == 1 }"),
            ("S-E002", "solve nothing"), ("S-E009", "let v = $1 * $2"), ("S-E009", "let v = @2020-01-01 + $1"),
            ("S-E005", 'let v = share_bounds([{"date": @2020-01-01, "amount": $5, "cls": "A"}], @2020-01-02, -$1, "A")'),
            ("S-E009", 'let book = []\npost(book, @2020-01-01, "a", "b", -$1)'), ("S-E002", "space P { a, b }\nlet m = sense(P, \"c\")"),
            ("S-E009", "space P { a, b }\nspace Q { x, y }\nlet v = fidelity(sense(P, \"a\"), sense(Q, \"x\"))"),
            ("S-E009", "space P { a, b }\nwire q\nq <- h\nencode(sense(P, \"a\"), q)"),
            ("S-E009", "space P { a }"), ("S-E009", 'space P { a, b }\nlet v = fidelity(sense(P, "a"), read(P, "x", {"y": "a"}))'), ("S-E009", "return 3"), ("S-E009", "break"),
            ("S-E014", "fn f(n) { return f(n + 1) }\nlet v = f(0)"), ("S-E004", "fn f(a) { return a }\nlet v = f(1, 2)"),
            ("S-E005", "let xs = [1, 2]\nlet v = xs[5]"), ("S-E005", 'let m = {"a": 1}\nlet v = m["b"]'),
            ("S-E009", "let v = [1] + 2"), ("S-E011", "fn h(x) { return x }"), ("S-E009", "for x in 5 { }"), ("S-E002", "let v = equal(h, x)"), ("S-E002", 'let g = qasm("/nonexistent.qasm")'), ("S-E009", "let v = 1/2 % 2 + 2 % 0"),
            ("S-E005", "wires q: 2\nq[3] <- h"), ("S-E003", "wire q\nq <- hh"), ("S-E006", "wires q: 2\n(q[0], q[0]) <- cx"),
            ("S-E007", "gate g: a { b <- measure a }\nwire q\nq <- g"), ("S-E001", "wires q: 2\nq[0 <- h"),
            ("S-E002", "wire q\nr <- h"), ("S-E004", "wires q: 3\nq <- cx"),
            ("S-E011", "wire q\nwire q"), ("S-E012", "wires a: 2, b: 3\n(a, b) <- cx"),
            ("S-E014", "gate g: a { a <- g }\nwire q\nq <- g"), ("S-E014", "gate g = g >> h\nwire q\nq <- g"),
            ("S-E011", "gate h = x"), ("S-E009", "wire q\nlet v = q + 1"),
            ("S-E007", "gate g: a { a <- dephase(1/2) }\nwire q\nq <- g"), ("S-E009", "wire q\nq <- bitflip(3/2)"),
            ("S-E007", "bit c\ngate g: a { if c == 1 { a <- x } }\nwire q\nq <- g"),
            ("S-E009", 'wire q\nq <- dephase(1/2)\nprint amp "0"'), ("S-E009", "wire q\nq <- ~dephase(1/2)"),
            ("S-E012", "gate g: a, r[] { a <- h }\nwires q: 2\nwire b\n(q, b) <- g"),
            ("S-E004", "wire q\nlet v = (h == cx)"), ("S-E003", "noise shake(1/2)"),
            ("S-E007", 'gate g: a { if prob(a, "1") == 1 { a <- x } }\nwire q\nq <- x\nq <- g'),
            ("S-E007", "wire q\nbit c\nq <- h\nc <- measure q\nlet m = c\ngate g: a { if m == 1 { a <- x } }\nwire r\nr <- g"),
            ("S-E007", "gate g: a { let e = (g == x)\n a <- x }\nwire q\nq <- g"),
            ("S-E010", "wires q: 11\nnoise depolarize(1/10)"),
            ("S-E009", "gate g: r[] { if r == 1 { r[0] <- x } }\nwires q: 2\nq <- g"),
            ("S-E009", 'wire q\nlet v = prob(q, 1/2)'), ("S-E009", "bit c\nexpect c == 1/2"),
            ("S-E009", "print (0-8)^(1/3)"), ("S-E009", "wire q\nq <- rx(float(2)^5000)"),
            ("S-E006", 'wire q\nlet v = expval(q, q, "XZ")'),
            ("S-E004", "wires q: 2\nq <- h >> cx"), ("S-E004", "wire q\nq <- rz"),
            ("S-E009", "wire q\nq <- 3"), ("S-E009", "let v = -h"), ("S-E011", "wire q\nlet x = 3"), ("S-E011", "let x = 3\nwire q"),
            ("S-E001", "ctl x q"), ("S-E004", "wire q\nq <- dephase")]
    passed, failed = 0, []
    for name, src, check in cases:
        try:
            it = Interp(seed=1, echo=False).run(src)
            good = bool(check(it))
        except SurdError as e:
            good, it = False, None
            name += " — " + e.render()
        except Exception as e:
            good, it = False, None
            name += f" — CRASH {type(e).__name__}: {str(e)[:80]}"
        (passed := passed + 1) if good else failed.append(name)
    for code, src in errs:
        try:
            Interp(seed=1, echo=False).run(src)
            failed.append(f"{code} not raised for {src!r}")
        except SurdError as e:
            if e.code == code: passed += 1
            else: failed.append(f"{code} expected, got {e.code} for {src!r}")
        except Exception as e:
            failed.append(f"{code} expected, CRASH {type(e).__name__}: {e} for {src!r}")
    exdir = os.path.join(here, "examples")
    for fn in sorted(os.listdir(exdir)) if os.path.isdir(exdir) else []:
        if not fn.endswith(".surd"): continue
        src = open(os.path.join(exdir, fn)).read()
        good = True
        for s in range(1, 9):
            try:
                it = Interp(seed=s, echo=False).run(src)
                if not ok_expects(it): good = False; break
            except SurdError as e:
                good = False; fn += " — " + e.render(); break
        (passed := passed + 1) if good else failed.append(f"example {fn} (seeds 1-8)")
    total = passed + len(failed)
    for f in failed: print("  ✗", f)
    print(f"selftest {passed}/{total}")
    return not failed


DESCRIBE = {
    "integrations": {"guide": "INTEGRATIONS.md", "functions": sorted(INTEGRATION_FUNCS),
                     "commands": ["timeline", "qulap", "qulaps", "graph", "research", "sqlite", "studio", "ontology", "normalize"], "qulap_version": "0.2.0",
                     "ontology_guide": "ONTOLOGY.md", "ontology_command": "ontology check|format|validate|expand|project|diff",
                     "studio_guide": "RESEARCH_STUDIO.md", "research_guide": "RESEARCH_ENGINE.md", "research_memory": "versioned SQLite graph; explicit local binding and bounded source-search capture",
                     "semantics": "finite declared domains and read-only source timelines; proof_effect NONE"},
    "collections": {"guide": "COLLECTIONS.md", "functions": sorted(surd_collections.FUNCTIONS),
                    "key_policy": "typed structural keys; exact values preserved; groups retain encounter order",
                    "limits": {"items": surd_collections.MAX_ITEMS, "structural_visits": surd_collections.MAX_VISITS,
                               "structural_depth": surd_collections.MAX_DEPTH},
                    "execution": "shared cooperative language budget; callback side effects are not rolled back"},
    "execution_limits": {"flags": ["--max-steps N", "--timeout SECONDS"], "error": "S-E017",
                         "scope": "statements, expressions and loop iterations; one shared budget across shots; reset per serve exec/eval",
                         "defaults": "opt-in; existing language limits remain", "atomicity": "earlier session mutations remain",
                         "boundary": "cooperative checkpoints; native calls, parsing, memory and external I/O need process-level limits"},
    "search_engine": {"guide": "SEARCH_ENGINE.md", "functions": sorted(SEARCH_FUNCS),
                      "retrieval": "field-weighted BM25, exact identifiers, phrases, filters, explicit aliases and RRF",
                      "evaluation": "precision, recall, MRR, MAP, linear-gain nDCG, paired bootstrap and regression gates",
                      "reasoning": "source quotation checks, explicit premises and rules, conflict and assumption tracking, source removal sensitivity",
                      "score_meaning": "relevance, not truth or probability"},
    "name": "surd", "version": VERSION,
    "what": "a qubit programming language where wires receive gates (q <- h) and gates are exact values with an "
            "algebra (>> then, & beside, ~ inverse, ctl control, ^n power, == exact equality, =~ equal up to phase); "
            "exact Clifford+T statevector and density-matrix simulation over Q(e^(iπ/4)), honest float fallback; "
            "simulation only",
    "bit_order": "bit strings read left-to-right in the order wires are named; q[0] is the leftmost character "
                 "(Qiskit prints the reverse). A bit register as a number reads c[0] as the most significant bit.",
    "programming": ["fn name(a, b) { … return x }", "fn(x) => x * 2  (anonymous; closures capture their scope)",
                    "while cond { … } · break · continue · for x in list { } · for i in lo..hi { }",
                    "[1, 2, 3] · {\"key\": value} · xs[i] · xs[lo..hi] · xs[i..] · negative indices",
                    "text: \"a\" + \"b\", str(v), split, join, upper, lower",
                    "test \"name\" { expect … }  — `surd test FILE --json`",
                    "functions take wires as arguments and can build circuits, also inside gate bodies"],
    "semantics": ["space NAME { sense, … }", "meanings: sense, blend (α = √(w/Σw)), read (cue words), joint (word ⊗ audience)",
                  "measures: fidelity, gap, entropy, purity, entanglement, distribution, given, normalize, condition, collapse, phase, reframe",
                  "on wires: encode(meaning, wires) and senses(wires, space)",
                  "truth: trust (numeric_spine fixed point), beliefs (superposed belief), belnap (T F B N), jaccard",
                  "a precise notation for meaning; encode puts it on simulated wires — no physical claim"],
    "values": ["exact number / exact real", "angle (k·π)", "float", "string", "bool", "none", "list", "map",
               "function", "gate", "channel", "formula", "realm", "record", "wires"],
    "statements": ["wire a, b", "wires q: 3", "bit m", "bits c: 3", "let x = expr", "seed n",
                   "TARGETS <- GATE_EXPR   (q[0] <- h · (a, b) <- cx · q <- h & id >> cx)",
                   "c <- measure q", "q <- reset", "q <- CHANNEL(p)", "measure q   (prints)",
                   "gate name(params) = GATE_EXPR", "gate name(params): a, b, r[] { body }",
                   "noise CHANNEL(p) | noise off", "barrier [q…]", "if cond { } else { }", "repeat n { }",
                   "for i in lo..hi { }", "print expr | print \"text\", expr | print state | print probs [q…] | "
                   "print bloch q | print amp \"bits\" | print unitary GATE_EXPR | print circuit | print stats",
                   "expect condition", "sample n [q…]"],
    "gate_algebra": {">>": "then (same wires)", "&": "beside (wires split left to right)", "~g": "inverse",
                     "ctl g": "controlled by one more wire, first", "g ^ n": "power (negative = inverse power)",
                     "==, !=": "exact unitary equality over every input", "=~": "equal up to a global phase",
                     "id": "nothing on that wire (emits no gate, no noise)"},
    "layout": "the wires left of <- feed the gate: k targets for a k-wire gate (registers zip), any wires for a "
              "1-wire gate (broadcast), or one register of exactly k wires",
    "primitive_gates": sorted(PRIM), "prelude_gates": [g["name"] for g in _PRELUDE_AST],
    "aliases": ALIAS,
    "exact_set": "h x y z s sdg t tdg sx sxdg id; p/rz at k·π/4 (rz: k·π/2); rx/ry at k·π/2; u with half-angles k·π/4",
    "functions": sorted(FUNCS),
    "noise": "channels depolarize dephase bitflip pauli ampdamp act on a density matrix, exact for rational p",
    "tools": "surd stats F · surd opt F (rewrites, then proves the result equal) · surd qasm F · surd draw F",
    "abilities": {
        "synth(G)": "1 wire: provably T-optimal Clifford+T circuit (T-count ≤ 12); 2 wires: shortest circuit "
                    "(≤ 14 gates, meet-in-the-middle); every result proved =~ G exactly before it is returned",
        "print paths \"bits\"": "Feynman path sum: every computational path into that amplitude, its exact weight, "
                                 "and which paths cancel",
        "stabilizers(q…) / is_stabilizer(q…)": "exact Pauli stabilizer generators (fewest Y, lowest weight), or the "
                                                "partial group size for a non-stabilizer state",
        "symbol p": "noise probabilities become exact polynomials in p: prob, purity, expval, bloch return formulas; "
                    "at(P, p, v) evaluates, deriv(P, p) differentiates, == compares formulas",
        "tcount(G) / gcount(G)": "T gates / primitive gates in a gate value",
        "realm r = load(path [, query])": "any .sqlite (read-only), .csv or .json table; columns named date, amount, "
                                          "meaning, locator are read; each record becomes a state in two bases",
        "compare A, B [, C…] within w": "anchor basis (exact cents ⊗ uniform window of 2w+1 days) and meaning basis "
                                        "(uniform word superposition); prints AGREE / PARTIAL / COLLIDE / UNLABELED / "
                                        "SOURCE-DIFFER with exact fidelities and both locators",
        "grover R where cond": "Grover search over the realm's records: exact success probability after ⌊π/4·√(N/M)⌋ "
                               "oracle calls, and the records found",
        "swaptest r1, r2": "the swap-test circuit on two records' meaning states: P(0) = 1/2 + F/2",
        "fidelity / anchor / agreements / collisions / field / has / len": "the same measurements as values",
        "game NAME \"question\" { players p(x[: n] -> a), … ; promise COND ; win COND }":
            "a nonlocal game of up to 3 players with one-bit answers and inputs of 1..4 values",
        "solve NAME [restarts N]": "classical value by every deterministic strategy (exact); quantum value by an AI "
                                   "search over shared GHZ/Bell measurements — 128 exact rotations rz(jπ/4)>>ry(kπ/8) or a "
                                   "fixed answer per player-input, coordinate ascent with restarts seeded by the best "
                                   "classical strategy — re-scored exactly; prints the answer to the game's question",
        "classical(g) / quantum(g)": "the two values, exact; quantum is a proved lower bound on the true optimum",
        "% and xor(…)": "integer remainder and parity, for win rules",
        "qasm(\"file.qasm\")": "an OpenQASM 2.0 circuit (qelib1 gates, custom gate definitions, exact pi angles) as a gate "
                                 "value: apply it to wires or prove it == / =~ another circuit",
        "surd from-qasm FILE": "the whole OpenQASM program (measurements and resets included) as Loom source",
        "surd explain FILE": "plain-English walk through a program: what each line does, what was measured, whether each "
                             "check passed, and the qubits (exact probabilities, entanglement) after every step"},
    "errors": ERRORS,
}


_DEMO_STATEMENT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "examples", "data", "statement_demo.csv").replace(chr(92), '/')
_BOOTSTRAP_DB = os.path.join(os.path.dirname(os.path.realpath(__file__)), "examples", "data", "loom-bootstrap.sqlite").replace(chr(92), '/')
_ONTOLOGY_SCHEMA = os.path.join(os.path.dirname(os.path.realpath(__file__)), "examples", "data", "ontology-schema.json").replace(chr(92), '/')
BUILTIN_DOCS = [
    ("ontology_check", "ontology_check(schema)", "Compile versioned inherited ontology shapes without mutating graph data",
     'ontology_check('+json.dumps(_ONTOLOGY_SCHEMA)+')["ok"]', "true"),
    ("sqlite_status", "sqlite_status(path, options?)", "Inspect a native SQLite graph without changing it",
     'sqlite_status('+json.dumps(_BOOTSTRAP_DB)+')["bootstrap_schema"]', "1"),
    ("research_plan", "research_plan(model, options?)", "Exact minimax record bundles, cost frontier and complementarity",
     'research_plan({"schema":"qulap.model.v1","source_tier":"synthetic","variables":[{"id":"x","domain":[0,1],"unit":"integer","next_record":"Resolve x"}],"constraints":[],"metrics":[{"id":"value","expr":"x","unit":"integer"}],"sources":[]})["bundles"][0]["guaranteed_gain"]', "1"),
    ("timeline_run", "timeline_run(program, options?)", "Run the read-only timeline dialect and return structured events",
     'timeline_run("say synthetic timeline")["state"]', "COMPLETE"),
    ("qulap_solve", "qulap_solve(packet, options?)", "Solve explicitly declared finite alternatives with exact integer units",
     'qulap_solve({"schema":"qulap.model.v1","source_tier":"synthetic","variables":[{"id":"x","domain":[0,1],"unit":"integer"}],"constraints":[],"metrics":[],"propositions":[],"sources":[]})["feasible_states"]', "2"),
    ("group_by", "group_by(f, list)", "Stable groups with typed exact keys; preserves source items", 'group_by(fn(x) => x % 2, [1, 2, 3])[0]["items"]', "[1, 3]"),
    ("unique_by", "unique_by(f, list)", "First item for each typed structural key", 'unique_by(fn(x) => x, [1/3, 2/6, 1])', "[1/3, 1]"),
    ("partition", "partition(f, list)", "Separate matched and remaining items in encounter order", 'partition(fn(x) => x > 1, [1, 2, 3])["matched"]', "[2, 3]"),
    ("scan", "scan(f, list, start)", "Running accumulators including the initial value; container snapshots", 'scan(fn(a, x) => a + x, [1/3, 1/3, 1/3], 0)', "[0, 1/3, 2/3, 1]"),
    ("flat_map", "flat_map(f, list)", "Map to lists and flatten exactly one level", 'flat_map(fn(x) => [x, x * 2], [1, 2])', "[1, 2, 2, 4]"),
    ("chunk", "chunk(list, size)", "Ordered batches; final batch may be shorter", 'chunk([1, 2, 3, 4, 5], 2)', "[[1, 2], [3, 4], [5]]"),
    ("flatten", "flatten(list)", "Flatten one level, preserving scalar and deeper values", 'flatten([1, [2, [3]], 4])', "[1, 2, [3], 4]"),
    ("search_index", "search_index(rows [, configuration])", "Build a source-aware text index", 'type(search_index([{"id": "a", "text": "exact gates"}]))', "search index"),
    ("search", "search(index, query [, options])", "Rank and explain source-linked matches", 'let ix = search_index([{"id": "a", "text": "exact gates"}])\nsearch(ix, "gates")["hits"][0]["id"]', "a"),
    ("search_reason", "search_reason(index, question [, contract])", "Audit caller-declared arguments and source quotations", 'let ix = search_index([{"id": "a", "text": "exact gates"}])\nsearch_reason(ix, "gates")["assessment"]["status"]', "not_assessed"),
    ("search_quantum", "search_quantum(index, question, contract)", "Exact phase-oracle scenario search with source posture and record discrimination",
     'let ix = search_index([])\nsearch_quantum(ix, "scenario", {"posture":{"argument":{"claims":[{"id":"a","text":"A candidate assumption.","assumption":true}]},"issues":[{"id":"i","title":"Scenario","goal":"a"}]},"variables":["a"],"target":"a"})["marked_states"]', "1"),
    ("search_posture", "search_posture(index, question, contract)", "Record floors, adverse evidence, isolated concession/hold scenarios and origin-family withdrawals",
     'let ix = search_index([{"id":"a","text":"The credit was issued."}])\nsearch_posture(ix, "credit", {"argument":{"claims":[{"id":"c","text":"A credit was issued.","support":[{"document":"a","quote":"The credit was issued."}]}]},"issues":[{"id":"credit","title":"Credit issuance","goal":"c"}]})["issues"][0]["assessment"]["status"]', "supported"),
    ("linear_entropy", "linear_entropy(wires...)", "1 - reduced purity, including mixed states", 'wire q\nq <- bitflip(1/2)\nlinear_entropy(q)', "1/2"),
    # (name, signature, doc, example program ending in an expression, expected display)
    ("range", "range(n) · range(lo, hi [, step])", "whole numbers lo..hi-1", "range(2, 6)", "[2, 3, 4, 5]"),
    ("map", "map(f, list)", "apply a function to each element", "map(fn(x) => x * x, [1, 2, 3])", "[1, 4, 9]"),
    ("filter", "filter(f, list)", "keep the elements where f is true; (list, f) reads the same", "filter(fn(x) => x > 1, [1, 2, 3])", "[2, 3]"),
    ("get", "get(map, key, default)", "the value at key, or default when the key is absent", 'get({"a": 1}, "b", 0)', "0"),
    ("replace", "replace(text, old, new)", "every old in text becomes new", 'replace("WIRE/LOT 2", "/", " ")', "WIRE LOT 2"),
    ("startswith", "startswith(text, part)", "true when text begins with part", 'startswith("DEMO_10001", "DEMO_")', "true"),
    ("endswith", "endswith(text, part)", "true when text ends with part", 'endswith("DEMO_00160", "160")', "true"),
    ("reduce", "reduce(f, list, start)", "fold a list into one value", "reduce(fn(a, b) => a * b, [1, 2, 3, 4], 1)", "24"),
    ("sum", "sum(list)", "exact total", "sum([1/3, 1/3, 1/3])", "1"),
    ("min", "min(list) · min(a, b, …)", "smallest (exact comparison)", "min([sqrt(2), 3/2, 7/5])", "7/5"),
    ("max", "max(list) · max(a, b, …)", "largest (exact comparison)", "max(cos(pi/8), 9/10)", "√(2+√2)/2"),
    ("sorted", "sorted(list [, key])", "sorted copy", "sorted([3, 1, 2])", "[1, 2, 3]"),
    ("reverse", "reverse(list)", "reversed copy", "reverse([1, 2, 3])", "[3, 2, 1]"),
    ("len", "len(list | text | map | realm | register)", "how many", 'len("qubit")', "5"),
    ("push", "push(list, value)", "append in place; returns the list", "push([1, 2], 3)", "[1, 2, 3]"),
    ("pop", "pop(list)", "remove and return the last element", "pop([1, 2, 3])", "3"),
    ("set", "set(list_or_map, key, value)", "store in place; returns the collection", 'set({"a": 1}, "b", 2)', '{"a": 1, "b": 2}'),
    ("keys", "keys(map)", "the keys", 'keys({"x": 1, "y": 2})', '["x", "y"]'),
    ("values", "values(map)", "the values", 'values({"x": 1, "y": 2})', "[1, 2]"),
    ("contains", "contains(collection, value)", "membership", "contains([1, 2, 3], 2)", "true"),
    ("count", "count(list, value)", "how many equal value", "count([1, 2, 1], 1)", "2"),
    ("any", "any(list)", "true if any element is true", "any([false, true])", "true"),
    ("all", "all(list)", "true if every element is true", "all([true, false])", "false"),
    ("zip", "zip(a, b)", "pairs", "zip([1, 2], [3, 4])", "[[1, 3], [2, 4]]"),
    ("enumerate", "enumerate(list)", "index, element pairs", 'enumerate(["a", "b"])', '[[0, "a"], [1, "b"]]'),
    ("str", "str(value)", "text form", "str(1/2)", "1/2"),
    ("join", "join(list [, sep])", "join into text", 'join(["a", "b"], "-")', "a-b"),
    ("split", "split(text [, sep])", "split into a list", 'split("a b c")', '["a", "b", "c"]'),
    ("upper", "upper(text)", "upper case", 'upper("cx")', "CX"),
    ("lower", "lower(text)", "lower case", 'lower("CX")', "cx"),
    ("type", "type(value)", "the value's type", "type(h >> t)", "gate"),
    ("exactness", "exactness(value)", "numeric representation of a value and its container items; separate from circuit exactness",
     'exactness([$1.25, float(1/3)])["status"]', "mixed"),
    ("normalize", "normalize(meaning)", "a new unit-norm meaning with relative phase preserved",
     'space P { a, b }\nspace A { left, right }\nlet mm = joint(P, A, [["a", "left", 1], ["b", "right", 1]])\nexactness(normalize(given(mm, "left")))["status"]', "exact"),
    ("condition", "condition(joint, label)", "normalized first-space meaning and probability of the second-space condition",
     'space P { a, b }\nspace A { left, right }\nlet mm = joint(P, A, [["a", "left", 1], ["b", "right", 1]])\ncondition(mm, "left")["probability"]', "1/2"),
    ("int", "int(value)", "whole part (also parses text)", "int(7/2)", "3"),
    ("floor", "floor(value)", "round down", "floor(0 - 7/2)", "-4"),
    ("sqrt", "sqrt(x)", "exact when the root is in the field", "sqrt(8)", "2√2"),
    ("cos", "cos(angle)", "exact at dyadic angles", "cos(pi/8)", "√(2+√2)/2"),
    ("sin", "sin(angle)", "exact at dyadic angles", "sin(pi/4)", "√2/2"),
    ("abs", "abs(x)", "absolute value", "abs(0 - 3)", "3"),
    ("float", "float(x)", "the decimal approximation", "float(1/4)", "0.25  (float)"),
    ("xor", "xor(a, b, …)", "parity of whole numbers", "xor(1, 1, 1)", "1"),
    ("tcount", "tcount(gate)", "T gates in a gate value", "tcount(h >> t >> h >> t)", "2"),
    ("gcount", "gcount(gate)", "primitive gates in a gate value", "gcount(cx >> cx)", "2"),
    ("synth", "synth(gate)", "T-optimal (1 wire) or shortest (2 wires) circuit, proved", "tcount(synth(t >> t))", "0"),
    ("prob", "prob(wires…, \"bits\")", "exact probability of a measurement result",
     'wires q: 2\nq <- h & id >> cx\nprob(q, "11")', "1/2"),
    ("purity", "purity(wires…)", "tr ρ² of the wires (1 = pure)", "wires q: 2\nq <- h & id >> cx\npurity(q[0])", "1/2"),
    ("entanglement", "entanglement(wires…)", "1 − reduced purity; requires a globally pure state", "wires q: 2\nq <- h & id >> cx\nentanglement(q[0])", "1/2"),
    ("expval", "expval(wires…, \"XZ…\")", "exact Pauli expectation", 'wires q: 2\nq <- h & id >> cx\nexpval(q, "XX")', "1"),
    ("bloch_x", "bloch_x(wire) · bloch_y · bloch_z", "Bloch vector components", "wire q\nq <- h\nbloch_x(q)", "1"),
    ("stabilizers", "stabilizers(wires…)", "exact stabilizer generators", "wires q: 2\nq <- h & id >> cx\nstabilizers(q)",
     "+XX +ZZ"),
    ("sense", "sense(space, \"label\")", "a meaning that is one sense", 'space P { a, b }\nsense(P, "a")', "1|a>   in P"),
    ("blend", "blend(space, {\"label\": weight})", "a superposed meaning, α = √(w/Σw)",
     'space P { a, b, c }\ndistribution(blend(P, {"a": 1, "b": 1, "c": 2}))', '{"a": 1/4, "b": 1/4, "c": 1/2}'),
    ("read", "read(space, text, {\"cue\": \"sense\"})", "meaning from cue words (word-start match); none if no cue fires",
     'space P { o, f }\nread(P, "cover overages", {"overage": "o"})', "1|o>   in P"),
    ("joint", "joint(spaceA, spaceB, [[a, b, w], …])", "a joint (possibly entangled) meaning",
     'space P { a, b }\nspace Q { x, y }\nentanglement(joint(P, Q, [["a", "x", 1], ["b", "y", 1]]))', "1/2"),
    ("given", "given(joint, \"label\")", "the first-space meaning when the second space is label",
     'space P { a, b }\nspace Q { x, y }\ngiven(joint(P, Q, [["a", "x", 1], ["b", "y", 1]]), "y")', "√2/2|b>   in P"),
    ("fidelity", "fidelity(m1, m2)", "1 = the same meaning, 0 = orthogonal (also records)",
     'space P { a, b, c }\nfidelity(blend(P, {"a": 1, "b": 1, "c": 2}), sense(P, "c"))', "1/2"),
    ("gap", "gap(m1, m2)", "1 − fidelity", 'space P { a, b }\ngap(sense(P, "a"), sense(P, "b"))', "1"),
    ("phase", "phase(m, \"label\", angle)", "turn one sense's amplitude by an angle",
     'space P { a, b }\nfidelity(phase(blend(P, {"a": 1, "b": 1}), "b", pi/2), blend(P, {"a": 1, "b": 1}))', "1/2"),
    ("reframe", "reframe(m, \"from\", \"to\", angle)", "rotate weight from one sense toward another",
     'space P { a, b }\nreframe(sense(P, "a"), "a", "b", pi/2)', "1|b>   in P"),
    ("entropy", "entropy(m)", "bits of uncertainty (float)", 'space P { a, b, c, d }\nentropy(blend(P, {"a": 1, "b": 1, "c": 1, "d": 1}))',
     "2  (float)"),
    ("encode", "encode(meaning, wires [, wires])", "load a meaning onto fresh wires", 'space P { a, b }\nwire w\n'
     'encode(blend(P, {"a": 1, "b": 1}), w)\nprob(w, "1")', "1/2"),
    ("senses", "senses(wires, space)", "read wires back as sense probabilities", 'space P { a, b }\nwire w\nw <- x\nsenses(w, P)',
     '{"b": 1}'),
    ("trust", "trust([[family, quantity, value], …])", "source-family trust earned by agreement (numeric_spine rule)",
     'trust([["X", "q", 1], ["Y", "q", 1], ["Z", "q", 2]])', '{"X": 0.949874, "Y": 0.949874, "Z": 0.05}'),
    ("beliefs", "beliefs(claims, \"quantity\")", "the superposed belief about one quantity, α = √(c/Σc)",
     'type(beliefs([["X", "q", 1], ["Z", "q", 2]], "q"))', "meaning"),
    ("belnap", "belnap(witnessed, contradicted)", "four-valued truth: T F B N", "belnap(true, true)", "B"),
    ("jaccard", "jaccard(text, text)", "1 − shared words / all words", 'jaccard("rough plumbing", "plumbing rough")', "0"),
    ("money", "money(value)", "exact money from a number or text like \"($1,234.50)\"", 'money("($1,234.50)")', "-$1,234.50"),
    ("date", "date(\"YYYY-MM-DD\")", "a calendar day", 'date("2040-01-01") + 63', "2040-03-04"),
    ("libr", "libr(events, class [, order])", "lowest intermediate balance: the most of a class that can remain each day",
     'libr([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-02, "amount": -$70, "cls": ""}], "A")[1]["class_left"]',
     "$30.00"),
    ("share_bounds", "share_bounds(events, date, outflow, class)", "floor..ceiling of a class in one outflow, every convention",
     'share_bounds([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $50, "cls": "B"}, '
     '{"date": @2020-01-02, "amount": -$120, "cls": ""}], @2020-01-02, -$120, "A")["floor"]', "$70.00"),
    ("bound", "bound(lo, hi)", "a closed interval floor..ceiling; + - and comparisons act on both ends",
     "bound($1, $2) + bound($3, $4)", "$4.00 .. $6.00"),
    ("lo", "lo(bound)", "the floor of a bound", "lo(bound($80, $100))", "$80.00"),
    ("hi", "hi(bound)", "the ceiling of a bound", "hi(bound($80, $100))", "$100.00"),
    ("width", "width(bound)", "ceiling minus floor: how much the convention decides", "width(bound($80, $100))", "$20.00"),
    ("within", "within(value, bound)", "true when floor ≤ value ≤ ceiling", "within($90, bound($80, $100))", "true"),
    ("set_bounds", "set_bounds(events, fn, class)", "the class money that reaches ALL outflows the function selects, together, under every convention",
     'set_bounds([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, {"date": @2020-01-02, "amount": -$120, "cls": ""}, {"date": @2020-01-03, "amount": -$60, "cls": ""}], fn(e) => e["amount"] < $0, "A")["range"]', "$80.00 .. $100.00"),
    ("timing", "timing(statements, events, before, after)", "do the statements land within before..after days of an event more often than every circular shift of their own calendar does? exact p",
     'timing([@2020-01-01, @2020-01-11], [@2020-01-02, @2020-01-12], 0, 1)["observed"]', "2"),
    ("net_bounds", "net_bounds(pipes, fn, sources)", "the class money (the source accounts' own money) that the picked pipes carry together, under every allocation across every account — exact min-cost flow in cents",
     'net_bounds([{"from": "A", "to": "P", "amount": $100}, {"from": "B", "to": "P", "amount": $100}, {"from": "P", "to": "X", "amount": $120}, {"from": "P", "to": "Y", "amount": $60}], fn(p) => p["from"] == "P", "A")["range"]', "$80.00 .. $100.00"),
    ("butfor", "butfor(events, class [, order])", "replay without a class's deposits: deepest shortfall, first uncovered day, payments made on missing money",
     'butfor([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, {"date": @2020-01-02, "amount": -$120, "cls": ""}, {"date": @2020-01-03, "amount": -$60, "cls": ""}], "A")["deepest_shortfall"]', "$80.00"),
    ("whatif", "whatif(events, fn [, order])", "butfor for any deposits the function selects",
     'whatif([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, {"date": @2020-01-02, "amount": -$120, "cls": ""}, {"date": @2020-01-03, "amount": -$60, "cls": ""}], fn(e) => e["cls"] == "B")["first"]', "2020-01-02"),
    ("verdict", "verdict(events, date, outflow, class, op, amount)", "Belnap T/F/B: does the claim hold under every convention, none, or some",
     'verdict([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, {"date": @2020-01-02, "amount": -$120, "cls": ""}, {"date": @2020-01-03, "amount": -$60, "cls": ""}], @2020-01-02, -$120, "A", ">=", $60)["verdict"]', "B"),
    ("bank", "bank(\"statement.csv\" | [paths])", "native statement rows as events {date, amount, memo, check, type, month, page, begin, end}; returned items kept",
     f'len(bank("{_DEMO_STATEMENT}"))', "10"),
    ("foot", "foot(events)", "each statement month: beginning + credits − debits against the printed ending balance, exact",
     f'foot(bank("{_DEMO_STATEMENT}"))[0]["ties"]', "true"),
    ("returns", "returns(events)", "each returned item paired with its deposit: days provisional, what cleared meanwhile, floor..ceiling of the bounced money spent",
     f'returns(bank("{_DEMO_STATEMENT}"))[0]["spent"]', "$0.00 .. $600.00"),
    ("reconcile", "reconcile(events_a, events_b [, days])", "two versions of one account: matched rows, netted deposit+return pairs, splits, date shifts, first divergence",
     'reconcile([{"date": @2020-01-01, "amount": $5, "memo": "ck 1"}, {"date": @2020-01-02, "amount": -$5, "memo": "RTRN CK 1"}], [])["netted"][0]["amount"]',
     "$5.00"),
    ("calibrate", "calibrate(events)", "which same-day posting order the bank's own overdraft notices confirm, and overdrafts an order invents",
     f'calibrate(bank("{_DEMO_STATEMENT}"))["deposits_first"]["consistent"]', "true"),
    ("accounts", "accounts({\"account\": events, …})", "many accounts as one ledger: every event tagged with its account, in date order",
     'len(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}], "C": []}))', "5"),
    ("transfers", "transfers(events [, days])", "a debit in one account matched to the same-amount credit in another (nearest day, memo hints noted)",
     'transfers(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}, {"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}]}))[0]["to"]', "B"),
    ("consolidate", "consolidate(events [, days])", "group balance with inter-account transfers eliminated; what came from and went to the outside",
     'consolidate(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}, {"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}]}))["external_out"]', "$50.00"),
    ("cycles", "cycles(events [, days])", "money that leaves an account and comes back through others within 30 days, with the smallest leg",
     'cycles(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}, {"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}]}))[0]["accounts"]', '["A", "B", "A"]'),
    ("floats", "floats(events [, days])", "transfers credited before their debit leaves: money counted in two accounts at once",
     'len(floats(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}, {"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}]})))', "0"),
    ("carry", "carry(events, \"fifo\"|\"lifo\"|\"prorata\" [, days])", "class tracing that follows money through transfers into other accounts",
     'filter(fn(o) => o["memo"] == "pay", carry(accounts({"A": [{"date": @2020-01-01, "amount": $100, "memo": "dep", "cls": "X"}, {"date": @2020-01-02, "amount": -$60, "memo": "TRANSFER TO B"}, {"date": @2020-01-04, "amount": $10, "memo": "XFER FROM B"}], "B": [{"date": @2020-01-02, "amount": $60, "memo": "TRANSFER FROM A"}, {"date": @2020-01-03, "amount": -$50, "memo": "pay"}, {"date": @2020-01-04, "amount": -$10, "memo": "XFER TO A"}]}), "fifo")["outflows"])[0]["from"]', '{"X": $50.00}'),
    ("perspectives", "perspectives(facts, observers, quantity, value, days [, n, seed])", "seeded random (observer, day) draws; each judges the claim from that observer's records alone (Belnap), with an exact distribution and a state",
     'perspectives([{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}], ["bank", "lender"], "q", "yes", [@2020-01-13], 40, 1)["overall"]["T"] + perspectives([{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}], ["bank", "lender"], "q", "yes", [@2020-01-13], 40, 1)["overall"]["F"]', "1"),
    ("every_perspective", "every_perspective(facts, observers, quantity, value, days)", "the claim judged by every observer on every day",
     'every_perspective([{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}], ["bank"], "q", "yes", [@2020-01-09, @2020-01-11])[1]["verdict"]', "T"),
    ("pooled", "pooled(facts, group, quantity, value, date)", "each observer alone vs the group's pooled records; flags a contradiction that appears only when pooled",
     'pooled([{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}], ["bank", "lender"], "q", "yes", @2020-01-13)["pooled"]', "B"),
    ("knew", "knew(facts [, quantity, value])", "knowledge clock: the first day a record put the fact in each holder's hands",
     'knew([{"id": "b", "date": @2020-01-10, "holders": ["bank"], "quantity": "q", "value": "yes"}, {"id": "d", "date": @2020-01-12, "holders": ["lender"], "quantity": "q", "value": "no"}], "q")["lender"]["date"]', "2020-01-12"),
    ("audit_language", "audit_language(text [, {speaker, audience, date, facts, quantity, said}])", "surface features of a message and the communication functions they carry (ranked, exact); with facts, what the speaker held that day",
     'audit_language("Don\'t worry, it will come back in the draw.")["functions"][0]["function"]', "persuade"),
    ("audit_thread", "audit_thread([{speaker, audience, date, text}, …])", "per speaker and audience: feature counts and per-message rates, and words used with one audience but not another",
     'audit_thread([{"speaker": "J", "audience": "o", "text": "don\'t worry"}, {"speaker": "J", "audience": "l", "text": "paid $5.00"}])["audience_split"][0]["speaker"]', "J"),
    ("portal", "portal(query [, {\"realm\": rows, …}])", "a qubit portal: the query's anchors searched in every realm in scope, a state over realms, tunnels where an anchor lands in two or more",
     'portal("$60.00", {"A": [{"amount": "60.00"}], "B": [{"amount": "60.00"}]})["tunnels"][0]["anchor"]', "amount:60"),
    ("portal_realm", "portal_realm(\"name\", rows)", "register rows as a realm every later portal and search will open",
     'portal_realm("x", [{"amount": "1.00"}])', "1"),
    ("portal_stats", "portal_stats([log])", "what the LOCAL-ONLY portal log has learned: per-realm yields and anchors found in only one realm",
     'type(portal_stats("/nonexistent/portals.jsonl"))', "map"),
    ("portal_order", "portal_order([realms] [, seed])", "realms ordered by a Thompson draw from their learned yields",
     'len(portal_order(["a", "b"], 1))', "2"),
    ("last_portal", "last_portal()", "the portal the last search opened", 'last_portal()', "none"),
    ("kinds", "kinds()", "document families explicitly supplied through LOOM_DOCUMENT_CONFIG; empty until configured",
     'len(kinds())', "0"),
    ("docs", "docs(\"kind\" [, {field: text, …}])", "a document family as rows {kind, id, date, amount, party, text, page, loan_file, …}, read-only; each read becomes a portal realm",
     'docs("invoices")[0]["id"]', "sample-1"),
    ("anchor_check", "anchor_check(\"BATES\", figure or text [, reach])", "does the cited page print it — and if not, which neighbouring page does",
     'anchor_check("DOC_10010", "42.50")["prints_it"]', 'true'),
    ("stages", "stages(amount [, loan_file])", "one exact amount through the money stages: budgeted, billed, requested, advanced, received, paid (a cleared instrument)",
     'stages($42.50)["reached"]', '["billed"]'),
    ("collections", "collections(items, claims [, group, [parts]])", "an item (invoice) whose amount, or a listed fraction of it, is claimed in two or more groups (draws)",
     'collections([{"id": "i", "amount": $40}], [{"id": "a", "amount": $40, "draw_no": 1}, {"id": "b", "amount": $20, "draw_no": 2}], "draw_no", [1, 1/2])[0]["groups"]', '["1", "2"]'),
    ("rayleigh", "rayleigh([days], period)", "Rayleigh power Z = 2|Σ e^{2πi t/P}|²/N of event times at a trial period (pulsar timing); 2N for a perfect line, χ²₂ for random times",
     "rayleigh([0, 7, 14, 21, 28], 7)", "10  (float)"),
    ("spectral_lines", "spectral_lines(events [, minimum count])", "recurring exact amounts at a steady period: Rayleigh and spacing-regularity chances, ±P²/T line width, stragglers",
     'spectral_lines(map(fn(k) => {"date": @2020-01-01 + 30 * k, "amount": -$500, "account": "A"}, [0, 1, 2, 3, 4, 5]))[0]["period"]', "30"),
    ("zeno_test", "zeno_test(events, [observation days] [, start, end])", "quantum-Zeno test: does weight sit far from (or near) the observation days, against every circular shift of them (exact)",
     'zeno_test([{"date": @2020-01-01, "amount": $1}, {"date": @2020-01-08, "amount": $1}], [@2020-01-01, @2020-01-08], @2020-01-01, @2020-01-20)["p_near"]', "1/20"),
    ("detailed_balance", "detailed_balance(events with accounts [, days])", "money that went both ways between accounts, net currents, Kirchhoff divergence, entropy production σ, circulation around loops",
     'detailed_balance(accounts({"A": [{"date": @2020-01-01, "amount": -$5, "memo": "TRANSFER TO B"}, {"date": @2020-01-02, "amount": $5, "memo": "TRANSFER FROM B"}], "B": [{"date": @2020-01-01, "amount": $5, "memo": "TRANSFER FROM A"}, {"date": @2020-01-02, "amount": -$5, "memo": "TRANSFER TO A"}]}))["reciprocal"]', "$5.00"),
    ("residence", "residence(events, class [, threshold])", "Little's law: mean days a class's money stayed, floor (spent first) .. ceiling (spent last, LIBR), and the day it was gone",
     'residence([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, {"date": @2020-01-11, "amount": -$100, "cls": ""}, {"date": @2020-01-21, "amount": -$100, "cls": ""}], "A")["mean_days"]', "10 .. 20"),
    ("trace", "trace(events, \"fifo\"|\"lifo\"|\"prorata\" [, order])", "each outflow's composition by class",
     'trace([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-01, "amount": $100, "cls": "B"}, '
     '{"date": @2020-01-02, "amount": -$50, "cls": ""}], "prorata")[0]["from"]', '{"A": $25.00, "B": $25.00}'),
    ("spectrum", "spectrum(events, date, outflow, class)", "a class's share of one outflow under each doctrine and order, with floor and ceiling",
     'spectrum([{"date": @2020-01-01, "amount": $100, "cls": "A"}, {"date": @2020-01-02, "amount": -$40, "cls": ""}], @2020-01-02, -$40, "A")["floor"]',
     "$40.00"),
    ("post", "post(book, date, debit, credit, amount [, memo])", "a double-entry posting (money moves credit → debit)",
     'len(post([], @2020-01-01, "cash", "owner", $10))', "1"),
    ("balances", "balances(book [, as_of])", "account balances (debits positive); they always sum to $0.00",
     'balances(post([], @2020-01-01, "cash", "owner", $10))', '{"cash": $10.00, "owner": -$10.00}'),
    ("net", "net(book, a, b)", "net money from a to b", 'net(post([], @2020-01-01, "b", "a", $10), "a", "b")', "$10.00"),
    ("roundtrips", "roundtrips(events [, minimum, days])", "same amount in and out within a window",
     'len(roundtrips([{"date": @2020-01-01, "amount": $5000, "cls": "A"}, {"date": @2020-01-02, "amount": -$5000, "cls": ""}]))', "1"),
    ("subset_sum", "subset_sum(amounts, target [, max items])", "every set of amounts with exactly that total",
     "subset_sum([$10, $20, $30], $30)", "[[2], [0, 1]]"),
    ("window_sums", "window_sums(amounts, target)", "every run of consecutive amounts with exactly that total",
     'window_sums([$10, $20, $30], $50)[0]["start"]', "1"),
    ("grover_sum", "grover_sum(amounts, target)", "Grover's search over every subset: oracle calls and exact success probability",
     'grover_sum([$10, $20, $30, $40], $50)["oracle_calls"]', "2"),
    ("provenance", "provenance(events, date)", "the pooled account's mix by class as a meaning (pro-rata)",
     'type(provenance([{"date": @2020-01-01, "amount": $1, "cls": "A"}], @2020-01-01))', "meaning"),
    ("at", "at(formula, p, value, …)", "evaluate a formula exactly", "symbol p\nat(3*p^2 - 2*p^3, p, 1/10)", "7/250"),
    ("deriv", "deriv(formula, p)", "derivative of a formula", "symbol p\nderiv(3*p^2 - 2*p^3, p)", "6p - 6p²"),
]


def describe_builtins(include_integrations=False):
    """Verify portable examples; native-store examples require explicit integration use."""
    out = []
    for name, sig, doc, ex_src, expect_txt in BUILTIN_DOCS:
        scope = "configured-data-integration" if name in {"docs", "anchor_check", "stages"} else "portable"
        if scope != "portable" and not include_integrations:
            out.append({"name": name, "signature": sig, "doc": doc, "example": ex_src.replace("\n", " ; "),
                        "result": None, "verified": None, "scope": scope, "status": "integration_required"})
            continue
        lines = ex_src.split("\n")
        prog = "\n".join(lines[:-1] + ["print " + lines[-1]])
        try:
            it = Interp(seed=1, echo=False).run(prog)
            got = it.lines[-1].split("  ≈")[0] if not expect_txt.endswith("(float)") else it.lines[-1]
        except SurdError as e:
            got = e.render()
        out.append({"name": name, "signature": sig, "doc": doc, "example": ex_src.replace("\n", " ; "),
                    "result": got, "verified": got == expect_txt, "scope": scope,
                    "status": "verified" if got == expect_txt else "failed"})
    return out


def repl():
    it = Interp(seed=None)
    print(f"loom {VERSION} — exact qubit REPL. 'quit' to leave.")
    buf = ""
    while True:
        try:
            line = input("... " if buf else "loom> ")
        except EOFError:
            print(); break
        if not buf and line.strip() in ("quit", "exit"): break
        buf += line + "\n"
        try:
            toks = lex(buf)
            depth = sum(1 if t[1] == "{" else -1 for t in toks if t[0] == "op" and t[1] in "{}")
        except SurdError:
            depth = 0
        if depth > 0: continue
        try:
            it.run(buf)
        except SurdError as e:
            print(e.render())
        except RecursionError:
            print("S-E014: nesting too deep")
        except Exception as e:  # keep the session alive; report the defect plainly
            print(f"internal error {type(e).__name__}: {e} — please report this program")
        buf = ""


def _main(argv):
    if argv and argv[0]=='normalize':
        from loom_research.normalization import cli
        return cli(argv[1:])
    if argv and argv[0] == "ontology":
        from loom_research.ontology import cli
        return cli(argv[1:])
    if argv and argv[0]=='studio':
        from loom_research.studio import cli
        return cli(argv[1:])
    if argv and argv[0]=='sqlite':
        from loom_sqlite import cli
        return cli(argv[1:])
    if argv and argv[0] in ("graph", "research"):
        from loom_research.cli import cli
        return cli(argv[0],argv[1:])
    if argv and argv[0] == "timeline":
        import surd_timeline
        return surd_timeline.cli(argv[1:])
    if argv and argv[0] in ("qulap", "qulaps"):
        import surd_integrations
        return surd_integrations.cli(argv[1:])
    if argv and argv[0] == "improve":
        import surd_improve
        return surd_improve.cli(argv[1:])
    if argv and argv[0] == "build":
        import surd_build
        return surd_build.cli(argv[1:])
    if argv and argv[0] == "search":
        return surd_search.cli(argv[1:])
    if argv and argv[0] in ("--contract", "--validate-exchange"):
        root = os.environ.get("LOOM_LANGUAGE_ROOT")
        if not root:
            print("Set LOOM_LANGUAGE_ROOT to your explicit language-suite installation.", file=sys.stderr)
            return 2
        sys.path.insert(0, os.path.join(root, "tools", "language_suite"))
        import contract
        contract.cli("surd", os.path.realpath(__file__), argv)
    args = list(argv)
    flags = {}
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--seed", "--shots", "--max-steps", "--timeout"):
            try:
                if i + 1 >= len(args): raise ValueError
                value = float(args[i + 1]) if a == "--timeout" else int(args[i + 1])
                if a != "--seed" and (value <= 0 or (a == "--timeout" and not math.isfinite(value))): raise ValueError
            except ValueError:
                e = SurdError("S-E004", f"{a} needs " + ("an integer" if a == "--seed" else "a finite positive number" if a == "--timeout" else "a positive integer"),
                              None, f"check the value after {a}")
                if "--json" in args: print(json.dumps({"ok": False, "error": e.as_dict()}))
                else: print(e.render(), file=sys.stderr)
                return 2
            flags[a[2:].replace("-", "_")] = value; i += 2; continue
        if a in ("--float", "--json", "--trace", "--mixed", "--require-proof", "--integrations"):
            flags[a[2:]] = True; i += 1; continue
        rest.append(a); i += 1
    cmd = rest[0] if rest else "help"
    if cmd in ("help", "-h", "--help"):
        print(__doc__); return 0
    if cmd == "version":
        print(f"loom {VERSION}"); return 0
    if cmd == "selftest":
        return 0 if selftest() else 1
    if cmd == "describe":
        if flags.get("json"): print(json.dumps(dict(DESCRIBE, builtins=describe_builtins(flags.get("integrations", False))), indent=2, ensure_ascii=False))
        else:
            for k, v in DESCRIBE.items(): print(f"{k}: {v}")
        return 0
    if cmd == "repl":
        repl(); return 0
    as_json = flags.get("json")
    limits = {"max_steps": flags.get("max_steps"), "timeout": flags.get("timeout")}
    if cmd == "eval" and len(rest) >= 2:
        src = rest[1].replace("\\n", "\n")
        it = Interp(seed=flags.get("seed"), echo=False, **limits)
        try:
            v = it.evaluate(src, setup=True)
            exactness = value_exactness(v)
            out = {"schema": "surd.eval.v1", "ok": True, "display": fmt_value(v), "type": type_name(v),
                   "exact": exactness["exact"] is True, "exactness": exactness, "exact_scope": "returned_value",
                   "float": finite_approximation(v), "notes": it.notes,
                   "execution": it.budget.info()}
        except SurdError as e:
            out = {"schema": "surd.eval.v1", "ok": False, "error": e.as_dict()}
        print(json.dumps(out, ensure_ascii=False) if as_json else (out.get("display") or out["error"]["message"]))
        return 0 if out["ok"] else 1
    if cmd == "test" and len(rest) >= 2:
        it = Interp(seed=flags.get("seed"), echo=False, **limits)
        err = None
        try:
            it.run(open(rest[1]).read())
        except SurdError as e:
            err = e.as_dict()
        passed = sum(1 for t_ in it.tests if t_["ok"])
        loose = [e_ for e_ in it.expects]
        res = {"schema": "surd.test.v1", "ok": err is None and passed == len(it.tests) and all(e_["ok"] for e_ in loose),
               "passed": passed, "total": len(it.tests), "tests": it.tests,
               "expects": {"passed": sum(1 for e_ in loose if e_["ok"]), "total": len(loose)}, "error": err}
        if as_json: print(json.dumps(res, ensure_ascii=False, default=str))
        else:
            for t_ in it.tests: print(f"  {'✓' if t_['ok'] else '✗'} {t_['name']}  (line {t_['line']}, {t_['checks']} checks)")
            print(f"tests {passed}/{len(it.tests)} · expects {res['expects']['passed']}/{res['expects']['total']}"
                  + (f" · error {err['code']}: {err['message']}" if err else ""))
        return 0 if res["ok"] else 1
    if cmd == "serve":
        it = Interp(seed=flags.get("seed"), echo=False, **limits)
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw: continue
            try:
                req = json.loads(raw)
                if not isinstance(req, dict): raise ValueError
            except ValueError:
                print(json.dumps({"ok": False, "id": None, "error": {"code": "S-E001", "message": "request must be a JSON object",
                                                                     "hint": "send an object with op and optional code/id"}})); sys.stdout.flush(); continue
            rid, op = req.get("id"), req.get("op")
            try:
                if op == "exec":
                    n0 = len(it.lines); it.run(req.get("code", ""))
                    rep_ = {"ok": True, "output": it.lines[n0:]}
                elif op == "eval":
                    v = it.evaluate(req.get("code", ""))
                    rep_ = {"ok": True, "display": fmt_value(v), "type": type_name(v), "exactness": value_exactness(v)}
                elif op == "reset":
                    seed = req.get("seed")
                    if seed is not None and type(seed) is not int:
                        raise SurdError("S-E004", "seed must be an integer or null", None, "send seed: 1")
                    it = Interp(seed=seed, echo=False, **limits); rep_ = {"ok": True}
                elif op == "describe":
                    rep_ = {"ok": True, "language": DESCRIBE["name"], "version": VERSION, "builtins": describe_builtins()}
                elif op == "status":
                    rep_ = {"ok": True, "wires": it.state.n, "exact": it.state.exact, "kind": it.state.kind,
                            "execution": it.budget.info(),
                            "registers": {k: len(v) for k, v in it.qregs.items()},
                            "names": sorted(k for k in it.globals if not k.startswith("__"))}
                else:
                    rep_ = {"ok": False, "error": {"code": "S-E002", "message": f"unknown op {op!r}",
                                                   "hint": "ops: exec eval reset describe status"}}
            except SurdError as e:
                rep_ = {"ok": False, "error": e.as_dict()}
            rep_["id"] = rid
            print(json.dumps(rep_, ensure_ascii=False, default=str)); sys.stdout.flush()
        return 0
    if cmd == "explain" and len(rest) >= 2:
        it = Interp(seed=flags.get("seed"), echo=False, **limits)
        try:
            print("\n".join(it.explain(open(rest[1]).read())))
        except SurdError as e:
            print(e.render(), file=sys.stderr); return 1
        return 0
    if cmd == "from-qasm" and len(rest) >= 2:
        try:
            it = Interp(seed=1, echo=False)
            n, ops, meas, (qregs, cregs) = qasm_parse(open(rest[1]).read(), it)
            names = []
            for rname, (base, size) in qregs.items():
                safe = rname if rname not in PRIM and rname not in KEYWORDS and rname not in it.gates else rname + "_"
                print(f"wires {safe}: {size}")
                names += [f"{safe}[{j}]" for j in range(size)]
            cnames = []
            for rname, (base, size) in cregs.items():
                safe = rname if rname not in PRIM and rname not in KEYWORDS else rname + "_"
                print(f"bits {safe}: {size}")
                cnames += [f"{safe}[{j}]" for j in range(size)]
            for (nm, ps, t, c) in ops:
                par = "(" + ", ".join(surd_angle(p_) for p_ in ps) + ")" if ps else ""
                w = [names[x] for x in tuple(c) + (t,)]
                print(f"{w[0] if len(w) == 1 else '(' + ', '.join(w) + ')'} <- {'ctl ' * len(c)}{nm}{par}")
            for q, cb in meas:
                print(f"{names[q]} <- reset" if cb is None else f"{cnames[cb]} <- measure {names[q]}")
            return 0
        except SurdError as e:
            print(e.render(), file=sys.stderr); return 1
    if cmd not in ("run", "draw", "qasm", "check", "stats", "opt") or len(rest) < 2:
        print(__doc__); return 2
    path = rest[1]
    with open(path) as source:
        src = source.read()
    as_json = flags.get("json")
    try:
        if cmd == "check":
            try:
                prog = parse(src)
                res = {"schema": "surd.check.v1", "ok": True, "statements": len(prog), "errors": []}
            except SurdError as e:
                res = {"schema": "surd.check.v1", "ok": False, "errors": [e.as_dict()]}
            if as_json: print(json.dumps(res, ensure_ascii=False))
            elif res["ok"]: print(f"{path}: ok ({res['statements']} statements)")
            else: print(SurdError(**{k: v for k, v in {"code": res["errors"][0]["code"], "msg": res["errors"][0]["message"]}.items()}).render())
            return 0 if res["ok"] else 1
        if cmd == "run" and flags.get("shots"):
            n = flags["shots"]
            hist, failed = run_shots(src, n, flags.get("seed"), flags.get("float"), flags.get("mixed"), ExecutionBudget(**limits))
            if as_json:
                print(json.dumps({"shots": n, "counts": dict(sorted(hist.items())), "expect_failures": failed}))
            else:
                print(f"{n} shots")
                for k in sorted(hist):
                    c = hist[k]
                    print(f"  {k}  {c:>7}  {c / n:.4f}  {'█' * round(40 * c / n)}")
                if failed: print(f"✗ {failed} expect failure(s) across shots")
            return 1 if failed else 0
        it = Interp(seed=flags.get("seed"), echo=not as_json and cmd == "run", force_float=flags.get("float"),
                    force_mixed=flags.get("mixed"), **limits)
        it.portals_on = cmd == "run" and os.environ.get("SURD_PORTALS", "1") != "0"     # every search opens a logged portal
        it.run(src)
        if cmd == "stats":
            st = circuit_stats(it.trace, it.state.n)
            print(json.dumps(st, indent=2) if as_json else "\n".join(fmt_stats(st)))
            return 0
        if cmd == "opt":
            if it.used_if and not flags.get("trace"):
                raise SurdError("S-E013", "the program uses measurement-dependent classical execution", None,
                               "opt rewrites only this run's path and parameters; --trace accepts that")
            after = optimize(it.trace)
            b, a = circuit_stats(it.trace, it.state.n), circuit_stats(after, it.state.n)
            proof = prove_same(it.trace, after, it.state.n)
            if proof.startswith("FAILED") or (flags.get("require-proof") and not proof.startswith("PROVED")):
                raise SurdError("S-E013", f"optimization verification: {proof}", None,
                                "exact proof requires a gate-only circuit of at most 8 wires using exact arithmetic")
            print(f"# optimized by loom {VERSION} from {os.path.basename(path)}")
            print(f"# before: {b['gates']} gates, depth {b['depth']}, T-count {b['t_count']}")
            print(f"# after:  {a['gates']} gates, depth {a['depth']}, T-count {a['t_count']}")
            print(f"# proof:  {proof}")
            print("\n".join(emit_surd(it, after)))
            return 0
        if cmd == "draw":
            print("\n".join(draw(it.trace, it.qnames, it.cnames))); return 0
        if cmd == "qasm":
            print(to_qasm(it, flags.get("trace"))); return 0
        bad = [e for e in it.expects if not e["ok"]]
        if as_json:
            print(json.dumps({"ok": not bad, "output": it.lines, "exact": it.state.exact,
                              "exact_scope": "quantum_state", "circuit_exact": it.state.exact, "notes": it.notes,
                              "expects": {"passed": len(it.expects) - len(bad),
                                          "failed_lines": [e["line"] for e in bad]},
                              "bits": {n: "".join(str(it.cbits[i]) for i in idx) for n, idx in it.cregs.items()}},
                             indent=2))
        else:
            for nt in it.notes: print("note:", nt)
            if it.expects:
                print(f"{'✓' if not bad else '✗'} {len(it.expects) - len(bad)}/{len(it.expects)} expects"
                      f"  ({'exact' if it.state.exact else 'float'})")
        return 1 if bad else 0
    except SurdError as e:
        if as_json: print(json.dumps({"ok": False, "error": e.as_dict()}))
        else: print(e.render(), file=sys.stderr)
        return 1


def main(argv):
    if argv and argv[0] == "usage":
        import surd_usage
        return surd_usage.cli(argv[1:])
    if os.environ.get("SURD_USAGE", "1") == "0" or os.environ.get("SURD_PORTABLE_TEST") == "1":
        return _main(argv)
    import surd_usage
    import time as _usage_time
    started = _usage_time.monotonic()
    source_pin = surd_usage.source_hash(argv) if argv and argv[0] in surd_usage.COMMANDS else None
    result, outcome = None, "interrupted"
    try:
        result = _main(argv)
        outcome = "completed" if result in (None, 0) else "rejected"
        return result
    except SystemExit as error:
        result = error.code if isinstance(error.code, int) else 1
        outcome = "completed" if result == 0 else "rejected"
        raise
    except Exception:
        outcome = "crashed"
        raise
    finally:
        try:
            surd_usage.record(argv, VERSION, round(_usage_time.monotonic()-started, 6), result, outcome, source_pin)
        except Exception as error:
            print("surd usage receipt unavailable: " + str(error), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
