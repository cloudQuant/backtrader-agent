# backtrader-agent

`backtrader-agent` 是一个可独立安装、离线优先的 Backtrader 策略编写 **agent 运行时**。
宿主 LLM agent(Claude Code、Codex、OpenCode、OpenClaw)通过类型化 CLI 驱动它:
它把本地 CSV 数据登记进不可变的内容寻址存储,校验规范策略规格,渲染 14 个策略脚手架
(7 个 archetype × 2 种输出 profile),在不导入候选项的前提下做静态审查,用各自独立的
哈希绑定审批把关写入与运行,只执行固定的子进程 profile,并记录可恢复的会话溯源。

它不会导入、启动、检查或依赖另一个 Backtrader AI 产品,也不嵌入 model SDK 或要求
model API key。

## 三个层次

- **原生宿主 adapter** — 各宿主自身格式下的极小发现文件(Claude Code / Codex /
  OpenCode / OpenClaw)。不含逻辑,只指向 payload 与已安装运行时。
- **agent payload**(`backtrader-agent payload`)— 带版本号的 persona、路由、
  生命周期与安全指令(含完整 worked trace 与 BTAG 错误恢复表)。
- **Python 运行时**(`backtrader_agent`)— typed 动作、契约、内容寻址存储、
  validator、审批、writer、受控子进程 runner、报告与日志恢复。

## 安装

```bash
# 基础运行时:离线数据、契约、校验、会话与 doctor。
python -m pip install .

# 受控 Backtrader 执行(安装 cloudQuant/backtrader 与 pandas)。
python -m pip install '.[backtest]'

backtrader-agent backtrader check
backtrader-agent doctor --json
backtrader-agent payload
```

需要 Python 3.8+。基础运行时没有强制的第三方依赖。

## 安装一个原生宿主 adapter

```bash
backtrader-agent install --target /path/to/project --host claude --preview
backtrader-agent install --target /path/to/project --host claude --apply
```

支持宿主:`claude`、`codex`、`opencode`、`openclaw`。所有安装均为
preview-first、create-only、记录哈希且幂等。

## 快速开始

```bash
export STATE=/path/to/workspace/.backtrader-agent
backtrader-agent --state-root $STATE roots register --id workspace --kind workspace --writable --path /path/to/workspace
backtrader-agent --state-root $STATE roots register --id prices --kind dataset --path /path/to/offline-data
backtrader-agent --state-root $STATE roots register --id engine --kind engine --path /path/to/cloudquant-backtrader
backtrader-agent --state-root $STATE session create --session-id session-001
backtrader-agent --state-root $STATE data inspect --spec data-spec.json
backtrader-agent --state-root $STATE data register --session-id session-001 --spec data-spec.json
backtrader-agent --state-root $STATE spec --session-id session-001 --approve --file strategy-spec.json
```

随后按[工作流](workflow.md)页走完 draft → validate → approval → run → report。

## 输出契约

每次成功调用输出 `{"status": "ok", "result": ...}`;每次失败输出
`{"status": "failed", "diagnostic": {"code": "BTAG-*", ...}}`。退出码:`0` 成功、
`2` 用法错误、`3` BTAG 领域失败、`4` 操作系统 I/O 失败。
`backtrader-agent actions --json` 输出机器可读的 action schema,宿主 adapter
可据此自动生成 tool 定义。

## 引擎

唯一被接受的 Backtrader 运行时是
[cloudQuant/backtrader](https://github.com/cloudQuant/backtrader) fork,
注册时按来源证据校验,且每次受控运行前在子环境中重新证明。

## 如实边界

- 仅离线本地文件:无下载、数据库、WebSocket、API key、实盘 broker/store 或真实订单。
- 受控子进程是纵深防御,不是容器或 OS 沙箱。
- renderer 提供功能性脚手架,不提供自动优化或盈利保证;sweep 动作只在
  你声明的参数网格上做有界枚举。
- `entry`、`exit`、`risk` 字段会被校验并记入 spec 哈希,但不翻译成可执行逻辑;
  `sizing`(fixed/percent)会被渲染。

兄弟产品见[生态](ecosystem.md)页。
