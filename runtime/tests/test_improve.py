"""Exercise promotion/hold, history integrity, retirement and bounded execution."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import surd_improve as improve

CASE = {'schema': improve.SCHEMA, 'name': 'fixture regression', 'oracle_basis': '1+1=2, independently specified',
        'original': {'mode': 'run', 'source': 'expect 1+1 == 2', 'outcome': 'accept'},
        'transfer': {'mode': 'run', 'source': 'expect 2+2 == 4', 'outcome': 'accept'},
        'control': {'mode': 'run', 'source': 'expect 1 == 1 # CONTROL', 'outcome': 'accept'}}


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixed, self.baseline, self.state = [self.root/n for n in ('fixed', 'baseline', 'state')]
        for path, broken in [(self.fixed, False), (self.baseline, True)]:
            path.mkdir()
            program = ('import json,sys\nfrom pathlib import Path\n'
                       'broken = '+repr(broken)+'\n'
                       'bad = broken and len(sys.argv)>2 and "CONTROL" not in Path(sys.argv[2]).read_text()\n'
                       'print(json.dumps({"ok":not bad,"error":{"code":"S-E009"},"expects":{"passed":1,"failed_lines":[]}}))\n'
                       'sys.exit(1 if bad else 0)\n')
            (path/'surd.py').write_text(program)
            (path/'surd.build.json').write_text(json.dumps({'files': ['surd.py', 'surd.build.json']}))

    def test_real_process_cycle_promotes_then_check_and_retire(self):
        with patch.object(improve, 'GUARDS', []):
            report = improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
        self.assertTrue(report['ok'], report)
        events, active = improve.history(self.state)
        self.assertEqual(len(events), 1); self.assertIn(report['case_id'], active)
        self.assertTrue(all(x['ok'] for x in improve.check_retained(self.fixed, self.state, time.monotonic()+10)))
        improve.append_event(self.state, 'retire', report['case_id'], {'reason': 'test rollback'})
        self.assertFalse(improve.history(self.state)[1])
        self.assertEqual(len(improve.history(self.state)[0]), 2)

    def test_unfixed_candidate_is_held_without_active_case(self):
        with patch.object(improve, 'GUARDS', []):
            report = improve.cycle(self.baseline, self.baseline, CASE, self.state, 20)
        self.assertFalse(report['ok']); self.assertEqual(report['action'], 'hold')
        self.assertFalse(improve.history(self.state)[1])

    def test_guard_failure_prevents_promotion(self):
        with patch.object(improve, 'GUARDS', ['missing_guard.py']):
            report = improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
        self.assertFalse(report['ok']); self.assertFalse(improve.history(self.state)[1])

    def test_source_drift_prevents_promotion(self):
        original = improve.scenario
        def change_source(*args):
            result = original(*args)
            with (self.fixed/'surd.py').open('a') as out: out.write('\n# concurrent change\n')
            return result
        with patch.object(improve, 'GUARDS', []), patch.object(improve, 'scenario', side_effect=change_source):
            report = improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
        self.assertFalse(report['ok']); self.assertIn('drift', report['error'])

    def test_receipt_and_case_tampering_are_detected(self):
        with patch.object(improve, 'GUARDS', []):
            report = improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
        path = self.state/'cases'/(report['case_id']+'.json')
        original = path.read_bytes(); changed = copy.deepcopy(CASE); changed['oracle_basis'] = 'tampered'
        path.write_bytes(improve.encoded(changed))
        with self.assertRaisesRegex(ValueError, 'integrity'): improve.history(self.state)
        path.write_bytes(original)
        event = self.state/'events/000000.json'
        data = json.loads(event.read_text()); data['action'] = 'retire'; event.write_bytes(improve.encoded(data))
        with self.assertRaisesRegex(ValueError, 'integrity'): improve.history(self.state)

    def test_expired_budget_cannot_become_success(self):
        result = improve.scenario(self.fixed, CASE['original'], time.monotonic()-1, 'expired')
        self.assertFalse(result['ok']); self.assertEqual(result['state'], 'timeout')

    def test_acceptance_requires_an_executed_assertion(self):
        (self.fixed/'surd.py').write_text('print(\'{"ok":true,"expects":{"passed":0,"failed_lines":[]}}\')\n')
        result = improve.scenario(self.fixed, CASE['original'], time.monotonic()+5, 'vacuous')
        self.assertFalse(result['ok'])

    def test_crashes_and_budget_exhaustion_cannot_demonstrate_a_fix(self):
        for program in ['raise RuntimeError("crash")\n',
                        'import sys\nprint(\'{"ok":false,"error":{"code":"S-E017"}}\')\nsys.exit(1)\n']:
            with self.subTest(program=program):
                (self.baseline/'surd.py').write_text(program)
                with patch.object(improve, 'GUARDS', []):
                    report = improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
                self.assertFalse(report['ok']); self.assertEqual(report['action'], 'hold')

    def test_structured_failed_expectation_is_a_clean_wrong_result(self):
        (self.baseline/'surd.py').write_text('import sys\nprint(\'{"ok":false,"expects":{"passed":0,"failed_lines":[1]}}\')\nsys.exit(1)\n')
        r=improve.scenario(self.baseline,CASE['original'],time.monotonic()+5,'wrong-value')
        self.assertFalse(r['ok']);self.assertTrue(r['clean_execution']);self.assertEqual(r['state'],'failed')

    def test_duplicate_case_and_unsupported_modes_rejected(self):
        with patch.object(improve, 'GUARDS', []):
            improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
            with self.assertRaisesRegex(ValueError, 'already active'):
                improve.cycle(self.fixed, self.baseline, CASE, self.state, 20)
        invalid = copy.deepcopy(CASE); invalid['original']['mode'] = 'shell'
        with self.assertRaises(ValueError): improve.validate_case(invalid)

    def test_stateful_fixture_is_copied_and_source_bytes_are_preserved(self):
        name='examples/data/isolated.sqlite';original=self.fixed/name;original.parent.mkdir(parents=True);original.write_bytes(b'original')
        cfg=self.fixed/'surd.build.json';data=json.loads(cfg.read_text());data['files'].append(name);cfg.write_text(json.dumps(data))
        spec={'mode':'run','outcome':'accept','source':'expect 1 == 1 # @@GRAPH@@','fixtures':{'GRAPH':name}}
        def mutate_copy(root,args,deadline,label):
            program=Path(args[2]).read_text();copied=Path(json.loads(program.split('# ')[1]))
            self.assertNotEqual(copied,original);self.assertEqual(copied.read_bytes(),b'original');copied.write_bytes(b'changed')
            return {'state':'completed','exit_code':0,'stdout':json.dumps({'ok':True,'expects':{'passed':1,'failed_lines':[]}})}
        with patch.object(improve,'command',side_effect=mutate_copy):self.assertTrue(improve.scenario(self.fixed,spec,time.monotonic()+5,'isolated')['ok'])
        self.assertEqual(original.read_bytes(),b'original')
        spec['fixtures']['GRAPH']='examples/data/unlisted.sqlite'
        with self.assertRaises(ValueError):improve.scenario(self.fixed,spec,time.monotonic()+5,'unlisted')
        invalid = copy.deepcopy(CASE); invalid['transfer'] = invalid['original']
        with self.assertRaises(ValueError): improve.validate_case(invalid)


if __name__ == '__main__':
    unittest.main()
