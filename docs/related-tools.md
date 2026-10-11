# Related tools

These projects cover adjacent HPC workflows. Their READMEs were useful examples
of how to explain a tool's purpose, show a workflow and help readers get started.
They are independent projects, not dependencies or compatibility certifications.

| Project | Focus | Presentation worth borrowing |
| --- | --- | --- |
| [MilaTools](https://github.com/mila-iqia/milatools) | Local CLI for connecting to and interacting with Slurm clusters. | A short purpose statement followed by installation and concrete command examples. |
| [NodeGrab](https://github.com/mbelgin/nodegrab) | Open OnDemand allocations with persistent tmux sessions. | A small workflow diagram and explicit site adaptation requirements. |
| [Fred Hutch HPC Skills](https://github.com/settylab/fh-hpc-skills) | Claude Code guidance for the Fred Hutch cluster and lab conventions. | Explaining how agents use skills through concrete questions and tasks. |
| [HPC Skill Hub](https://github.com/hwang595/hpc-skill-hub) | Registry of reusable HPC workflow skills. | Clear links between the entry page, catalog and deeper documentation. |
| [stui](https://github.com/Antvirf/stui) | Terminal interface for inspecting and managing Slurm clusters. | Describing observable actions and giving a direct installation/usage path. |

HPC Agent Kit focuses on establishing a persistent GPU workspace and carrying
verified workspace snapshots between allocations. Other Slurm sites adapt the
shared core through private profiles and tested access backends.
