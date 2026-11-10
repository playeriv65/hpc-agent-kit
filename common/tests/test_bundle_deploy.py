import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import hashlib

from test_profiles import profile, renderer

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bundle_deploy', str(ROOT / 'common/deploy_bundle.py'))
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    def test_installed_nonexported_archive_configuration_holds_its_lease(self):
        import time
        config = self.home / '.config/test-lab-allocate/allocate.env'; config.parent.mkdir(parents=True)
        archive = self.root / 'configured/work.tar.zst'; archive.parent.mkdir()
        config.write_text("HPC_ARCHIVE='" + str(archive) + "'\n")
        payload = dict(self.payload); payload.pop('archive')
        with subprocess.Popen(['flock', str(archive) + '.autosave.core.lock', 'sleep', '2']) as owner:
            time.sleep(0.2)
            result = self.run_install(payload)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(self.target.read_bytes(), b'old release')
            self.assertIsNone(owner.poll())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.home = self.root / 'home'; self.home.mkdir()
        self.fake = self.root / 'fake'; self.fake.mkdir()
        queue = self.fake / 'squeue'; queue.write_text('#!/bin/bash\nexit 0\n'); queue.chmod(0o700)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.fake) + ':' + os.environ['PATH'])
        self.site = 'test-lab'
        self.archive = self.root / 'archive/work.tar.zst'
        files = {'bin/test-lab-allocate': b'#!/usr/bin/env bash\necho release\n',
                 'config/allocate.env': b'# release configuration\n'}
        self.payload = dict(site=self.site, archive=str(self.archive), manifest=dict(core_version='test'),
                            files={k:base64.b64encode(v).decode() for k,v in files.items()},
                            hashes={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
        self.target = self.home / '.local/bin/test-lab-allocate'; self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'old release'); self.target.chmod(0o750)

    def run_install(self, payload=None, injection=''):
        payload = payload or self.payload
        source = deploy.REMOTE
        if injection:
            source = source.replace("if __name__ == '__main__':", injection + "\nif __name__ == '__main__':")
        script = 'PAYLOAD = ' + repr(base64.b64encode(json.dumps(payload).encode()).decode()) + '\n' + source
        return subprocess.run(['python3', '-'], input=script, env=self.env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)

    def test_mid_install_failure_restores_bytes_modes_and_absent_files(self):
        # Fail only on the new configuration target; rollback also removes a newly installed helper.
        injection = '''original_atomic = atomic
failed_once = False
def atomic(path, data, mode=0o600):
    global failed_once
    if str(path).endswith('/.config/test-lab-allocate/allocate.env') and not failed_once:
        failed_once = True
        raise OSError('simulated full filesystem')
    return original_atomic(path, data, mode)
'''
        self.payload['files']['bin/test-lab-new'] = base64.b64encode(b'new helper').decode()
        self.payload['hashes']['bin/test-lab-new'] = hashlib.sha256(b'new helper').hexdigest()
        config = self.payload['files'].pop('config/allocate.env'); self.payload['files']['config/allocate.env'] = config
        result = self.run_install(injection=injection)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.target.read_bytes(), b'old release')
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o750)
        self.assertFalse((self.home / '.local/bin/test-lab-new').exists())
        journal = json.loads((self.home / '.local/state/test-lab-allocate/deployment.json').read_text())
        self.assertEqual(journal['phase'], 'rolled_back')

    def test_forced_process_loss_recovers_and_explicit_rollback_restores_prior_manifest(self):
        manifest = self.home / '.config/test-lab-allocate/core-manifest.json'; manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b'old manifest')
        injection = '''original_atomic = atomic
def atomic(path, data, mode=0o600):
    original_atomic(path, data, mode)
    if str(path).endswith('/.local/bin/test-lab-allocate'): os._exit(99)
'''
        lost = self.run_install(injection=injection)
        self.assertEqual(lost.returncode, 99, lost.stdout)
        state = self.home / '.local/state/test-lab-allocate'
        guard = state / 'allocations/hpc-deployment-guard.json'
        self.assertTrue(guard.exists())
        # Simulate an older allocation entry that does not know the new journal.
        from test_allocation_health import module
        from unittest.mock import patch
        import argparse
        allocator = module('allocation')
        journal = state / 'deployment.json'; hidden = state / 'journal-hidden'
        journal.rename(hidden)
        try:
            args = argparse.Namespace(request='fresh', field=[], command=['/bin/true'], socket='test', target='test')
            with patch.object(allocator, 'queue', return_value=[]):
                with self.assertRaisesRegex(RuntimeError, 'requires recovery'):
                    allocator.submit(state / 'allocations', args)
        finally: hidden.rename(journal)
        committed = self.run_install()
        self.assertEqual(committed.returncode, 0, committed.stdout)
        self.assertFalse(guard.exists())
        backup = json.loads(committed.stdout.strip().splitlines()[-1])['backup']
        rollback = dict(self.payload, rollback=backup)
        result = self.run_install(rollback)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.target.read_bytes(), b'old release')
        self.assertEqual(manifest.read_bytes(), b'old manifest')

    def test_retired_paths_move_into_backup_and_return_on_rollback(self):
        old_helper = self.home / '.local/bin/test-lab-old'; old_helper.write_bytes(b'old helper')
        old_state = self.home / '.local/state/test-lab-old'; (old_state / 'allocations').mkdir(parents=True)
        (old_state / 'allocations/done.json').write_text(json.dumps(dict(phase='terminal')))
        old_config = self.home / '.config/test-lab-old'; old_config.mkdir(parents=True)
        (old_config / 'settings.env').write_text('OLD=1\n')
        retire = ['.local/bin/test-lab-old', '.local/state/test-lab-old', '.config/test-lab-old', '.local/bin/absent']
        committed = self.run_install(dict(self.payload, retire=retire))
        self.assertEqual(committed.returncode, 0, committed.stdout)
        proof = json.loads(committed.stdout.strip().splitlines()[-1])
        self.assertEqual(proof['retired'], sorted(retire[:3]))
        self.assertFalse(old_helper.exists() or old_state.exists() or old_config.exists())
        backup = Path(proof['backup'])
        self.assertEqual((backup / 'retired/.config/test-lab-old/settings.env').read_text(), 'OLD=1\n')
        result = self.run_install(dict(self.payload, rollback=str(backup)))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(old_helper.read_bytes(), b'old helper')
        self.assertEqual((old_config / 'settings.env').read_text(), 'OLD=1\n')
        self.assertTrue((old_state / 'allocations/done.json').exists())
        self.assertEqual(self.target.read_bytes(), b'old release')

    def test_retired_state_with_unresolved_request_refuses_deployment(self):
        old_state = self.home / '.local/state/test-lab-old'; (old_state / 'allocations').mkdir(parents=True)
        (old_state / 'allocations/pending.json').write_text(json.dumps(dict(phase='dispatched', job_id=None)))
        for retire in (['.local/state/test-lab-old'], ['/etc/passwd'], ['.local/state/test-lab-allocate'], ['.local/bin/test-lab-allocate']):
            with self.subTest(retire=retire):
                result = self.run_install(dict(self.payload, retire=retire))
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.target.read_bytes(), b'old release')
                self.assertTrue((old_state / 'allocations/pending.json').exists())

    def test_backup_tampering_and_foreign_lease_refuse_changes(self):
        committed = self.run_install(); self.assertEqual(committed.returncode, 0, committed.stdout)
        backup = Path(json.loads(committed.stdout.strip().splitlines()[-1])['backup'])
        (backup / 'bin/test-lab-allocate').write_bytes(b'tampered')
        prior = self.target.read_bytes()
        result = self.run_install(dict(self.payload, rollback=str(backup)))
        self.assertNotEqual(result.returncode, 0); self.assertEqual(self.target.read_bytes(), prior)
        import time
        with subprocess.Popen(['flock', str(self.archive) + '.autosave.core.lock', 'sleep', '2']) as holder:
            time.sleep(0.2)
            result = self.run_install()
            self.assertNotEqual(result.returncode, 0); self.assertEqual(self.target.read_bytes(), prior)
            self.assertIsNone(holder.poll())

    def test_frozen_bundle_hashes_backup_and_queue_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home = root / 'home'; home.mkdir(); bundle = root / 'bundle'; bundle.mkdir()
            data = profile(); data['transport']['exec_argv'] = ['bash', '-s']
            data['storage']['archive'] = str(root / 'persistent/work.tar.zst')
            files = renderer.render(data)
            import hashlib
            for name, body in files.items():
                path = bundle / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
            manifest = dict(site=data['name'], core_version='0.3.0', files={name: hashlib.sha256(body.encode()).hexdigest() for name, body in files.items()})
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            _, payload = deploy.freeze(bundle)
            changed = bundle / ('bin/' + data['name'] + '-allocate'); changed.write_text('changed after freeze')
            with self.assertRaisesRegex(ValueError, 'Bundle changed'):
                deploy.freeze(bundle)
            fake = root / 'fake'; fake.mkdir(); busy = root / 'busy'
            queue = fake / 'squeue'; queue.write_text('#!/bin/bash\nif [[ -f "' + str(busy) + '" ]]; then echo "123|RUNNING"; fi\n'); queue.chmod(0o700)
            target = home / '.local/bin' / (data['name'] + '-allocate'); target.parent.mkdir(parents=True); target.write_text('old release')
            env = dict(os.environ, HOME=str(home), PATH=str(fake) + ':' + os.environ['PATH'])
            script = 'PAYLOAD = ' + repr(base64.b64encode(json.dumps(payload).encode()).decode()) + '\n' + deploy.REMOTE
            result = subprocess.run(['python3', '-'], input=script, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            proof = json.loads(result.stdout.strip().splitlines()[-1]); backup = Path(proof['backup'])
            self.assertEqual((backup / 'bin' / target.name).read_text(), 'old release')
            self.assertEqual(target.read_text(), files['bin/' + target.name])
            busy.touch()
            result = subprocess.run(['python3', '-'], input=script, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertNotEqual(result.returncode, 0); self.assertIn('Live jobs exist', result.stdout)
            self.assertEqual(target.read_text(), files['bin/' + target.name])
