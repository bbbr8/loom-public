"""Verify watchdog cleanup and the independent oracle's phase sensitivity."""
import cmath
import io
import json
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuzz_surd as F


class HarnessTests(unittest.TestCase):
    def test_unexpected_battery_crash_emits_failed_json(self):
        output = io.StringIO()
        with patch.object(F, '_main', side_effect=RuntimeError('injected')), patch('sys.stdout', output):
            self.assertEqual(F.main([]), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report['ok'])
        self.assertIn('injected', report['harness_error'])

    def test_deadline_interrupts_native_call_and_restores_handler(self):
        old = signal.getsignal(signal.SIGALRM)
        with patch.object(F.surd.Interp, 'run', side_effect=lambda _: time.sleep(.2)):
            with self.assertRaises(F.CaseTimeout):
                F.bounded_run('print 1', .01)
        self.assertEqual(signal.getsignal(signal.SIGALRM), old)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_execution_budget_stops_infinite_loop(self):
        with self.assertRaises(F.surd.SurdError) as caught:
            F.bounded_run('while true { let x = 1 }', 1)
        self.assertEqual(caught.exception.code, 'S-E017')

    def test_numpy_oracle_distinguishes_relative_phase(self):
        # Identical probabilities, different amplitudes: a probability-only test misses this.
        a = F.np_state(1, [('h', (), 0, ())])
        b = F.np_state(1, [('h', (), 0, ()), ('z', (), 0, ())])
        self.assertTrue(F.close(F.probs_np(a, 1), F.probs_np(b, 1)))
        self.assertFalse(F.np.allclose(a, b, atol=1e-9, rtol=0))

    def test_recorded_global_phase_matches_reference(self):
        source = 'wires q: 1\nq[0] <- h\nq[0] <- rz(pi/4)'
        it = F.surd.Interp(seed=1, echo=False).run(source)
        actual = F.np.array([complex(a) for a in it.state.v]) * cmath.exp(1j*F.surd.ang_float(it.gphase))
        expected = F.np_state(1, [('h', (), 0, ()), ('rz', (F.math.pi/4,), 0, ())])
        self.assertTrue(F.np.allclose(actual, expected, atol=1e-9, rtol=0))


if __name__ == '__main__':
    unittest.main()
