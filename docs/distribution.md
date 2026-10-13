# Skill distribution

The canonical source is [playeriv65/hpc-agent-kit](https://github.com/playeriv65/hpc-agent-kit).
The complete installable skill is in `skills/hpc-workflow/`. It includes runtime
helpers under `scripts/`, site examples under `assets/`, the operating contract,
reference documents and a hash manifest. No separate repository checkout is
needed after skill installation.

Public listings: [skills.sh](https://skills.sh/playeriv65/hpc-agent-kit/hpc-workflow)
and [ClawHub](https://clawhub.ai/playeriv65/skills/hpc-workflow).

## Install from GitHub

```sh
npx skills add playeriv65/hpc-agent-kit --skill hpc-workflow
```

Select the agent and installation scope using the CLI prompts. The installed
skill uses its bundled helpers and runs
`uv run --no-project --python 3.12 python scripts/verify_bundle.py` from its own
directory. Installing the package does not authenticate to a cluster, deploy
remote helpers or request GPUs; the agent performs setup within the user's scope.

To inspect available skills without installing them:

```sh
npx skills add playeriv65/hpc-agent-kit --list
```

Use the current CLI's documented Node.js requirement. The same skill folder
works with agents that support the `SKILL.md` format; agent-specific installation
locations are managed by their installer.

## Registries and catalogs

| Service | Distribution mechanism |
| --- | --- |
| [skills.sh](https://skills.sh/docs/faq) | Discoverable through the Skills CLI. Its leaderboard is populated from real installations with anonymous telemetry enabled; there is no separate upload form. |
| [ClawHub](https://github.com/openclaw/clawhub/blob/main/docs/cli.md) | Publish the skill directory with the official `clawhub skill publish` CLI. A new release may wait for automated review before installation becomes available. |
| [SkillsMP](https://skillsmp.com/docs/faq) | Indexes public GitHub skills. A public, compatible skill is eligible for discovery; this does not guarantee a listing or a crawl date. |
| [SkillHub](https://github.com/skillhub-club/cli) | The official `@skill-hub/cli` provides `skillhub push`, `skillhub publish` and `skillhub status`. Verify the resulting listing rather than treating upload success as public visibility. |

Catalog inclusion and an automated scan are not independent validation of an
HPC installation. MSI and PSC are the verified reference sites; other Slurm
sites still need probing and acceptance checks. The current allocation route
uses `salloc` and a bootstrap `srun`; an `sbatch`-only adapter is not implemented.

## Publish a matching revision

Maintain one source folder for all catalogs. Run `uv run python common/build_skill.py`
after changing maintained helpers, templates or contracts, then run it with
`--check`. Check the skill's local references, run
`uv run python common/check_repo.py` in the full checkout, and perform a
fresh installation into an isolated directory before publishing. Keep private
profiles, credentials, probes and research data out of all upload packages.

Use the official CLIs for routine releases. Install them with npm in a maintained
Node.js environment, then verify their versions and authentication:

```sh
npm install -g clawhub @skill-hub/cli skills
clawhub whoami
skillhub whoami
skills --version
```

Initial authorization may require a browser. Reuse saved credentials for subsequent
uploads; report authentication errors instead of silently switching to web uploads.

For ClawHub, publish `skills/hpc-workflow` from a reviewed public commit:

```sh
clawhub skill publish skills/hpc-workflow \
  --slug hpc-workflow --name "HPC Agent Kit" --version VERSION \
  --categories 'operations,development,automation' \
  --topics 'slurm,hpc,gpu,tmux,backup' \
  --source-repo playeriv65/hpc-agent-kit --source-commit COMMIT \
  --source-path skills/hpc-workflow --dry-run
```

Replace `VERSION` and `COMMIT` with the reviewed release values, then remove
`--dry-run` for an authorized publication. Use the platform's normal login flow;
never commit a registry token or place one in the skill. Record the published
version, source commit, URL and review status in release notes. Upload success,
public visibility and successful installation are separate checks.

For SkillHub, prepare a separate copy of the skill directory and link an existing
listing with `skillhub init --link SKILL_ID`. Keep its local `.skillhub.json` and
authentication data out of the canonical skill and other registry packages. Use
`skillhub status`, then `skillhub push --message "Describe the changes"` and
`skillhub publish --yes` for an authorized release.

The verified SkillHub CLI `0.1.9` requires browser OAuth for its first login. Its
file scanner excludes extensionless files, so the complete package uses
`LICENSE.txt` and names its PSC shell template `psc-gpu.sh`. Check
the full scanned inventory before uploading. Linking, staging and local validation
do not establish authentication, a successful upload or public availability.

## Licensing

Repository runtime source code remains [MIT](../LICENSE). All content inside the
packaged `skills/hpc-workflow/` directory, including scripts and templates, is
also offered under MIT-0, as stated in
[the skill license](../skills/hpc-workflow/LICENSE.txt), for ClawHub distribution.
This additional grant does not change the license of files outside that folder.
