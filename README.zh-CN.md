# HPC Agent Kit

[English](README.md) | [简体中文](README.zh-CN.md)

**让 coding agent 用上可持续连接、自动恢复的 Slurm GPU 工作环境。**

把这个仓库和 HPC 访问方式交给 Agent。它会按站点实际情况适配连接和配置，建立
嵌套 tmux，并在新的资源申请获批后恢复经过校验的工作目录备份。

[开始使用](#开始使用) · [工作流程](#工作流程) · [兼容性](#兼容性) · [文档导航](#文档导航)

## 它能帮你做什么？

- 桌面断线后，仍在有效期内的远程作业和任务继续运行。
- 代码、环境和缓存放在高速作业本地磁盘上，减少 home 占用。
- 作业启动时恢复经过校验的快照，运行期间自动保存。
- 断线后复用申请 ID 和任务 ID，避免重复申请、重复启动任务。
- 需要交互登录时，让普通命令复用健康的已认证 SSH 连接。

Agent 留在你自己的机器上；HPC 运行远程辅助脚本和任务。`AGENTS.md` 与 `CLAUDE.md`
提供相同的操作契约。

## 开始使用

克隆仓库，或者让 Agent 获取它：

```sh
git clone https://github.com/playeriv65/hpc-agent-kit.git
```

把本地仓库路径和下面的提示词交给 coding agent。将占位符替换为你的集群名称或
已有 SSH 别名：

```text
请根据 https://github.com/playeriv65/hpc-agent-kit 为我建立 Slurm HPC 工作流程。
我的集群是：[集群名称或 SSH 别名]。
先阅读 AGENTS.md 和 skills/hpc-workflow/SKILL.md，再检查现有连接、作业、资源权限
和存储。按实际情况建立私有站点 profile，部署辅助脚本，并验证工作环境、自动备份
和恢复。保留我现有的作业和数据；缺少访问资料或需要交互认证时再向我询问。
```

Agent 负责探测站点、生成部署包并验证安装后的工作流程。完整步骤见
[skill](skills/hpc-workflow/SKILL.md)。单独安装 skill 时，需要取得与之匹配的完整仓库。

建立环境后，命令采用下面的形式。`SITE`、GPU 类型、作业 ID 和命令根据实际安装
与项目填写：

```sh
SITE-gpu --request allocation-001 GPU_TYPE 1 01:00:00
SITE-gpu allocation allocation-001
SITE-gpu run --job JOB_ID --request task-001 --wait train -- COMMAND ...
SITE-agent-state doctor --verify-backup
```

## 工作流程

```mermaid
flowchart LR
    A["本地 coding agent"] --> L["登录节点 tmux<br/>control + 资源申请"]
    L --> C["计算节点 tmux<br/>setup + autosave + 任务"]
    C --> W["作业本地工作目录<br/>代码 + 缓存"]
    W -->|"校验后保存快照"| P["持久备份存储"]
    P -->|"下次资源申请后恢复"| W
```

登录会话持有 Slurm 资源申请；每个作业有自己的计算节点 tmux 和具名任务窗口。
启动时恢复经过校验的快照，并开启定期保存。默认每五分钟增量保存、每六小时全量
合并，并在作业截止前保存。最新代次损坏时，回退到已经验证的前序备份。
环境通过 `uv sync --locked` 重建。

需要交互认证时，常开跳板可以在桌面关机后继续持有健康的 SSH master。
[持久 SSH 指南](docs/msi-persistent-ssh.md)说明了认证负责人、连接复用、通道饱和
和恢复方式。

会话仍受 Slurm walltime 限制。节点突然丢失时，只能恢复已提交的备份。实验结果
和完整 checkpoint 应写到持久存储；恢复工作目录本身不等于恢复实验进度。

## 兼容性

通用核心面向 Linux 上的 Slurm。**MSI 和 PSC Bridges-2 是已经验证的参考站点**，
具体版本与验证范围见[验收记录](docs/verification.md)。其他 Slurm 站点由 Agent
通过私有 profile 适配，再完成本站验收。

| 连接方式或配置 | 支持情况 |
| --- | --- |
| 从登录节点 SSH 到获配计算节点 | 使用 `direct-ssh` 适配器；需确认站点允许且计算节点访问已验证。 |
| PSC 资源申请终端与 tmux 桥接 | 使用 `psc-bridge` 适配器；其他站点的终端行为需要适配。 |
| 账号、分区、QoS、GPU GRES 与存储路径 | 根据实际探测配置；[MSI](examples/msi.example.json)和 [PSC](examples/psc.example.json)是模板。 |
| 只允许批作业的 Slurm 站点 | 需要资源申请后端；当前后端使用 `salloc` + 启动用的 `srun --pty`。 |
| 其他调度系统 | 需要对应的调度器适配器。 |

[`sbatch`](https://slurm.schedmd.com/sbatch.html)、
[`salloc`](https://slurm.schedmd.com/salloc.html)和
[`srun`](https://slurm.schedmd.com/srun.html)都是 Slurm 命令；是否可用以及允许怎样使用，
取决于站点规则。本地工具使用 uv 和 Python 3.12+，支持 Windows 和 Linux。远程
脚本要求登录、计算节点具备 Bash 4+、Python 3.6+、tmux、GNU tar、zstd、flock 和
GNU 工具。详见[运行条件与操作说明](docs/usage.md)。

## 文档导航

- [Agent skill](skills/hpc-workflow/SKILL.md)：建立、使用与恢复站点环境。
- [站点探测](skills/hpc-workflow/references/site-probing.md)：连接、资源、存储和工具链的证据要求。
- [持久 SSH](docs/msi-persistent-ssh.md)：MSI 与可适配的跳板连接方式。
- [操作说明](docs/usage.md)：组件、部署、命令、健康检查和回滚。
- [验收步骤](skills/hpc-workflow/references/acceptance.md)与[验收记录](docs/verification.md)：已经验证什么，以及如何验证新站点。
- [恢复说明](skills/hpc-workflow/references/recovery.md)：不同故障的处理方式。
- [相关工具](docs/related-tools.md)：其他 HPC 客户端、交互界面和 skill 集合。

## 在你的集群上试试

正在适配另一个 Slurm 站点？欢迎[提交 issue](https://github.com/playeriv65/hpc-agent-kit/issues/new/choose)，
说明连接方式、资源申请规则和存储布局。站点试用报告、适配器修复和更清楚的操作
说明都很有帮助。公开报告不要包含私有 profile、认证材料、研究数据或原始日志。

同步维护两个 README 语言版本和相同的 Agent 契约。文件清单检查和已有离线测试
见[维护说明](docs/usage.md#maintaining-the-repository)。代码和发布包分别位于
[本仓库](https://github.com/playeriv65/hpc-agent-kit)和
[Releases](https://github.com/playeriv65/hpc-agent-kit/releases)。
采用 [MIT](LICENSE) 许可证。
