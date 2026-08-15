# 最终收敛审计（迭代 001–011）

**审计日期：** 2026-08-02  
**结论：** 在当前产品承诺内，未发现新的本地、可复现且可安全修复的高优先级缺口；停止继续增加迭代。

## 1. 审计范围与停止准则

本次审计仅覆盖本仓库承诺的离线、受控回测产品：本机 state root、注册 root、session/approval/change/run
持久化、native adapter 安装/卸载、wheel 分发和恢复路径。停止条件不是“理论上再无任何增强想法”，而是：

1. 不能再构造一个会导致用户可见的数据丢失、越界删除、原始竞态异常或错误重放的本地复现；
2. 每个剩余写入/删除路径都有明确的覆盖、锁、exact replay 或刻意的覆盖契约；
3. 完整三解释器、clean-wheel、静态和独立性门均已通过；
4. 余下项目属于明确的产品边界或需要外部环境/授权验证，而不是可在本仓库内臆测修复的 defect。

## 2. 已审计的写入与并发边界

| 区域 | 结论 | 证据 |
| --- | --- | --- |
| immutable dataset、draft、product record、adapter install | 同内容重试按 byte-exact replay；冲突/symlink 拒绝 | 迭代 008 的 real spawn race tests |
| session create/transition/recover | per-session stable lock；空 journal/无 manifest 的 crash bootstrap 可恢复，其余残留保守拒绝 | 迭代 002、010 的 spawn/crash tests |
| root registry、token secret、approval、change/run action | 各自 stable lock 或随机唯一 ID；mutable checkpoint/transaction 在对应临界区更新 | 迭代 003–007 的 concurrency tests |
| controlled run reports | public runner 经 action lock 与 exact persisted bytes；低层 `ReportRenderer.render` 未承诺 standalone retry，且只由 staged runner 使用 | `runner.py` exact persist path、迭代 008 边界说明 |
| adapter lifecycle | apply install/uninstall 按 target/host 串行；manifest 仅允许当前 host 的精确路径集合，拒绝 `..`、symlink、重复/未知条目及非 regular adapter | 迭代 009、011 的 spawn/tamper tests |
| registered workspace targets | `RootRegistry.resolve` 拒绝绝对、父路径和 symlink escape；变更 rollback 在目标 root lock 内执行 | `roots.py`、迭代 004–005 tests |

`CatalogSnapshot` 的输出文件是调用方显式指定的覆盖型 export；`ReportRenderer` 的 direct API 是内部低层渲染器。
两者均不属于持久化 action 的幂等 public contract，受控 CLI/runner 路径没有使用它们作为未加保护的重放点。

## 3. 最终发行证据

- `pytest tests`：base、py38、py312 各 **128 项通过**；base 仅有既有 Backtrader Quandl 弃用警告。
- `ruff check src/backtrader_agent tests scripts`：通过。
- `scripts/audit_independence.py`：6/6 通过；最终验收前代码快照 hash 为
  `f49ee8ed1e0ef0cacb638ec352fee79ab6557cf268fb5518c6bd19fd95f98d8d`。
- `scripts/doctor.py`：`status=ready`。没有注册 engine root 时 `execution_ready=false` 是预期环境提示，
  不是产品失败。
- `scripts/run_acceptance.py`：clean wheel build/install/probe 通过；7 archetypes × 2 profiles 的 14 cell
  全部通过（14 passed，1 个既有 warning，143.68s），crash-resume 与 repair 独立门通过。
- `build_manifest.py`：本轮验收时 root 145 files、package 43 files；最终文档改动后会重新生成并复核。

## 4. 明确不作为本轮 defect 的边界

| 边界 | 原因与后续条件 |
| --- | --- |
| NFS/SMB、跨主机分布式锁 | 当前 advisory lock 的声明仅限本机 state root；需要真实共享存储和部署授权后才能定义/验证协议。 |
| OS 级 sandbox、可验证网络隔离 | doctor 已明确列为 policy-only limitation；引入容器/权限隔离是产品与运维范围扩展。 |
| 实际 Claude/Codex/OpenCode/OpenClaw 注册命令 | adapter 仅生成经验证文件和显式手动命令；执行第三方 host 注册需要用户授权和真实 host 环境。 |
| live trading、在线数据 | 产品明确离线且 `live_trading=false`；不能将其视作缺失实现。 |
| Windows/NFS 实机矩阵 | Python 3.8 兼容和 Windows lock code 已有源码/测试设计，但本次物理环境是 macOS；需对应 runner 才能声明平台实测。 |
| 后续 adapter 资源集合演化 | strict manifest schema 会安全拒绝旧/未知形状；若发布扩展 adapter 文件集合，应新增有版本迁移的专门迭代，而不是弱化删除边界。 |

## 5. 停止决定

迭代 001–011 已把审计中实际发现的缺口闭环：发行契约、session 线性化、token/change/run 锁、create-only
无覆盖、immutable replay、adapter lifecycle、bootstrap crash recovery 与 adapter manifest 删除边界。继续修改
不会修复已证实的 defect，而会进入新的产品能力或外部环境项目。因此本轮按停止准则验收通过。
