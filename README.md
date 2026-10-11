# HPC Agent Kit

An agent-operated Slurm GPU workspace: nested tmux, durable allocation identities,
job-local workspaces and verified automatic backup/restore.

Repository: [playeriv65/hpc-agent-kit](https://github.com/playeriv65/hpc-agent-kit).
Get a matching release or clone the repository, then give its path to your agent:

```sh
git clone https://github.com/playeriv65/hpc-agent-kit.git
```

Give your agent this checkout and a request such as:
“I have access to an HPC cluster. Use this repository to establish and verify its
connection and GPU workspace workflow.” `AGENTS.md` and `CLAUDE.md` contain the
same operating contract. The agent reads
[`skills/hpc-workflow/SKILL.md`](skills/hpc-workflow/SKILL.md), probes the actual
site, writes a private profile, renders a bundle and deploys through the existing
authenticated route. There is no human initialization wizard.

For MSI or other routes with interactive authentication, read the
[persistent SSH and jump-host guide](docs/msi-persistent-ssh.md). It explains how
an always-on host retains the authenticated connection, how ordinary calls reuse
it without starting new MFA, and how to recover after a real disconnect.

| Component | Purpose |
| --- | --- |
| `common/profile.py` | Validate supplied site facts and render a standalone bundle. |
| `common/allocation.py` | Persist request identity before tmux dispatch; recover by Slurm owner/comment. |
| `common/task-runner.sh` | Deduplicate named experiment commands and retain output/exit status. |
| `common/local-snapshot.sh` | Restore, verify, autosave, compact and retain immutable generations. |
| `common/agent-state.py`, `common/health.py` | Separate queue observations, current process health and archive integrity checks. |
| `common/deploy_bundle.py` | Freeze bytes, check quiescent state, preserve prior files, install and roll back failures. |

The `direct-ssh` adapter uses login-to-compute SSH. The `psc-bridge` adapter uses
PSC's existing allocation-terminal/tmux bridge. Both are templates: the agent must
establish site policy, storage and permitted QoS before marking facts verified.
Unfilled example accounts/routes are intentionally rejected by the renderer.
Framework/CUDA setup belongs to explicit repository or site launchers.

For agent tooling:

```sh
uv run python common/profile.py private/site.json --output outputs/site-bundle
uv run python common/deploy_bundle.py outputs/site-bundle
# When deployment is within the requested setup scope:
uv run python common/deploy_bundle.py outputs/site-bundle --apply
# Review or apply rollback to the exact backup reported by deployment:
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP --apply
```

Once deployed, `SITE-gpu --request ID TYPE COUNT WALLTIME` requests/reuses an
allocation. `SITE-gpu allocation ID` recovers its state. Use
`SITE-gpu run --job JOB --request TASK --wait WINDOW -- COMMAND ...` for work.
`SITE-agent-state doctor` checks the current writer and save freshness;
`doctor --verify-backup` also verifies archive content. Doctor records an isolated
health task; it does not alter experiments.
Every check reports passed, failed, unknown or not_checked. With no compute route,
doctor cannot establish storage/autosave health. Follow the skill's
[recovery guide](skills/hpc-workflow/references/recovery.md) after failures.

Supported scope: Slurm on Linux with direct allocated-node SSH or the PSC terminal
bridge. Local profile/deployment tooling uses uv and Python 3.12+ on Windows or
Linux; remote helpers use Bash 4+, Python 3.6+, tmux, GNU tar, zstd, flock and GNU
coreutils. Probe these on both login and compute nodes. Other schedulers and
compute-access mechanisms require an adapter. CUDA/GCC belong to site/repository
launchers. Installing only the skill requires obtaining its matching full checkout.

Tests use synthetic data and stub schedulers with real tmux on Linux:

```sh
uv run python -m unittest discover -s common/tests -v
uv run python common/check_repo.py
```

These tests do not consume GPUs. Real GPU acceptance is a separate opt-in procedure
in the skill. The shared core was developed against MSI and PSC; source-site
verification does not certify a newly generated installation. Abrupt node loss
can recover only already committed backups. Keep checkpoints and results on
persistent storage through explicit experiment options.

Release v0.3.1 preserves the allocation's controlling terminal while logging,
including through the PSC bridge, and isolates a PSC scratch root of `/local`
inside a job-specific subdirectory. These corrections have Linux fixture
coverage and passed a real PSC two-allocation V100 lifecycle on 2026-10-06:
request replay, live backup health/integrity, automatic recovery on a different
node, current-writer lease exclusion and pre-deadline backup. Tiny archives
can trigger the configured incremental-size limit; successful full compaction
is a valid deadline backup fallback. The source MSI two-allocation test used
v0.3.0. Perform the skill's live acceptance before adopting a new site.

Private profiles, authentication material, research data and deployment logs do
not belong in a public checkout. The export manifest binds this release's files;
it is an integrity inventory, not a cryptographic signature. Licensed under MIT.

Version 0.4.0 adds deployment journaling, automatic failure rollback and explicit
rollback, check coverage reporting, expected-compaction classification, identical
agent contracts and public generated-workspace regression tests. CI checks the
public checkout on Linux and its inventory/contracts on Windows. Historical GPU
acceptance above remains evidence for v0.3.1, not a new v0.4.0 GPU certification.

When editing a development checkout, update both contract copies and refresh
`export-manifest.json` hashes with `uv run python common/check_repo.py --refresh`
for intentional source changes before validation. Add new maintained files to
that inventory explicitly; the refresh command never sweeps private directories.
Keep text files in LF format as specified by `.gitattributes`, so Git checkouts
and release archives retain the same inventory hashes on Windows and Linux.
Report problems with a redacted version/adapter/status/error record using the
issue template. Do not post raw authentication material or full private task logs.
