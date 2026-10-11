import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    path = ROOT / 'remote' / ('msi-' + name)
    spec = importlib.util.spec_from_loader(name, loader=None)
    result = importlib.util.module_from_spec(spec)
    result.__file__ = str(path)
    exec(compile(path.read_text(), str(path), 'exec'), result.__dict__)
    return result


class AllocationTests(unittest.TestCase):
    def test_interrupted_deployment_blocks_allocation_before_queue_dispatch(self):
        self.root.joinpath('deployment.json').write_text(json.dumps(dict(phase='installing')))
        allocations = self.root / 'allocations'; allocations.mkdir()
        with patch.object(self.module, 'queue') as query:
            with self.assertRaisesRegex(RuntimeError, 'Incomplete deployment'):
                self.module.submit(allocations, self.args)
        query.assert_not_called()

    def setUp(self):
        self.module = module('allocation')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(request='stable', socket='isolated', target='test:gpu-request',
            field=['count=1'], command=['/bin/true'])

    def reserve(self):
        with patch.object(self.module, 'queue', return_value=[]), patch.object(self.module, 'command', return_value='bash|123'), patch.object(self.module.subprocess, 'run', return_value=argparse.Namespace(returncode=1)), patch.object(self.module.subprocess, 'check_call') as dispatch:
            record = self.module.submit(self.root, self.args)
        self.assertEqual(dispatch.call_count, 2)
        return record

    def test_replay_and_changed_configuration(self):
        self.reserve()
        with patch.object(self.module, 'queue', return_value=[]), patch.object(self.module.subprocess, 'check_call') as dispatch:
            record = self.module.submit(self.root, self.args)
            self.assertEqual(record['phase'], 'submission_unknown')
            dispatch.assert_not_called()
            self.args.field = ['count=2']
            with self.assertRaisesRegex(RuntimeError, 'changed configuration'):
                self.module.submit(self.root, self.args)

    def test_other_request_and_unknown_queue_fail_closed(self):
        self.reserve(); self.args.request = 'second'
        with patch.object(self.module, 'queue', return_value=[]), self.assertRaisesRegex(RuntimeError, 'requires recovery'):
            self.module.submit(self.root, self.args)
        with patch.object(self.module, 'queue', side_effect=RuntimeError('scheduler offline')), self.assertRaisesRegex(RuntimeError, 'scheduler offline'):
            self.module.submit(self.root, self.args)
        self.assertFalse((self.root / 'second.json').exists())

    def test_disconnect_recovers_pending_job_by_comment_and_owner(self):
        record = self.reserve()
        jobs = [dict(job_id='123', slurm_state='PENDING', node='', name=record['job_name'])]
        detail = 'JobId=123 UserId=' + os.environ['USER'] + '(42) Comment=' + record['comment']
        with patch.object(self.module, 'command', return_value=detail):
            restored = self.module.reconcile(record, jobs)
        self.assertEqual(restored['job_id'], '123'); self.assertEqual(restored['phase'], 'pending')
        with patch.object(self.module, 'command', return_value=detail.replace(record['comment'], 'foreign')), self.assertRaisesRegex(RuntimeError, 'comment mismatch'):
            self.module.reconcile(record, jobs)

    def test_old_job_does_not_adopt_later_same_name(self):
        record = self.reserve(); record['job_id'] = '123'
        jobs = [dict(job_id='124', slurm_state='RUNNING', node='node', name=record['job_name'])]
        with patch.object(self.module, 'command', return_value='123|TIMEOUT|0:0'):
            restored = self.module.reconcile(record, jobs)
        self.assertEqual(restored['job_id'], '123'); self.assertEqual(restored['phase'], 'terminal')

    def test_concurrent_cli_reserves_only_one_dispatch(self):
        fake = self.root / 'bin'; fake.mkdir()
        log = self.root / 'dispatch.log'
        for name, body in {'squeue': 'exit 0', 'pgrep': 'exit 1', 'tmux':
            'if [[ "$*" == *display-message* ]]; then echo "bash|123"; else echo dispatch >> "' + str(log) + '"; fi'}.items():
            path = fake / name; path.write_text('#!/bin/bash\n' + body + '\n'); path.chmod(0o700)
        env = dict(os.environ, PATH=str(fake) + ':' + os.environ['PATH'], MSI_GPU_STATE_DIR=str(self.root / 'state'))
        argv = ['python3', str(ROOT / 'remote/msi-allocation'), 'submit', 'race', '--socket', 'fake', '--target', 'test:gpu-request', '--field', 'count=1', '--', '/bin/true']
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True), range(2)))
        self.assertTrue(all(r.returncode == 0 for r in results), [r.stdout for r in results])
        self.assertEqual(log.read_text().splitlines(), ['dispatch', 'dispatch'])

    def test_holder_preserves_controlling_terminal_and_exit_status(self):
        record = self.reserve()
        self.module.atomic(self.root / 'stable.json', record)
        args = argparse.Namespace(request='stable', command=['python3', '-c',
            'import os; assert all(os.isatty(fd) for fd in (0,1,2)); '
            'fd=os.open("/dev/tty",os.O_RDWR); os.close(fd); '
            'print("CONTROLLING_TTY_PASSED",flush=True); raise SystemExit(7)'])
        with patch.object(self.module, 'queue', return_value=[]), patch.dict(os.environ, HPC_ALLOCATION_KEEP_PANE='0'):
            self.assertEqual(self.module.hold(self.root, args), 7)
        self.assertIn(b'CONTROLLING_TTY_PASSED', (self.root / 'stable.log').read_bytes())
        self.assertEqual(json.loads((self.root / 'stable.json').read_text())['holder_exit_code'], 7)


class HealthTests(unittest.TestCase):
    def test_doctor_without_compute_does_not_claim_checks_ran(self):
        state = module('agent-state')
        with tempfile.TemporaryDirectory() as temporary, patch.object(state, 'capture', return_value=('', None)):
            result = state.status(Path(temporary), True, True)
        self.assertEqual(set(result['checks_not_performed']),
                         {'compute_storage', 'autosave_liveness', 'snapshot_integrity', 'toolchain'})
        self.assertTrue(result['next_actions'])

    def test_unreachable_compute_keeps_storage_and_autosave_unchecked(self):
        state = module('agent-state')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'routes').mkdir()
            (root / 'routes/123.route').write_text('123\tnode\tgpu-123\twork\n')
            with patch.object(state, 'capture', side_effect=[('123|RUNNING|test|node', None), (None, 'SSH disconnected')]):
                result = state.status(root, True)
            self.assertIn('compute_storage', result['checks_not_performed'])
            self.assertIn('autosave_liveness', result['checks_not_performed'])

    def test_current_live_writer_stale_time_and_job_mismatch(self):
        health = module('health')
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'work.tar.zst'; archive.touch()
            pid = os.getpid(); start = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
            Path(str(archive)[:-8] + '.autosave.pid').write_text('%s\t%s\t%s\n' % (pid, socket.gethostname(), start))
            record = dict(last_success_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), last_success_job='123',
                          last_success_autosave_id='%s:%s:%s' % (socket.gethostname(), pid, start), consecutive_failures=0)
            path = Path(str(archive) + '.health.json'); path.write_text(json.dumps(record))
            self.assertTrue(health.observe(archive, '123')['healthy'])
            self.assertFalse(health.observe(archive, '124')['healthy'])
            record['last_success_at'] = '2000-01-01T00:00:00+00:00'; path.write_text(json.dumps(record))
            self.assertEqual(health.observe(archive, '123')['freshness'], 'stale')
            record['last_success_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat(); record['consecutive_failures'] = 1
            path.write_text(json.dumps(record)); self.assertFalse(health.observe(archive, '123')['healthy'])

    def test_doctor_reports_unhealthy_compute_backup(self):
        state = module('agent-state')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'routes').mkdir(); (root / 'routes/123.route').write_text('123\tnode\tgpu-123\twork\n')
            observations = [('123|RUNNING|hpc-msi-test|node', None), ('', None),
                            ('HPC_HEALTH_JSON=' + json.dumps(dict(job_id='123', healthy=False, autosave_state='stale')), None)]
            with patch.object(state, 'capture', side_effect=observations):
                result = state.status(root, True)
            self.assertEqual(result['routes'][0]['health']['autosave_state'], 'stale')
            self.assertIn('unhealthy backup on job 123', result['errors'])
