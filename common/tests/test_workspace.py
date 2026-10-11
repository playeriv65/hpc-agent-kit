"""Run on Linux in an isolated fixture; no scheduler or GPU is needed."""
import base64
import concurrent.futures
import importlib.util
import json
import os
import random
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT
from test_profiles import profile, renderer


def setUpModule():
    global ROOT, GENERATED
    GENERATED = tempfile.TemporaryDirectory()
    ROOT = Path(GENERATED.name)
    for site, folder in (('msi','remote'),('psc','psc-remote-install')):
        data = profile(site)
        if site == 'psc':
            data['adapter'] = 'psc-bridge'
            data['allocation']['default_gpu'] = 'v100'
            data['allocation']['gpus'] = {'v100':dict(gres='v100-32',partition='accelerator',qos='gpu',max_count=1)}
        for name, body in renderer.render(data).items():
            if name.startswith('bin/'):
                target = ROOT / folder / Path(name).name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(body); target.chmod(0o700)
    (ROOT / 'common').mkdir()
    (ROOT / 'common/boot-guard.sh').write_bytes((SOURCE_ROOT / 'common/boot-guard.sh').read_bytes())


def tearDownModule():
    GENERATED.cleanup()



class SnapshotTests(unittest.TestCase):
    def test_expected_compaction_is_not_a_backup_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            workspace = fixture / 'base/workspace'; workspace.mkdir(parents=True)
            (workspace / 'tiny').write_text('data')
            archive = str(fixture / 'test.tar.zst')
            saved = self.generation_run('msi', fixture, 'save', archive)
            self.assertEqual(saved.returncode, 0, saved.stdout)
            result = self.generation_run('msi', fixture, 'incsave', archive,
                                         MSI_LOCAL_SNAPSHOT_MAX_INCREMENTAL_RATIO='1')
            self.assertEqual(result.returncode, 3, result.stdout)
            health = json.loads(Path(archive + '.health.json').read_text())
            self.assertEqual(health['consecutive_failures'], 0)
            self.assertEqual(health['maintenance']['operation'], 'compaction_required')
            compacted = self.generation_run('msi', fixture, 'compact', archive)
            self.assertEqual(compacted.returncode, 0, compacted.stdout)
            self.assertNotIn('maintenance', json.loads(Path(archive + '.health.json').read_text()))

    def generation_run(self, site, fixture, *args, **extra):
        folder = 'remote' if site == 'msi' else 'psc-remote-install'
        helper = ROOT / folder / (site + '-local-snapshot')
        env = dict(os.environ, fixture=str(fixture), SLURM_JOB_ID='900001')
        env[site.upper() + '_LOCAL_SNAPSHOT_MAX_INCREMENTAL_RATIO'] = '0'
        env.update(extra)
        code = 'source <(sed \'$d\' "$1"); shift; local_base() { echo "$fixture/base"; }; main "$@"'
        return subprocess.run(['bash', '-c', code, 'test', str(helper)] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=20)

    def test_generation_commit_interruption_fallback_and_retention(self):
        for site in ('msi', 'psc'):
            with self.subTest(site=site), tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                workspace = fixture / 'base/workspace'
                workspace.mkdir(parents=True)
                archive = str(fixture / 'archive/test.tar.zst')
                marker = workspace / 'value.txt'
                marker.write_text('first')
                def run(*args, **env):
                    result = self.generation_run(site, fixture, *args, **env)
                    self.assertEqual(result.returncode, 0, result.stdout)
                    return result
                run('save', archive)
                first = Path(archive + '.current').resolve()
                with subprocess.Popen(['flock', archive + '.autosave.core.lock', 'sleep', '3']) as owner:
                    time.sleep(0.2)
                    blocked = self.generation_run(site, fixture, 'restore', archive)
                    self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
                    self.assertEqual(Path(archive + '.current').resolve(), first)
                    self.assertIsNone(owner.poll())
                if site == 'psc' and (ROOT / 'psc-remote-install/psc-local-mirror').exists():
                    mirror = fixture / 'mirror'
                    result = subprocess.run(['bash', str(ROOT / 'psc-remote-install/psc-local-mirror'), 'sync', archive],
                        env=dict(os.environ, PSC_LOCAL_MIRROR_DIR=str(mirror)),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual((mirror / 'workspace/value.txt').read_text(), 'first')
                marker.write_text('latest')
                run('incsave', archive)
                second = Path(archive + '.current').resolve()
                if site == 'psc' and (ROOT / 'psc-remote-install/psc-local-mirror').exists():
                    result = subprocess.run(['bash', str(ROOT / 'psc-remote-install/psc-local-mirror'), 'sync', archive],
                        env=dict(os.environ, PSC_LOCAL_MIRROR_DIR=str(mirror)),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual((mirror / 'workspace/value.txt').read_text(), 'latest')
                # Immutable predecessor still verifies after a new baseline.
                old_manifest = json.loads((first / 'manifest.json').read_text())
                import hashlib
                for name, digest in old_manifest['files'].items():
                    self.assertEqual(hashlib.sha256((first / name).read_bytes()).hexdigest(), digest)
                marker.write_text('uncommitted')
                result = self.generation_run(site, fixture, 'save', archive,
                    **{site.upper() + '_LOCAL_SNAPSHOT_TEST_FAILPOINT': 'before_commit'})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(Path(archive + '.current').resolve(), second)
                run('restore', archive)
                self.assertEqual(marker.read_text(), 'latest')
                # A truncated newest incremental falls back automatically.
                newest = Path(archive + '.current').resolve()
                next((newest / 'full.tar.zst.inc').glob('*.tar.zst')).write_bytes(b'broken')
                marker.write_text('local')
                fallback = run('restore', archive)
                self.assertIn('SNAPSHOT_FALLBACK', fallback.stdout)
                self.assertEqual(marker.read_text(), 'first')
                self.assertTrue(newest.exists(), 'corruption evidence was removed')
                for n in range(4):
                    marker.write_text(str(n)); run('save', archive)
                gens = list(Path(archive + '.generations').glob('gen-*'))
                valid = [g for g in gens if not list((g / 'full.tar.zst.inc').glob('*.tar.zst'))]
                self.assertEqual(len(valid), 3)
                self.assertTrue(newest.exists())
                run('verify', archive)

    def test_retention_keeps_an_independent_previous_full_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            workspace = fixture / 'base/workspace'
            workspace.mkdir(parents=True)
            marker = workspace / 'value.txt'
            archive = str(fixture / 'test.tar.zst')
            def run(*args):
                r = self.generation_run('msi', fixture, *args)
                self.assertEqual(r.returncode, 0, r.stdout)
                return r
            marker.write_text('old full'); run('save', archive)
            marker.write_text('old chain latest'); run('incsave', archive)
            old = Path(archive + '.current').resolve()
            marker.write_text('new full'); run('save', archive)
            for i in range(5):
                marker.write_text('new increment ' + str(i)); run('incsave', archive)
            self.assertTrue(old.exists(), 'previous full family was pruned by incrementals')
            newest = Path(archive + '.current').resolve()
            self.assertFalse(os.path.samefile(old / 'full.tar.zst', newest / 'full.tar.zst'))
            (newest / 'full.tar.zst').write_bytes(b'corrupt full')
            marker.write_text('local')
            restored = run('restore', archive)
            self.assertIn('SNAPSHOT_FALLBACK', restored.stdout)
            self.assertEqual(marker.read_text(), 'old chain latest')

    def test_autosave_initial_deadline_and_final_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            (fixture / 'base/workspace').mkdir(parents=True)
            marker = fixture / 'base/workspace/value.txt'
            marker.write_text('initial')
            archive = str(fixture / 'test.tar.zst')
            helper = ROOT / 'remote/msi-local-snapshot'
            code = 'source <(sed \'$d\' "$1"); shift; local_base() { echo "$fixture/base"; }; main "$@"'
            env = dict(os.environ, fixture=str(fixture), SLURM_JOB_ID='900001',
                       HPC_SNAPSHOT_END_EPOCH=str(int(time.time()) + 120),
                       MSI_LOCAL_SNAPSHOT_MAX_INCREMENTAL_RATIO='0')
            log = fixture / 'autosave.log'
            with log.open('w') as stream:
                process = subprocess.Popen(['bash', '-c', code, 'test', str(helper), 'autosave', '1', archive],
                                           env=env, stdout=stream, stderr=subprocess.STDOUT)
                try:
                    for _ in range(100):
                        if 'before_deadline ok' in log.read_text(): break
                        self.assertIsNone(process.poll(), log.read_text()); time.sleep(0.1)
                    self.assertIn('before_deadline ok', log.read_text())
                    marker.write_text('final')
                    process.terminate(); process.wait(timeout=15)
                    self.assertEqual(process.returncode, 0, log.read_text())
                finally:
                    if process.poll() is None: process.kill(); process.wait()
            marker.write_text('changed')
            result = self.generation_run('msi', fixture, 'restore', archive)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(marker.read_text(), 'final')
            self.assertFalse(Path(archive.removesuffix('.tar.zst') + '.autosave.pid').exists())

    def test_foreign_autosave_identity_is_never_signaled(self):
        with tempfile.TemporaryDirectory() as temporary:
            identity = Path(temporary) / "identity"
            identity.write_text(str(os.getpid()) + "\tforeign-node\t1\n")
            code = 'source <(sed \'$d\' "$1"); read_autosave_identity "$2"'
            result = subprocess.run(["bash", "-c", code, "test", str(ROOT / "remote/msi-local-snapshot"), str(identity)])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(identity.exists())

    def test_both_sites_restore_and_reject_corrupt_chain(self):
        for site, folder in (("msi", "remote"), ("psc", "psc-remote-install")):
            with self.subTest(site=site), tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                helper = ROOT / folder / (site + "-local-snapshot")
                script = (SOURCE_ROOT / "common/tests/snapshot-roundtrip.sh").read_text()
                # The shared engine keeps each site's legacy layout; test its
                # operations against temporary directories rather than real data.
                env = dict(os.environ, fixture=str(fixture))
                env[site.upper() + "_LOCAL_SNAPSHOT_MAX_INCREMENTAL_RATIO"] = "0"
                result = subprocess.run(["bash", "-c", script, "fixture", str(helper), str(fixture)], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                health = json.loads((fixture / "archive/test.tar.zst.health.json").read_text())
                self.assertEqual(health["exit_code"], 19)
                self.assertEqual(health["consecutive_failures"], 1)
                self.assertIn("last_success_at", health)

    def test_lock_timeout_preserves_foreign_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "test.tar.zst"
            with subprocess.Popen(["flock", str(archive) + ".core.lock", "sleep", "3"]) as holder:
                time.sleep(0.2)
                code = 'source <(sed \'$d\' "$1"); noop() { echo BAD; }; with_lock "$2" noop'
                result = subprocess.run(["bash", "-c", code, "test", str(ROOT / "remote/msi-local-snapshot"), str(archive)],
                                        env=dict(os.environ, MSI_LOCAL_SNAPSHOT_LOCK_SECONDS="1"),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("BAD", result.stdout)
                self.assertIsNone(holder.poll())


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Unique socket for the test-owned server, but matching production naming.
        self.job = str(900000000 + random.randrange(100000000))
        self.socket = "gpu-" + self.job
        subprocess.check_call(["tmux", "-L", self.socket, "new-session", "-d", "-s", "work", "-n", "setup",
                               "bash", "--noprofile", "--norc"])
        subprocess.check_call(["tmux", "-L", self.socket, "new-session", "-d", "-s", "zovllm", "-n", "zovllm-setup",
                               "bash", "--noprofile", "--norc"])
        subprocess.check_call(["tmux", "-L", self.socket, "set-environment", "-g", "SLURM_JOB_ID", self.job])
        self.addCleanup(subprocess.call, ["tmux", "-L", self.socket, "kill-server"])

    def run_task(self, site, request, command, window="test", wait=True):
        folder = "remote" if site == "msi" else "psc-remote-install"
        env = dict(os.environ, SLURM_JOB_ID=self.job, MSI_WORKSPACE=str(self.base), LOCAL=str(self.base))
        env[site.upper() + "_GPU_STATE_DIR"] = str(self.base / site)
        argv = ["bash", str(ROOT / folder / (site + "-gpu-inner-run")), "--job", self.job,
                "--window", window, "--request", request, "--payload", base64.b64encode(command.encode()).decode()]
        if wait:
            argv.append("--wait")
        return subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=15)

    def test_exit_log_and_idempotent_replay_both_sites(self):
        for site in ("msi", "psc"):
            marker = self.base / (site + "-count")
            command = "echo once >> '%s'; echo visible; exit 7" % marker
            for _ in range(2):
                result = self.run_task(site, "stable", command)
                self.assertEqual(result.returncode, 7, result.stdout)
            self.assertEqual(marker.read_text(), "once\n")
            directory = self.base / site / "requests/stable"
            self.assertIn("visible", (directory / "output.log").read_text())
            conflict = self.run_task(site, "stable", "echo changed")
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("different", conflict.stdout)

    def test_concurrent_window_reservation(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.run_task, "msi", "race" + str(i), "sleep 2; echo done", wait=False) for i in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])

    def test_literal_command_and_json_query(self):
        result = self.run_task("msi", "literal", "printf '%s\\n' 'a b' '$HOME' \"single'quote\"", window="literal")
        self.assertEqual(result.returncode, 0, result.stdout)
        state = self.base / "msi"
        query = subprocess.check_output(["python3", str(ROOT / "remote/msi-agent-state"), "task", "literal"],
                                        env=dict(os.environ, MSI_GPU_STATE_DIR=str(state)), universal_newlines=True)
        record = json.loads(query)
        self.assertEqual(record["phase"], "completed")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(Path(record["log_path"]).read_text(), "a b\n$HOME\nsingle'quote\n")

    def test_protected_window_rejected(self):
        result = self.run_task("msi", "protected", "echo BAD", window="setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.base / "msi/requests/protected").exists())

    def test_partial_boot_preserves_existing_session(self):
        state = self.base / "partial"
        state.mkdir()
        script = 'state_dir="$1"; job_id="$2"; host_name=node; session=work; route_file="$1/missing"; inner_tmux=(tmux -L "gpu-$2"); source "$3"; echo BAD'
        result = subprocess.run(["bash", "-c", script, "test", str(state), self.job, str(ROOT / "common/boot-guard.sh")],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertEqual(subprocess.call(["tmux", "-L", self.socket, "has-session", "-t", "work"]), 0)

    def test_wrong_allocation_is_rejected(self):
        subprocess.check_call(["tmux", "-L", self.socket, "set-environment", "-g", "SLURM_JOB_ID", "wrong"])
        result = self.run_task("msi", "wrong", "echo BAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.base / "msi/requests/wrong").exists())

    def test_boot_guard_reuses_without_touching_windows(self):
        state = self.base / "boot"
        state.mkdir()
        route = state / (self.job + ".route")
        route.write_text(self.job + "\tnode\tgpu-" + self.job + "\twork\n")
        script = 'state_dir="$1"; job_id="$2"; host_name=node; session=work; route_file="$1/$2.route"; inner_tmux=(tmux -L "gpu-$2"); source "$3"; echo BAD'
        result = subprocess.run(["bash", "-c", script, "test", str(state), self.job, str(ROOT / "common/boot-guard.sh")],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("REUSED", result.stdout)
        self.assertNotIn("BAD", result.stdout)
        self.assertTrue(route.exists())




class StateTests(unittest.TestCase):
    def test_scheduler_failure_is_unknown(self):
        path = ROOT / "remote/msi-agent-state"
        spec = importlib.util.spec_from_loader("state", loader=None)
        module = importlib.util.module_from_spec(spec)
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        with tempfile.TemporaryDirectory() as temporary, patch.object(module, "capture", return_value=(None, "controller unavailable")):
            result = module.status(Path(temporary))
        self.assertEqual(result["queue_state"], "unknown")
        self.assertEqual(result["errors"], ["controller unavailable"])


class SyntaxTests(unittest.TestCase):
    def test_all_installed_shell_helpers_parse(self):
        for folder in ("remote", "psc-remote-install"):
            for path in (ROOT / folder).iterdir():
                if path.is_file() and path.read_bytes().startswith(b"#!/usr/bin/env bash"):
                    with self.subTest(path=path.name):
                        subprocess.check_call(["bash", "-n", str(path)])


if __name__ == "__main__":
    unittest.main()
