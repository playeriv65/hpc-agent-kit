import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_profiles import profile, renderer

ROOT = Path(__file__).resolve().parents[2]


class StandaloneSkillTests(unittest.TestCase):
    def test_copied_skill_renders_both_adapters_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            skill = work / 'isolated-skill'
            shutil.copytree(ROOT / 'skills/hpc-workflow', skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            verifier = [sys.executable, str(skill / 'scripts/verify_bundle.py')]
            subprocess.run(verifier, cwd=work, check=True, capture_output=True)
            for adapter in ('direct-ssh', 'psc-bridge'):
                data = profile(); data['adapter'] = adapter
                data['allocation']['default_gpu'] = 'v100'
                data['allocation']['gpus'] = {'v100': dict(gres='v100', partition='accelerator', qos='gpu', max_count=1)}
                config = work / (adapter + '.json')
                config.write_text(json.dumps(data))
                bundle = work / adapter
                subprocess.run([sys.executable, str(skill / 'scripts/common/profile.py'), str(config), '--output', str(bundle)], cwd=work, check=True, capture_output=True)
                expected = renderer.render(data)
                for name, body in expected.items():
                    self.assertEqual((bundle / name).read_text(), body)
                preview = subprocess.run([sys.executable, str(skill / 'scripts/common/deploy_bundle.py'), str(bundle)], cwd=work, check=True, capture_output=True, text=True)
                self.assertFalse(json.loads(preview.stdout)['applied'])
            template = skill / 'scripts/common/snapshot-generation.sh'
            template.write_text(template.read_text() + '\n# changed\n')
            result = subprocess.run(verifier, cwd=work, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Packaged file changed', result.stderr)
