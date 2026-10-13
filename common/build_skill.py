"""Bundle maintained helpers into the independently installable skill."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'kit' if (ROOT / 'kit').is_dir() else ROOT
SKILL = ROOT / 'skills/hpc-workflow'
VERSION = '0.4.1'
RUNTIME = ('profile.py', 'deploy_bundle.py', 'install-release.py', 'shim_exec.py',
    'allocation.py', 'agent-state.py', 'health.py', 'task-runner.sh',
    'local-snapshot.sh', 'snapshot-generation.sh', 'boot-guard.sh',
    'slurm-gpu.sh', 'slurm-boot.sh', 'sites/psc-boot.sh', 'tests/benchmark_snapshot.py')


def generated():
    files = {}
    for name in RUNTIME:
        text = (ROOT / 'common' / name).read_text(encoding='utf-8')
        if name == 'profile.py':
            old = "'psc-remote-install/psc-gpu'"
            if old not in text:
                raise ValueError('Review the packaged PSC template path')
            text = text.replace(old, "'psc-remote-install/psc-gpu.sh'")
        files['scripts/common/' + name] = text
    files['scripts/psc-remote-install/psc-gpu.sh'] = (ROOT / 'psc-remote-install/psc-gpu').read_text(encoding='utf-8')
    for path in (DOCS / 'examples').glob('*.json'):
        files['assets/' + path.name] = path.read_text(encoding='utf-8')
    files['scripts/verify_bundle.py'] = (ROOT / 'common/verify_skill.py').read_text(encoding='utf-8')
    guide = (DOCS / 'docs/msi-persistent-ssh.md').read_text(encoding='utf-8')
    guide = guide.replace('(../common/', '(../scripts/common/').replace('(../skills/hpc-workflow/references/', '(')
    files['references/msi-persistent-ssh.md'] = guide
    contract = (DOCS / 'AGENTS.md').read_text(encoding='utf-8')
    contract = contract[contract.index('## Deliver the user'):contract.index('## Maintaining this repository')]
    contract = contract.replace('skills/hpc-workflow/references/', '').replace('skills/hpc-workflow/SKILL.md', '../SKILL.md')
    contract = contract.replace('outside the checkout', 'outside the installed skill directory')
    files['references/agent-contract.md'] = '# Packaged operating contract\n\n' + contract
    return {name: text.encode('utf-8') for name, text in files.items()}


def check_or_build(check=False):
    files = generated()
    entry = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    if not re.search(r'^  version: [\"\']?' + re.escape(VERSION) + r'[\"\']?$', entry, re.M):
        raise ValueError('Skill metadata and package version differ')
    for name, data in files.items():
        path = SKILL / name
        if check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError('Packaged source differs: ' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    members = {}
    for path in SKILL.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        name = path.relative_to(SKILL).as_posix()
        if name == 'bundle-manifest.json':
            continue
        data = path.read_bytes()
        data.decode('utf-8')
        if re.search(rb'BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY', data):
            raise ValueError('Private key in skill: ' + name)
        members[name] = hashlib.sha256(data).hexdigest()
    core = tomllib.loads((DOCS / 'pyproject.toml').read_text())['project']['version']
    manifest = dict(skill_version=VERSION, core_version=core, files=dict(sorted(members.items())))
    content = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    target = SKILL / 'bundle-manifest.json'
    if check:
        if target.read_bytes() != content:
            raise ValueError('Skill inventory changed; rebuild the package')
    else:
        target.write_bytes(content)
    return dict(skill_version=VERSION, core_version=core, files=len(members) + 1, mode='check' if check else 'build')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    print(json.dumps(check_or_build(parser.parse_args().check)))
