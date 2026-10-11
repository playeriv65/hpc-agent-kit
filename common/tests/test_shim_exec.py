import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('shim_exec', str(Path(__file__).resolve().parents[1] / 'shim_exec.py'))
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)


class ShimTransportTests(unittest.TestCase):
    def test_large_script_uses_binary_push_and_small_arguments(self):
        source = b'echo literal\n' * 50000
        sent = []; calls = []
        def transport(argv, **options):
            calls.append(argv)
            if argv[1] == 'push': sent.append(Path(argv[2]).read_bytes())
        with patch.object(bridge.subprocess, 'run', side_effect=transport):
            bridge.run_script(['supported-shim'], source)
        self.assertEqual(sent, [source])
        self.assertTrue(all(sum(len(arg) for arg in call) < 2048 for call in calls))
        self.assertEqual([call[1] for call in calls], ['exec', 'push', 'exec'])

    def test_auth_failure_does_not_push_or_execute_payload(self):
        with patch.object(bridge.subprocess, 'run', side_effect=subprocess.CalledProcessError(3, 'auth')) as call:
            with self.assertRaises(subprocess.CalledProcessError): bridge.run_script(['supported-shim'], b'echo payload')
        self.assertEqual(call.call_count, 1)
