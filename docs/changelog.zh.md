# 变更日志

权威变更历史在仓库的
[`CHANGELOG.md`](https://github.com/cloudQuant/backtrader-agent/blob/master/CHANGELOG.md)。
本页只做当前版本摘要。

## 0.2.0 — 2026-08-16(迭代 013)

Agentic 工程化 + 量化能力扩展。

### 破坏性变更

- **Spec-hash 兼容性。** 规范 `StrategySpec` 现在恒含 `timers` 与 `cheat`
  字段,因此所有先前计算的 `spec_hash` 都会变化。接受 pre-1.0 破坏:重新
  审批受影响的 spec(遗留输入别名不受影响)。

### 安全

- 可执行校验的引擎与解释器证据只来自已注册的只读 engine root;不再接受
  调用方提供的 engine/environment hash。
- Sweep 是 run-only 能力,授权面严格小于 apply+run;一个 sweep token 只覆盖
  绑定的确定性枚举计划。

### 新增

- **工具面契约** — 统一 `{"status": "ok", ...}` / `{"status": "failed",
  ...}` envelope、退出码矩阵(0/2/3/4)、`actions --json` 机器可读 schema、
  内联 JSON 参数、诚实的 `BTAG-CLI-IO` 标记。
- **Eval-first 验证** — 23 任务确定性 scripted-host 套件进 CI、opt-in
  LLM 在环门、payload 版本化(黄金哈希 + 变更日志)。
- **参数 sweep** — `sweep prepare / run / report`,密封 SweepPlan、专用审批
  kind、逐 cell 受控运行与排名报告。
- **瞬态失败重试** — 超时类失败后同 effect 重试,`retry_of` 链。
- **扩展指标** — 在 11 个 required 标量之外提供可选 TradeAnalyzer/SQN/
  Calmar/VWR/GrossLeverage/PositionsValue 区块。
- **Sizers** — `sizing`(fixed/percent)已生效渲染。
- **Timers 与 cheat 模式** — 可选 timer/cheat spec 区块,字面量形式
  validator 门禁。
- **指标注册表** — 打包的 417 类元数据注册表,`catalog search --kind
  indicator` 检索。
- **可观测性** — 逐调用追踪、子进程 stdout/stderr 保留、`doctor --audit`
  状态体检。
- **跨会话记忆** — 数据集笔记与 sweep 参数先验。
- **工程健康** — archetype/adapter 单源注册表、进程内哈希缓存、大文件拆分、
  死代码清理。
