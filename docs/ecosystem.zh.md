# cloudQuant Backtrader 生态

cloudQuant 围绕 Backtrader 引擎维护了一组产品。`backtrader-agent` 是其中之一;
其余成员分别覆盖引擎本身、策略编写技能、工具服务器接入、Web 研究平台与
绩效分析。

## [cloudQuant/backtrader](https://github.com/cloudQuant/backtrader)

引擎。这是 [mementum/backtrader](https://github.com/mementum/backtrader)
的性能优化 fork,100% API 兼容:Cython 强化内核带来约 45% 提速,57 个核心 +
209 个 contrib 指标模块、18 个分析器、参数优化后端、实盘交易(经 `bt_api_py`
接入 CTP 与加密货币交易所)、HFT 订单簿 broker、统一的 Plotly/Bokeh 绘图,
以及 1,152 个策略回归语料。当前版本 1.3.0,Python 3.8+。另有 C++20 伴随移植
(`back_trader`)逐指标对齐 1.1.0 API。

`backtrader-agent` **只接受本 fork** 作为引擎:注册的 engine root 按来源证据
校验,且每次受控运行在子环境中重新证明。

## [cloudQuant/backtrader-skills](https://github.com/cloudQuant/backtrader-skills)

面向该 fork 的离线、可独立安装的**编写/审查/测试**产品。它把已登记的本地
数据集与类型化 `StrategySpec v1` 变成收集好的 pytest 策略或三文件 Python
bundle,对候选项做静态审查(绝不导入),并把获批候选项放进独立的
`runonce`/`runnext` 子进程运行。内置 catalog 快照含 1,152 个功能策略测试与
1,035 个三文件包的元数据,常规使用无需源码语料。
文档:[cloudquant.github.io/backtrader-skills](https://cloudquant.github.io/backtrader-skills/) ·
[backtrader-skills.readthedocs.io](https://backtrader-skills.readthedocs.io/)

## [cloudQuant/backtrader-mcp](https://github.com/cloudQuant/backtrader-mcp)

独立、本地优先的 **MCP 服务器**:受限 CSV 变成不可变数据集,类型化策略意图
变成私有草稿,审查通过的草稿变成有持久状态与报告的有界子进程运行。30 个
工具带 readOnly/destructive/idempotent 注解与结构化 `[code] message` 错误;
状态存于 SQLite/WAL,数据内容寻址,HMAC 能力管控。离线且仅回测;Python 3.10+。

## [cloudQuant/backtrader_web](https://github.com/cloudQuant/backtrader_web)

**AI for Investor** —— AI 驱动的量化研究、策略生成、回测验证与交易辅助平台
([aifortrader.cn](https://aifortrader.cn/))。FastAPI + Vue 3:带引用的知识库
问答、策略起草/审查、数据覆盖与质量预检、回测报告与稳健性验证及参数优化、
交易工作区、组合 P&L/回撤观察。MySQL 本地优先 + AkShare 刷新;OpenTelemetry
埋点。

## [cloudQuant/backtrader-agent](https://github.com/cloudQuant/backtrader-agent)

本产品。可独立安装、离线优先的 **agent 运行时**:宿主 LLM agent(Claude Code、
Codex、OpenCode、OpenClaw)驱动类型化 CLI 动作,配内容寻址数据、哈希绑定审批、
受控子进程 runner 与可恢复会话溯源,另有参数 sweep 环与确定性 eval 套件
([cloudquant.github.io/backtrader-agent](https://cloudquant.github.io/backtrader-agent/) ·
[backtrader-agent.readthedocs.io](https://backtrader-agent.readthedocs.io/))。

## [cloudQuant/fincore](https://github.com/cloudQuant/fincore)

量化**绩效与风险分析**库 —— empyrical/pyfolio/alphalens 技术栈的持续维护
续作。150+ 金融指标、组合优化、蒙特卡洛模拟与绩效归因,三套 API 面(冻结的
empyrical 兼容面、pyfolio 外观面、增强的 `fincore.metrics`)。版本 0.3.0
(beta),Apache 2.0,Python 3.11+。
文档:[cloudquant.github.io/fincore](https://cloudquant.github.io/fincore/)

## 组件关系

```text
                    cloudQuant/backtrader (引擎 fork)
                                   │
        ┌──────────────┬───────────┴────────────┬──────────────┐
        │              │                        │              │
 backtrader-agent  backtrader-skills      backtrader-mcp   backtrader_web
 (agent 运行时,    (编写/审查/测试,      (MCP 服务器,     (AI for Investor,
  宿主 LLM 驱动)    CLI 产品)             工具接入)         Web 平台)
                                                        fincore
                                             (绩效与风险分析)
```

`backtrader-agent`、`backtrader-skills` 与 `backtrader-mcp` 共享同一套规范契约
(StrategySpec v1、run result、dataset manifest),并且刻意保持相互独立 ——
任何一个都不导入或启动另一个。
