# HPC Agent Kit

[English](README.md) | [简体中文](README.zh-CN.md)

供 Agent 在 **基于 Slurm 的 Linux HPC 集群**上建立 GPU 工作环境的通用工具和
skill：持久连接、嵌套 tmux、持久资源申请 ID、作业本地工作目录，以及经过校验的
自动备份与恢复。

**MSI 和 PSC Bridges-2 是已经验证过的参考站点。** 通用核心通过私有站点 profile
配置；Agent 根据其他 Slurm 集群的实际情况适配连接、资源规则和存储路径。

仓库：[playeriv65/hpc-agent-kit](https://github.com/playeriv65/hpc-agent-kit)。
获取配套发布版本或克隆仓库，再把路径交给 Agent：

```sh
git clone https://github.com/playeriv65/hpc-agent-kit.git
```

把仓库和这样的需求交给 Agent：

> 我有一个使用 Slurm 的 HPC。请根据这个仓库，为我建立并验证连接和 GPU 工作环境。
> 先检查现有登录方式、作业和存储，再按这个站点的实际情况配置。

`AGENTS.md` 和 `CLAUDE.md` 包含相同的操作契约。Agent 读取
[`skills/hpc-workflow/SKILL.md`](skills/hpc-workflow/SKILL.md)，探测实际站点，编写
私有 profile，生成部署包，再通过已有的认证连接部署。无需人工初始化向导。

## 持久 SSH 与跳板

MSI 或其他使用交互认证的连接方式，参见
[持久 SSH 与跳板指南](docs/msi-persistent-ssh.md)。指南说明了如何让常开主机持有
已认证连接、如何让普通命令复用连接而不重新发起 MFA，以及如何处理通道饱和或在
连接真正断开后恢复。

## Slurm 兼容性与站点适配

调度系统叫 **Slurm**。它的命令包括用于提交批作业的
[`sbatch`](https://slurm.schedmd.com/sbatch.html)、用于申请资源的
[`salloc`](https://slurm.schedmd.com/salloc.html)，以及用于运行作业步骤的
[`srun`](https://slurm.schedmd.com/srun.html)。当前工作环境后端在登录节点 tmux 中
保留 `salloc`，通过一次启动用的 `srun --pty` 建立计算节点 tmux；之后的普通任务
交给本工具的任务执行器。

| 站点或条件 | 当前支持情况 |
| --- | --- |
| MSI | 已验证的参考部署：持有已认证 SSH 连接，并直接 SSH 到获配的计算节点。 |
| PSC Bridges-2 | 已验证的参考部署：通过资源申请终端与 tmux 桥接访问计算节点。 |
| 其他 Slurm Linux 集群 | 适配私有站点 profile 并完成本站验收。允许且已经验证登录节点到获配计算节点 SSH 时，使用 `direct-ssh`。 |
| 通过资源申请终端访问计算节点 | `psc-bridge` 实现了 PSC 的连接方式；其他站点的终端行为需要适配和测试。 |
| 只允许 `sbatch` 的站点 | 需要适配资源申请后端；当前发布版本没有批作业专用后端。 |
| 其他调度系统 | 需要对应的调度器适配器。 |

Agent 需要确认登录路径、认证负责人、账号、分区与 QoS 权限、GPU GRES 名称、
资源限制、作业本地磁盘、持久备份位置，以及实际项目的工具链。参见
[站点探测说明](skills/hpc-workflow/references/site-probing.md)和
[MSI](examples/msi.example.json) / [PSC](examples/psc.example.json) profile 模板。
模板中的账号、路径和编译器选择属于站点实例。

## Agent 工具与部署后的命令

| 组件 | 用途 |
| --- | --- |
| `common/profile.py` | 校验提供的站点事实，生成独立部署包。 |
| `common/allocation.py` | 在 tmux 派发前持久化申请 ID；通过 Slurm 作业所属用户和 comment 找回申请。 |
| `common/task-runner.sh` | 对具名实验命令去重，保留输出和退出状态。 |
| `common/local-snapshot.sh` | 恢复、校验、自动保存、合并并保留不可变备份代次。 |
| `common/agent-state.py`、`common/health.py` | 区分队列观察、当前进程健康和归档完整性检查。 |
| `common/deploy_bundle.py` | 固定部署内容，确认系统处于可升级状态，保留旧文件，安装并在失败时回滚。 |

`direct-ssh` 适配器使用登录节点到计算节点的 SSH。`psc-bridge` 使用 PSC 的资源
申请终端与 tmux 桥接。两者都是模板：Agent 必须确认站点规则、存储和允许的 QoS，
再将相关事实标为已验证。生成器会拒绝未填写的示例账号和连接路径。框架与 CUDA
配置由明确的项目或站点启动脚本负责。

Agent 使用的工具命令：

```sh
uv run python common/profile.py private/site.json --output outputs/site-bundle
uv run python common/deploy_bundle.py outputs/site-bundle
# When deployment is within the requested setup scope:
uv run python common/deploy_bundle.py outputs/site-bundle --apply
# Review or apply rollback to the exact backup reported by deployment:
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP
uv run python common/deploy_bundle.py outputs/site-bundle --rollback REMOTE_BACKUP --apply
```

部署后，`SITE-gpu --request ID TYPE COUNT WALLTIME` 申请或复用资源，
`SITE-gpu allocation ID` 找回申请状态。实验通过
`SITE-gpu run --job JOB --request TASK --wait WINDOW -- COMMAND ...` 运行。
`SITE-agent-state doctor` 检查当前写入者和保存的新鲜程度；
`doctor --verify-backup` 进一步校验归档内容。健康检查记录一个独立任务，不改动实验。
每项结果明确区分 `passed`、`failed`、`unknown` 和 `not_checked`。没有计算节点访问
路径时，不能确认存储和自动保存健康。失败后遵循 skill 的
[恢复说明](skills/hpc-workflow/references/recovery.md)。

运行条件：Linux 上的 Slurm，支持直接 SSH 到获配计算节点或 PSC 终端桥接。
本地 profile 与部署工具使用 uv 和 Python 3.12+，支持 Windows 和 Linux。
远程脚本使用 Bash 4+、Python 3.6+、tmux、GNU tar、zstd、flock 和 GNU coreutils；
需要在登录节点和计算节点分别探测。其他调度器和计算节点访问方式需要适配器。
CUDA/GCC 由站点或项目启动脚本配置。单独安装 skill 时，需要取得与之匹配的完整仓库。

## 验证与参考站点

测试在 Linux 上使用真实 tmux、合成数据和模拟调度器：

```sh
uv run python -m unittest discover -s common/tests -v
uv run python common/check_repo.py
```

这些测试不消耗 GPU。真实 GPU 验收是 skill 中独立、明确选择执行的流程。
通用核心在 MSI 和 PSC 上开发；源站点验收不能替代新生成安装的本站验收。
节点突然丢失时，只能恢复已经提交的备份。通过明确的实验选项，将 checkpoint 和
结果写到持久存储。

v0.3.1 在记录日志时保留资源申请的控制终端，包括 PSC 桥接中的终端，并在 PSC
本地存储根目录 `/local` 下使用作业专属子目录。这些修正有 Linux 测试覆盖，并在
2026-10-06 通过了真实 PSC 两次 V100 资源申请的生命周期验收：申请重放、在线备份
健康与完整性、换节点自动恢复、当前写入者租约互斥，以及截止前备份。很小的归档
可能触发增量体积阈值；此时成功的全量合并可以作为有效的截止前备份。MSI 源部署的
两次资源申请测试使用 v0.3.0。接入新站点前，请执行 skill 中的真实验收。

私有 profile、认证材料、研究数据和部署日志不应进入公开仓库。导出清单绑定本版本
的文件内容，用于完整性校验，不是密码学签名。采用 MIT 许可证。

0.4.0 增加了部署日志、失败自动回滚和显式回滚、检查覆盖情况、预期合并行为分类、
一致的 Agent 契约，以及公开生成工作环境的回归测试。CI 在 Linux 上检查公开仓库，
在 Windows 上检查文件清单与契约。上述 GPU 验收仍属于历史版本的证据，不代表新做
了一次 v0.4.0 GPU 验收。

## 贡献与隐私

编辑开发仓库时，同步维护两个 README 语言版本和两个契约副本。有意修改文件后，
用 `uv run python common/check_repo.py --refresh` 更新 `export-manifest.json` 中
已有文件的哈希，再执行校验。新增受维护文件需显式加入清单；refresh 不会扫描私有
目录。文本使用 `.gitattributes` 规定的 LF 换行，确保 Windows、Linux 的 Git 检出和
发布归档保留相同的文件哈希。报告问题时使用 issue 模板中的脱敏版本、适配器、状态
和错误字段；不要发布原始认证材料或完整私有任务日志。
