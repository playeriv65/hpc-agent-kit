import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('profile_renderer', str(ROOT / 'common/profile.py'))
renderer = importlib.util.module_from_spec(spec); spec.loader.exec_module(renderer)


def profile(name='test-lab'):
    return dict(version=1, name=name, adapter='direct-ssh', transport=dict(exec_argv=['ssh', 'test-login', 'bash -s']),
        verified=dict(scheduler='slurm', local_disk=True, persistent_archive=True, compute_ssh=True,
                      project_qos=['gpu'], partition_qos={'accelerator': ['gpu']}, default_qos_allowed=False),
        storage=dict(local_template='/scratch/$USER/job-$SLURM_JOB_ID/hpc', local_prefix='/scratch/$USER/',
                     archive='/project/$USER/work.tar.zst', results='/project/$USER/results'),
        allocation=dict(account='testproject', cpus=2, memory='8G', walltime='00:04:00', default_gpu='mi300',
                        gpus={'mi300': dict(gres='mi300a', partition='accelerator', qos='gpu', max_count=4)}))


class ProfileTests(unittest.TestCase):
    def test_snapshot_policy_is_validated_and_reaches_generated_config(self):
        data = profile(); data['snapshot'] = dict(interval=600, end_margin=900, generations_keep=4)
        config = renderer.render(data)['config/gpu.env']
        self.assertIn('TEST_LAB_LOCAL_SNAPSHOT_END_MARGIN=900', config)
        self.assertIn('TEST_LAB_GPU_AUTOSAVE_INTERVAL=600', config)
        data['snapshot']['generations_keep'] = 1
        with self.assertRaisesRegex(ValueError, 'Invalid snapshot policy'):
            renderer.render(data)

    def test_multiple_partitions_require_qos_permission_in_every_partition(self):
        data = profile()
        data['allocation']['gpus']['mi300']['partition'] = 'accelerator,accelerator-small'
        with self.assertRaisesRegex(ValueError, 'both project and partition'):
            renderer.validate(data)
        data['verified']['partition_qos']['accelerator-small'] = ['gpu']
        renderer.validate(data)
        data['allocation']['gpus']['mi300']['partition'] = 'accelerator,accelerator'
        with self.assertRaisesRegex(ValueError, 'Invalid GRES or partition'):
            renderer.validate(data)

    def test_psc_scratch_root_is_isolated_before_loading_site_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary) / 'local'; scratch.mkdir()
            header = (ROOT / 'common/sites/psc-boot.sh').read_text().split('if [[ -f "$HOME/.psc-local-env.sh" ]]')[0]
            header = header.replace('/local', str(scratch))
            env = dict(os.environ, LOCAL=str(scratch), USER='fixture', SLURM_JOB_ID='123',
                       PSC_GPU_CONFIG=str(Path(temporary) / 'absent.env'))
            expected = scratch / 'fixture/hpc-job-123'
            for initial in (str(scratch), None):
                if initial is None: env.pop('LOCAL', None)
                else: env['LOCAL'] = initial
                result = subprocess.run(['bash', '-c', header + '\nprintf "%s" "$LOCAL"'], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, str(expected)); self.assertTrue(expected.is_dir())

    def test_qos_intersection_and_unknown_compute_access(self):
        data = profile(); data['verified']['project_qos'] = ['low']
        with self.assertRaisesRegex(ValueError, 'both project and partition'):
            renderer.render(data)
        data = profile(); data['verified']['compute_ssh'] = False
        with self.assertRaisesRegex(ValueError, 'verified login-to-compute'):
            renderer.render(data)

    def test_render_both_adapters_and_parse_generated_shell(self):
        for adapter in ('direct-ssh', 'psc-bridge'):
            data = profile(); data['adapter'] = adapter
            if adapter == 'psc-bridge':
                data['allocation']['default_gpu'] = 'v100'
                data['allocation']['gpus'] = {'v100': dict(gres='v100-32', partition='accelerator', qos='gpu', max_count=8)}
            files = renderer.render(data)
            with tempfile.TemporaryDirectory() as temporary:
                for name, body in files.items():
                    path = Path(temporary) / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
                    self.assertNotIn('@SITE@', body); self.assertNotIn('@ADAPTER@', body)
                    if body.startswith('#!/usr/bin/env bash') or name.endswith(('.env', '.sh')):
                        subprocess.check_call(['bash', '-n', str(path)])
                    elif body.startswith('#!/usr/bin/env python3'):
                        compile(body, name, 'exec')

    def test_generic_adapter_submits_profile_gres_in_real_tmux(self):
        data = profile('lab-' + uuid.uuid4().hex[:10])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, body in renderer.render(data).items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body); path.chmod(0o700)
            fake = root / 'fake'; fake.mkdir(); marker = root / 'submitted'
            for name, body in {'squeue': 'exit 0', 'pgrep': 'exit 1', 'salloc': 'printf "%s\\n" "$@" > "' + str(marker) + '"'}.items():
                path = fake / name; path.write_text('#!/bin/bash\n' + body + '\n'); path.chmod(0o700)
            prefix = data['name'].upper().replace('-', '_')
            env = dict(os.environ, HPC_CORE_BIN=str(root / 'bin'), PATH=str(fake) + ':' + os.environ['PATH'],
                       **{prefix + '_GPU_CONFIG': str(root / 'config/gpu.env'), prefix + '_GPU_STATE_DIR': str(root / 'state')})
            socket = data['name'] + '-login'
            try:
                command = ['bash', str(root / 'bin' / (data['name'] + '-gpu')), '--request', 'stable', 'mi300', '1', '00:04:00']
                result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                for _ in range(100):
                    if marker.exists(): break
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), 'candidate never reached the stub scheduler')
                args = marker.read_text().splitlines()
                self.assertIn('--gres=gpu:mi300a:1', args); self.assertIn('--account=testproject', args)
                record = json.loads((root / 'state/allocations/stable.json').read_text())
                self.assertIn('--comment=' + record['comment'], args)
                repeated = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(repeated.returncode, 0, repeated.stdout)
                self.assertEqual(marker.read_text().splitlines(), args)
            finally:
                subprocess.call(['tmux', '-L', socket, 'kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_psc_adapter_keeps_terminal_through_salloc_and_srun(self):
        data = profile('bridge-' + uuid.uuid4().hex[:10]); data['adapter'] = 'psc-bridge'
        data['allocation']['default_gpu'] = 'v100'
        data['allocation']['gpus'] = {'v100': dict(gres='v100-16', partition='accelerator', qos='gpu', max_count=8)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); fake = root / 'fake'; fake.mkdir(); marker = root / 'tty-passed'
            stubs = {'squeue': '#!/bin/bash\nexit 0\n', 'pgrep': '#!/bin/bash\nexit 1\n',
                     'salloc': '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n',
                     'srun': '#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n'
                             'assert all(os.isatty(fd) for fd in (0,1,2))\n'
                             'fd=os.open("/dev/tty",os.O_RDWR); os.close(fd)\n'
                             'Path(' + repr(str(marker)) + ').write_text("passed")\n'}
            for name, body in stubs.items():
                path = fake / name; path.write_text(body); path.chmod(0o700)
            for name, body in renderer.render(data).items():
                body = body.replace('/usr/bin/salloc', str(fake / 'salloc')).replace('/usr/bin/srun', str(fake / 'srun'))
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body); path.chmod(0o700)
            prefix = data['name'].upper().replace('-', '_'); socket = data['name'] + '-login'
            # Model the installed config location inside a private fixture HOME.
            # tmux 3.2 opens a startup error mode when an explicit -f is absent.
            login_config = root / '.config' / (data['name'] + '-gpu') / 'tmux.login.conf'
            login_config.parent.mkdir(parents=True)
            login_config.write_text((root / 'config/tmux.login.conf').read_text())
            env = dict(os.environ, HOME=str(root), HPC_CORE_BIN=str(root / 'bin'), PATH=str(fake) + ':' + os.environ['PATH'],
                       **{prefix + '_GPU_CONFIG': str(root / 'config/gpu.env'), prefix + '_GPU_STATE_DIR': str(root / 'state')})
            try:
                result = subprocess.run(['bash', str(root / 'bin' / (data['name'] + '-gpu')), '--request', 'stable',
                                         'v100', '1', '00:04:00'], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                for _ in range(100):
                    if marker.exists(): break
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), 'Slurm delegation lost its controlling terminal')
            finally:
                subprocess.call(['tmux', '-L', socket, 'kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
