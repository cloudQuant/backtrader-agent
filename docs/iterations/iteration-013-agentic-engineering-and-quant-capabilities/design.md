# 迭代 013：设计文档

## 1. 工具面契约(Phase 0)

### 1.1 统一 envelope

所有 CLI 成功输出统一为 `{"status": "ok", "result": ...}`,错误为
`{"status": "failed", "diagnostic": {"code": "BTAG-*", "severity", "message", "hint",
"details"}}`。`_emit()`(`cli.py:39-40`)改为包装函数;各命令的裸 dict 输出移入 `result`。
这是 breaking change,但宿主 adapter 尚未大规模部署,迁移代价窗口最小;全部既有测试随本轮
同步更新。

```text
before: {"datasets": [...]}          after: {"status": "ok", "result": {"datasets": [...]}}
before: {"status": "ready"}          after: {"status": "ok", "result": {"status": "ready"}}
```

### 1.2 退出码

| 码 | 含义 | 触发 |
| --- | --- | --- |
| 0 | 成功 | — |
| 2 | 用法错误 | argparse 原生 |
| 3 | 领域失败 | `AgentError`(BTAG-*) |
| 4 | 运行时 I/O 失败 | `OSError`(磁盘满/权限/损坏文件) |

`main()` 的兜底 `except (OSError, ValueError, json.JSONDecodeError)`(`cli.py:527-538`)拆分:
`AgentError → 3`;`OSError → 4` + `BTAG-CLI-IO`;`ValueError/JSONDecodeError` 保持
`BTAG-CLI-INPUT`(exit 3)。状态冲突(BTAG-STATE-*)属领域失败,统一 exit 3。

### 1.3 机器可读 action schema

`backtrader-agent actions --json` 从 `build_parser()` 反射:遍历每个 subparser 的参数定义
(name、type、required、choices、default、help),输出 `actions-v1` 结构。同结构打包进
`resources/contracts/actions-v1.schema.json` 与 `resources/actions-v1.json`(实际快照)。
golden 测试校验两者一致,防止 argparse 变化与打包资源漂移。宿主 adapter 可据此自动生成
tool 定义,不再从 `--help` 散文逆向。

### 1.4 内联 JSON 输入

所有 `--*-file` 参数解析顺序:参数值以 `@` 开头 → 读文件;否则先尝试 `json.loads` 作为内联
JSON,解析成功按 JSON 处理,失败则按文件路径处理。错误信息区分"内联 JSON 解析失败"与
"文件不可读"。

## 2. Eval harness(Phase 1)

### 2.1 确定性 scripted-host

`tests/evals/tasks/*.json` 定义任务:fixture CSV、NL 意图(供 LLM 门复用)、脚本化步骤序列、
grader 断言。脚本化宿主的执行引擎 `tests/evals/harness.py` 以 subprocess 驱动 CLI,把
`agent-payload.md` 当作 spec 执行——payload 写的每一条指令都必须在任务里被逐步执行到。
grader 只做确定性断言:exit code、envelope 形状、schema 校验、hash 相等、文件存在。

失败注入任务在固定步骤处注入异常输入(过期 token、preimage 不符、未批准 run、损坏 journal
后缀),断言恢复路径按 payload 恢复表走通。

### 2.2 opt-in LLM 门

`scripts/eval_llm_loop.py`:配置 `BACKTRADER_AGENT_EVAL_API_KEY` 时,用宿主 LLM 对任务子集
执行完整工作流,统计 pass@1/pass@3;结果落 `docs/evals/<版本>-llm-loop.log`。默认 CI 不配置
key、不运行。产品政策"不嵌入 model SDK、不要求 API key"不变——评测脚本是独立工具,不进
runtime。

### 2.3 提示词版本化

payload 头部增加 `version: "13.0.0"` 式字段;`tests/test_payload_contract.py` 固定 payload
SHA-256 常量(golden)。payload 变更必须:bump 版本、更新常量、在
`docs/evals/payload-changelog.md` 记录变更动机与对应 eval 基线。

## 3. Sweep 安全模型(Phase 1)

### 3.1 能力面

Sweep 是 **run-only** 能力:为每个参数组合渲染 renderer-owned 私有草稿,直接经受控 runner
执行,**不写用户 workspace、不经 apply 两段式**。授权面严格小于 apply+run(无 workspace
写入),因此单独 `sweep` token 一次覆盖整个枚举计划是安全的。若未来 sweep 需要把产物写入
workspace,必须回到 apply 两段式并逐产物审批。

### 3.2 记录与 token

```text
sweep prepare  -> SweepPlan(不可变): sweep_id=sweep_<64hex>, spec hash, dataset hash,
                  engine hash, environment hash, cells[] (每 cell: 参数值 + 确定性 cell hash)
approval request --kind sweep --subject sweep_<id>   -> PENDING(复用现有审批记录)
approval grant --confirm                              -> 一次性 sweep token(绑定 SweepPlan hash)
sweep run --sweep-id ... --token ...                  -> 逐 cell: 渲染私有草稿 -> 受控 runner
                                                         -> 每 cell 独立 RunManifest/RunResult
```

cell 渲染确定性:同一 spec + 同一参数值 + 同一运行时版本 → 相同字节。SweepPlan 的 cell
hash 绑定参数值,防止 plan 被篡改后复用 token。token 消费、重放、跨会话复用沿用现有
`TokenAuthority` 纪律。

### 3.3 有界执行

`--max-cells` 默认上限(如 100),`--timeout-per-cell` 逐 cell 生效;cell 级瞬态失败按
R14 重试语义处理,重试次数有界。会话 journal 记录 `sweep` action 事件:`sweep prepare`
落新状态 `SWEEP_PREPARED`(复用 `RUNNING` 会与 `recover()` 对 RUNNING 的强制
PAUSE 语义冲突);cell 运行(T15)复用 `RUNNING` 状态,中断恢复沿用 PAUSED/恢复路径。

### 3.4 参数消费

renderer 目前只消费 `fast_period`/`slow_period`(`scaffold.py:137-141`)。sweep v1 把参数
网格值注入各 archetype 模板的数值默认值位置;spec 的 `minimum`/`maximum` 作为网格值的合法
界,越界拒绝。多 archetype 的额外数值参数在模板注册表(R6)中声明,由同一机制消费。

## 4. 瞬态重试(Phase 1)

合法迁移 `FAILED → RUN_APPROVED` 的条件:前一 run 失败码 ∈ 瞬态白名单
(`BTAG-RUN-TIMEOUT`、OOM 类),且新 run 的 subject/effect hash 与已批准一致。授权语义:
run token 已为该 effect 授权过一次执行,同 effect 重试在授权范围内;新 RunManifest 显式
`retry_of` 引用前一 run id。非瞬态失败必须走 repair;`ARCHIVED`/`CANCELLED` 会话不复活。

## 5. 可观测性与记忆(Phase 2)

### 5.1 宿主追踪

`dispatch()` 入口写 JSONL:

```json
{"ts": "...", "session_id": "session-001", "command": "spec", "arg_hashes": {...},
 "duration_ms": 120, "exit_code": 0, "error_code": null}
```

session 内调用写 `<state>/trace/<session-id>.jsonl`,session 外写
`<state>/trace/global.jsonl`。append 遵循既有 stable lock 纪律;不记录 secret 与绝对
target 路径(与 BTAG 脱敏纪律一致)。

### 5.2 子进程输出保留

runner 成功路径把子进程 stdout(除 `BACKTRADER_AGENT_RESULT=` 行外)与 stderr 落 run 目录
`stdout.log`/`stderr.log`(截断至配额);失败路径维持现有脱敏 + 尾部 2000 字节语义。

### 5.3 doctor 状态审计

`doctor --state-root <root> --audit` 逐项检查:journal 链完整性(可复用 session recover 的
验证逻辑,只读不修)、`RUNNING` 孤儿(超出时长阈值)、CAS 对象 hash、过期审批计数、trace/
记忆目录健康。输出结构化诊断列表;listing 命令跳过损坏记录时计数并在结果中报告。

### 5.4 记忆存储

`<state>/memory/datasets.json` 与 `<state>/memory/params.json`,原子写、schema 校验、hash
绑定(session 无关,供跨会话复用)。payload 更新:"NW 入口先 `data list` 检查已注册数据集,
仅新数据才走 `data register`";压缩规则:被 hash/token 固定的 artifact 可安全摘要,draft
路径与未消费 token 不可丢弃。

## 6. 分析器与 Sizers(Phase 2)

### 6.1 扩展指标

`run-result-v1` schema 新增可选 `extended_metrics`(`$defs` 版本化),11 个 required 标量不变。
runner 模板在受控装配路径注册 analyzers:TradeAnalyzer(子集:profit factor、平均持仓时长、
连赢连亏)、SQN、Calmar、VWR、GrossLeverage、PositionsValue。NaN/Infinity 沿用现有
失败纪律;分析器缺失时 `extended_metrics` 为 null 而非失败(向后兼容)。

### 6.2 Sizers

spec 的 `sizing` 字段本轮有限落地:`{method: fixed|percent, fixed_size|percent}`。
scaffold 模板增加渲染段,经 `cerebro.addsizer` 固定装配;validator 白名单扩展
`FixedSize`/`PercentSizer` 及受限参数;非法 method/越界值在 spec 校验阶段拒绝。
`entry`/`exit`/`risk` 继续不翻译,README 的诚实边界段落同步更新。

## 7. 指标注册表与 Timers(Phase 3)

### 7.1 指标注册表

离线静态扫描 fork 语料的指标模块,提取 `{module, class_name, param_names}` 到
`resources/catalog/indicator-registry-v1.json`(纯元数据、`source_available=false`,与
corpus 快照同一纪律)。`catalog search --kind indicator` 词法检索。提取脚本进
`scripts/`(只读 fork,不 import)。

### 7.2 Timers/cheat

spec 新增可选 `timers`/`cheat` 区块(默认关,向后兼容)。validator 白名单扩展 Timer 与
cheat-on-close 相关 API;`multi_timeframe` 等 archetype 模板增加可渲染段。`run_modes`
保持 `runonce`/`runnext` 不变。

## 8. 工程健康(Phase 0)

### 8.1 注册表单源

`archetypes.py`:`ARCHETYPE_SPECS` dict(id → {contract_value, template, allowed_params});
`adapters.py`:`ADAPTER_SPECS` dict(format → {columns, runner_assembly})。`contracts`/
`scaffold`/`catalog`/`data` 的校验与渲染均从注册表读取;`DatasetManifest` allowlist 由
`ADAPTER_SPECS` 派生,`canonical_csv_v1` 不一致随之消除。测试断言三处枚举的一致性由构造
保证(单一来源,无需同步)。

### 8.2 缓存纪律

进程内 memoize(key = 对象路径 + 内容身份):engine 树哈希、探测结果、feed 哈希。跨进程
**不做**安全敏感缓存的持久化(engine/feed 哈希是安全绑定,只允许同进程复用)。catalog
按 manifest 级 `snapshot_hash` 单次验证(防部分损坏强度等价,成本 ~1000 倍降低)。

### 8.3 拆分

`runner.py` → `runner/{__init__,profiles,execute,reports,resume}.py`;
`changes.py` → `changes/{__init__,prepare,apply,rollback}.py`;`REQUIRED_BINDINGS` 移入
`tokens.py` 单一定义,`runner`/`changes` 调用点引用之。`_single_test_source` 改为模板函数
(参数化插值替代三处 `str.replace`)。

## 9. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 范围过大 | 每阶段独立验收/发布;Phase 1 结束即可停 |
| envelope 破坏性 | 与全部既有测试同步迁移;acceptance 矩阵保持绿 |
| sweep 扩大攻击面 | run-only 设计;token 绑定 plan hash;red tests 覆盖伪造 plan/重放 |
| 缓存引入 TOCTOU | 安全敏感哈希仅进程内 memoize;catalog 用 manifest 级 hash |
| 分析器指标漂移 | 扩展指标可选、nullable;分析器缺失不失败 |
