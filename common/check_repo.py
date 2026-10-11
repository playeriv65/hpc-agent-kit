"""Verify the public checkout's inventory, version, contracts and remote syntax."""
import ast
import argparse
import hashlib
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT, refresh=False):
    if (root / 'AGENTS.md').read_bytes() != (root / 'CLAUDE.md').read_bytes():
        raise ValueError('AGENTS.md and CLAUDE.md differ; update both copies')
    metadata = tomllib.loads((root / 'pyproject.toml').read_text())
    manifest = json.loads((root / 'export-manifest.json').read_text())
    if not refresh and manifest['core_version'] != metadata['project']['version']:
        raise ValueError('Release versions differ')
    for name, expected in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('Invalid inventory path')
        if not refresh and hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Release inventory changed: ' + name)
    # Templates and remote installer must remain compatible with compute runtimes.
    for name in ('allocation.py', 'agent-state.py', 'health.py', 'install-release.py'):
        ast.parse((root / 'common' / name).read_text(), feature_version=(3, 6))
    skill = root / 'skills/hpc-workflow/SKILL.md'
    for reference in ('site-probing.md', 'acceptance.md', 'recovery.md'):
        if not (skill.parent / 'references' / reference).is_file(): raise ValueError('Missing skill reference')
    if refresh:
        manifest['core_version'] = metadata['project']['version']
        manifest['files'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in manifest['files']}
        (root / 'export-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    return dict(version=manifest['core_version'], verified_files=len(manifest['files']), contracts='identical')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Refresh existing allowlisted hashes after intentional edits')
    args = parser.parse_args()
    print(json.dumps(check(refresh=args.refresh), sort_keys=True))
