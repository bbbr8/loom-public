"""Synthetic regressions for 1.8: conditional states, value metadata and bounded sessions."""
import contextlib
from fractions import Fraction
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd


PREP = '''space P { a, b }
space A { left, right }
let mm = joint(P, A, [["a", "left", 1], ["b", "left", 1], ["a", "right", 2]])
'''


class ConditionalTests(unittest.TestCase):
    def test_condition_probability_and_encode(self):
        it = surd.Interp(echo=False).run(PREP + '''let cc = condition(mm, "left")
expect cc["probability"] == 1/2
wires q: 1
encode(cc["meaning"], q)
expect norm() == 1
expect prob(q, "0") == 1/2''')
        self.assertTrue(all(x['ok'] for x in it.expects))
        self.assertTrue(it.globals['cc']['meaning'].exact)

    def test_given_retains_projection_and_normalize_preserves_phase(self):
        it = surd.Interp(echo=False).run(PREP + '''let gg = phase(given(mm, "left"), "b", pi)
let nn = normalize(gg)''')
        g, n = it.globals['gg'], it.globals['nn']
        self.assertEqual(g.norm(), surd.R2(Fraction(1, 2)))
        self.assertEqual(n.norm(), surd.R2(1))
        self.assertEqual(surd.meaning_fidelity(g, n, None), surd.R2(1))
        self.assertTrue((n.amps['a'] + n.amps['b']).is_zero())
        self.assertIsNot(g, n)

    def test_float_fallback_is_local_to_meaning_and_visible(self):
        it = surd.Interp(echo=False).run('''space P { a, b }
space A { left, right }
let mm = joint(P, A, [["a", "left", 1], ["b", "left", 2], ["a", "right", 1]])
let cc = condition(mm, "left")''')
        cc = it.globals['cc']
        self.assertEqual(cc['probability'], surd.R2(Fraction(3, 4)))
        self.assertFalse(cc['meaning'].exact)
        self.assertTrue(it.state.exact)
        self.assertTrue(any('float meaning' in x for x in it.notes))
        self.assertAlmostEqual(cc['meaning'].norm(), 1)
        self.assertAlmostEqual(float(cc['meaning'].probs()['a']), 1/3)

    def test_already_normalized_retains_amplitudes(self):
        m = surd.Meaning((surd.Space('P', ['a', 'b']),), {'a': surd.QH, 'b': -surd.QH})
        n = surd.normalize_meaning(m)
        self.assertEqual(n.amps.keys(), m.amps.keys())
        self.assertTrue(all((n.amps[k] - m.amps[k]).is_zero() for k in m.amps))

    def test_zero_condition_and_zero_norm_fail_cleanly(self):
        sp = surd.Space('P', ['a', 'b'])
        with self.assertRaises(surd.SurdError): surd.normalize_meaning(surd.Meaning((sp,), {}))
        with self.assertRaises(surd.SurdError):
            surd.Interp(echo=False).run('space P { a, b }\nspace A { left, right }\ncondition(joint(P, A, [["a", "left", 1]]), "right")')

    def test_float_input_stays_float(self):
        sp = surd.Space('P', ['a', 'b'])
        m = surd.Meaning((sp,), {'a': 0.2j, 'b': 0.4})
        n = surd.normalize_meaning(m)
        self.assertFalse(n.exact)
        self.assertAlmostEqual(n.norm(), 1)
        self.assertAlmostEqual(complex(n.amps['a']/n.amps['b']).imag, 0.5)
        self.assertEqual(m.amps, {'a': 0.2j, 'b': 0.4})

    def test_invalid_function_signatures(self):
        for expr in ['normalize()', 'normalize(1)', 'condition()', 'condition(1, "left")', 'beliefs([])']:
            with self.subTest(expr=expr), self.assertRaises(surd.SurdError):
                surd.Interp(echo=False).evaluate(expr)


class ValueTests(unittest.TestCase):
    def evaluate(self, src):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = surd.main(['eval', src, '--json'])
        return code, json.loads(out.getvalue())

    def test_exact_money_date_angle_and_collections(self):
        for expr in ['$1.25', '@2026-10-12', 'pi/8', '[1/3, 2/3]', '{"amount": $1.25, "label": "x"}']:
            with self.subTest(expr=expr):
                code, r = self.evaluate(expr)
                self.assertEqual(code, 0)
                self.assertTrue(r['exact'])
                self.assertEqual(r['exactness']['status'], 'exact')
                self.assertEqual(r['exact_scope'], 'returned_value')

    def test_mixed_nested_metadata(self):
        _, r = self.evaluate('{"exact": $1.25, "more": [1/3, float(1/3)], "name": "x"}')
        self.assertFalse(r['exact'])
        self.assertEqual(r['exactness']['status'], 'mixed')
        self.assertEqual(r['exactness']['items']['more']['items'][1]['status'], 'approximate')

    def test_nonnumeric_and_unknown_are_distinct(self):
        for expr in ['none', '"text"', 'true', '[]']:
            self.assertEqual(self.evaluate(expr)[1]['exactness']['status'], 'not_applicable')
        self.assertEqual(self.evaluate('h')[1]['exactness']['status'], 'unknown')

    def test_builtin_none_and_cycle(self):
        it = surd.Interp(echo=False).run('expect exactness(none)["exact"] == none\nlet xs = []\npush(xs, xs)')
        self.assertTrue(it.expects[0]['ok'])
        self.assertEqual(surd.value_exactness(it.globals['xs'])['status'], 'unknown')

    def test_huge_exact_value_keeps_json_output(self):
        _, r = self.evaluate('10 ^ 400')
        self.assertTrue(r['exact'])
        self.assertIsNone(r['float'])

    def test_full_expression_is_consumed(self):
        for expr in ['1 2', '1; 2', '1\n2', '1 garbage', 'sqrt(2) )']:
            with self.subTest(expr=expr), self.assertRaises(surd.SurdError):
                surd.Interp(echo=False).evaluate(expr)
        self.assertEqual(surd.Interp(echo=False).evaluate('1 + 2\n'), surd.R2(3))

    def test_cli_setup_and_trailing_newline(self):
        code, r = self.evaluate('let nn = 2\nnn + 1\n')
        self.assertEqual(code, 0)
        self.assertEqual(r['display'], '3')


class BudgetTests(unittest.TestCase):
    def test_empty_loops_and_recursion_exhaust_steps(self):
        for src in ['while true { }', 'repeat 1000000000 { }', 'for i in 0..1000000000 { }',
                    'fn ff(n) => ff(n + 1)\nff(0)']:
            with self.subTest(src=src):
                it = surd.Interp(echo=False, max_steps=100)
                with self.assertRaises(surd.SurdError) as cm: it.run(src)
                self.assertEqual(cm.exception.code, 'S-E017')
                self.assertLessEqual(it.budget.steps, 101)

    def test_budget_is_shared_with_eval_setup(self):
        it = surd.Interp(echo=False, max_steps=4)
        with self.assertRaises(surd.SurdError) as cm: it.evaluate('let nn = 2\nnn + 2', setup=True)
        self.assertEqual(cm.exception.code, 'S-E017')

    def test_test_block_does_not_swallow_budget_error(self):
        with self.assertRaises(surd.SurdError) as cm:
            surd.Interp(echo=False, max_steps=50).run('test "limit" { while true { } }')
        self.assertEqual(cm.exception.code, 'S-E017')

    def test_shots_share_one_budget(self):
        with self.assertRaises(surd.SurdError) as cm:
            surd.run_shots('wire q\nbit c\nc <- measure q', 1000, 1, False,
                           budget=surd.ExecutionBudget(max_steps=100))
        self.assertEqual(cm.exception.code, 'S-E017')

    def test_timeout_uses_monotonic_checkpoint(self):
        b = surd.ExecutionBudget(timeout=1)
        with patch.object(surd.time, 'monotonic', side_effect=[100, 100.5, 101]):
            with b.execution():
                b.checkpoint()
                with self.assertRaises(surd.SurdError) as cm: b.checkpoint()
                self.assertEqual(cm.exception.code, 'S-E017')

    def test_limits_reset_after_failed_run(self):
        it = surd.Interp(echo=False, max_steps=20)
        with self.assertRaises(surd.SurdError): it.run('while true { }')
        self.assertEqual(it.evaluate('2 + 3'), surd.R2(5))
        self.assertEqual(it.budget.depth, 0)

    def test_invalid_budgets(self):
        for kw in [{'max_steps': 0}, {'max_steps': True}, {'max_steps': 1.5}, {'timeout': 0},
                   {'timeout': True}, {'timeout': float('nan')}, {'timeout': float('inf')}]:
            with self.subTest(kw=kw), self.assertRaises(surd.SurdError): surd.ExecutionBudget(**kw)

    def test_bad_cli_numbers_return_structured_error(self):
        for flag, value in [('--max-steps', 'x'), ('--max-steps', '0'), ('--timeout', 'nan'), ('--timeout', '-1'),
                            ('--shots', '0'), ('--seed', 'bad')]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out): code = surd.main(['eval', '1', flag, value, '--json'])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())['error']['code'], 'S-E004')

    def test_serve_survives_bad_requests_and_budget_exhaustion(self):
        reqs = ['[]', 'null', '{',
                json.dumps({'id': 'badcode', 'op': 'exec', 'code': 1}),
                json.dumps({'id': 'tail', 'op': 'eval', 'code': '1 2'}),
                json.dumps({'id': 'limit', 'op': 'exec', 'code': 'while true { }'}),
                json.dumps({'id': 'ok', 'op': 'eval', 'code': '$1.25'}),
                json.dumps({'id': 'seed', 'op': 'reset', 'seed': []}),
                json.dumps({'id': 'reset', 'op': 'reset'}),
                json.dumps({'id': 'limit2', 'op': 'exec', 'code': 'while true { }'})]
        p = subprocess.run([sys.executable, str(ROOT/'surd.py'), 'serve', '--max-steps', '50'],
                           input='\n'.join(reqs)+'\n', text=True, capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertEqual(len(rows), len(reqs))
        self.assertEqual(rows[5]['error']['code'], 'S-E017')
        self.assertEqual(rows[6]['display'], '$1.25')
        self.assertTrue(rows[6]['exactness']['exact'])
        self.assertFalse(rows[7]['ok'])
        self.assertEqual(rows[9]['error']['code'], 'S-E017')


if __name__ == '__main__':
    unittest.main()
