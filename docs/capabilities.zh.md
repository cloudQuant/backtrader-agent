# 能力

迭代 013(v0.2.0)在原有离线管线(data → spec → draft → validate → approve →
run → report)之上增加了下述 Agentic 工程化与量化能力层。

## 工具面契约(面向宿主 LLM)

- **统一 envelope** — 成功恒为 `{"status": "ok", "result": ...}`,失败
  `{"status": "failed", "diagnostic": {"code": "BTAG-*", ...}}`。
- **退出码** — `0` 成功、`2` 用法错误、`3` BTAG 领域失败、`4` 操作系统
  I/O 失败(`BTAG-CLI-IO`)。磁盘满不再被误报为输入解析错误。
- **机器可读 action schema** — `backtrader-agent actions --json` 枚举全部
  子命令及其类型化参数,随 wheel 打包,宿主 adapter 可据此生成 tool 定义,
  不必再解析 `--help`。
- **内联 JSON 参数** — 每个文件型参数都接受内联 JSON、`@file` 或普通路径。

## Eval-first 验证

- **确定性 scripted-host 套件** — 23 个 eval 任务(7 条 archetype 全管线、
  6 个 adapter 登记、幂等重放、6 个失败注入)以脚本化宿主身份驱动真实 CLI;
  grader 纯确定性。每次 push 都在 CI 运行。
- **opt-in LLM 在环门** — 配置 `BACKTRADER_AGENT_EVAL_API_KEY` 后,
  `scripts/eval_llm_loop.py` 用真实宿主 LLM 在同一任务集上统计 pass@1/
  pass@3。永不进 CI,永不成运行时依赖。
- **payload 版本化** — agent payload 带版本号与固定 SHA-256 golden 测试;
  每次变更都记录在 `docs/evals/payload-changelog.md` 并附 eval 基线。

## 参数 sweep / 优化环

- `sweep prepare` 把声明的参数网格展开成不可变、密封的 SweepPlan
  (界来自 spec 的 `minimum`/`maximum`)。
- `approval request --kind sweep` + `grant` 签发一次性 sweep token,
  绑定 plan hash、会话、数据集、引擎与环境。
- `sweep run` 让每个 cell 从 renderer 拥有的私有草稿经受控 runner 执行 ——
  **run-only** 能力,绝不写你的 workspace。`--max-cells` 与
  `--timeout-per-cell` 有界;cell 级瞬态失败重试一次。
- `sweep report` 按 `final_value` 排名,并把每 archetype 的前 5 参数先验
  写入记忆存储。

## 瞬态失败重试

超时类失败把会话置为 `FAILED` 且 `retry_eligible=true`;为同一 effect 重新
审批一次 run 即可恢复(`FAILED → RUN_APPROVED`),新 RunManifest 记录
`retry_of` 链。非瞬态失败仍必须携带修订 spec 走 `repair`。

## 扩展指标

RunResult 保持 11 个 required 标量,并新增可选 `extended_metrics` 区块:
TradeAnalyzer 子集(profit factor、平均持仓 bar 数、连赢/连亏)、SQN、Calmar、
VWR、GrossLeverage、PositionsValue。分析器出错或缺失只把该字段降级为
`null` —— 绝不让 run 失败。

## Sizers

spec 的 `sizing` 区块现已生效:`{method: fixed|percent, fixed_size|percent}`
渲染为 `cerebro.addsizer(bt.sizers.FixedSize, stake=n)` /
`PercentSizer(percents=p)` 注入每个 archetype。`entry`、`exit`、`risk`
仍只校验不翻译(诚实边界)。

## Timers 与 cheat 模式

可选 `timers`(`{when: session|cheat|both, callback}`)与 `cheat`
(`{on_open|on_close}`)区块渲染为 `self.add_timer(...)` /
`cerebro.broker.set_coo(...)` / `set_coc(...)` 段。validator 对 timer 构造与
broker cheat 调用施加字面量白名单门禁。

## 指标注册表

`catalog search --kind indicator` 搜索打包的 `indicator-registry-v1.json`
(56 个核心 + 207 个 contrib 模块共 417 个类),离线从引擎源码提取 —— 纯
元数据、`source_available=false`,运行时绝不 import。

## 可观测性

- **调用追踪** — 每笔 CLI 调用以哈希参数、耗时与退出码追加进
  `<state>/trace/<session-id>.jsonl`(或 `global.jsonl`);失败调用同样记录。
- **子进程输出保留** — 每次受控 run 都保留 `stdout.log`/`stderr.log`
  (失败路径按尾部 2000 字节脱敏纪律处理)。
- **`doctor --audit`** — 只读状态根体检:撕裂 journal、`RUNNING` 孤儿、
  CAS 违规、过期审批堆积、trace/memory 健康。`--audit-deep` 增加全量逐文件
  哈希。

## 跨会话记忆

`<state>/memory/datasets.json` 与 `params.json` 存数据集笔记与 sweep 产出的
参数先验(原子写、schema 校验、哈希密封)。payload 指示宿主先 `data list`
复用已登记数据集,再考虑重新登记。
