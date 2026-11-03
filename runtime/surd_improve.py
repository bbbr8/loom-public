"""Bounded regression learning: reproduce, transfer, control, validate, then retain.

Only the regression curriculum is promoted automatically. Runtime fixes remain explicit
source edits. Synthetic cases are executable hypotheses, never promoted case evidence.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

SCHEMA = 'surd.improvement.v1'
MAX_CASES = 64
MAX_EVENTS = 2048
GUARDS = ['tests/test_usage.py', 'tests/test_architecture.py', 'tests/test_search.py', 'tests/test_reasoning.py',
          'tests/test_upgrade.py', 'tests/test_runtime_upgrade.py', 'tests/test_posture.py',
          'tests/test_assumption_frontiers.py', 'tests/test_review_regressions.py', 'tests/test_improve.py', 'tests/test_research.py', 'tests/test_scalability.py', 'tests/test_storage_architecture.py', 'tests/test_sqlite_bootstrap.py']


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.improve-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def source_files(root):
    root = Path(root)
    names = json.loads((root/'surd.build.json').read_text())['files']
    if not isinstance(names, list) or not names or len(names) > 512 or len(set(names)) != len(names):
        raise ValueError('invalid source allowlist')
    result = {}
    for name in sorted(names):
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name:
            raise ValueError('unsafe source path')
        full = root/path
        if any(root.joinpath(*path.parts[:i]).is_symlink() for i in range(1, len(path.parts)+1)):
            raise ValueError('linked source input')
        result[name] = full.read_bytes()
    if sum(map(len, result.values())) > 32*1024*1024:
        raise ValueError('source exceeds size budget')
    return result


def fingerprint(root):
    return digest(encoded({n: digest(data) for n, data in source_files(root).items()}))


def snapshot(root, destination):
    files = source_files(root)
    for name, data in files.items():
        path = destination/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    pin = digest(encoded({n: digest(data) for n, data in files.items()}))
    if fingerprint(root) != pin:
        raise ValueError('source changed while taking snapshot')
    return pin


def validate_case(case):
    if not isinstance(case, dict) or case.get('schema') != SCHEMA or not case.get('name') or not case.get('oracle_basis'):
        raise ValueError('case requires schema, name and independently stated oracle_basis')
    if len(encoded(case)) > 65536:
        raise ValueError('case exceeds 64 KiB')
    for role in ('original', 'transfer', 'control'):
        row = case.get(role, {})
        if not isinstance(row.get('source'), str) or len(row['source']) > 16384:
            raise ValueError(role+' requires bounded source text')
        if row.get('mode') not in ('run', 'qasm', 'opt'):
            raise ValueError('mode must be run, qasm or opt')
        if row.get('outcome') not in ('accept', 'reject'):
            raise ValueError('outcome must be accept or reject')
        if row['outcome'] == 'reject' and not isinstance(row.get('error_code'), str):
            raise ValueError('rejection requires an exact error_code')
        if row['outcome'] == 'accept' and row['mode'] == 'run' and 'expect ' not in row['source']:
            raise ValueError('accepted run needs an explicit behavioral expect assertion')
        fixtures=row.get('fixtures',{})
        if not isinstance(fixtures,dict) or len(fixtures)>8:raise ValueError('at most eight isolated fixtures')
        for token,name in fixtures.items():
            path=Path(name) if isinstance(name,str) else None
            if not isinstance(token,str) or not re.fullmatch('[A-Z_]+',token) or path is None or path.is_absolute() or '..' in path.parts or not str(path).startswith('examples/data/'):
                raise ValueError('fixtures need named relative example-data paths')
            if '@@'+token+'@@' not in row['source']:raise ValueError('fixture token is unused')
    if case['original']['source'] == case['transfer']['source']:
        raise ValueError('transfer must differ from original')
    return digest(encoded(case))[:20]


def command(root, args, deadline, label):
    seconds = min(60, deadline-time.monotonic())
    if seconds <= 0:
        return {'name': label, 'state': 'timeout', 'ok': False}
    env = dict(os.environ, SURD_PORTALS='0', SURD_PORTABLE_TEST='1', PYTHONPATH='',
               PYTHONDONTWRITEBYTECODE='1', CASE_LANGUAGE_ROOT=str(root/'unavailable-native-case'))
    began = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, timeout=seconds)
        result = {'name': label, 'exit_code': proc.returncode, 'stdout': proc.stdout[-65536:], 'stderr': proc.stderr[-65536:],
                  'state': 'completed', 'ok': proc.returncode == 0 and not re.search(r'\bSKIP\b|\bskipped\b', proc.stdout+proc.stderr, re.I)}
    except subprocess.TimeoutExpired:
        result = {'name': label, 'state': 'timeout', 'ok': False}
    result['seconds'] = round(time.monotonic()-began, 4)
    return result


def scenario(root, spec, deadline, label):
    with tempfile.TemporaryDirectory(prefix='surd-case-') as temp:
        source=spec['source'];total=0
        if spec.get('fixtures'):
            allowed=set(json.loads((root/'surd.build.json').read_text())['files'])
            for token,name in spec['fixtures'].items():
                original=root/name
                if name not in allowed or original.is_symlink() or not original.is_file() or original.stat().st_size>4*1024**2:
                    raise ValueError('fixture must be a bounded allowlisted example')
                total+=original.stat().st_size
                if total>16*1024**2:raise ValueError('fixture copies exceed 16 MiB')
                copied=Path(temp)/(token+original.suffix);shutil.copy2(original,copied)
                source=source.replace('@@'+token+'@@',json.dumps(str(copied)))
        path = Path(temp)/'case.surd'; path.write_text(source)
        result = command(root, ['surd.py', spec['mode'], str(path), '--json', '--seed', '1',
                                '--max-steps', '200000', '--timeout', '5'], deadline, label)
    if result['state'] != 'completed':
        return result
    try:
        data = json.loads(result['stdout'])
    except ValueError:
        data = {}
    result['observed_error'] = data.get('error', {}).get('code')
    failed_assertions = (spec['mode'] == 'run' and data.get('ok') is False
                         and isinstance(data.get('expects'),dict)
                         and isinstance(data['expects'].get('failed_lines'),list)
                         and bool(data['expects']['failed_lines'])
                         and all(type(n) is int and n > 0 for n in data['expects']['failed_lines'])
                         and type(data['expects'].get('passed')) is int and data['expects']['passed'] >= 0)
    result['clean_execution'] = result['exit_code'] == 0 or (result['exit_code'] == 1 and failed_assertions) or (
        result['exit_code'] == 1 and isinstance(result['observed_error'], str)
        and result['observed_error'].startswith('S-E'))
    if spec['outcome'] == 'reject':
        result['ok'] = result['exit_code'] == 1 and data.get('error', {}).get('code') == spec['error_code']
    elif spec['mode'] == 'run':
        expects = data.get('expects', {})
        result['ok'] = (result['exit_code'] == 0 and data.get('ok') is True
                        and expects.get('passed', 0) > 0 and expects.get('failed_lines') == [])
    else:
        result['ok'] = result['exit_code'] == 0 and spec.get('contains', '') in result['stdout']
    if result['observed_error'] == 'S-E017': result['ok'] = False
    result['state'] = 'passed' if result['ok'] else ('incomplete' if result['observed_error'] == 'S-E017' else 'failed')
    return result


@contextlib.contextmanager
def locked(state):
    state.mkdir(parents=True, exist_ok=True)
    with (state/'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('improvement state is busy; resume the pinned cycle after its writer finishes')
        yield


def history(state):
    events, active, previous = [], {}, None
    for path in sorted((state/'events').glob('*.json')) if (state/'events').exists() else []:
        event = json.loads(path.read_text()); recorded = event.pop('sha256')
        if digest(encoded(event)) != recorded or event['previous'] != previous or event['sequence'] != len(events):
            raise ValueError('improvement receipt chain failed integrity check')
        event['sha256'] = recorded; events.append(event); previous = recorded
        if event['action'] == 'promote': active[event['case_id']] = event
        elif event['action'] == 'retire': active.pop(event['case_id'], None)
    for key in active:
        case = json.loads((state/'cases'/f'{key}.json').read_text())
        if validate_case(case) != key: raise ValueError('retained case failed integrity check')
    return events, active


def append_event(state, action, case_id, report):
    events, active = history(state)
    if len(events) >= MAX_EVENTS: raise ValueError('receipt budget reached; archive state explicitly')
    event = {'schema': SCHEMA, 'sequence': len(events), 'previous': events[-1]['sha256'] if events else None,
             'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'action': action,
             'case_id': case_id, 'report_sha256': digest(encoded(report)), 'report': report}
    event['sha256'] = digest(encoded(event))
    atomic(state/'events'/f'{len(events):06}.json', encoded(event))
    return event


def check_retained(root, state, deadline):
    _, active = history(state)
    results = []
    for key in sorted(active):
        case = json.loads((state/'cases'/f'{key}.json').read_text())
        for role in ('original', 'transfer', 'control'):
            results.append(scenario(root, case[role], deadline, key+':'+role))
    return results


def cycle(root, baseline, case, state, seconds=300):
    root, baseline, state = Path(root).resolve(), Path(baseline).resolve(), Path(state).resolve()
    case_id = validate_case(case)
    if not 1 <= seconds <= 600: raise ValueError('cycle budget must be in 1..600 seconds')
    deadline = time.monotonic()+seconds
    report = {'schema': SCHEMA, 'ok': False, 'case_id': case_id, 'host': platform.node(),
              'scope': 'same-agent synthetic regression curriculum; no runtime edits or native evidence promotion', 'checks': []}
    with locked(state), tempfile.TemporaryDirectory(prefix='surd-improve-') as tmp:
        _, active = history(state)
        if case_id in active: raise ValueError('case already active; use check to revalidate')
        if len(active) >= MAX_CASES: raise ValueError('active case budget reached')
        fixed, before = Path(tmp)/'fixed', Path(tmp)/'baseline'
        report['source_sha256'] = snapshot(root, fixed)
        report['baseline_sha256'] = snapshot(baseline, before)
        for role in ('original', 'transfer', 'control'):
            report['checks'].append(scenario(before, case[role], deadline, 'baseline:'+role))
            report['checks'].append(scenario(fixed, case[role], deadline, 'fixed:'+role))
        rows = {r['name']: r for r in report['checks']}
        demonstrated = all(rows['baseline:'+r]['state'] == 'failed' and rows['baseline:'+r].get('clean_execution')
                           for r in ('original', 'transfer'))
        demonstrated = demonstrated and rows['baseline:control']['ok'] and all(rows['fixed:'+r]['ok'] for r in ('original','transfer','control'))
        if demonstrated:
            report['checks'].extend(check_retained(fixed, state, deadline))
            for guard in GUARDS:
                if not (fixed/guard).is_file():
                    report['checks'].append({'name': guard, 'ok': False, 'state': 'missing'})
                else:
                    report['checks'].append(command(fixed, [guard], deadline, guard))
            report['checks'].append(command(fixed, ['surd.py', 'selftest'], deadline, 'selftest'))
            # Baseline failures are required evidence. Every fixed/control/guard result must pass.
            report['ok'] = all(r['ok'] for r in report['checks'] if r['name'] not in ('baseline:original', 'baseline:transfer'))
        if fingerprint(root) != report['source_sha256'] or fingerprint(baseline) != report['baseline_sha256']:
            report['ok'] = False; report['error'] = 'source drift during validation; no promotion'
        report['action'] = 'promote' if report['ok'] else 'hold'
        if report['ok']:
            atomic(state/'cases'/f'{case_id}.json', encoded(case))
        event = append_event(state, report['action'], case_id, report)
        report['receipt_sha256'] = event['sha256']
    return report


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['cycle', 'check', 'status', 'retire'])
    parser.add_argument('--root', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--state', default=str(Path(__file__).resolve().parent/'state/improvement'))
    parser.add_argument('--case'); parser.add_argument('--baseline'); parser.add_argument('--case-id')
    parser.add_argument('--reason'); parser.add_argument('--report'); parser.add_argument('--seconds', type=float, default=300)
    args = parser.parse_args(argv)
    state, root = Path(args.state).resolve(), Path(args.root).resolve()
    try:
        if not 1 <= args.seconds <= 600: raise ValueError('budget must be in 1..600 seconds')
        if args.action == 'cycle':
            if not args.case or not args.baseline: raise ValueError('cycle requires --case and --baseline')
            report = cycle(root, args.baseline, json.loads(Path(args.case).read_text()), state, args.seconds)
        elif args.action == 'check':
            with locked(state), tempfile.TemporaryDirectory(prefix='surd-check-') as tmp:
                frozen = Path(tmp)/'source'; pin = snapshot(root, frozen)
                checks = check_retained(frozen, state, time.monotonic()+args.seconds)
                report = {'schema': SCHEMA, 'ok': all(r['ok'] for r in checks) and fingerprint(root) == pin,
                          'source_sha256': pin, 'checks': checks, 'active_cases': len(history(state)[1])}
        elif args.action == 'retire':
            if not args.case_id or not args.reason: raise ValueError('retire requires --case-id and --reason')
            with locked(state):
                if args.case_id not in history(state)[1]: raise ValueError('case is not active')
                report = {'ok': True, 'case_id': args.case_id, 'reason': args.reason}
                report['receipt_sha256'] = append_event(state, 'retire', args.case_id, report)['sha256']
        else:
            with locked(state):
                events, active = history(state)
                report = {'ok': True, 'events': len(events), 'active_cases': sorted(active), 'head': events[-1]['sha256'] if events else None}
    except (ValueError, OSError, KeyError, TypeError) as error:
        report = {'ok': False, 'error': str(error)}
    if args.report: atomic(Path(args.report), encoded(report))
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(cli())
