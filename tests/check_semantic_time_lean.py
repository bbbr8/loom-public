# Private - personal, financial, and mental-health data
"""Check exact Loom polynomial and time-evolution results using Lean 4 on stdin."""
import argparse
from math import comb
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import surd


def integer(value):
    if not isinstance(value, surd.R2) or not value.is_rational() or value.a.denominator != 1:
        raise ValueError("expected an exact integer")
    return str(value.a.numerator)


def pair(value):
    return f"({integer(value['real'])}, {integer(value['imag'])})"


def scaled_pair(value, scale):
    components = []
    for key in ("real", "imag"):
        part = value[key]
        if not isinstance(part, surd.R2) or not part.is_rational():
            raise ValueError("polynomial result lost exact rational arithmetic")
        number = part.a * scale
        if number.denominator != 1:
            raise ValueError("unexpected denominator after clearing input fractions")
        components.append(number.numerator)
    return tuple(components)


def polynomial_certificates():
    rng = random.Random(2100)
    sizes = [0, 1, 2, 3, 5, 9, 13, 17, 25, 33, 49, 65]
    checks = ["open SemanticTime"]
    probes = 0
    for trial in range(144):
        size = sizes[trial % len(sizes)]
        degree = max(size - 1, 0)
        family = (trial // len(sizes)) % 4
        if family == 0:
            coefficients = [(rng.randint(-9, 9), rng.randint(-9, 9)) for _ in range(size)]
        elif family == 1:
            coefficients = [(0, 0)] * degree + ([(3, -5)] if size else [])
        elif family == 2:
            coefficients = [(comb(degree, j) * (-1)**j, 0) for j in range(size)]
        else:
            coefficients = [(j*j, -j) if j % 4 == 1 else (0, 0) for j in range(size)]
        den = rng.choice([1, 2, 3, 7])
        q = rng.choice([1, 2, 5])
        x, y = rng.randint(-5, 5), rng.randint(-5, 5)
        literals = ",".join(f"complex({a}/{den},{b}/{den})" for a, b in coefficients)
        program = (f"let c = [{literals}]\nlet x = complex({x}/{q},{y}/{q})\n"
                   "let v = poly_eval(c,x)\nlet s = poly_shift(c,x)")
        values = surd.Interp(echo=False).run(program).globals
        # Clear denominators by checking den*q^degree * P(X/q) over Gaussian integers.
        source = [(a*q**(degree-j), b*q**(degree-j)) for j, (a,b) in enumerate(coefficients)]
        shifted = [scaled_pair(v, den*q**(degree-j)) for j,v in enumerate(values["s"])]
        value = scaled_pair(values["v"], den*q**degree)
        checks.extend([
            f"private def src{trial} : List Gaussian := {source}",
            f"#guard powerSum ({x},{y}) (1,0) src{trial} == {value}",
            f"#guard checkShift src{trial} {shifted} ({x},{y})",
        ])
        probes += max(1, size)
    checks.extend([
        "#guard !checkShift [(1,0),(0,0),(1,0)] [(1,0),(1,0),(1,0)] (0,0)",
        "#guard !checkShift [(1,0)] [(1,0),(0,0)] (0,0)",
    ])
    return "\n".join(checks), probes


def certificates():
    program = ['space S { anchor, signal }', 'let x = complex(2,-3)',
               'let p0 = complex(1,0)', 'let m0 = blend(S,{"anchor":1,"signal":1})',
               'let t0 = "2030-01-01T00:00:00Z"']
    ticks = 0
    totals = []
    for n in range(1, 26):
        # Interleave signed intervals with signed frequencies, measured in quarter seconds.
        interval = -n if n % 3 == 0 else n
        rate = -1 if n % 2 == 0 else 1
        ticks += interval * rate
        totals.append(ticks)
        program.extend([
            f"let p{n} = complex_add(complex_mul(p{n-1},x),complex({n},{1-n}))",
            f"let t{n} = shift_instant(t{n-1},{interval}/4)",
            f'let m{n} = evolve_phase(m{n-1},"signal",{rate},elapsed_seconds(t{n-1},t{n}))',
            f"let o{n} = complex_mul(complex(2,0),meaning_overlap(m0,m{n}))",
            f"let f{n} = 4 * fidelity(m0,m{n})",
        ])
    values = surd.Interp(echo=False).run("\n".join(program)).globals
    checks = ["open SemanticTime"]
    for n, total in enumerate(totals, start=1):
        checks.extend([
            f"example : polynomial (2,-3) {n} = {pair(values[f'p{n}'])} := by decide",
            f"example : twiceOverlap ({total}) = {pair(values[f'o{n}'])} := by decide",
            f"example : normSq (twiceOverlap ({total})) = {integer(values[f'f{n}'])} := by decide",
        ])
    return "\n".join(checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean", default="lean", help="Lean 4 executable")
    args = parser.parse_args()
    model = Path(__file__).with_name("SemanticTime.lean").read_text(encoding="utf-8")
    stress, probes = polynomial_certificates()
    subprocess.run([args.lean, "--stdin"], input=model + "\n" + certificates() + "\n" + stress + "\n",
                   encoding="utf-8", check=True)
    print(f"Lean verified 6 identities, 75 semantic-time results, 144 polynomial values, "
          f"{probes} translation probes and 2 negative controls.")
