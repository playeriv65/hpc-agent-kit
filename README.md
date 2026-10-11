# HPC Agent Kit

[English](README.md) | [简体中文](README.zh-CN.md)

**Persistent Slurm GPU workspaces for coding agents.**

Give your agent this repository and access to your HPC. It adapts the connection
and site configuration, sets up nested tmux, and restores verified workspace
backups when a new allocation starts.

[Quick start](#quick-start) · [Workflow](#how-it-works) · [Compatibility](#compatibility) · [Guides](#guides)

## Why use it?

- Keep remote work running through desktop disconnects while the allocation is alive.
- Put code, environments and caches on fast, job-local disk instead of filling home.
- Restore verified snapshots at bootstrap and autosave during the allocation.
- Reuse allocation and task IDs after disconnects, avoiding duplicate work.
- Reuse a healthy authenticated SSH connection on routes that require interactive login.

The agent stays on your own machine. The HPC runs the remote helpers and your
workloads. `AGENTS.md` and `CLAUDE.md` provide the same operating contract.

## Quick start

Clone the repository, or have your agent obtain it:

```sh
git clone https://github.com/playeriv65/hpc-agent-kit.git
```

Give your coding agent the checkout path and this prompt. Replace the cluster
placeholder with your cluster name or existing SSH alias:

```text
Use https://github.com/playeriv65/hpc-agent-kit to set up my Slurm HPC workflow.
My cluster is [cluster name or SSH alias].
Read AGENTS.md and skills/hpc-workflow/SKILL.md, then inspect my existing
connection, jobs, resource permissions and storage. Adapt a private site profile,
deploy the helpers and verify the workspace, automatic backups and recovery.
Preserve my existing jobs and data. Ask me for missing access details or
interactive authentication when needed.
```

The agent probes the site, renders a deployment bundle and verifies the installed
workflow. See the [skill](skills/hpc-workflow/SKILL.md) for the complete procedure.
A separately installed skill needs the matching full repository checkout.

After setup, commands have this form. `SITE`, GPU type, job ID and command are
values from your installation and project:

```sh
SITE-gpu --request allocation-001 GPU_TYPE 1 01:00:00
SITE-gpu allocation allocation-001
SITE-gpu run --job JOB_ID --request task-001 --wait train -- COMMAND ...
SITE-agent-state doctor --verify-backup
```

## How it works

```mermaid
flowchart LR
    A["Local coding agent"] --> L["Login tmux<br/>control + allocation"]
    L --> C["Compute tmux<br/>setup + autosave + tasks"]
    C --> W["Job-local workspace<br/>code + caches"]
    W -->|"verified snapshots"| P["Persistent archive"]
    P -->|"restore on next allocation"| W
```

The login session holds the Slurm allocation; each job has its own compute tmux
and named task windows. Bootstrap restores verified snapshots and starts
periodic saves. Defaults are five-minute increments, six-hour full compaction
and a save before the job deadline. Corrupt latest generations fall back to
verified predecessors. Environments are recreated with `uv sync --locked`.

For interactive authentication, an always-on jump host can retain a healthy SSH
master across desktop shutdown. Read the
[persistent SSH guide](docs/msi-persistent-ssh.md) for login ownership,
connection reuse, channel saturation and recovery.

Sessions remain subject to Slurm walltime. Abrupt node loss can recover only
committed backups. Put results and complete experiment checkpoints on persistent
storage; restoring a workspace does not itself resume an experiment.

## Compatibility

The shared core targets Slurm on Linux. **MSI and PSC Bridges-2 are verified
reference sites**, with version-specific evidence in the
[verification record](docs/verification.md). Agents adapt other Slurm sites
through private profiles and site acceptance.

| Route | Support |
| --- | --- |
| Login-to-allocated-node SSH | `direct-ssh` adapter; verify site permission and compute access. |
| PSC allocation-terminal/tmux bridge | `psc-bridge` adapter; another site's terminal behavior needs adaptation. |
| Account, partitions, QoS, GPU GRES and storage paths | Configure from observed site facts; [MSI](examples/msi.example.json) and [PSC](examples/psc.example.json) are templates. |
| Batch-only Slurm sites | Need an allocation backend; current backends use `salloc` + bootstrap `srun --pty`. |
| Other schedulers | Need a scheduler adapter. |

[`sbatch`](https://slurm.schedmd.com/sbatch.html),
[`salloc`](https://slurm.schedmd.com/salloc.html) and
[`srun`](https://slurm.schedmd.com/srun.html) are Slurm commands; their availability
and permitted use depend on the site. Local tooling uses uv and Python 3.12+ on
Windows or Linux. Remote helpers require Bash 4+, Python 3.6+, tmux, GNU tar,
zstd, flock and GNU tools on login and compute nodes. See
[runtime and operations](docs/usage.md) for details.

## Guides

- [Agent skill](skills/hpc-workflow/SKILL.md): establish, operate and recover a site.
- [Site probing](skills/hpc-workflow/references/site-probing.md): connection, resources, storage and toolchain evidence.
- [Persistent SSH](docs/msi-persistent-ssh.md): MSI and adaptable jump-host routes.
- [Operations](docs/usage.md): components, deployment, commands, health and rollback.
- [Operating lessons](skills/hpc-workflow/references/operations.md): tmux namespaces, desktop arguments, CUDA/worker environments and job completion.
- [Acceptance](skills/hpc-workflow/references/acceptance.md) and [verification record](docs/verification.md): what has been checked and how to verify a new site.
- [Recovery](skills/hpc-workflow/references/recovery.md): failure-specific actions.
- [Related tools](docs/related-tools.md): other HPC clients, interfaces and skill collections.

## Try it on your cluster

Adapting another Slurm site? [Open an issue](https://github.com/playeriv65/hpc-agent-kit/issues/new/choose)
with the connection method, allocation policy and storage layout. Site reports,
adapter fixes and clearer operating notes are useful contributions. Keep private
profiles, credentials, research data and raw logs out of public reports.

Maintain both README languages and identical agent contracts. See the
[maintenance notes](docs/usage.md#maintaining-the-repository) for inventory checks
and the existing offline test suite. Code and release assets are available in
[this repository](https://github.com/playeriv65/hpc-agent-kit) and
[releases](https://github.com/playeriv65/hpc-agent-kit/releases).
Licensed under [MIT](LICENSE).
