# 迭代 013：需求文档

## 1. 目标

1. **可度量**:用 eval-first 方法度量产品承诺本身("宿主 LLM + 离线 CSV + 自然语言意图 → 合法回测"),
   并让后续所有改动都建立在可回归的评测基线之上。
2. **可用**:让类型化 CLI 对 LLM 调用者真正友好——统一 envelope、可区分的退出码、机器可读
   action schema、内联 JSON 输入、payload 含可执行示例与错误恢复手册。
3. **可观测**:宿主调用可追踪、子进程输出可保留、状态根可审计。
4. **有闭环**:参数 sweep/优化环成为一等能力,瞬态失败可安全重试,分析器/Sizers/指标注册表/
   Timers 扩大策略表达面。
5. **可维护**:archetype/adapter 单源注册表、安全哈希缓存、大文件拆分、死代码清理。

## 2. 功能需求

### 2.1 Phase 0:工具面契约与工程健康

**R1 统一成功 envelope。** 所有 CLI 成功输出统一为 `{"status": "ok", "result": <原输出>}`;
错误保持 `{"status": "failed", "diagnostic": {...}}`。`--json` 输出必须始终可被
`json.loads` 解析;`doctor` 等命令的既有字段移入 `result`。

**R2 退出码区分。** `0` 成功;`2` 用法错误(argparse);`3` 领域失败(BTAG-*);`4` 运行时
I/O 失败(OSError/磁盘满/权限,不再伪装为 `BTAG-CLI-INPUT`)。状态冲突属领域失败,统一为 `3`。

**R3 机器可读 action schema。** 新增 `backtrader-agent actions --json`,从 argparse 反射输出
全部子命令的参数定义(名称、类型、required/optional、choices、默认值);同时作为 packaged 资源
随 wheel 分发,含 golden 测试(与 `build_parser()` 结构同步校验)。

**R4 内联 JSON 输入。** 所有 `--*-file` 类参数支持直接传 JSON 字符串或 `@file` 引用;保持
旧的文件路径行为兼容(纯路径字符串仍按文件解释)。

**R5 OSError 诚实标记。** `main()` 的兜底异常处理区分 `AgentError`(exit 3)与
`OSError`(exit 4,`BTAG-CLI-IO`),message 说明是 I/O 层失败而非输入解析失败。

**R6 注册表单源。** 新增 `archetypes.py`(7 个 archetype 元数据:ID、契约值、模板、允许参数)
与 `adapters.py`(6 个 adapter 元数据:格式、列名、runner 装配路径)作为唯一事实源;删除
`contracts.py`/`scaffold.py`/`catalog.py`/`data.py` 中的三处硬编码副本;修复
`DatasetManifest` allowlist 中的 `canonical_csv_v1` 不一致(注册器不产、runner 拒绝的格式
不得出现在 allowlist)。

**R7 安全哈希缓存。** 单次 CLI 调用内 memoize:engine 树哈希、探测子进程结果、数据集 feed
哈希;catalog 按 packaged 资产的 manifest 级 `snapshot_hash` 做一次 SHA-256 验证,替代逐条
~1000 entry 重哈希。禁止对安全敏感哈希(engine 树、feed 文件)做跨进程持久缓存。

**R8 大文件拆分与死代码。** `runner.py`/`changes.py` 拆为 <400 行的子模块;
`REQUIRED_BINDINGS` 集中定义并供所有 verify 调用点引用;`_single_test_source` 的字符串替换
改为模板函数;`doctor --json` 未读 flag 修正为 `--json` 实际生效;`catalog refresh` 的带源快照
接入 `search`/`inspect`(新增 `--snapshot-path`);`build/lib/` 过期构建产物清理出工作区。

### 2.2 Phase 1:工程轨——Eval harness、payload、提示词版本化

**R9 确定性 scripted-host harness(CI 默认门)。** `tests/evals/` 提供 15–25 个任务,每个任务 =
原始 CSV fixture + NL 意图 + 脚本化宿主步骤序列 + 确定性 grader。脚本化宿主以子进程方式驱动
CLI,严格按 `agent-payload.md` 的指令执行;grader 只做 exit code/schema/hash 断言,不用 LLM
评判。任务覆盖:全部 7 个 archetype 的完整管线、6 个 adapter 的注册、失败注入(过期 token、
非法 spec、中途崩溃后 `session recover`)、幂等重放。

**R10 失败注入任务。** 至少 4 个任务在管线中途注入失败(过期 token、preimage 不符、未批准
run、损坏 journal),断言脚本化宿主能按 payload 的错误恢复表走通恢复路径。

**R11 opt-in LLM 在环门(不阻塞 CI)。** 配置 `BACKTRADER_AGENT_EVAL_API_KEY` 时运行
`scripts/eval_llm_loop.py`,用真实宿主 LLM 对 R9 任务子集执行 pass@1/pass@3 统计,目标
pass@3 > 90%;结果写入 `docs/evals/<版本>-llm-loop.log`。未配置时跳过,默认 CI 不依赖任何
API key。

**R12 payload 重写。** `agent-payload.md` 增加:一条完整的端到端 worked trace(register →
spec → draft → validate → changes prepare → approval → apply → approval → run → report,
含逐字命令与最小 JSON 示例)、BTAG 错误码 → 恢复动作对照表、上下文压缩规则(哪些 artifact
被 hash 固定可安全摘要、哪些 token/路径不可丢失)。

**R13 提示词版本化。** payload 增加 `version` 字段;新增内容 hash golden test 固定 payload
的 SHA-256 常量;`docs/evals/payload-changelog.md` 记录每次 payload 变更与对应 eval 基线
分数。payload 内容变更必须 bump 版本。

### 2.3 Phase 1:功能轨——瞬态重试与 sweep

**R14 瞬态失败重试。** 新增合法状态迁移 `FAILED → RUN_APPROVED`,仅当:前一 run 失败类别为
瞬态白名单(`BTAG-RUN-TIMEOUT`,以及子进程被 OS 资源限制终止的 `RUN` 前缀资源类错误码,以
实施时 runner 的实际错误码枚举为准并在测试中固定),且新 run 的 subject/effect hash 与已批准
的一致。新 RunManifest 记录 `retry_of` 链;非瞬态失败仍必须走 repair。终端会话不因重试静默
复活。

**R15 SweepPlan。** 新增 `sweep prepare` 动作:输入已批准 spec + 参数网格(每参数值列表,
内联 JSON 或 `@file`),枚举展开为 N 个参数组合,产出不可变 SweepPlan 记录
`sweep_<64 hex hash>`(绑定 spec/dataset/engine/environment,含逐 cell 参数值与其确定性
哈希)。参数值必须落在 spec 声明的 `minimum`/`maximum` 界内,越界拒绝。

**R16 sweep 审批。** `approval request --kind sweep --subject sweep_<id>` 与独立的
`approval grant --confirm` 签发一次性 `sweep` token,绑定 SweepPlan hash 与会话 checkpoint。
token 复用、重放、跨会话使用一律拒绝。

**R17 sweep 执行。** `sweep run` 消费 token 后:为每个 cell 确定性渲染 renderer-owned 私有
草稿(不写入用户 workspace,不经 apply 两段式——sweep 是 run-only 能力,授权面严格小于
apply+run),逐 cell 复用现有受控 runner 执行(固定 argv、最小环境、超时、配额),每 cell 落
独立 RunManifest/RunResult;`--max-cells` 与 `--timeout-per-cell` 有界;cell 级瞬态失败按
R14 语义重试。会话 journal 记录 sweep 事件,复用 `RUNNING` 状态与 `sweep` action 类型。

**R18 sweep 报告。** `sweep report` 按 `final_value`/`sharpe_ratio` 排序输出
`sweep-result-v1` 结构化结果,含逐 cell 指标、参数值、run id;`compare` 可比较任意两个 cell
run。v1 只扫数值参数;不做遗传/贝叶斯优化;`entry`/`exit`/`risk` 保持不翻译(诚实边界)。

### 2.4 Phase 2:工程轨——可观测性与记忆

**R19 宿主调用追踪。** `dispatch()` 对每笔 CLI 调用写 append-only JSONL trace(命令、参数
hash、耗时、exit code、session 上下文);session 内调用写
`<state>/trace/<session-id>.jsonl`,session 外写 `<state>/trace/global.jsonl`;失败调用同样
记录。遵循既有 stable lock 纪律,不含 secret。

**R20 子进程输出保留。** 受控 runner 成功路径也保留子进程 stdout/stderr 到 run 目录
(截断至配额,stderr 保留最后 N 字节),失败路径维持现有脱敏截断语义。

**R21 doctor 状态审计。** `doctor --state-root <root> --audit` 扫描:损坏/撕裂 journal、
`RUNNING` 孤儿会话、CAS 对象 hash 违规、过期审批堆积、trace/记忆目录健康,输出结构化诊断
(逐项 status + 可修复提示)。listing 命令跳过损坏记录时同时报告跳过计数。

**R22 跨会话记忆。** state root 新增轻量 JSON 记忆存储:`memory/datasets.json`
(dataset_id → 注册时间、最近使用、宿主笔记)与 `memory/params.json`(archetype → sweep 产出
的参数先验,由 R17 完成时写入)。payload 增加"先 `data list` 复用已注册数据集"指令与压缩
边界说明。`parent_session_id` 字段保留并文档化为预留(fork/session 派生未实现,删除属破坏性
契约变更,不做)。

### 2.5 Phase 2:功能轨——分析器与 Sizers

**R23 扩展指标。** RunResult 在 11 个 required 标量之外新增可选 `extended_metrics` 区块
(TradeAnalyzer 子集:profit factor、平均持仓时长、连赢连亏;SQN;Calmar;VWR;GrossLeverage;
PositionsValue)。11 标量保持 required,旧消费者不受影响;schema 以 `$defs` 版本化扩展。

**R24 Sizers。** StrategySpec 的 `sizing` 字段本轮有限落地:`{method: fixed|percent,
fixed_size|percent}` 渲染进各 archetype 模板;`cerebro.addsizer` 经固定装配路径注入;
validator 白名单同步扩展(FixedSize、PercentSizer 及其受限参数)。`entry`/`exit`/`risk`
继续不翻译。

### 2.6 Phase 3:指标注册表与 Timers

**R25 指标注册表。** 新增 packaged 资产 `resources/catalog/indicator-registry-v1.json`
(从 fork 语料离线静态提取:模块名、类名、参数名,`source_available=false`,纯元数据);
`catalog search --kind indicator` 支持按指标名检索。不 import fork。

**R26 Timers/cheat 模式。** StrategySpec 新增可选 `timers` 与 `cheat` 区块(默认关);validator
白名单扩展 Timer 与 cheat-on-close 相关 API;`multi_timeframe`/`time_based` 相关 archetype
模板获得可渲染 timer/cheat 段;run_modes 保持 `runonce`/`runnext` 不变。

## 3. 非功能约束

- 兼容 Python 3.8+、POSIX/Windows、MIT 许可证;无新强依赖。
- 审批/安全模型不变弱:sweep 是 run-only 能力;所有安全敏感哈希不做跨进程持久缓存。
- 既有发行门全程保持绿:pytest(128+ 项)、ruff、black、`audit_independence.py`、`doctor`、
  `run_acceptance.py` 14-cell 矩阵、分发契约/独立性测试;Phase 0 的 envelope 迁移与全部
  既有测试同步更新。
- 新增发行门:R9/R10 harness 纳入 CI;R13 payload hash golden test;R16 sweep 安全 red tests。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1–R5 | `tests/test_cli_contract.py`:envelope/exit code/actions schema/inline JSON/OSError 标注 |
| R6 | 注册表单源后 `tests/test_distribution_contracts.py` + archetype/adapter 一致性测试 |
| R7 | `tests/test_cache_semantics.py`(进程内 memoize、跨进程不缓存安全哈希)+ catalog 验证改造测试 |
| R8 | 拆分后全量回归 + `_single_test_source` 模板函数 golden 输出测试 |
| R9–R11 | `tests/evals/` 任务集 + `scripts/run_evals.py` + CI 门 + opt-in LLM 报告 |
| R12–R13 | payload hash golden test + 语义测试(菜单行都指向真实子命令) |
| R14 | `tests/test_run_retry.py`:瞬态重试 legal transition + 非瞬态拒绝 red tests |
| R15–R18 | `tests/test_sweep.py`:枚举/越界拒绝/token 安全 red tests + 14-cell 风格 sweep 冒烟 |
| R19–R21 | `tests/test_observability.py`:trace 形状、stderr 保留、doctor audit |
| R22 | `tests/test_memory_store.py` + payload 复用指令语义测试 |
| R23–R24 | 扩展指标 schema 测试 + sizer 渲染/validator 白名单测试 + 真实 cell 运行 |
| R25–R26 | 注册表资产 golden 测试 + timer/cheat 渲染与 validator 测试 |
