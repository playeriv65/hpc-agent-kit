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
        self.args = argparse.Namespace(request='stable', socket='isolated', target='test:allocate',
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
        env = dict(os.environ, PATH=str(fake) + ':' + os.environ['PATH'], MSI_ALLOCATE_STATE_DIR=str(self.root / 'state'))
        argv = ['python3', str(ROOT / 'remote/msi-allocation'), 'submit', 'race', '--socket', 'fake', '--target', 'test:allocate', '--field', 'count=1', '--', '/bin/true']
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


SITE_CONFIG = {
    'MSI_ALLOCATE_ACCOUNT': 'lab',
    'MSI_ALLOCATE_GPU_TYPES': 'a100 h100', 'MSI_ALLOCATE_GPU_DEFAULT_TYPE': 'a100',
    'MSI_ALLOCATE_GPU_DEFAULT_COUNT': '2', 'MSI_ALLOCATE_GPU_WALLTIME': '08:00:00',
    'MSI_ALLOCATE_GPU_CPUS': '8', 'MSI_ALLOCATE_GPU_MEMORY': '64G', 'MSI_ALLOCATE_GPU_TMP': '100G',
    'MSI_ALLOCATE_GPU_A100_PARTITION': 'a100-4', 'MSI_ALLOCATE_GPU_A100_COUNTS': '1 2 3 4',
    'MSI_ALLOCATE_GPU_H100_PARTITION': 'hopper', 'MSI_ALLOCATE_GPU_H100_GRES': 'h100-80',
    'MSI_ALLOCATE_GPU_H100_QOS': 'gpuq', 'MSI_ALLOCATE_GPU_H100_COUNTS': '1 2 4',
    'MSI_ALLOCATE_GPU_H100_NAME_PATTERN': 'H100',
    'MSI_ALLOCATE_CPU_PARTITION': 'small', 'MSI_ALLOCATE_CPU_PARTITIONS': 'small shared',
    'MSI_ALLOCATE_CPU_CORES': '8', 'MSI_ALLOCATE_CPU_MAX_CORES': '128', 'MSI_ALLOCATE_CPU_MAX_CORES_SHARED': '64',
    'MSI_ALLOCATE_CPU_MEMORY': '32G', 'MSI_ALLOCATE_CPU_WALLTIME': '04:00:00', 'MSI_ALLOCATE_CPU_TMP': '50G',
}


class PlanTests(unittest.TestCase):
    """allocate gpu|cpu builds salloc arguments from site configuration only."""

    def setUp(self):
        self.module = module('allocation')
        patcher = patch.dict(os.environ, SITE_CONFIG)
        patcher.start(); self.addCleanup(patcher.stop)

    def plan(self, *argv):
        return self.module.plan(list(argv))

    def test_cpu_request_is_one_task_on_one_node_with_explicit_memory(self):
        result = self.plan('cpu', '4', '00:30:00', '--mem', '8G', '--tmp', '10G', '--request', 'cpu-1')
        self.assertEqual(result['salloc'], ['--partition=small', '--nodes=1', '--ntasks=1', '--cpus-per-task=4',
                                            '--mem=8G', '--time=00:30:00', '--tmp=10G', '--account=lab'])
        self.assertEqual(result['env'], {'HPC_ALLOCATION_KIND': 'cpu'})
        self.assertEqual((result['request'], result['fields']['cores'], result['fields']['partition']), ('cpu-1', '4', 'small'))

    def test_cpu_defaults_come_from_configuration(self):
        result = self.plan('cpu')
        self.assertIn('--cpus-per-task=8', result['salloc']); self.assertIn('--mem=32G', result['salloc'])
        self.assertIn('--time=04:00:00', result['salloc']); self.assertIn('--tmp=50G', result['salloc'])
        self.assertNotIn('--gres', ' '.join(result['salloc']))

    def test_partition_override_is_limited_to_configured_partitions(self):
        result = self.plan('cpu', '64', '--partition', 'shared', '--constraint', 'genoa')
        self.assertIn('--partition=shared', result['salloc']); self.assertIn('--constraint=genoa', result['salloc'])
        with self.assertRaisesRegex(ValueError, 'between 1 and 64'):
            self.plan('cpu', '65', '--partition', 'shared')
        with self.assertRaisesRegex(ValueError, 'must be one of: small, shared'):
            self.plan('cpu', '8', '--partition', 'gpu-nodes')

    def test_partition_that_assigns_memory_refuses_mem(self):
        with patch.dict(os.environ, MSI_ALLOCATE_CPU_MEMORY_REQUEST='0'):
            self.assertFalse(any(arg.startswith('--mem') for arg in self.plan('cpu', '16')['salloc']))
            with self.assertRaisesRegex(ValueError, 'assigns memory per core'):
                self.plan('cpu', '16', '--mem', '8G')

    def test_gpu_type_selects_partition_gres_qos_and_name_pattern(self):
        result = self.plan('gpu', 'h100', '4', '02:00:00', '--begin', '10:00 09/01/26')
        self.assertEqual(result['salloc'], ['--partition=hopper', '--nodes=1', '--ntasks=1', '--cpus-per-task=8',
                                            '--gres=gpu:h100-80:4', '--mem=64G', '--time=02:00:00', '--tmp=100G',
                                            '--qos=gpuq', '--account=lab', '--begin=10:00 09/01/26'])
        self.assertEqual(result['env'], {'HPC_ALLOCATION_KIND': 'gpu', 'HPC_EXPECTED_GPU_NAME_PATTERN': 'H100'})
        self.assertIn('--gres=gpu:a100:2', self.plan('gpu')['salloc'])

    def test_invalid_requests_fail_before_submission(self):
        for argv, message in ((['h100', '1'], 'usage'), (['gpu', 'v100'], 'one of: a100, h100'),
                              (['gpu', 'h100', '3'], 'one of: 1, 2, 4'), (['cpu', '8', '8h'], 'HH:MM:SS'),
                              (['cpu', '0'], 'between 1 and 128'), (['cpu', '--mem'], 'requires a value'),
                              (['cpu', '--mem', 'lots'], 'size'), (['cpu', '--memory', '8G'], 'unknown option'),
                              (['cpu', '8', '01:00:00', 'extra'], 'too many')):
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, message):
                self.plan(*argv)

    def test_shell_plan_round_trips_through_bash(self):
        rendered = self.module.shell_plan(self.plan('cpu', '2', '--begin', "now+1hour", '--request', 'quoted'))
        script = rendered + 'printf "%s\\n" "$allocation_request" "$allocation_kind" "${salloc_args[@]}"'
        output = subprocess.check_output(['bash', '-c', script], universal_newlines=True).splitlines()
        self.assertEqual(output[:2], ['quoted', 'cpu'])
        self.assertIn('--begin=now+1hour', output)


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
            (root / 'routes/123.route').write_text('123\tnode\talloc-123\twork\n')
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
            root = Path(temporary); (root / 'routes').mkdir(); (root / 'routes/123.route').write_text('123\tnode\talloc-123\twork\n')
            observations = [('123|RUNNING|hpc-msi-test|node', None), ('', None),
                            ('HPC_HEALTH_JSON=' + json.dumps(dict(job_id='123', healthy=False, autosave_state='stale')), None)]
            with patch.object(state, 'capture', side_effect=observations):
                result = state.status(root, True)
            self.assertEqual(result['routes'][0]['health']['autosave_state'], 'stale')
            self.assertIn('unhealthy backup on job 123', result['errors'])
