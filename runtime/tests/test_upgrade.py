#!/usr/bin/env python3
"""Behavior regressions for amount direction, proof claims and release failures."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd
spec = importlib.util.spec_from_file_location('release_gate', ROOT / 'tests/gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class UpgradeTests(unittest.TestCase):
    def test_signed_and_exact_money(self):
        for raw, expected in [(100, '100'), (-100, '-100'), ('($1,234.56)', '-30864/25'),
                              ('0.001', '1/1000'), (9007199254740993, '9007199254740993')]:
            self.assertEqual(str(surd._cents(raw)), expected)
        for raw in ('nan', 'inf', '', None):
            self.assertIsNone(surd._cents(raw))

    def test_opposite_directions_do_not_match(self):
        def rec(amount):
            return surd.Record(None, 0, dict(amount=amount, date='2026-01-01', meaning='window'))
        self.assertEqual(surd.anchor_fid(rec(100), rec(-100), 3), 0)
        self.assertEqual(surd.anchor_fid(rec(100), rec(100), 0), 1)

    def test_grover_scan_disclosed_including_empty_result(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'rows.json'
            p.write_text(json.dumps([dict(amount=100), dict(amount=-100)]))
            for cond, count in [('amount == 100', '1 marked'), ('abs(amount) == 100', '2 marked'),
                                ('amount == 999', '0 marked')]:
                it = surd.Interp(echo=False).run(f'realm r = load({json.dumps(str(p))})\ngrover r where {cond}')
                out = '\n'.join(it.lines)
                self.assertIn(count, out)
                self.assertIn('classical predicate evaluations: 2', out)

    def test_missing_amount_is_not_negative_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'rows.json'
            p.write_text(json.dumps([dict(amount=None), dict(amount=-1)]))
            it = surd.Interp(echo=False).run(f'realm r = load({json.dumps(str(p))})\ngrover r where amount == -1')
            self.assertIn('1 marked', '\n'.join(it.lines))
            self.assertIs(surd.record_value(it.globals['r'].recs[0], 'amount'), surd.NONE)

    def test_compare_labels_and_window(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'rows.json'
            p.write_text(json.dumps([dict(amount=100, date='2026-01-01', meaning='window purchase')]))
            src = f'realm a = load({json.dumps(str(p))})\nrealm b = load({json.dumps(str(p))})\n'
            it = surd.Interp(echo=False).run(src + 'compare a, b within 3')
            out = '\n'.join(it.lines)
            self.assertIn('token overlap meets threshold', out)
            self.assertIn('separation <= 6', out)
            with self.assertRaises(surd.SurdError):
                surd.Interp(echo=False).run(src + 'compare a, b within -1')

    def test_proof_modes(self):
        for src, prefix in [('wire q\nq <- h', 'PROVED'),
                            ('wire q\nq <- rz(0.3)', 'NUMERICALLY VERIFIED'),
                            ('wires q: 9\nq[0] <- h', 'not checked'),
                            ('wire q\nq <- bitflip(1/2)', 'not checked')]:
            it = surd.Interp(echo=False).run(src)
            self.assertTrue(surd.prove_same(it.trace, it.trace, it.state.n).startswith(prefix))

    def test_opt_require_proof(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'in.surd'
            for src, code in [('wire q\nq <- h >> h', 0), ('wire q\nq <- rz(0.3)', 1),
                              ('wires q: 9\nq[0] <- h', 1)]:
                p.write_text(src)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(surd.main(['opt', str(p), '--require-proof']), code)

    def test_gate_rejects_exit_and_false_or_invalid_verdict(self):
        from subprocess import CompletedProcess, TimeoutExpired
        for code, out in [(1, ''), (0, '{"ok":false}'), (0, 'broken'), (0, '[]')]:
            with patch.object(gate.subprocess, 'run', return_value=CompletedProcess([], code, out, '')):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertFalse(gate.run('injected', ['unused'], verdict=True))
        with patch.object(gate.subprocess, 'run', side_effect=TimeoutExpired([], 1)):
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(gate.run('timeout', ['unused']))

    def test_gate_exit_accumulates_failures(self):
        for failed in ('selftest', 'selftest without numpy', 'upgrade regressions',
                       'case known answers (explicit skip when stores absent)', 'numpy oracle fuzz'):
            with patch.object(gate, 'run', side_effect=lambda label, *a, **k: label != failed):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gate.main(), 1)
        with patch.object(gate, 'run', side_effect=lambda label, *a, **k: 'seed 2' not in label):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(), 1)


if __name__ == '__main__':
    unittest.main()
