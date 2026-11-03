"""Use-loop checks with isolated state and real CLI execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import surd_usage as usage
ROOT = Path(__file__).resolve().parent.parent

class UsageTests(unittest.TestCase):
    def test_real_cli_keeps_output_and_records_success_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'usage.sqlite'
            program = Path(tmp)/'private-name.surd'
            env = dict(os.environ, SURD_USAGE_STATE=str(state), SURD_USAGE='1')
            env.pop('SURD_PORTABLE_TEST', None)
            for source, expected in [('expect 1 == 1', 0), ('expect 1 == 2', 1)]:
                program.write_text(source)
                command = [sys.executable, str(ROOT/'surd.py'), 'run', str(program), '--json']
                enabled = subprocess.run(command, env=env, capture_output=True, text=True)
                disabled = subprocess.run(command, env=dict(env, SURD_USAGE='0'), capture_output=True, text=True)
                self.assertEqual(enabled.returncode, expected)
                self.assertEqual((enabled.stdout,enabled.stderr), (disabled.stdout,disabled.stderr))
            report = usage.review(state)
            self.assertEqual(report['outcomes'], {'completed':1,'rejected':1})
            self.assertNotIn('private-name', state.read_bytes().decode('utf8', errors='ignore'))
            key = report['review_queue'][0]['id']
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(usage.cli(['feedback','--state',str(state),'--use-id',key,'--category','confusing','--note','Improve assertion diagnosis']),0)
            self.assertEqual(usage.review(state)['review_queue'][0]['category'], 'confusing')

    def test_bounds_unknown_feedback_and_ignored_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'usage.sqlite'
            self.assertIsNone(usage.record(['selftest'], 'test', 0, 0, 'completed', path=state))
            self.assertFalse(state.exists())
            with patch.object(usage,'LIMIT',1):
                usage.record(['run'], 'test', 0, 0, 'completed', path=state)
                with self.assertRaises(ValueError):
                    usage.record(['run'], 'test', 0, 0, 'completed', path=state)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(usage.cli(['feedback','--state',str(state),'--use-id','missing','--category','correct']),1)

    def test_record_failure_preserves_result_and_crash(self):
        import surd
        with patch.dict(os.environ, {'SURD_USAGE':'1'}, clear=True), patch.object(usage,'record',side_effect=OSError('read only')), contextlib.redirect_stderr(io.StringIO()) as err:
            with patch.object(surd,'_main',return_value=7):
                self.assertEqual(surd.main(['run']),7)
            with patch.object(surd,'_main',side_effect=RuntimeError('original')):
                with self.assertRaisesRegex(RuntimeError,'original'):
                    surd.main(['run'])
            self.assertIn('receipt unavailable', err.getvalue())

    def test_lock_contention_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'usage.sqlite'
            db = usage.connect(state)
            try:
                db.execute('BEGIN EXCLUSIVE')
                with self.assertRaises(sqlite3.OperationalError):
                    usage.record(['run'],'test',0,0,'completed',path=state)
            finally:
                db.rollback(); db.close()
            self.assertEqual(usage.review(state)['uses'],0)

if __name__ == '__main__':
    unittest.main()
