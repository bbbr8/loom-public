#!/usr/bin/env python3
"""Surd fuzz battery — every check compares Ket against an independent numpy simulator written here from the
textbook formulas (none of ket's matrices or index code is reused), or checks that garbage input only ever
raises SurdError. Prints one JSON summary; exit 1 on any mismatch or crash."""
import json, math, cmath, random, sys, os
import argparse, hashlib, signal, time, traceback
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import surd

I2 = np.eye(2, dtype=complex)
P1 = np.array([[0, 0], [0, 1]], dtype=complex)


def U(name, ps):
    c = lambda t: math.cos(t / 2); s = lambda t: math.sin(t / 2); e = lambda t: complex(math.cos(t), math.sin(t))
    if name == "h": return np.array([[1, 1], [1, -1]]) / math.sqrt(2)
    if name == "x": return np.array([[0, 1], [1, 0]], dtype=complex)
    if name == "y": return np.array([[0, -1j], [1j, 0]])
    if name == "z": return np.diag([1, -1]).astype(complex)
    if name == "s": return np.diag([1, 1j])
    if name == "sdg": return np.diag([1, -1j])
    if name == "t": return np.diag([1, e(math.pi / 4)])
    if name == "tdg": return np.diag([1, e(-math.pi / 4)])
    if name == "sx": return np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]]) / 2
    if name == "sxdg": return np.array([[1 - 1j, 1 + 1j], [1 + 1j, 1 - 1j]]) / 2
    if name == "rx": t = ps[0]; return np.array([[c(t), -1j * s(t)], [-1j * s(t), c(t)]])
    if name == "ry": t = ps[0]; return np.array([[c(t), -s(t)], [s(t), c(t)]], dtype=complex)
    if name == "rz": t = ps[0]; return np.diag([e(-t / 2), e(t / 2)])
    if name == "p": return np.diag([1, e(ps[0])])
    if name == "u":
        t, ph, la = ps
        return np.array([[c(t), -e(la) * s(t)], [e(ph) * s(t), e(ph + la) * c(t)]])
    raise KeyError(name)


def full(n, mat, t, ctrls):
    """I + (prod P1_c) (x) (U - I)_t, qubit q = bit q of the index (kron puts qubit n-1 first)"""
    f = []
    for q in reversed(range(n)):
        f.append(mat - I2 if q == t else (P1 if q in ctrls else I2))
    M = f[0]
    for x in f[1:]: M = np.kron(M, x)
    return np.eye(2 ** n, dtype=complex) + M


EXACT_ANG = ["pi/4", "pi/2", "-pi/4", "3*pi/4", "pi", "0", "-pi/2"]
ONE_Q = ["h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "sxdg", "rx", "ry", "rz", "p", "u"]
NPAR = {"rx": 1, "ry": 1, "rz": 1, "p": 1, "u": 3}


def rand_angle(rng, exact_bias):
    if rng.random() < exact_bias:
        a = rng.choice(EXACT_ANG); return a, eval(a.replace("pi", "math.pi"))
    v = round(rng.uniform(-3, 3), 3); return repr(v), v


def rand_circuit(rng, n, depth, exact_bias=0.85, multi=True):
    """returns (ket source lines, list of (name, ps_float, target, ctrls))"""
    src, ops = [], []
    q = lambda i: f"q[{i}]"
    for _ in range(depth):
        r = rng.random()
        if n >= 2 and multi and r < 0.3:
            kind = rng.choice(["cx", "cz", "swap", "cp", "crz", "ch", "ctrl"] + (["ccx"] if n >= 3 else []))
            qs = rng.sample(range(n), 3 if kind == "ccx" else 2)
            if kind == "cx": src.append(f"({q(qs[0])}, {q(qs[1])}) <- cx"); ops.append(("x", (), qs[1], (qs[0],)))
            elif kind == "cz": src.append(f"({q(qs[0])}, {q(qs[1])}) <- cz"); ops.append(("z", (), qs[1], (qs[0],)))
            elif kind == "ch": src.append(f"({q(qs[0])}, {q(qs[1])}) <- ch"); ops.append(("h", (), qs[1], (qs[0],)))
            elif kind == "ccx":
                src.append(f"({q(qs[0])}, {q(qs[1])}, {q(qs[2])}) <- ccx"); ops.append(("x", (), qs[2], (qs[0], qs[1])))
            elif kind == "swap":
                a, b = qs; src.append(f"({q(a)}, {q(b)}) <- swap")
                ops += [("x", (), b, (a,)), ("x", (), a, (b,)), ("x", (), b, (a,))]
            elif kind in ("cp", "crz"):
                s, v = rand_angle(rng, exact_bias); src.append(f"({q(qs[0])}, {q(qs[1])}) <- {kind}({s})")
                ops.append(("p" if kind == "cp" else "rz", (v,), qs[1], (qs[0],)))
            else:  # general ctrl on a random 1q gate
                g = rng.choice(ONE_Q); k = NPAR.get(g, 0)
                angs = [rand_angle(rng, exact_bias) for _ in range(k)]
                par = "(" + ", ".join(a for a, _ in angs) + ")" if k else ""
                src.append(f"({q(qs[0])}, {q(qs[1])}) <- ctl {g}{par}")
                ops.append((g, tuple(v for _, v in angs), qs[1], (qs[0],)))
        else:
            g = rng.choice(ONE_Q); k = NPAR.get(g, 0); t = rng.randrange(n)
            angs = [rand_angle(rng, exact_bias) for _ in range(k)]
            par = "(" + ", ".join(a for a, _ in angs) + ")" if k else ""
            inv = rng.random() < 0.15
            src.append(f"{q(t)} <- {'~' if inv else ''}{g}{par}")
            m = U(g, tuple(v for _, v in angs))
            ops.append(("__mat__", m.conj().T if inv else m, t, ()))
    return src, ops


def np_state(n, ops):
    v = np.zeros(2 ** n, dtype=complex); v[0] = 1
    for name, ps, t, c in ops:
        m = ps if name == "__mat__" else U(name, ps)
        v = full(n, m, t, c) @ v
    return v


def probs_np(v, n):
    return {format(i, f"0{n}b")[::-1]: abs(a) ** 2 for i, a in enumerate(v) if abs(a) ** 2 > 1e-14}


def probs_ket(it):
    d = it.state.distribution(list(range(it.state.n)))
    return {k: float(p) for k, p in d.items()}


def close(d1, d2, tol=1e-9):
    keys = set(d1) | set(d2)
    return all(abs(d1.get(k, 0) - d2.get(k, 0)) <= tol for k in keys)


class CaseTimeout(BaseException):
    pass


def bounded_run(text, seconds):
    def expired(*_):
        raise CaseTimeout()
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        return surd.Interp(seed=1, echo=False, max_steps=100_000).run(text)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, old_timer[0]-(time.monotonic()-started)), old_timer[1])


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Seeded Surd differential and crash fuzz battery")
    ap.add_argument('--seed', type=int, default=20261011)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--crash-cases', type=int, default=1500)
    ap.add_argument('--case-timeout', type=float, default=5.0)
    fuzz_options = ap.parse_args(argv)
    if fuzz_options.crash_cases < 1 or not 0 < fuzz_options.case_timeout <= 60:
        ap.error('crash cases must be positive; case timeout must be in (0, 60]')
    started = time.monotonic()
    rng = random.Random(fuzz_options.seed)
    res = {"statevector": [0, 0], "float_mode": [0, 0], "mixed_mode": [0, 0], "expval": [0, 0], "bloch": [0, 0],
           "noise": [0, 0], "optimizer": [0, 0], "equality": [0, 0], "algebra": [0, 0], "field": [0, 0], "paths": [0, 0], "stabilizers": [0, 0], "synth": [0, 0], "symbolic": [0, 0], "realms": [0, 0], "grover": [0, 0], "games": [0, 0], "float_vector": [0, 0], "noisy_vector": [0, 0], "qasm_roundtrip": [0, 0], "programs": [0, 0], "semantics": [0, 0], "money": [0, 0], "bank": [0, 0], "ledger": [0, 0], "observe": [0, 0], "physics": [0, 0], "exact_kept": 0, "crash_fuzz": [0, 0],
           "amplitudes": [0, 0], "normalization": [0, 0], "crash_timeouts": 0, "failures": []}

    global _active_report, _active_options
    _active_report, _active_options = res, fuzz_options

    def tally(key, ok, info):
        res[key][0] += bool(ok); res[key][1] += 1
        if not ok: res["failures"].append({"category": key, "case_index": res[key][1]-1, "seed": fuzz_options.seed, "detail": info})

    for trial in range(300):
        n = rng.randint(1, 4)
        src, ops = rand_circuit(rng, n, rng.randint(1, 14))
        text = f"wires q: {n}\n" + "\n".join(src)
        ref = probs_np(np_state(n, ops), n)
        it = surd.Interp(seed=1, echo=False).run(text)
        res["exact_kept"] += it.state.exact
        tally("statevector", close(probs_ket(it), ref), text)
        actual_vector = np.array([complex(a) for a in it.state.v]) * cmath.exp(1j * surd.ang_float(it.gphase))
        # Probability-only comparisons miss wrong relative phases.
        tally("amplitudes", bool(np.allclose(actual_vector, np_state(n, ops), atol=1e-9, rtol=0)), text)
        tally("normalization", bool(np.all(np.isfinite(actual_vector)) and abs(np.vdot(actual_vector, actual_vector)-1) < 1e-9), text)
        itf = surd.Interp(seed=1, echo=False, force_float=True).run(text)
        tally("float_mode", close(probs_ket(itf), ref), text)
        if n <= 3:
            itm = surd.Interp(seed=1, echo=False, force_mixed=True).run(text)
            tally("mixed_mode", close(probs_ket(itm), ref), text)
        # expval of a random Pauli string
        ps = "".join(rng.choice("IXYZ") for _ in range(n))
        v = np_state(n, ops)
        P = np.array([[1]], dtype=complex)
        PM = {"I": I2, "X": U("x", ()), "Y": U("y", ()), "Z": U("z", ())}
        for q in reversed(range(n)): P = np.kron(P, PM[ps[q]])
        ev_ref = float(np.real(v.conj() @ P @ v))
        ev = float(it.state.expval(list(range(n)), ps))
        tally("expval", abs(ev - ev_ref) < 1e-9, f"{text}\n{ps}: {ev} vs {ev_ref}")
        # bloch vector of every qubit, against numpy's reduced density matrix
        rho = np.outer(v, v.conj()).reshape([2] * (2 * n))
        bl_ok = True
        for q in range(n):
            ax = n - 1 - q
            keep = [a for a in range(n) if a != ax]
            sub = rho
            for a in sorted(keep, reverse=True):
                sub = np.trace(sub, axis1=a, axis2=a + sub.ndim // 2)
            bx, by, bz = 2 * sub[0, 1].real, -2 * sub[0, 1].imag, (sub[0, 0] - sub[1, 1]).real
            kx, ky, kz = (float(x) for x in it.state.bloch(q))
            bl_ok &= abs(bx - kx) < 1e-9 and abs(by - ky) < 1e-9 and abs(bz - kz) < 1e-9
        tally("bloch", bl_ok, text)
        # optimizer: proof + numpy re-check of the emitted program
        after = surd.optimize(it.trace)
        proof = surd.prove_same(it.trace, after, n)
        src2 = f"wires q: {n}\n" + "\n".join(surd.emit_surd(it, after)[1:])
        it2 = surd.Interp(seed=1, echo=False).run(src2)
        tally("optimizer", proof.startswith(("PROVED", "NUMERICALLY VERIFIED")) and close(probs_ket(it2), ref), f"{text}\n=>\n{src2}\n{proof}")

    # targeted optimizer stress: circuits built to cancel
    for trial in range(100):
        n = rng.randint(1, 3)
        src, ops = rand_circuit(rng, n, rng.randint(2, 8), exact_bias=0.95)
        mirror = []
        for line in reversed(src):
            lhs, rhs = line.split(" <- ", 1)
            mirror.append(f"{lhs} <- ~({rhs})")
        text = f"wires q: {n}\n" + "\n".join(src + mirror)
        it = surd.Interp(seed=1, echo=False).run(text)
        after = surd.optimize(it.trace)
        proof = surd.prove_same(it.trace, after, n)
        tally("optimizer", proof.startswith(("PROVED", "NUMERICALLY VERIFIED")), f"{text}\n{proof}")

    # noise: Kraus reference
    def kraus(name, p):
        X, Y, Z = U("x", ()), U("y", ()), U("z", ())
        if name == "dephase": return [math.sqrt(1 - p) * I2, math.sqrt(p) * Z]
        if name == "bitflip": return [math.sqrt(1 - p) * I2, math.sqrt(p) * X]
        if name == "depolarize": return [math.sqrt(1 - p) * I2] + [math.sqrt(p / 3) * M for M in (X, Y, Z)]
        if name == "ampdamp": return [np.array([[1, 0], [0, math.sqrt(1 - p)]]), np.array([[0, math.sqrt(p)], [0, 0]])]
    for trial in range(120):
        n = rng.randint(1, 3)
        src, ops = rand_circuit(rng, n, rng.randint(1, 8))
        rho = np.outer(np_state(n, ops), np_state(n, ops).conj())
        extra, src2 = [], list(src) + ["q[0] <- dephase(0)"]
        for _ in range(rng.randint(1, 3)):
            if rng.random() < 0.25:
                t = rng.randrange(n); src2.append(f"q[{t}] <- reset")
                ks = [np.array([[1, 0], [0, 0]], dtype=complex), np.array([[0, 1], [0, 0]], dtype=complex)]
                rho = sum(_lift(n, K, t) @ rho @ _lift(n, K, t).conj().T for K in ks)
                continue
            ch = rng.choice(["dephase", "bitflip", "depolarize", "ampdamp"])
            pn, pd = rng.randint(0, 5), rng.choice([5, 8, 10])
            p = pn / pd; t = rng.randrange(n)
            src2.append(f"q[{t}] <- {ch}({pn}/{pd})")
            ks = kraus(ch, p)
            rho = sum(_lift(n, K, t) @ rho @ _lift(n, K, t).conj().T for K in ks)
            # follow with a random gate to mix
            g = rng.choice(["h", "s", "t", "x"]); t2 = rng.randrange(n)
            src2.append(f"q[{t2}] <- {g}"); M = full(n, U(g, ()), t2, ()); rho = M @ rho @ M.conj().T
        text = f"wires q: {n}\n" + "\n".join(src2)
        it = surd.Interp(seed=1, echo=False).run(text)
        ref = {format(i, f"0{n}b")[::-1]: float(np.real(rho[i, i])) for i in range(2 ** n)
               if np.real(rho[i, i]) > 1e-14}
        pur_ref = float(np.real(np.trace(rho @ rho)))
        ok = close(probs_ket(it), ref) and abs(float(it.state.purity(list(range(n)))) - pur_ref) < 1e-9
        tally("noise", ok, text)

    # equality: random 1-2 qubit gates vs numpy unitary
    for trial in range(80):
        n = rng.randint(1, 2)
        a_src, a_ops = rand_circuit(rng, n, rng.randint(1, 5), exact_bias=0.9)
        if rng.random() < 0.5:
            b_src, b_ops = a_src + ["q[0] <- x", "q[0] <- x"], a_ops
        elif rng.random() < 0.5:
            b_src, b_ops = a_src + ["q[0] <- rz(2*pi)"], a_ops + [("rz", (2 * math.pi,), 0, ())]
        else:
            b_src, b_ops = rand_circuit(rng, n, rng.randint(1, 5), exact_bias=0.9)
        args = ", ".join(f"q{i}" for i in range(n))
        sub = lambda lines: "; ".join(l.replace("q[0]", "q0").replace("q[1]", "q1") for l in lines)
        text = f"gate ga: {args} {{ {sub(a_src)} }}\ngate gb: {args} {{ {sub(b_src)} }}\nwire zz\n" \
               f"let e1 = (ga == gb)\nlet e2 = (ga =~ gb)\nprint e1\nprint e2"
        UA = np.eye(2 ** n, dtype=complex); UB = np.eye(2 ** n, dtype=complex)
        for name, ps, t, c in a_ops: UA = full(n, ps if name == "__mat__" else U(name, ps), t, c) @ UA
        for name, ps, t, c in b_ops: UB = full(n, ps if name == "__mat__" else U(name, ps), t, c) @ UB
        eq = np.allclose(UA, UB, atol=1e-9)
        k = np.argmax(np.abs(UA).ravel()); lam = UB.ravel()[k] / UA.ravel()[k]
        eqv = np.allclose(UA * lam, UB, atol=1e-9)
        it = surd.Interp(seed=1, echo=False).run(text)
        got = (it.globals["e1"], it.globals["e2"])
        tally("equality", got == (bool(eq), bool(eqv)), f"{text}\nket {got} numpy {(eq, eqv)}")

    # gate algebra: >> & ~ ctl ^ against numpy built from the same expression tree
    P0 = np.array([[1, 0], [0, 0]], dtype=complex)

    def rand_expr(depth, arity):
        if arity == 1 and (depth <= 0 or rng.random() < 0.3):
            g = rng.choice(ONE_Q); k = NPAR.get(g, 0)
            angs = [rand_angle(rng, 0.85) for _ in range(k)]
            par = "(" + ", ".join(a for a, _ in angs) + ")" if k else ""
            return f"{g}{par}", U(g, tuple(v for _, v in angs))
        r = rng.random()
        if arity >= 2 and (r < 0.35 or depth <= 0):          # tensor: split the wires
            na = rng.randint(1, arity - 1)
            ea, ma = rand_expr(depth - 1, na); eb, mb = rand_expr(depth - 1, arity - na)
            return f"({ea} & {eb})", np.kron(mb, ma)          # wire 0 = low bit
        if arity >= 2 and r < 0.6:           # control on wire 0
            ea, ma = rand_expr(depth - 1, arity - 1)
            return f"(ctl {ea})", np.kron(np.eye(ma.shape[0]), P0) + np.kron(ma, P1)
        if r < 0.8:
            ea, ma = rand_expr(depth - 1, arity); eb, mb = rand_expr(depth - 1, arity)
            return f"({ea} >> {eb})", mb @ ma
        if r < 0.9:
            ea, ma = rand_expr(depth - 1, arity)
            return f"~({ea})", ma.conj().T
        n_ = rng.randint(-3, 3)
        ea, ma = rand_expr(depth - 1, arity)
        return f"({ea}) ^ {n_}", np.linalg.matrix_power(ma if n_ >= 0 else ma.conj().T, abs(n_))
    for trial in range(250):
        arity = rng.randint(1, 3)
        ex, M = rand_expr(rng.randint(1, 4), arity)
        it = surd.Interp(seed=1, echo=False)
        val = it.ev(surd.Parser(surd.lex(ex)).expr(), it.globals)
        cols, exact = it.unitary(val, (0, 0))
        ok = val.arity == arity and all(abs(complex(cols[c][r]) - M[r, c]) < 1e-9
                                        for c in range(2 ** arity) for r in range(2 ** arity))
        tally("algebra", ok, ex)
        # == must agree with numpy when the expression is compared with its own simplification
        ex2, M2 = rand_expr(rng.randint(1, 3), arity)
        same = np.allclose(M, M2, atol=1e-9)
        got = it.ev(surd.Parser(surd.lex(f"({ex}) == ({ex2})")).expr(), it.globals)
        tally("algebra", got == same, f"{ex} == {ex2}")

    # exact field arithmetic in Q(ζ_N), N up to 64: inverse, distributivity, conjugation, float agreement
    from fractions import Fraction as Fr
    for trial in range(300):
        k = rng.choice([1, 2, 4, 8, 16, 32])
        def relem():
            return surd._mk(tuple(Fr(rng.randint(-5, 5), rng.choice([1, 2, 3, 4])) for _ in range(k)))
        x, y, z = relem(), relem(), relem()
        ok = True
        if not x.is_zero():
            xr = surd.R2.of(x * x.conj())                       # |x|², a nonzero real
            ok &= (xr * xr.inv() - surd.R2(1)).is_zero()
            ok &= abs(float(xr) - abs(complex(x)) ** 2) < 1e-9 * max(1, float(xr))
        ok &= ((x + y) * z - (x * z + y * z)).is_zero()
        ok &= (x.conj().conj() - x).is_zero()
        ok &= abs(complex(x * y) - complex(x) * complex(y)) < 1e-9 * max(1, abs(complex(x) * complex(y)))
        tally("field", ok, f"k={k} x={x.c}")

    # ability: path sums equal the state's amplitudes, exactly
    for trial in range(80):
        n = rng.randint(1, 3)
        src, ops = rand_circuit(rng, n, rng.randint(1, 7), exact_bias=1.0)
        it = surd.Interp(seed=1, echo=False).run(f"wires q: {n}\n" + "\n".join(src))
        if not it.state.exact: continue
        ok = True
        for idx in range(2 ** n):
            bits = "".join("1" if idx >> q & 1 else "0" for q in range(n))
            it.lines.clear()
            it.st_print_paths({"bits": bits, "pos": (0, 0)}, it.globals, None)
            head = it.lines[0]
            amp = head.split("→ amplitude ", 1)[1]
            ok &= amp == str(it.state.v[idx])
        tally("paths", ok, "\n".join(src))

    # ability: stabilizers of random Clifford circuits, signs checked by numpy
    PM = {"I": I2, "X": U("x", ()), "Y": U("y", ()), "Z": U("z", ())}
    for trial in range(60):
        n = rng.randint(1, 4)
        src, ops = [], []
        for _ in range(rng.randint(1, 10)):
            if n > 1 and rng.random() < 0.4:
                a, b = rng.sample(range(n), 2); src.append(f"(q[{a}], q[{b}]) <- cx"); ops.append(("x", (), b, (a,)))
            else:
                g = rng.choice(["h", "s", "x", "z", "sdg", "y"]); t = rng.randrange(n)
                src.append(f"q[{t}] <- {g}"); ops.append((g, (), t, ()))
        it = surd.Interp(seed=1, echo=False).run(f"wires q: {n}\n" + "\n".join(src))
        gens, is_full, _ = it.stabilizers(list(range(n)), (0, 0))
        v = np_state(n, ops)
        ok = is_full and len(gens) == n
        for gtxt in gens:
            sgn = 1 if gtxt[0] == "+" else -1
            P = np.array([[1]], dtype=complex)
            for q in reversed(range(n)): P = np.kron(P, PM[gtxt[1 + q]])
            ok &= abs(np.real(v.conj() @ P @ v) - sgn) < 1e-9
        tally("stabilizers", bool(ok), "\n".join(src) + f" -> {gens}")

    # ability: synthesis — every result is proved =~ inside surd; here: never worse than the input
    for trial in range(40):
        w = [rng.choice(["h", "s", "t", "tdg", "x", "sdg"]) for _ in range(rng.randint(1, 9))]
        ex = " >> ".join(w)
        it = surd.Interp(seed=1, echo=False)
        g = it.ev(surd.Parser(surd.lex(ex)).expr(), it.globals)
        r = it.synth(g, (0, 0))
        tin = sum(1 for x in w if x in ("t", "tdg"))
        tout = sum(1 for (n_, ps, t, c) in r.a if surd.clifford_class(n_, ps, c) == "T")
        M = np.eye(2, dtype=complex)
        for x in w: M = U(x, ()) @ M
        ok = tout <= tin and (tin - tout) % 2 == 0      # T-count parity is invariant
        tally("synth", ok, f"{ex} -> {r.label()} (T {tin} -> {tout})")
    for trial in range(12):
        w = [rng.choice(["(q0, q1) <- cx", "(q1, q0) <- cx", "q0 <- h", "q1 <- h", "q0 <- s", "q1 <- t"])
             for _ in range(rng.randint(1, 4))]
        it = surd.Interp(seed=1, echo=False).run("gate gw: q0, q1 { " + "; ".join(w) + " }")
        g = it.gate_value("gw", [], (0, 0))
        r = it.synth(g, (0, 0))
        tally("synth", len(r.a) <= len(w), f"{w} -> {r.label()}")

    # ability: symbolic noise — formulas evaluated at a random p agree with numpy at that p
    from fractions import Fraction as Fr
    for trial in range(60):
        n = rng.randint(1, 3)
        src, ops = rand_circuit(rng, n, rng.randint(1, 6), exact_bias=1.0)
        pv = Fr(rng.randint(0, 20), 40)
        rho = np.outer(np_state(n, ops), np_state(n, ops).conj())
        lines = list(src)
        for _ in range(rng.randint(1, 3)):
            ch = rng.choice(["dephase", "bitflip", "depolarize"]); t = rng.randrange(n)
            lines.append(f"q[{t}] <- {ch}(p)")
            X, Y, Z = U("x", ()), U("y", ()), U("z", ()); pf = float(pv)
            ks = {"dephase": [math.sqrt(1 - pf) * I2, math.sqrt(pf) * Z], "bitflip": [math.sqrt(1 - pf) * I2, math.sqrt(pf) * X],
                  "depolarize": [math.sqrt(1 - pf) * I2] + [math.sqrt(pf / 3) * M for M in (X, Y, Z)]}[ch]
            rho = sum(_lift(n, K, t) @ rho @ _lift(n, K, t).conj().T for K in ks)
            g = rng.choice(["h", "s", "x"]); t2 = rng.randrange(n)
            lines.append(f"q[{t2}] <- {g}"); M = full(n, U(g, ()), t2, ()); rho = M @ rho @ M.conj().T
        text = f"symbol p\nwires q: {n}\n" + "\n".join(lines)
        try:
            it = surd.Interp(seed=1, echo=False).run(text)
        except surd.SurdError as e:
            tally("symbolic", False, f"{text}\n{e.render()}"); continue
        ok = True
        d = it.state.distribution(list(range(n)))
        for k_, P in d.items():
            val = float(surd.R2.of(surd.Poly.coerce(P).at({"p": surd.R2(pv)})))
            i = sum(1 << q for q, b in enumerate(k_) if b == "1")
            ok &= abs(val - np.real(rho[i, i])) < 1e-9
        pur = surd.Poly.coerce(it.state.purity(list(range(n))))
        ok &= abs(float(surd.R2.of(pur.at({"p": surd.R2(pv)}))) - np.real(np.trace(rho @ rho))) < 1e-9
        tally("symbolic", bool(ok), text)

    # realms: compare's classes against brute force; Grover's exact P against a full numpy state vector
    import tempfile
    VOC = ["roof", "lumber", "framing", "kitchen", "cabinets", "furniture", "excavation", "permit", "windows",
           "concrete", "deposit", "owner", "advance", "draw"]
    for trial in range(40):
        d = tempfile.mkdtemp()
        def rows(n):
            out = []
            for _ in range(n):
                out.append({"date": f"2020-01-{rng.randint(1, 20):02d}", "amount": rng.choice([10, 20, 30, 40.5]),
                            "meaning": " ".join(rng.sample(VOC, rng.randint(0, 3))), "locator": rng.choice(["L1", "L2"])})
            return out
        ra, rb = rows(rng.randint(1, 12)), rows(rng.randint(1, 12))
        json.dump(ra, open(f"{d}/a.json", "w")); json.dump(rb, open(f"{d}/b.json", "w"))
        w = rng.randint(0, 4)
        it = surd.Interp(seed=1, echo=False).run(f'realm a = load("{d}/a.json")\nrealm b = load("{d}/b.json")')
        pairs, _, _ = surd.realm_compare(it.globals["a"], it.globals["b"], w)
        got = sorted((p[3].i, p[4].i, p[0]) for p in pairs)
        exp = []
        gen = surd.GENERIC_WORDS
        for i, x in enumerate(ra):
            for j, y in enumerate(rb):
                dd = abs(int(x["date"][-2:]) - int(y["date"][-2:]))
                if float(x["amount"]) != float(y["amount"]) or dd > 2 * w: continue
                A = {t for t in x["meaning"].split() if t not in gen}; B = {t for t in y["meaning"].split() if t not in gen}
                if not A or not B: k = "UNLABELED"
                else:
                    f = len(A & B) ** 2 / (len(A) * len(B))
                    k = "AGREE" if f >= 0.25 else ("COLLIDE" if f == 0 else "PARTIAL")
                exp.append((i, j, k))
        tally("realms", got == sorted(exp), f"w={w} {ra} {rb}")
    for trial in range(40):
        N = rng.randint(2, 200); M = rng.randint(1, max(1, N // 4))
        marked = rng.sample(range(N), M)
        n, k, p, _ = surd.grover_realm(N, marked)
        D = 1 << n
        v = np.zeros(D); v[:] = 1 / math.sqrt(D)
        for _ in range(k):
            v[marked] *= -1
            v = 2 * v.mean() - v
        tally("grover", abs(float(p) - float(np.sum(v[marked] ** 2))) < 1e-9, f"N={N} M={M}")

    # games: classical value vs brute force; the found quantum strategy re-scored by numpy; no signalling
    import itertools
    for trial in range(60):
        table = [rng.random() < 0.5 for _ in range(16)]           # win[(x,y,a,b)]
        if trial % 3 == 1:                                         # XOR game: quantum strategies get used
            f_ = [rng.randint(0, 1) for _ in range(4)]
            table = [(a ^ b) == f_[x * 2 + y] for x in (0, 1) for y in (0, 1) for a in (0, 1) for b in (0, 1)]
        signal = trial % 3 == 0
        if signal:                                                 # alice must output bob's bit: no protocol beats 1/2
            g_ = rng.choice([lambda y: y, lambda y: 1 - y])
            table = [a == g_(y) for x in (0, 1) for y in (0, 1) for a in (0, 1) for b in (0, 1)]
        idx = lambda x, y, a, b: ((x * 2 + y) * 2 + a) * 2 + b
        it = surd.Interp(seed=trial, echo=False)
        it.games["g"] = {"name": "g", "question": None, "players": ["a1", "b1"], "ins": ["x", "y"], "outs": ["a", "b"],
                         "sizes": [2, 2], "win_fn": (lambda inp, out, t=table: t[idx(inp[0], inp[1], out[0], out[1])]),
                         "promise_fn": lambda inp: True, "solved": None}
        cv, qv, carg, qarg, _, _ = it.solve_game("g", (0, 0), restarts=6)
        brute = max(sum(table[idx(x, y, sa[x], sb[y])] for x in (0, 1) for y in (0, 1)) / 4
                    for sa in itertools.product((0, 1), repeat=2) for sb in itertools.product((0, 1), repeat=2))
        phi = np.array([1, 0, 0, 1], dtype=complex) / math.sqrt(2)     # index = a_raw*2 + b_raw (alice high bit)
        def mat(c):
            cand = surd._GAME_CANDS[c]
            if cand[0] == "fix": return None, cand[1]
            j, k = cand[3]
            return U("ry", (k * math.pi / 8,)) @ U("rz", (j * math.pi / 4,)), None
        tot = 0.0
        for x in (0, 1):
            for y in (0, 1):
                (MA, fa), (MB, fb) = mat(qarg[0][x]), mat(qarg[1][y])
                st_ = np.kron(MA if MA is not None else I2, MB if MB is not None else I2) @ phi
                for ra in (0, 1):
                    for rb in (0, 1):
                        a = fa if MA is None else ra; b = fb if MB is None else rb
                        if table[idx(x, y, a, b)]: tot += abs(st_[ra * 2 + rb]) ** 2
        tot /= 4
        ok = abs(float(cv) - brute) < 1e-12 and float(qv) >= float(cv) - 1e-12 and abs(float(qv) - tot) < 1e-9
        if signal: ok = ok and abs(float(qv) - 0.5) < 1e-9
        tally("games", bool(ok), f"table={table} cv={cv} qv={qv} numpy={tot}")

    # the evaluator on a fixed textbook CHSH strategy (alice 0, π/2; bob π/4, -π/4 as ry angles 2θ)
    it = surd.Interp(seed=1, echo=False).run("game c {\n players a1(x -> a), b1(y -> b)\n win (a + b) % 2 == x * y\n}")
    gg = it.games["c"]
    allowed, win = surd.game_tables(gg)
    if surd._GAME_CANDS is None: surd._GAME_CANDS = surd._game_candidates()
    pick = {(j, k): i for i, c in enumerate(surd._GAME_CANDS) if c[0] == "rot" for (j, k) in [c[3]]}
    strat = [[pick[(0, 0)], pick[(0, 4)]], [pick[(0, 2)], pick[(0, 14)]]]
    v = surd.game_eval(gg, allowed, win, strat, True)
    tally("games", v == surd.R2(surd.F(1, 2), surd.F(1, 4)), f"textbook CHSH scored {v}")

    # vectorized float path (6+ wires): numpy path vs the pure-Python path vs the numpy oracle
    for trial in range(40):
        n = rng.randint(6, 9)
        src, ops = rand_circuit(rng, n, rng.randint(3, 25), exact_bias=0.4)
        text = f"wires q: {n}\n" + "\n".join(src)
        ref = probs_np(np_state(n, ops), n)
        fast = probs_ket(surd.Interp(seed=1, echo=False, force_float=True).run(text))
        saved = surd._np; surd._np = None
        try:
            slow = probs_ket(surd.Interp(seed=1, echo=False, force_float=True).run(text))
        finally:
            surd._np = saved
        tally("float_vector", close(fast, ref) and close(slow, ref), text)

    # vectorized noisy float path: numpy vs pure Python vs an independent Kraus density matrix
    for trial in range(40):
        n = rng.randint(3, 5)
        src, ops = rand_circuit(rng, n, rng.randint(2, 10), exact_bias=0.3)
        rho = np.outer(np_state(n, ops), np_state(n, ops).conj())
        lines = list(src) + ["q[0] <- dephase(0)"]
        X, Y, Z = U("x", ()), U("y", ()), U("z", ())
        for _ in range(rng.randint(1, 5)):
            t = rng.randrange(n); kind = rng.choice(["dephase", "bitflip", "depolarize", "ampdamp", "pauli", "reset", "gate"])
            pv = rng.choice([0.05, 0.1, 0.25, 0.4])
            if kind == "reset":
                lines.append(f"q[{t}] <- reset")
                ks = [np.array([[1, 0], [0, 0]], dtype=complex), np.array([[0, 1], [0, 0]], dtype=complex)]
            elif kind == "gate":
                g = rng.choice(["h", "s", "t", "x"]); c = rng.randrange(n)
                if c != t:
                    lines.append(f"(q[{c}], q[{t}]) <- ctl {g}"); M = full(n, U(g, ()), t, (c,)); rho = M @ rho @ M.conj().T
                continue
            elif kind == "pauli":
                a, b, c = 0.1, 0.05, pv / 2
                lines.append(f"q[{t}] <- pauli({a}, {b}, {c})")
                ks = [math.sqrt(1 - a - b - c) * I2, math.sqrt(a) * X, math.sqrt(b) * Y, math.sqrt(c) * Z]
            else:
                lines.append(f"q[{t}] <- {kind}({pv})")
                ks = {"dephase": [math.sqrt(1 - pv) * I2, math.sqrt(pv) * Z], "bitflip": [math.sqrt(1 - pv) * I2, math.sqrt(pv) * X],
                      "depolarize": [math.sqrt(1 - pv) * I2] + [math.sqrt(pv / 3) * M_ for M_ in (X, Y, Z)],
                      "ampdamp": [np.array([[1, 0], [0, math.sqrt(1 - pv)]]), np.array([[0, math.sqrt(pv)], [0, 0]])]}[kind]
            rho = sum(_lift(n, K, t) @ rho @ _lift(n, K, t).conj().T for K in ks)
        text = f"wires q: {n}\n" + "\n".join(lines)
        it_f = surd.Interp(seed=1, echo=False, force_float=True).run(text)
        saved = surd._np; surd._np = None
        try:
            it_s = surd.Interp(seed=1, echo=False, force_float=True).run(text)
        finally:
            surd._np = saved
        ref = {format(i, f"0{n}b")[::-1]: float(np.real(rho[i, i])) for i in range(2 ** n) if np.real(rho[i, i]) > 1e-14}
        pur = float(np.real(np.trace(rho @ rho)))
        ok = close(probs_ket(it_f), ref) and close(probs_ket(it_s), ref) and \
            abs(float(it_f.state.purity(list(range(n)))) - pur) < 1e-9
        tally("noisy_vector", bool(ok), text)

    # OpenQASM round trip: export -> import must give the identical unitary, exactly
    import tempfile
    qd = tempfile.mkdtemp()
    for trial in range(40):
        n = rng.randint(1, 3)
        src, ops = rand_circuit(rng, n, rng.randint(1, 10), exact_bias=0.9)
        it = surd.Interp(seed=1, echo=False).run(f"wires q: {n}\n" + "\n".join(src))
        if not it.trace: continue
        fn = os.path.join(qd, f"c{trial}.qasm")
        try:
            open(fn, "w").write(surd.to_qasm(it))
        except surd.SurdError:
            continue                                   # e.g. 2-controlled gates qelib1 cannot hold
        it2 = surd.Interp(seed=1, echo=False)
        g = it2.ev(surd.Parser(surd.lex(f'qasm("{fn}")')).expr(), it2.globals)
        A = surd.ops_unitary([op[1:] for op in it.trace], n)
        B = it2.unitary(g, (0, 0))
        tally("qasm_roundtrip", surd.unitaries_match(A, B, up_to_phase=False), "\n".join(src))

    # programs: random list/number programs in Surd vs the same computation in Python with exact Fractions
    from fractions import Fraction as Fq
    for trial in range(80):
        xs = [Fq(rng.randint(-20, 20), rng.choice([1, 1, 2, 3])) for _ in range(rng.randint(0, 8))]
        lit = "[" + ", ".join(f"{x.numerator}/{x.denominator}" for x in xs) + "]"
        k = Fq(rng.randint(1, 5))
        ops = rng.sample(["map", "filter", "sorted", "slice", "reduce", "fn", "while", "skip", "skip"], 3)
        prog = [f"let xs = {lit}", f"let k = {k.numerator}"]
        py = list(xs)
        for op in ops:
            if op == "map": prog.append("let xs = map(fn(x) => x * k - 1, xs)"); py = [x * k - 1 for x in py]
            elif op == "filter": prog.append("let xs = filter(fn(x) => x > 0, xs)"); py = [x for x in py if x > 0]
            elif op == "sorted": prog.append("let xs = sorted(xs)"); py = sorted(py)
            elif op == "slice": prog.append("let xs = xs[1..]"); py = py[1:]
            elif op == "reduce":
                prog.append("let xs = [reduce(fn(a, b) => a + b * b, xs, 0)]"); py = [sum((b * b for b in py), Fq(0))]
            elif op == "fn":
                prog.append("fn twice(f, v) { return f(f(v)) }"); prog.append("let xs = map(fn(x) => twice(fn(y) => y / 2, x), xs)")
                py = [x / 4 for x in py]
            elif op == "skip":
                prog.append("let out = []\nfor x in xs {\n if x < 0 { continue }\n push(out, x)\n}\nlet xs = out")
                py = [x for x in py if x >= 0]
            elif op == "while":
                prog.append("let i = 0\nlet out = []\nwhile i < len(xs) { push(out, xs[i] + i)\n let i = i + 1 }\nlet xs = out")
                py = [x + i for i, x in enumerate(py)]
        prog.append("let total = sum(xs)")
        try:
            it = surd.Interp(seed=1, echo=False).run("\n".join(prog))
            got = [x.a for x in it.globals["xs"]]
            ok = got == py and it.globals["total"].a == sum(py, Fq(0))
        except surd.SurdError as e:
            ok = False; prog.append(e.render())
        tally("programs", ok, "\n".join(prog))

    # semantics: meanings vs numpy vectors; joint entanglement vs numpy partial trace; encode -> senses;
    # truth discovery vs the reference quantum_logic.py (an independent implementation)
    oracle_path = os.environ.get("LOOM_NUMERIC_ORACLE")
    if oracle_path:
        sys.path.insert(0, os.path.expanduser(oracle_path))
    try:
        if not oracle_path or os.environ.get("SURD_PORTABLE_TEST") == "1":
            raise ImportError("external oracle excluded in portable profile")
        import quantum_logic as _ql
    except Exception:
        _ql = None
    for trial in range(50):
        k = rng.randint(2, 6)
        labels = [f"s{i}" for i in range(k)]
        w1 = {l: rng.randint(0, 5) for l in labels}; w2 = {l: rng.randint(0, 5) for l in labels}
        if not sum(w1.values()): w1[labels[0]] = 1
        if not sum(w2.values()): w2[labels[-1]] = 1
        mp = lambda w: "{" + ", ".join(f'"{l}": {v}' for l, v in w.items() if v) + "}"
        ph1, ph2 = (rng.choice(labels), rng.randint(0, 7)), (rng.choice(labels), rng.randint(0, 7))
        kb = rng.randint(2, 3); blabs = [f"b{i}" for i in range(kb)]
        rows = [[rng.choice(labels), rng.choice(blabs), rng.randint(1, 4)] for _ in range(rng.randint(1, 5))]
        rtxt = "[" + ", ".join(f'["{a}", "{b}", {c}]' for a, b, c in rows) + "]"
        src = (f"space P {{ {', '.join(labels)} }}\nspace B {{ {', '.join(blabs)} }}\nlet m1 = blend(P, {mp(w1)})\n"
               f"let m2 = blend(P, {mp(w2)})\nlet m1 = phase(m1, \"{ph1[0]}\", {ph1[1]}*pi/4)\n"
               f"let m2 = phase(m2, \"{ph2[0]}\", {ph2[1]}*pi/4)\nlet f = fidelity(m1, m2)\nlet j = joint(P, B, {rtxt})\nlet e = entanglement(j)\n"
               f"wires wp: {max(1, (k - 1).bit_length())}\nwires wb: {max(1, (kb - 1).bit_length())}\nencode(j, wp, wb)\n"
               f"let sp = senses(wp, P)\nlet dj = distribution(given(j, \"{rows[0][1]}\"))")
        it = surd.Interp(seed=1, echo=False).run(src)
        v1 = np.array([math.sqrt(w1[l] / sum(w1.values())) for l in labels])
        v2 = np.array([math.sqrt(w2[l] / sum(w2.values())) for l in labels])
        v1 = v1.astype(complex); v2 = v2.astype(complex)
        v1[labels.index(ph1[0])] *= cmath.exp(1j * ph1[1] * math.pi / 4)
        v2[labels.index(ph2[0])] *= cmath.exp(1j * ph2[1] * math.pi / 4)
        f_ref = abs(np.vdot(v1, v2)) ** 2
        J = np.zeros((k, kb))
        for a, b, c in rows: J[labels.index(a), blabs.index(b)] += c
        J = np.sqrt(J / J.sum())
        rhoA = J @ J.T
        e_ref = 1 - float(np.trace(rhoA @ rhoA))
        pa = {labels[i]: float((J[i] ** 2).sum()) for i in range(k) if (J[i] ** 2).sum() > 1e-15}
        ok = abs(float(it.globals["f"]) - f_ref) < 1e-9 and abs(float(it.globals["e"]) - e_ref) < 1e-9
        ok &= all(abs(float(it.globals["sp"].get(l, 0)) - pa.get(l, 0)) < 1e-9 for l in set(pa) | set(it.globals["sp"]))
        tally("semantics", bool(ok), src)
    if _ql is not None:
        for trial in range(30):
            fams = [f"F{i}" for i in range(rng.randint(2, 5))]
            cl = [(rng.choice(fams), rng.choice(["Q1", "Q2", "Q3"]), str(rng.choice([0, 1, 2]))) for _ in range(rng.randint(2, 10))]
            W = {}
            for f, q, v in cl: W.setdefault(("L", q, v), set()).add(f)
            t_ref, _ = _ql.truth_discovery(W)
            t, _ = surd.truth_discovery(cl)
            tally("semantics", all(abs(t_ref[f] - t[f]) < 1e-12 for f in t_ref) and set(t) == set(t_ref), str(cl))

    # money: LIBR vs an independent replay; every doctrine's share inside share_bounds; compositions foot;
    # books always balance; subset_sum vs brute force; grover_sum vs a numpy state vector
    from fractions import Fraction as Fm
    import itertools as _itm
    def ref_libr(evs, cls):
        """independent two-pool replay (others first, overdraft debt), deposits before withdrawals each day"""
        C = O = debt = Fm(0); out = {}
        for d in sorted({e[0] for e in evs}):
            day = [e for e in evs if e[0] == d]
            for e in [x for x in day if x[1] > 0] + [x for x in day if x[1] < 0]:
                a = e[1]
                if a > 0:
                    pay = min(debt, a); debt -= pay; a -= pay
                    if e[2] == cls: C += a
                    else: O += a
                else:
                    need = -a; t = min(O, need); O -= t; need -= t; t = min(C, need); C -= t; need -= t; debt += need
            out[d] = (C, C + O)
        return out
    for trial in range(60):
        n = rng.randint(3, 14)
        evs = []
        for _ in range(n):
            d = rng.randint(1, 9)
            amt = Fm(rng.randint(1, 60) * 5) * (1 if rng.random() < 0.55 else -1)
            evs.append((d, amt, rng.choice(["A", "B", "C"]) if amt > 0 else ""))
        lit = "[" + ", ".join(f'{{"date": @2020-01-0{d}, "amount": {"-" if a < 0 else ""}${abs(a)}, "cls": "{c}"}}' for d, a, c in evs) + "]"
        it = surd.Interp(seed=1, echo=False).run(f"let ev = {lit}\nlet lb = libr(ev, \"A\")")
        ref = ref_libr(evs, "A")
        ok = all(r["class_left"].d == ref[r["date"].n - surd.Date.parse("2020-01-01").n + 1][0] and
                 r["balance"].d == ref[r["date"].n - surd.Date.parse("2020-01-01").n + 1][1] for r in it.globals["lb"])
        ev_n = surd.norm_events(it.globals["ev"], None)
        for (i, d, a, c, pg) in ev_n:
            if a >= 0: continue
            b = surd.share_bounds(ev_n, d, a, {"A"})
            for order in ("deposits_first", "withdrawals_first"):
                for doc in ("fifo", "lifo", "prorata"):
                    comp = next(cc for ii, dd, aa, cc in surd.tranche_trace(ev_n, order, doc) if ii == i)
                    ok &= sum(comp.values()) == -a                                  # composition foots
                    shareA = comp.get("A", Fm(0))
                    # the bounds hold for every convention, the one-day order included
                    if order == "deposits_first" or True: ok &= b[0] <= shareA <= b[1]
        tally("money", bool(ok), lit)
    for trial in range(30):
        accts = ["cash", "owner", "lumber", "builder", "bank"]
        lines = ["let book = []"]
        for _ in range(rng.randint(1, 12)):
            a, b = rng.sample(accts, 2)
            lines.append(f'post(book, @2020-01-0{rng.randint(1, 9)}, "{a}", "{b}", ${rng.randint(1, 900)}.{rng.randint(0, 99):02d})')
        lines.append("let bal = balances(book)")
        it = surd.Interp(seed=1, echo=False).run("\n".join(lines))
        tally("money", sum(v.d for v in it.globals["bal"].values()) == 0, "\n".join(lines))
    for trial in range(30):
        xs = [Fm(rng.randint(1, 40)) for _ in range(rng.randint(1, 10))]
        tgt = Fm(rng.randint(1, 80))
        it = surd.Interp(seed=1, echo=False).run("let xs = [" + ", ".join(f"${x}" for x in xs) + f"]\nlet ss = subset_sum(xs, ${tgt}, 4)\nlet g = grover_sum(xs, ${tgt})")
        brute = sorted(c for r in range(1, 5) for c in _itm.combinations(range(len(xs)), r) if sum(xs[i] for i in c) == tgt)
        got = sorted(tuple(int(i.a) for i in sub) for sub in it.globals["ss"])
        marked = [m for m in range(1 << len(xs)) if sum((xs[i] for i in range(len(xs)) if m >> i & 1), Fm(0)) == tgt]
        g = it.globals["g"]
        okg = True
        if marked:
            D = 1 << int(g["qubits"].a); vv = np.full(D, 1 / math.sqrt(D))
            for _ in range(int(g["oracle_calls"].a)):
                vv[marked] *= -1; vv = 2 * vv.mean() - vv
            okg = abs(float(g["p_success"]) - float(np.sum(vv[marked] ** 2))) < 1e-9
        tally("money", got == brute and okg, str(xs) + " -> " + str(tgt))

    # joint bounds hold every doctrine's total; but-for balances; verdicts agree with the doctrines
    for trial in range(50):
        n = rng.randint(3, 12)
        evs = []
        for _ in range(n):
            d = rng.randint(1, 9)
            amt = Fm(rng.randint(1, 60) * 5) * (1 if rng.random() < 0.55 else -1)
            evs.append((d, amt, rng.choice(["A", "B"]) if amt > 0 else "", rng.choice(["x", "y"])))
        lit = "[" + ", ".join(f'{{"date": @2020-01-0{d}, "amount": {"-" if a < 0 else ""}${abs(a)}, "cls": "{c}", "memo": "{m}"}}'
                              for d, a, c, m in evs) + "]"
        it = surd.Interp(seed=1, echo=False).run(f'let ev = {lit}\nlet jb = set_bounds(ev, fn(e) => e["amount"] < $0 and e["memo"] == "x", "A")\n'
                                                 f'let bf = butfor(ev, "A")')
        ev_n = surd.norm_events(it.globals["ev"], None)
        jb, bf = it.globals["jb"], it.globals["bf"]
        ok = True
        targets = [e[0] for e in ev_n if e[2] < 0 and evs[e[0]][3] == "x"]
        for order in ("deposits_first", "withdrawals_first"):
            for doc in ("fifo", "lifo", "prorata"):
                tot = sum((cc.get("A", Fm(0)) for ii, dd, aa, cc in surd.tranche_trace(ev_n, order, doc) if ii in targets), Fm(0))
                ok &= jb["floor"].d <= tot <= jb["ceiling"].d
        actual_final = sum(a for d, a, c, m in evs)
        ok &= bf["final_balance_without"].d == actual_final - sum(a for d, a, c, m in evs if c == "A")
        outs = [e for e in ev_n if e[2] < 0]
        if outs:
            o = rng.choice(outs)
            x = Fm(rng.randint(0, 300))
            it2 = surd.Interp(seed=1, echo=False).run(f'let ev = {lit}\nlet vd = verdict(ev, @2020-01-0{o[1] - surd.Date.parse("2020-01-01").n + 1}, '
                                                      f'-${-o[2]}, "A", ">=", ${x})')
            vd = it2.globals["vd"]
            if vd["verdict"] == "T": ok &= not vd["fails_under"]
            if vd["verdict"] == "F": ok &= not vd["holds_under"]
        tally("money", bool(ok), lit)

    # native statements: foot, reconcile, returned items, but-for with returns, notice calibration
    import surd_bank as SB
    for trial in range(40):
        base = 737000
        rows, bal = [], Fm(0)
        for _ in range(rng.randint(4, 14)):
            amt = Fm(rng.randint(1, 400) * 5, rng.choice([1, 1, 4])) * (1 if rng.random() < 0.5 else -1)
            rows.append({"day": base + rng.randint(0, 20), "amount": amt, "memo": "row", "type": "", "page": "P1",
                         "month": "M", "begin": None, "end": None})
        rows.sort(key=lambda r: r["day"])
        end = sum((r["amount"] for r in rows), Fm(0))
        rows[0]["begin"], rows[0]["end"] = Fm(0), end
        ok = SB.foot(rows)[0]["ties"]
        bump = [dict(r) for r in rows]; bump[-1]["amount"] += Fm(1, 100)
        ok &= SB.foot(bump)[0]["gap"] == Fm(1, 100)
        a = [(r["day"], r["amount"], r["memo"], r["page"]) for r in rows]
        rc = SB.reconcile(a, a)
        ok &= rc["explained"] and rc["matched"] == len(a) and rc["first_divergence"] is None
        X = Fm(rng.randint(1, 900) * 7 + 1, 100); d0 = base + rng.randint(0, 15); d1 = d0 + rng.randint(1, 10)
        pair = [{"day": d0, "amount": X, "memo": "ck 9", "type": "", "page": "P2", "month": "M", "begin": None, "end": None, "cls": "B"},
                {"day": d1, "amount": -X, "memo": "RTRN CK 9", "type": "", "page": "P3", "month": "M", "begin": None, "end": None, "cls": ""}]
        Y = Fm(rng.randint(1, 900) * 11 + 3, 100)                       # decoy: a transfer out matching a deposit is not a return
        decoy = [{"day": d0, "amount": Y, "memo": "INTERNET FROM CHECKING 3122", "type": "", "page": "P4", "month": "M", "begin": None, "end": None, "cls": ""},
                 {"day": d1, "amount": -Y, "memo": "ONLINE TRANSFER TO SAVINGS", "type": "", "page": "P5", "month": "M", "begin": None, "end": None, "cls": ""}]
        both = rows + pair
        rc2 = SB.reconcile([(r["day"], r["amount"], r["memo"], r["page"]) for r in both], a)
        ok &= rc2["explained"] and len(rc2["netted"]) == 1 and rc2["max_divergence"] == X and rc2["final_gap"] == 0
        # split: one deposit of b shown as two parts in a
        dep = [r for r in rows if r["amount"] > 0 and r["amount"] > Fm(1, 100)]
        if dep:
            r0 = dep[0]; part = Fm(1, 100)
            spl = [x for x in a if x != (r0["day"], r0["amount"], r0["memo"], r0["page"])] + \
                  [(r0["day"], part, "p1", "P"), (r0["day"], r0["amount"] - part, "p2", "P")]
            if len(spl) == len(a) + 1:
                rc3 = SB.reconcile(spl, a)
                ok &= rc3["explained"] and len(rc3["split"]) == 1
        # language: returns finds the pair with sane bounds; butfor drops deposit and return together
        def lit(rs):
            return "[" + ", ".join(
                f'{{"date": @{surd.Date(r["day"])}, "amount": {"-" if r["amount"] < 0 else ""}${abs(r["amount"])}, '
                f'"memo": "{r["memo"]}", "type": "", "cls": "{r.get("cls", "")}"}}' for r in rs) + "]"
        it = surd.Interp(seed=1, echo=False).run(f'let ev = {lit(both)}\nlet rt = returns(ev)\nlet bf = butfor(ev, "B")\n'
                                                 f'let rtd = returns({lit(both + decoy)})')
        rt = [x for x in it.globals["rt"] if x.get("deposit_date") is not surd.NONE and x["amount"].d == X]
        ok &= len(rt) >= 1
        ok &= not any(x["amount"].d == Y for x in it.globals["rtd"])
        if rt:
            lo_, hi_ = rt[0]["spent_floor"].d, rt[0]["spent_ceiling"].d
            ok &= Fm(0) <= lo_ <= hi_ <= min(X, rt[0]["paid_while_provisional"].d)
        bf = it.globals["bf"]
        low_a, run = Fm(0), Fm(0)
        for r in sorted(rows, key=lambda r: (r["day"], 0 if r["amount"] > 0 else 1)):
            run += r["amount"]; low_a = min(low_a, run)
        ok &= bf["final_balance_without"].d == end and int(bf["returns_removed_with_them"].a) == 1
        ok &= bf["deepest_shortfall"].d == -low_a
        # calibrate: notices written from a deposits-first replay are confirmed by deposits-first
        dips = SB.replay_dips(rows)
        nrows = [dict(r) for r in rows]
        for dday, (lowv, items) in dips.items():
            if items:
                dt_ = surd.Date(dday).__str__()
                y, m, dd = dt_.split("-")
                nrows.append({"day": dday, "amount": Fm(0), "memo": f"{items} Overdraft Item{'s' if items > 1 else ''} on {int(m)}/{int(dd)}/{y[2:]}",
                              "type": "overdraft_notice", "page": "N", "month": "M", "begin": None, "end": None})
        cal = SB.calibrate(nrows)
        ok &= cal["deposits_first"]["consistent"] and cal["notices"] == sum(1 for v in dips.values() if v[1])
        tally("bank", bool(ok), str(trial))

    # multi-account ledger: injected transfers are found, the group balance and class money are conserved
    import surd_ledger as SL, surd_observe as SO, surd_portal as SPO
    for trial in range(40):
        accts = ["A", "B", "C"][:rng.randint(2, 3)]
        rows = []
        for _ in range(rng.randint(3, 12)):
            amt = Fm(rng.randint(1, 300) * 3 + 1, rng.choice([1, 4])) * (1 if rng.random() < 0.6 else -1)
            rows.append({"account": rng.choice(accts), "day": rng.randint(0, 20), "amount": amt, "memo": "row", "page": "", "cls": rng.choice(["X", "Y"]) if amt > 0 else ""})
        injected = []
        for _ in range(rng.randint(1, 3)):
            a, b = rng.sample(accts, 2); d = rng.randint(0, 20); x = Fm(rng.randint(1, 999) * 1000 + 7, 100) + len(injected)
            rows.append({"account": a, "day": d, "amount": -x, "memo": "ONLINE TRANSFER TO " + b, "page": "", "cls": ""})
            rows.append({"account": b, "day": d + rng.randint(0, 2), "amount": x, "memo": "ONLINE TRANSFER FROM " + a, "page": "", "cls": ""})
            injected.append(x)
        if len(accts) == 3:                                   # decoy: the memo names C, an equal credit sits in B too
            xd = Fm(rng.randint(1, 99) * 100 + 13, 100) + 1000
            rows += [{"account": "A", "day": 30, "amount": -xd, "memo": "XFER TO 3333", "page": "", "cls": ""},
                     {"account": "B", "day": 30, "amount": xd, "memo": "deposit", "page": "", "cls": ""},
                     {"account": "C", "day": 31, "amount": xd, "memo": "XFER FROM A", "page": "", "cls": ""}]
            for r in rows:
                if r["account"] == "C": r["account"] = "3333"
            accts = ["A", "B", "3333"]
        pairs = SL.transfers(rows, 3)
        found = {p_["amount"] for p_ in pairs}
        ok = all(x in found for x in injected)
        if "3333" in accts:
            ok &= any(p_["amount"] == xd and p_["to"] == "3333" for p_ in pairs) and not any(p_["amount"] == xd and p_["to"] == "B" for p_ in pairs)
        c = SL.consolidate(rows, pairs)
        ok &= c["group"] == sum((r["amount"] for r in rows), Fm(0)) and c["external_in"] - c["external_out"] == c["group"]
        for doc in ("fifo", "lifo", "prorata"):
            comp, left = SL.carry(rows, pairs, doc)
            ok &= all(sum(comp[i].values(), Fm(0)) == abs(rows[i]["amount"]) for i in comp if rows[i]["amount"] < 0)
            inner = {p_["out"] for p_ in pairs}
            for k in ("X", "Y"):
                put = sum((r["amount"] for i, r in enumerate(rows) if r["amount"] > 0 and r["cls"] == k and i not in {p_["in"] for p_ in pairs}), Fm(0))
                spent = sum((comp[i].get(k, Fm(0)) for i in comp if rows[i]["amount"] < 0 and i not in inner), Fm(0))
                remain = sum((m.get(k, Fm(0)) for m in left.values()), Fm(0))
                # money of a class either left the group or is still in it, less what repaid overdrafts
                ok &= spent + remain <= put
        tally("ledger", bool(ok), str(trial))

    # observers: exhaustive perspectives match a direct recomputation; draws total n; pooled B needs two values
    for trial in range(40):
        obs = ["o1", "o2", "o3"]
        facts = [{"id": f"f{i}", "day": rng.randint(0, 9), "holders": rng.sample(obs, rng.randint(1, 3)),
                  "quantity": "q", "value": rng.choice(["a", "b"])} for i in range(rng.randint(1, 6))]
        days = sorted(rng.sample(range(10), 3))
        ok = True
        for r in SO.every_perspective(facts, obs, "q", "a", days):
            vals = {f["value"] for f in facts if f["day"] <= r["day"] and r["observer"] in f["holders"]}
            ok &= r["verdict"] == SO.belnap_of(vals, "a")
        pr = SO.perspectives(facts, obs, "q", "a", days, n=97, seed=trial)
        ok &= sum(pr["overall"].values()) == 1 and len(pr["draws"]) == 97
        pr2 = SO.perspectives(facts, obs, "q", "a", days, n=97, seed=trial)
        ok &= pr2["overall"] == pr["overall"]                        # seeded: reproducible
        po = SO.pooled(facts, obs[:2], "q", "a", days[-1])
        ok &= (po["pooled"] == "B") == (set(po["values"]) == {"a", "b"})
        alone = {}
        for o_ in obs[:2]:                                            # independent recomputation of each member alone
            vs = {f["value"] for f in facts if f["day"] <= days[-1] and o_ in f["holders"]}
            alone[o_] = "B" if vs == {"a", "b"} else ("T" if vs == {"a"} else ("F" if vs == {"b"} else "N"))
        ok &= po["only_when_pooled"] == (set(po["values"]) == {"a", "b"} and all(v != "B" for v in alone.values()))
        msgs = [{"speaker": "s", "audience": rng.choice(["x", "y"]), "day": None,
                 "text": rng.choice(["don't worry", "paid $5.00 maybe", "send me the draw asap", "it was handled"])} for _ in range(rng.randint(2, 7))]
        th = SO.audit_thread(msgs)
        for aud, v in th["by_speaker"]["s"].items():
            ok &= all(v["per_message"][k] * v["messages"] == c for k, c in v["features"].items())
        # portals: an anchor planted in k realms tunnels exactly when k >= 2
        k = rng.randint(1, 3)
        amt = f"{rng.randint(100, 999)},{rng.randint(100, 999)}.{rng.randint(10, 99)}"
        realms = {f"R{j}": [{"memo": "filler row", "amount": "1.00"}] + ([{"memo": "hit", "amount": amt}] if j < k else []) for j in range(3)}
        pt = SPO.open_portal(f"look for ${amt}", realms)
        ok &= (len(pt["tunnels"]) == 1) == (k >= 2) and (len(pt["one_sided"]) == 1) == (k == 1)
        tally("observe", bool(ok), str(trial))

    # physics: a jittered periodic line is found at its period; random placement rarely is; detailed balance and Zeno invariants
    import surd_physics as SPH
    false_alarms = 0
    for trial in range(40):
        per = rng.randint(14, 35); n = rng.randint(6, 10); t0 = rng.randint(0, 50)
        amt = Fm(rng.randint(100, 99999), 100)
        line = [(t0 + k * per + rng.randint(-1, 1), amt, "A") for k in range(n)]
        noise = [(rng.randint(0, 400), Fm(rng.randint(100, 99999), 100), "A") for _ in range(rng.randint(5, 30))]
        ls = SPH.lines(line + noise)
        ok = any(l["amount"] == amt and abs(l["period"] - per) <= 2 and l["count"] >= n - 1 for l in ls)
        rnd_amt = Fm(rng.randint(100, 99999), 100) + Fm(1, 3)
        rnd = [(rng.randint(0, 400), rnd_amt, "A") for _ in range(rng.randint(5, 9))]
        if any(l["amount"] == rnd_amt for l in SPH.lines(rnd)):
            false_alarms += 1
        nodes = ["a", "b", "c", "d"][:rng.randint(2, 4)]
        flows = {(i, j): Fm(rng.randint(0, 50)) for i in nodes for j in nodes if i != j and rng.random() < 0.7}
        b = SPH.balance(flows)
        ok &= b["entropy_production"] >= -1e-9 and 2 * b["reciprocal"] <= b["total"] and sum(b["divergence"].values(), Fm(0)) == 0
        ok &= b["circulating"] <= b["total"]
        ev = [(rng.randint(0, 60), Fm(rng.randint(1, 9))) for _ in range(rng.randint(1, 8))]
        obs = rng.sample(range(0, 61), rng.randint(1, 4))
        zz = SPH.zeno(ev, obs, 0, 60)
        ok &= zz["p_far"] + zz["p_near"] >= 1 and zz["shifts"] == 61 and zz["mean_distance"] >= 0
        tally("physics", bool(ok), str(trial))
    tally("physics", false_alarms <= 6, f"random-placement false alarms {false_alarms}/40")
    # residence: floor <= ceiling; one class alone has floor == ceiling == an independent dollar-day sum
    for trial in range(30):
        n = rng.randint(2, 9); evs = []; day = 0
        for _ in range(n):
            day += rng.randint(0, 5)
            evs.append((day, Fm(rng.randint(1, 50) * 10) * (1 if rng.random() < 0.6 else -1), rng.choice(["A", "B"])))
        evs.insert(0, (0, Fm(100), "A"))
        lit = "[" + ", ".join(f'{{"date": @{surd.Date(737000 + d)}, "amount": {"-" if a < 0 else ""}${abs(a)}, "cls": "{c if a > 0 else ""}"}}'
                              for d, a, c in evs) + "]"
        it = surd.Interp(seed=1, echo=False).run(f'let r = residence({lit}, "A")\nlet solo = residence({lit.replace(chr(34) + "B" + chr(34), chr(34) + "A" + chr(34))}, "A")')
        r, solo = it.globals["r"], it.globals["solo"]
        ok = r["floor_days"].a <= r["ceiling_days"].a and solo["floor_days"].a == solo["ceiling_days"].a
        tot = sum((a for d, a, c in evs if a > 0), Fm(0)); bal, deficit, dd, last = Fm(0), Fm(0), Fm(0), None
        days = sorted({d for d, _, _ in evs}); end = max(days)
        for d in days:                                   # independent replay: the only class is the whole pool
            for _, a, _ in sorted([e for e in evs if e[0] == d], key=lambda e: -e[1]):
                if a > 0:
                    cov = min(deficit, a); deficit -= cov; bal += a - cov
                else:
                    take = min(bal, -a); bal -= take; deficit += -a - take
            nxt = next((x for x in days if x > d), end)
            dd += bal * (nxt - d)
        ok &= solo["dollar_days_ceiling"].a == dd
        tally("physics", bool(ok), "residence " + lit[:120])

    # crash fuzz: token soup and mutated examples must raise SurdError or run
    toks = ["wire", "wires", "bits", "q", "q[0]", "q[1..]", ":", "3", "[", "]", "(", ")", "{", "}", ",", ";", "\n",
            "<-", "h", "cx", ">>", "&", "~", "ctl", "^", "==", "=~", "!=", "measure", "reset", "c", "if", "1",
            '"01"', "pi", "/", "*", "-", "gate", "g", "r[]", "=", "noise", "depolarize(1/2)", "off", "for", "i",
            "in", "0..2", "repeat", "2", "print", "state", "expect", "prob(q,\"0\")", "len(q)", "expval(q,\"Z\")",
            "let", "sample", "dephase", "u", "unitary", "stats", "probs", "bloch", "π", "x", "id", "t", "rz(pi/4)",
            "symbol", "p", "bitflip(p)", "synth(cz)", "stabilizers(q)", "paths", "at(", "deriv(",
            "fn", "f", "return", "while", "break", "continue", "=>", "[1, 2]", '{"a": 1}', "test", '"t"', "none",
            "map(", "sum(", "range(3)", "push(", "space", "P", "sense(", "blend(", "joint(", "encode(", "trust(",
            "$1,000.00", "-$5", "@2020-01-01", "libr(", "post(", "balances(", "trace(", "subset_sum("]
    exdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")
    bases = [open(os.path.join(exdir, f)).read() for f in sorted(os.listdir(exdir)) if f.endswith(".surd")]
    for trial in range(fuzz_options.crash_cases):
        if rng.random() < 0.5:
            text = " ".join(rng.choice(toks) for _ in range(rng.randint(1, 25)))
        else:
            b = list(rng.choice(bases))
            for _ in range(rng.randint(1, 4)):
                k = rng.randrange(len(b)); b[k:k + rng.randint(0, 3)] = list(rng.choice(toks))
            text = "".join(b)
        try:
            bounded_run(text, fuzz_options.case_timeout)
            tally("crash_fuzz", True, None)
        except CaseTimeout:
            res["crash_timeouts"] += 1
            tally("crash_fuzz", False, f"TIMEOUT {fuzz_options.case_timeout}s\n{text!r}")
        except surd.SurdError as e:
            # Exhaustion is incomplete coverage, never a successful malformed-input check.
            limited = e.code == 'S-E017'
            tally("crash_fuzz", not limited, f"RESOURCE_LIMIT {e}\n{text!r}" if limited else None)
        except Exception as e:
            tally("crash_fuzz", False, f"{type(e).__name__}: {e}\n{text!r}")
    bad = [k for k, v in res.items() if isinstance(v, list) and len(v) == 2 and v[0] != v[1]]
    res["ok"] = not bad
    res["external_truth_oracle"] = "executed" if _ql is not None else "excluded or unavailable (30 checks)"
    res["schema"] = "surd.fuzz.v2"
    res["seed"] = fuzz_options.seed
    res["crash_cases"] = fuzz_options.crash_cases
    res["elapsed_seconds"] = round(time.monotonic()-started, 3)
    res["source_sha256"] = hashlib.sha256(Path(surd.__file__).read_bytes()).hexdigest()
    res["replay"] = [sys.executable, str(Path(__file__).resolve()), "--seed", str(fuzz_options.seed), "--crash-cases", str(fuzz_options.crash_cases), "--case-timeout", str(fuzz_options.case_timeout)]
    payload = json.dumps(res, indent=1, default=str)
    if fuzz_options.report:
        fuzz_options.report.parent.mkdir(parents=True, exist_ok=True)
        fuzz_options.report.write_text(payload + "\n")
    print(payload)
    return 0 if not bad else 1


def main(argv=None):
    global _active_report, _active_options
    _active_report, _active_options = {}, None
    try:
        return _main(argv)
    except Exception as error:
        report = _active_report
        report.update(schema="surd.fuzz.v2", ok=False,
                      harness_error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        if _active_options is not None:
            report["seed"] = _active_options.seed
            if _active_options.report:
                _active_options.report.parent.mkdir(parents=True, exist_ok=True)
                _active_options.report.write_text(json.dumps(report, indent=1, default=str)+"\n")
        print(json.dumps(report, indent=1, default=str))
        return 1


def _lift(n, K, t):
    f = [K if q == t else I2 for q in reversed(range(n))]
    M = f[0]
    for x in f[1:]: M = np.kron(M, x)
    return M


if __name__ == "__main__":
    sys.exit(main())
