# Agent tooling and operations

Start with the repository's [README](../README.md) and
[hpc-workflow skill](../skills/hpc-workflow/SKILL.md). The agent stays on the
user's machine, establishes site facts and installs remote helpers through the
existing authenticated route. An independently installed skill carries its own
scripts, templates and references, and needs no separate checkout. Use its
`scripts/common/` paths and `scripts/verify_bundle.py` integrity check. The commands
below use the full repository layout. Keep private profiles and installed-site
notes outside public files.

## Components

| Component | Purpose |
| --- | --- |
| `common/profile.py` | Validate supplied site facts and render a standalone bundle. |
| `common/allocation.py` | Persist request identity before tmux dispatch; recover by Slurm owner/comment. |
| `common/task-runner.sh` | Deduplicate named experiment commands and retain output/exit status. |
| `common/local-snapshot.sh` | Restore, verify, autosave, compact and retain immutable generations. |
| `common/agent-state.py`, `common/health.py` | Separate queue observations, current process health and archive integrity checks. |
| `common/deploy_bundle.py` | Freeze bytes, check quiescent state, preserve prior files, install and roll back failures. |

The `direct-ssh` adapter uses login-to-compute SSH. The `psc-bridge` adapter uses
PSC's allocation-terminal/tmux bridge. Establish site policy, storage and
permitted QoS before marking facts verified. Unfilled example accounts/routes
are rejected by the renderer. Framework/CUDA setup belongs to explicit
repository or site launchers. See [site probing](../skills/hpc-workflow/references/site-probing.md).

## Render and deploy

Run from the checkout root. `private/site.json` contains the observed and
reviewed site configuration, based on the [MSI](../examples/msi.example.json) or
[PSC](../examples/psc.example.json) template. Profile validation checks supplied
facts; it does not independently prove them.

```sh
uv run python common/profile.py private/site.json --output outputs/site-bundle
uv run python common/deploy_bundle.py outputs/site-bundle
# When deployment is within the requested setup scope:
uv run python common/deploy_bundle.py outputs/site-bundle --apply
# Review or apply rollback to the exact backup reported by deployment:
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP --apply
```

Deployments refuse live jobs, unresolved allocation reservations and active
archive writers. Preserve the exact reported backup. After interruption, inspect
the deployment journal and replay the frozen release; do not clear uncertain
records by hand. Follow [recovery](../skills/hpc-workflow/references/recovery.md).

## Request and run

Replace `SITE` with the profile name and the other placeholders with verified
site values. Inspect existing jobs first. Keep the same allocation/task request
ID and parameters when recovering from a transport failure.

```sh
SITE-gpu --request ID TYPE COUNT WALLTIME
SITE-gpu allocation ID
SITE-gpu run --job JOB --request TASK --wait WINDOW -- COMMAND ...
SITE-agent-state status
SITE-agent-state doctor
SITE-agent-state doctor --verify-backup
```

Login tmux holds the allocation. Each granted job owns compute tmux with
`setup`, `autosave` and named task windows. After bootstrap, route ordinary work
through `SITE-gpu run`, without additional `srun` steps. Use the repository's
launcher to configure its framework and recreate `.venv` with `uv sync --locked`.

## Backups and health

Bootstrap restores and verifies committed snapshots, starts autosave and waits
for a successful save from the current writer. Defaults are five-minute
increments, six-hour full compaction and a save before Slurm's end time.
Immutable generations commit through an atomic pointer; corrupt latest
generations fall back to verified predecessors. Snapshots exclude `.venv` and
large model caches.

`doctor` checks current compute access, autosave process identity, save freshness,
current-writer identity and failure counts. `--verify-backup` also reads the full
archive chain. Doctor records an isolated health task without altering experiments.
Checks distinguish `passed`, `failed`, `unknown` and `not_checked`. Without a
compute route, storage/autosave health is unverified. Historical READY is not a
current health guarantee.

An SSH disconnect does not prove that the allocation ended. Persistent SSH does
not extend walltime. Abrupt node loss can recover only already committed data;
store results and complete experiment checkpoints explicitly on persistent
storage. Workspace recovery does not imply experiment resume.

## Runtime requirements

Local profile/deployment tooling uses uv and Python 3.12+ on Windows or Linux.
Remote helpers use Bash 4+, Python 3.6+, tmux, GNU tar with listed incrementals,
zstd, flock, GNU coreutils/date, awk, find, sort and pgrep. Probe both login and
compute nodes. The current allocation backends require Slurm `salloc` plus a
bootstrap `srun --pty`, and permitted compute access through an implemented
adapter. Batch-only `sbatch` sites require a new allocation backend.

## Boundaries of the public bundle

The public bundle provides remote helpers and the agent adaptation workflow.
The reference site's complete Windows `msi`/`psc` wrappers, PSC browsable mirror,
experiment recipe controller and MSI CUDA toolkit materializer are not exported.
Generated profiles do not configure every model/JIT cache or supply a universal
quota provider. Repository/site launchers still own that configuration.
See [operating lessons](../skills/hpc-workflow/references/operations.md) for the
practical decisions behind these boundaries and the required compute probes.

## Maintaining the repository

Maintain both README languages together and keep `AGENTS.md` / `CLAUDE.md`
byte-identical. Shared templates under `common/` are authoritative. For
intentional edits, refresh existing allowlisted hashes before validation:

```sh
uv run python common/build_skill.py
uv run python common/build_skill.py --check
uv run python common/check_repo.py --refresh
uv run python common/check_repo.py
uv run python -m unittest discover -s common/tests -v
```

Add new maintained files to the inventory explicitly; refresh never sweeps
private directories. Keep text files in LF format as specified by
`.gitattributes`, so Git and ZIP retain the same hashes on Windows and Linux.
The export manifest is an integrity inventory, not a cryptographic signature.
Keep credentials, private profiles, research data and deployment logs out of the
public checkout. Report only redacted version/adapter/status/error details.
