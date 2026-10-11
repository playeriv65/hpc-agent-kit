# Skill distribution

The canonical source is [playeriv65/hpc-agent-kit](https://github.com/playeriv65/hpc-agent-kit).
The installable agent instructions are in `skills/hpc-workflow/`. Runtime helpers,
site examples and the operating contract live in the full repository.

## Install from GitHub

```sh
npx skills add playeriv65/hpc-agent-kit --skill hpc-workflow
```

Select the agent and installation scope using the CLI prompts. The installed
skill tells the agent to find or obtain the matching helper checkout, compare
the instructions and run `common/check_repo.py` before setting up a site. An
installation of the instructions alone does not install remote HPC helpers,
authenticate to a cluster or request GPUs.

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
| [ClawHub](https://github.com/openclaw/clawhub/blob/main/docs/publishing.md) | Import the public GitHub skill directory or publish that directory with the official CLI. A new release may wait for automated review before installation becomes available. |
| [SkillsMP](https://skillsmp.com/docs/faq) | Indexes public GitHub skills. A public, compatible skill is eligible for discovery; this does not guarantee a listing or a crawl date. |
| [SkillHub](https://www.skillhub.club) | Offers a signed-in publishing interface and GitHub discovery. Verify the resulting listing rather than treating a public repository as proof of inclusion. |

Catalog inclusion and an automated scan are not independent validation of an
HPC installation. MSI and PSC are the verified reference sites; other Slurm
sites still need probing and acceptance checks. The current allocation route
uses `salloc` and a bootstrap `srun`; an `sbatch`-only adapter is not implemented.

## Publish a matching revision

Maintain one source folder for all catalogs. Check the skill's local references,
run `uv run python common/check_repo.py` in the full checkout, and perform a
fresh installation into an isolated directory before publishing. Keep private
profiles, credentials, probes and research data out of all upload packages.

For ClawHub, import `skills/hpc-workflow` from a reviewed public commit. The CLI
alternative is:

```sh
clawhub skill publish skills/hpc-workflow \
  --slug hpc-workflow --name "HPC Agent Kit" --version VERSION \
  --categories operations,development,automation \
  --topics slurm,hpc,gpu,tmux,backup \
  --source-repo playeriv65/hpc-agent-kit --source-commit COMMIT \
  --source-path skills/hpc-workflow --dry-run
```

Replace `VERSION` and `COMMIT` with the reviewed release values, then remove
`--dry-run` for an authorized publication. Use the platform's normal login flow;
never commit a registry token or place one in the skill. Record the published
version, source commit, URL and review status in release notes. Upload success,
public visibility and successful installation are separate checks.

## Licensing

Repository runtime code remains [MIT](../LICENSE). The instructions and reference
documents inside `skills/hpc-workflow/` are also offered under MIT-0, as stated
in [the skill license](../skills/hpc-workflow/LICENSE), for ClawHub distribution.
This additional grant does not change the license of files outside that folder.
