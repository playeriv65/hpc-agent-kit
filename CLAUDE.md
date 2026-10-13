# Agent operating contract

`AGENTS.md` and `CLAUDE.md` are byte-identical copies. Update both together and
run `uv run python common/check_repo.py`; Git and ZIP distributions preserve
copies, so correctness never depends on a local hardlink.
For intentional edits, refresh existing allowlisted hashes with
`uv run python common/check_repo.py --refresh` and run the checks again.

## Deliver the user's working site

Read `skills/hpc-workflow/SKILL.md` to establish, adapt or recover an installation.
The output is an authenticated connection, reviewed private site profile,
installed helpers and a verified operating contract. Use the user's existing
route and authorization. Do not install an agent on the HPC or invent an
authentication mechanism. A natural-language setup request is the entry point.

Keep profiles, credentials, probe output and installed-site notes under `private/`
or outside the checkout. Document observed login identity, authentication owner,
adapter, accounts/partitions/QoS, local and persistent paths, runtime requirements,
defaults, resource limits, installed version/hashes and recovery commands.
Separate verified facts from pending checks and requested resources from actual
grants. Validate compute capabilities on compute, not only on login nodes.

## Operating invariants

- Keep allocation ownership in login tmux and tasks in job-specific compute tmux.
  After bootstrap, route work through `SITE-gpu run`; do not create extra steps.
- Persist an allocation request ID before submitting. Replay that ID with the
  same parameters after a disconnect. An unknown scheduler result or ambiguous
  reservation must not produce another allocation or deletion of its record.
- Use stable task IDs and named windows. Inspect durable logs and completion
  records; a historical READY or running marker is not a current health check.
- Use the allocated route and repository launcher. Shared home/hostnames do not
  prove socket access; backup readiness does not prove application readiness.
  If a job leaves the queue, establish its terminal outcome with accounting.
- Use disk-backed, job-isolated local storage for environments and caches, and
  persistent storage for snapshots, results and complete checkpoints. Restore
  verifies before extraction; `.venv` is recreated with `uv sync --locked`.
- Check `SITE-agent-state doctor --verify-backup` when adopting a restored
  workspace. Distinguish `passed`, `failed`, `unknown` and `not_checked` checks.
  Workspace recovery does not resume an experiment unless it has a checkpoint.
- Use frozen bundles and the supported deployment helper for upgrades. Refuse
  live jobs, unresolved reservations and active archive writers. Retain the
  exact prior installation; inspect `deployment.json` after interruptions.

Read `skills/hpc-workflow/references/recovery.md` for failure-specific actions.
Read `skills/hpc-workflow/references/operations.md` for desktop transports,
compute environments, storage budgets and scoped job observation.
Preserve research jobs, uncertain records, archives and failure evidence. Do not
cancel unrelated work, overwrite checkpoints, retry MFA automatically or replace
a healthy transport. A user's setup/repair request authorizes ordinary scoped
installation; it does not authorize publication or unrelated cleanup.

## Maintaining this repository

Keep `README.md` and `README.zh-CN.md` aligned when changing user-facing behavior
or supported scope. Describe MSI and PSC as verified reference sites; adapt
other Slurm sites through observed profiles and tested adapters. The current
allocation backends use `salloc` plus bootstrap `srun`; do not imply that a
batch-only `sbatch` route is already implemented or that every site is verified.

Use uv and Python 3.12+ locally; installed Linux helpers support Python 3.6+.
Keep remote code compatible with that runtime. Shared templates under `common/`
are authoritative; site adapters describe actual differences. Do not patch an
installed or generated helper without fixing its maintained source.

Run `common/check_repo.py` and the Linux suite under `common/tests` before shipping.
The installable skill includes generated helpers and site templates. After
changing maintained sources, run `common/build_skill.py` and then
`common/build_skill.py --check`; do not hand-edit packaged script copies.
Standalone skill operation needs no external checkout. Keep its private outputs
outside the installed directory and use its packaged integrity verifier.
The suite renders actual helpers and uses real tmux, synthetic storage and stub
schedulers; it consumes no GPUs. Follow the skill's acceptance procedure for
authorized live checks. Report fixture tests, actual installation checks and real
GPU acceptance separately. Keep pending validation visible in the handoff.
