"""surd_physics — three pieces of real physics, computed exactly where the mathematics allows.

1. Spectroscopy of point events (pulsar-timing style). For event times t_k and a trial period P, the Rayleigh power
   Z(P) = 2 |Σ_k e^{2πi t_k / P}|² / N is distributed χ²₂ for random times (single-trial p = e^{-Z/2}). A scan over
   periods multiplies the false-alarm chance by the number of independent periods, about T/P_min − T/P_max (the
   Gabor limit: frequency resolution Δf = 1/T, so a period is known only to ΔP ≈ P²/T over a record span T).
   lines(): groups of identical exact amounts that recur, with their period, Rayleigh Z, trials-corrected chance,
   and period uncertainty.
2. Quantum Zeno test. A system watched often does not drift; drift happens between observations. For observation
   days O and events (day, weight), the weighted mean distance to the nearest observation is compared with the
   same statistic for every circular shift of the observation pattern across the record span (an exact permutation
   null over shifts). Also the share of weight in the first fifth of each observation window.
3. Detailed balance and entropy production (non-equilibrium statistical mechanics, Schnakenberg). For pair flows
   F_ij (dollars i→j): reciprocal (detailed-balanced) part R_ij = min(F_ij, F_ji) — money that went both ways —,
   net current J_ij = F_ij − F_ji, Kirchhoff divergence per node, and entropy production
   σ = Σ_{i<j} (F_ij − F_ji) ln(F_ij / F_ji) (zero exactly when every pair is balanced; one-way pairs counted separately
   because σ diverges). Directed cycles are removed by cycle cancelling (largest bottleneck first) to give the
   circulating dollars around loops of three or more accounts.
Numbers that can be exact are Fractions; Z, chances and σ are floats and say so.
"""
import cmath
import math
from fractions import Fraction as F

F0 = F(0)


# ───────────────────────── 1. spectroscopy ─────────────────────────
def rayleigh(times, period):
    if not times or period <= 0:
        return 0.0
    s = sum(cmath.exp(2j * math.pi * t / period) for t in times)
    return 2 * abs(s) ** 2 / len(times)


def scan(times, p_min=5.0, p_max=None, steps=400):
    """Rayleigh power over a log-spaced period grid; returns (best period, Z, trials, chance after trials)"""
    if len(times) < 3:
        return None
    T = max(times) - min(times)
    if T <= 0:
        return None
    p_max = p_max or T / 2
    if p_max <= p_min:
        return None
    best = (0.0, None)
    for k in range(steps + 1):
        P = p_min * (p_max / p_min) ** (k / steps)
        z = rayleigh(times, P)
        if z > best[0]:
            best = (z, P)
    trials = max(1.0, T / p_min - T / p_max)
    single = math.exp(-best[0] / 2)
    chance = 1 - (1 - single) ** trials
    return {"period": best[1], "z": best[0], "trials": trials, "chance": chance, "span": T,
            "period_uncertainty": best[1] ** 2 / T}


def regularity_chance(run, sims=20000, seed=1):
    """chance that the interior events, placed uniformly at random between the run's first and last day, give gaps
    as steady as observed (every gap within max(3 days, 10%) of the median). Seeded Monte Carlo; (hits+1)/(sims+1)."""
    import random as _r
    rng = _r.Random(seed)
    a, b, k = run[0], run[-1], len(run) - 2
    if k < 1:
        return 1.0
    hits = 0
    for _ in range(sims):
        pts = sorted([a] + [rng.uniform(a, b) for _ in range(k)] + [b])
        g = [y - x for x, y in zip(pts, pts[1:])]
        med = sorted(g)[len(g) // 2]
        if all(abs(x - med) <= max(3, med * 0.1) for x in g):
            hits += 1
    return (hits + 1) / (sims + 1)


def lines(events, min_count=4, p_min=5.0, max_chance=0.05):
    """events: [(day, amount, label)]. A line = one exact amount (per label) recurring at a steady period. The line is
    the longest run of consecutive gaps within max(3 days, 10%) of the run's median gap; members outside the run are
    reported as stragglers. Z, span and trials are measured on the run itself (a far straggler would otherwise widen
    the span and swamp the line, as the Gabor limit says)."""
    groups = {}
    for d, a, lab in events:
        groups.setdefault((a, lab), []).append(d)
    out = []
    for (a, lab), ds in groups.items():
        ds = sorted(set(ds))
        if len(ds) < min_count:
            continue
        best = None
        for i0 in range(len(ds)):
            for i1 in range(i0 + min_count - 1, len(ds)):
                run = ds[i0:i1 + 1]
                gaps = [y - x for x, y in zip(run, run[1:])]
                med = sorted(gaps)[len(gaps) // 2]
                if med < p_min or any(abs(g - med) > max(3, med * 0.1) for g in gaps):
                    continue
                if best is None or len(run) > len(best[0]):
                    best = (run, med, gaps)
        if best is None:
            continue
        run, med, gaps = best
        T = run[-1] - run[0]
        z = rayleigh(run, med)
        trials = max(1.0, T / p_min)
        c_scan = 1 - (1 - math.exp(-z / 2)) ** trials
        c_reg = regularity_chance(run, seed=len(run) * 7919 + int(T))
        chance = min(1.0, 2 * min(c_scan, c_reg))          # two tests, Bonferroni
        if chance > max_chance:
            continue
        out.append({"amount": a, "label": lab, "count": len(run), "first": run[0], "last": run[-1], "period": med,
                    "gaps": gaps, "z": z, "chance": chance, "period_uncertainty": med * med / T if T else None,
                    "stragglers": [d for d in ds if d not in run], "trials": trials,
                    "scan_chance": c_scan, "regularity_chance": c_reg})
    out.sort(key=lambda r: (r["chance"], -r["count"] * abs(r["amount"])))
    return out


# ───────────────────────── 2. Zeno ─────────────────────────
def _nearest(day, obs):
    return min(abs(day - o) for o in obs)


def zeno(events, observations, start=None, end=None):
    """events: [(day, weight>0)]; observations: [day]. Exact permutation null over every circular shift."""
    obs = sorted(set(observations))
    if not obs or not events:
        raise ValueError("zeno needs events and observations")
    lo = start if start is not None else min(min(d for d, _ in events), obs[0])
    hi = end if end is not None else max(max(d for d, _ in events), obs[-1])
    span = hi - lo + 1
    W = sum((w for _, w in events), F0)

    def stat(o):
        return sum((F(_nearest(d, o)) * w for d, w in events), F0) / W

    def early_share(o):
        tot = F0
        for d, w in events:
            prev = [x for x in o if x <= d]
            nxt = [x for x in o if x > d]
            if prev and nxt and (d - prev[-1]) * 5 < (nxt[0] - prev[-1]):
                tot += w
        return tot / W

    observed = stat(obs)
    null = []
    for s in range(span):
        shifted = sorted(lo + (o - lo + s) % span for o in obs)
        null.append(stat(shifted))
    ge = sum(1 for x in null if x >= observed)
    le = sum(1 for x in null if x <= observed)
    return {"mean_distance": observed, "null_mean": sum(null, F0) / len(null), "shifts": len(null),
            "p_far": F(ge, len(null)), "p_near": F(le, len(null)), "early_share": early_share(obs),
            "weight": W, "observations": len(obs), "span": span}


# ───────────────────────── 3. detailed balance ─────────────────────────
def balance(flows):
    """flows: {(i, j): dollars}. Reciprocal part, net currents, divergence, entropy production, cycle currents."""
    F_ = {k: v for k, v in flows.items() if v > 0 and k[0] != k[1]}
    nodes = sorted({x for k in F_ for x in k})
    pairs, recip, one_way = [], F0, []
    sigma = 0.0
    seen = set()
    for (i, j), v in F_.items():
        if (j, i) in seen or (i, j) in seen:
            continue
        seen.add((i, j))
        back = F_.get((j, i), F0)
        r = min(v, back)
        recip += r
        net = v - back
        if back > 0:
            sigma += float(v - back) * math.log(float(v) / float(back))
        else:
            one_way.append({"from": i, "to": j, "dollars": v})
        pairs.append({"a": i, "b": j, "a_to_b": v, "b_to_a": back, "reciprocal": r, "net": net,
                      "sigma": (float(v - back) * math.log(float(v) / float(back))) if back > 0 else None})
    div = {n: sum((v for (i, j), v in F_.items() if i == n), F0) - sum((v for (i, j), v in F_.items() if j == n), F0)
           for n in nodes}
    # circulation: cancel directed cycles of length >= 3 on the net-current graph, largest bottleneck first
    J = {}
    for p in pairs:
        if p["net"] > 0: J[(p["a"], p["b"])] = p["net"]
        elif p["net"] < 0: J[(p["b"], p["a"])] = -p["net"]
    cycles = []

    def find_cycles():
        found = []
        adj = {}
        for (i, j), v in J.items():
            adj.setdefault(i, []).append(j)

        def dfs(start, node, path):
            for nx in adj.get(node, []):
                if nx == start and len(path) >= 3:
                    found.append(list(path))
                elif nx not in path and len(path) < 6:
                    dfs(start, nx, path + [nx])
        for n in sorted(adj):
            dfs(n, n, [n])
        return found

    while True:
        cs = find_cycles()
        if not cs:
            break
        best = max(cs, key=lambda c: min(J[(c[k], c[(k + 1) % len(c)])] for k in range(len(c))))
        b = min(J[(best[k], best[(k + 1) % len(best)])] for k in range(len(best)))
        for k in range(len(best)):
            e = (best[k], best[(k + 1) % len(best)])
            J[e] -= b
            if J[e] == 0: del J[e]
        cycles.append({"accounts": best + [best[0]], "dollars": b})
    total = sum(F_.values(), F0)
    return {"total": total, "reciprocal": recip, "reciprocal_share": (recip * 2 / total) if total else F0,
            "pairs": sorted(pairs, key=lambda p: -p["reciprocal"]), "one_way": sorted(one_way, key=lambda x: -x["dollars"]),
            "divergence": div, "entropy_production": sigma, "cycles": cycles,
            "circulating": sum((c["dollars"] for c in cycles), F0)}


# ───────────────────────── 4. residence time (Little's law) and depletion ─────────────────────────
def residence(points, inflow, end_day):
    """points: [(day, stock at the end of that day)] in day order; inflow: total money that entered the stock.
    Little's law: mean residence time W = (∫ stock dt) / inflow, exact in dollar-days per dollar."""
    if inflow <= 0:
        raise ValueError("residence needs a positive inflow")
    dd = F0
    for (d0, c0), (d1, _) in zip(points, points[1:] + [(end_day, None)]):
        if d1 > d0:
            dd += c0 * (d1 - d0)
    return {"dollar_days": dd, "mean_days": dd / inflow}


def depletion(points, after_day, threshold):
    """first day on or after `after_day` when the stock is at or below `threshold`"""
    for d, c in points:
        if d >= after_day and c <= threshold:
            return d
    return None
