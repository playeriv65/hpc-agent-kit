---
name: New Slurm site
about: Help adapt HPC Agent Kit to another Slurm cluster.
title: "[Site] "
---

## Cluster and intended workflow

Name the cluster if it is public, or describe it anonymously. What would you
like your agent to set up? Include the kit version or commit.

## Connection and allocation

Does the route use SSH keys, interactive MFA or a retained SSH connection?
Does the site permit `salloc` + `srun --pty`, or only `sbatch`?
Can the login node SSH into an allocated compute node, or is a terminal bridge
required? Link public site documentation when available.

## Storage and runtime

Describe job-local disk and persistent storage using placeholders for private
paths. Which required tools are available on login and compute nodes?

## What is already verified

Distinguish observations from assumptions and pending checks. Include redacted
errors where relevant. Do not include credentials, private profiles, raw task
logs or research data.
