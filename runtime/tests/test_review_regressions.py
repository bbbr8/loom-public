"""Review-derived invariants, transfer cases, and controls for Surd 1.9.1."""
import contextlib
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd

PREP = 'wires q: 1\nbits c: 1\nq <- h\nc <- measure q\n'


class ReviewRegressions(unittest.TestCase):
    def run_source(self, source, seed=1):
        return surd.Interp(seed=seed, echo=False).run(source)

    def assert_code(self, code, fn):
        with self.assertRaises(surd.SurdError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_float_dispatch_only_evaluates_selected_operation(self):
        for expression, expected in [('float(1)+0', 1), ('float(1)*0', 0),
                                     ('float(1000)+1000', 2000), ('float(1)-float(-1)', 2),
                                     ('float(8)/2', 4), ('float(2)^3', 8)]:
            with self.subTest(expression=expression):
                self.assertEqual(surd.Interp(echo=False).evaluate(expression), expected)
        self.assert_code('S-E015', lambda: surd.Interp(echo=False).evaluate('float(1)/0'))
        self.assert_code('S-E009', lambda: surd.Interp(echo=False).evaluate('float(1000)^1000'))
        self.assert_code('S-E009', lambda: surd.Interp(echo=False).evaluate('float(-1)^(1/2)'))

    def test_every_measurement_execution_lane_requires_trace(self):
        lanes = ['if c == "1" { q <- x }',
                 'let mm = c\nlet nn = mm\nif nn == "1" { q <- x }',
                 'q <- rx(pi*c[0])', 'q <- rx(pi*prob(q,"1"))', 'repeat c[0] { q <- x }',
                 'for j in 0..c[0] { q <- x }',
                 'let mm = c[0]\nwhile mm > 0 { q <- x; mm = 0 }',
                 'fn ident(vv) { return vv }\nlet mm = ident(c[0])\nq <- rx(pi*mm)',
                 'let mm = [c[0]]\nq <- rx(pi*mm[0])']
        for seed in (1, 2):
            for lane in lanes:
                with self.subTest(seed=seed, lane=lane):
                    it = self.run_source(PREP + lane, seed)
                    self.assertTrue(it.used_if)
                    self.assertTrue(it.dynamic_reasons)
                    self.assert_code('S-E013', lambda: surd.to_qasm(it))
                    self.assertIn('trace of one run', surd.to_qasm(it, allow_trace=True))

    def test_opt_guard_cannot_emit_an_unlabelled_dynamic_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'dynamic.surd'
            path.write_text(PREP + 'q <- rx(pi*c[0])')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = surd.main(['opt', str(path), '--json'])
            self.assertEqual(code, 1)
            self.assertIn('S-E013', out.getvalue())

    def test_copied_measurement_and_helper_closure_cannot_enter_gate(self):
        lanes = ['let mm = c\nlet nn = mm\ngate gg: a { if nn == "1" { a <- x } }\nq <- gg',
                 'let mm = c[0]\nfn getmm() { return mm }\ngate gg: a { a <- rx(pi*getmm()) }\nq <- gg',
                 'fn getbit() { return c[0] }\ngate gg: a { a <- rx(pi*getbit()) }\nq <- gg',
                 'let mm = [c[0]]\nlet nn = mm\ngate gg: a { a <- rx(pi*nn[0]) }\nq <- gg',
                 'let mm = []\nlet nn = mm\npush(mm, c[0])\ngate gg: a { a <- rx(pi*nn[0]) }\nq <- gg']
        for lane in lanes:
            with self.subTest(lane=lane):
                self.assert_code('S-E007', lambda: self.run_source(PREP + lane))

    def test_shadowing_and_observation_preserve_static_export(self):
        it = self.run_source(PREP + 'print c\nexpect c == "0" or c == "1"')
        self.assertFalse(it.used_if)
        self.assertIn('measure', surd.to_qasm(it))
        # A clean function parameter must shadow a measured global of the same name.
        it = self.run_source(PREP + 'let mm = c[0]\nfn clean(mm) { return mm }\n'
                             'gate gg: a { a <- rx(pi*clean(0)) }\nq <- gg')
        self.assertTrue(it.used_if)  # conservative alias capture is explicitly trace-only
        self.assertEqual(it.globals['mm'], surd.R2(1))

    def test_exact_channel_domain_and_float_nonfinite(self):
        bad = ['pauli(1,1/2000000000,0)', 'pauli(1/2,1/2,1/10^30)',
               'bitflip(1+1/10^30)', 'bitflip(-1/10^30)']
        for expression in bad:
            with self.subTest(expression=expression):
                self.assert_code('S-E009', lambda: self.run_source('wires q: 1\nq <- '+expression))
        it = surd.Interp(echo=False)
        for value in [float('nan'), float('inf'), -float('inf'), 1.0000000000000002]:
            self.assert_code('S-E009', lambda: it.check_channel('bitflip', [value], None))
        for expression in ['pauli(1,0,0)', 'pauli(1/3,1/3,1/3)', 'bitflip(0)',
                           'bitflip(1)', 'bitflip(1/10^30)', 'bitflip(1-1/10^30)']:
            with self.subTest(expression=expression):
                result = self.run_source('wires q: 1\nq <- '+expression)
                probabilities = result.state.distribution([0]).values()
                self.assertTrue(all(p.sign() >= 0 for p in probabilities))

    def test_qasm_declares_extensions_and_preserves_full_unitary(self):
        for name in ['sx', 'sxdg', 'crx', 'cry']:
            controlled = name.startswith('c')
            expression = name+'(pi/3)' if controlled else name
            source = ('wires q: 2\n(q[0],q[1]) <- ' if controlled else 'wires q: 1\nq <- ') + expression
            original = self.run_source(source)
            qasm = surd.to_qasm(original)
            self.assertEqual(len(re.findall(r'\bgate '+name+r'\b', qasm)), 1)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'roundtrip.qasm'; path.write_text(qasm)
                replay = self.run_source('let imported = qasm("'+str(path)+'")\nwires q: '+str(original.state.n)+'\nq <- imported')
            self.assertTrue(surd.unitaries_match(surd.Interp(echo=False).unitary(
                surd.GateVal('word', [o[1:] for o in original.trace], arity=original.state.n), None),
                surd.Interp(echo=False).unitary(
                surd.GateVal('word', [o[1:] for o in replay.trace], arity=replay.state.n), None),
                up_to_phase=False))


if __name__ == '__main__':
    unittest.main()
