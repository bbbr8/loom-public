# Private - personal, financial, and mental-health data
"""Exact arithmetic, civil time and semantic phase through the Loom interpreter."""
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import surd


class SemanticTimeTests(unittest.TestCase):
    def check(self, program):
        runtime = surd.Interp(echo=False).run(program)
        self.assertTrue(runtime.expects)
        self.assertTrue(all(row["ok"] for row in runtime.expects), runtime.expects)
        return runtime

    def test_program_and_time_boundaries(self):
        self.check((ROOT/"examples/semantic_time.loom").read_text(encoding="utf-8"))
        self.check('''
let before = "2024-11-03T01:30:00-06:00"
let after = "2024-11-03T01:30:00-07:00"
expect elapsed_seconds(before,after) == 3600
expect elapsed_seconds(after,before) == -3600
expect shift_instant("2024-03-01T00:00:00Z",-1/1000000) == "2024-02-29T23:59:59.999999Z"
expect elapsed_seconds("2024-01-01T00:00:00.000001Z","2024-01-01T00:00:00.000003Z") == 1/500000
expect phasor(10000000000000000000000000000 + 1/4) == complex(0,1)
expect complex_div(complex(sqrt(2),1),complex(sqrt(2),1)) == complex(1,0)
expect exactness(phasor(1/3))["status"] == "approximate"
''')

    def test_exact_arithmetic(self):
        lines = []
        for a in range(-2, 3):
            for b in range(-2, 3):
                for c, d in ((1,-2), (-1,0), (0,2)):
                    left, right = f"complex({a}/3,{b}/3)", f"complex({c}/5,{d}/5)"
                    lines.append(f"expect complex_add({left},{right}) == complex({5*a+3*c}/15,{5*b+3*d}/15)")
                    lines.append(f"expect complex_sub({left},{right}) == complex({5*a-3*c}/15,{5*b-3*d}/15)")
                    lines.append(f"expect complex_mul({left},{right}) == complex({a*c-b*d}/15,{a*d+b*c}/15)")
                    den = 3*(c*c+d*d)
                    lines.append(f"expect complex_div({left},{right}) == complex({5*(a*c+b*d)}/{den},{5*(b*c-a*d)}/{den})")
        self.check("\n".join(lines))

    def test_joint_states_and_unscaled_overlap(self):
        runtime = self.check('''
space P { a,b }
space Q { x,y }
let j = joint(P,Q,[["a","x",1],["b","y",1]])
let k = evolve_phase(j,["b","y"],1,1/4)
expect meaning_overlap(j,k) == complex(1/2,1/2)
let projected = given(j,"x")
expect meaning_overlap(projected,projected) == complex(1/2,0)
expect fidelity(projected,projected) == 1
let third = meaning_overlap(sense(P,"a"),evolve_phase(sense(P,"a"),"a",1,1/3))
expect exactness(third)["status"] == "approximate"
''')
        third = runtime.globals["third"]
        self.assertAlmostEqual(third["real"], -0.5)
        self.assertAlmostEqual(third["imag"], math.sqrt(3)/2)

    def test_polynomial_translation(self):
        self.check('''
let c = [0,0,0,1]
let shifted = poly_shift(c,2)
expect shifted == [complex(8,0),complex(12,0),complex(6,0),complex(1,0)]
expect c == [0,0,0,1]
expect poly_eval([],2) == complex(0,0)
expect poly_shift([],2) == []
expect poly_shift([1,0,0],2) == [complex(1,0),complex(0,0),complex(0,0)]
let p = [1/3,complex(sqrt(2),-1/2),0,2/7]
let z = complex(1/2,2)
let h = complex(-1/3,1/4)
expect poly_eval(poly_shift(p,z),h) == poly_eval(p,complex_add(z,h))
expect poly_eval([0,1],phasor(1/64)) == phasor(1/64)
expect poly_eval([1,2],float(1/4)) == complex(float(3/2),float(0))
let mixed = poly_shift([1,1],float(1/3))
expect exactness(mixed)["status"] == "mixed"
expect exactness(mixed[0])["status"] == "approximate"
expect exactness(mixed[1])["status"] == "exact"
''')
        with self.assertRaises(surd.SurdError) as failure:
            surd.Interp(echo=False, max_steps=200).run("poly_shift(range(50),1)")
        self.assertEqual(failure.exception.code, "S-E017")

    def test_rejected_inputs(self):
        invalid = [
            ('complex_div(complex(1,1),complex(0,0))', "S-E015"),
            ('complex_mul({"real":1},complex(0,1))', "S-E009"),
            ('complex(true,0)', "S-E009"), ('phasor()', "S-E004"),
            ('phasor(float(1/3))', "S-E009"),
            ('poly_eval("123",1)', "S-E009"),
            ('poly_shift([0,true],1)', "S-E009"),
            ('poly_eval([],{"imag":0})', "S-E009"),
            ('poly_shift([1,2])', "S-E004"),
            ('shift_instant("2030-01-01T00:00:00Z",1/3)', "S-E009"),
            ('shift_instant("9999-12-31T23:59:59Z",1)', "S-E009"),
            ('elapsed_seconds("2030-01-01T00:00:00","2030-01-01T00:00:00Z")', "S-E009"),
            ('elapsed_seconds("2030-01-01T00:00:00+00:60","2030-01-01T00:00:00Z")', "S-E009"),
            ('elapsed_seconds("2030-01-01T00:00:00.1234567Z","2030-01-01T00:00:00Z")', "S-E009"),
            ('space P { a,b }\nspace Q { a,b }\nmeaning_overlap(sense(P,"a"),sense(Q,"a"))', "S-E009"),
            ('space P { a,b }\nevolve_phase(sense(P,"a"),"missing",1,1)', "S-E002"),
        ]
        for program, code in invalid:
            with self.subTest(program=program), self.assertRaises(surd.SurdError) as failure:
                surd.Interp(echo=False).run(program)
            self.assertEqual(failure.exception.code, code)


if __name__ == "__main__":
    unittest.main()
