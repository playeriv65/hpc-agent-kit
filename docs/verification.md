# Verification record

MSI and PSC Bridges-2 are reference deployments of the shared workspace design.
A generated installation still needs its own
[site acceptance](../skills/hpc-workflow/references/acceptance.md).

## Source-site GPU acceptance

| Site | Evidence | Version boundary |
| --- | --- | --- |
| PSC Bridges-2 | Two-allocation V100 lifecycle on 2026-10-06: request replay, live backup health/integrity, automatic recovery on a different node, current-writer lease exclusion and pre-deadline backup. | v0.3.1 |
| MSI | Source-site two-allocation test. | v0.3.0 |

Version 0.3.1 preserves the allocation's controlling terminal while logging,
including through the PSC bridge, and isolates a PSC scratch root of `/local`
inside a job-specific subdirectory. These corrections have Linux fixture
coverage and the PSC live acceptance above. Tiny archives can trigger the
configured incremental-size limit; successful full compaction is a valid
deadline backup fallback.

## Public version 0.4.0

Version 0.4.0 adds deployment journaling, automatic failure rollback and explicit
rollback, check coverage reporting, expected-compaction classification, identical
agent contracts and public generated-workspace regression tests. The historical
GPU acceptance above is not a new v0.4.0 GPU certification.

The Linux suite uses synthetic data and stub schedulers with real tmux. Windows
checks verify the public inventory, contracts and syntax compatibility. These
tests do not consume GPUs and do not certify site permissions, actual compute
access or remote storage. Current CI results are available in
[GitHub Actions](https://github.com/playeriv65/hpc-agent-kit/actions/workflows/validate.yml).

```sh
uv run python -m unittest discover -s common/tests -v
uv run python common/check_repo.py
```

Keep fixture tests, installed-site checks and real GPU acceptance distinct in
reports. Abrupt node loss can recover only already committed backups. Keep
checkpoints and results on persistent storage through explicit experiment options.
