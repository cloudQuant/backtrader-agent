# cloudQuant Backtrader ecosystem

cloudQuant maintains a family of products around the Backtrader engine.
`backtrader-agent` is one member; the others cover the engine itself,
authoring skills, tool-server access, a web research platform, and
performance analytics.

## [cloudQuant/backtrader](https://github.com/cloudQuant/backtrader)

The engine. A performance-oriented fork of
[mementum/backtrader](https://github.com/mementum/backtrader) with 100% API
compatibility: ~45% faster execution via a Cython-enhanced core, 57 core +
209 contrib indicator modules, 18 analyzers, optimization backends, live
trading (CTP and crypto exchanges through `bt_api_py`), HFT order-book
brokers, unified Plotly/Bokeh plotting, and a 1,152-strategy regression
corpus. Current version 1.3.0, Python 3.8+. A C++20 companion port
(`back_trader`) mirrors the 1.1.0 API metric-for-metric.

`backtrader-agent` accepts **only this fork** as its engine: the registered
engine root is verified by source evidence, and every controlled run
re-proves it inside the child environment.

## [cloudQuant/backtrader-skills](https://github.com/cloudQuant/backtrader-skills)

Offline, independently installable **author/review/test** product for the
fork. It turns a registered local dataset and a typed `StrategySpec v1` into
a collected pytest strategy or a three-file Python bundle, reviews the
candidate statically (never importing it), and runs approved candidates in
separate `runonce`/`runnext` child processes. The bundled catalog snapshot
carries metadata for 1,152 functional strategy tests and 1,035 three-file
packages, so normal operation needs no source corpus.
Docs: [cloudquant.github.io/backtrader-skills](https://cloudquant.github.io/backtrader-skills/) ·
[backtrader-skills.readthedocs.io](https://backtrader-skills.readthedocs.io/)

## [cloudQuant/backtrader-mcp](https://github.com/cloudQuant/backtrader-mcp)

Independent, local-first **MCP server** for the same style of work: confined
CSV files become immutable datasets, typed strategy intent becomes private
drafts, and reviewed drafts become bounded subprocess runs with durable
status and reports. 30 tools carry readOnly/destructive/idempotent
annotations and structured `[code] message` errors; state lives in
SQLite/WAL with content-addressed data and HMAC capabilities. Offline and
backtest-only; Python 3.10+.

## [cloudQuant/backtrader_web](https://github.com/cloudQuant/backtrader_web)

**AI for Investor** — an AI-driven quant research, strategy generation,
backtest validation, and trading-assistance platform
([aifortrader.cn](https://aifortrader.cn/)). FastAPI + Vue 3: knowledge-base
retrieval with citations, strategy drafting/reviewing, data coverage and
quality pre-checks, backtest reports with robustness validation and
parameter optimization, trading workspaces, and portfolio P&L/drawdown
observation. MySQL-first local data with AkShare refresh; OpenTelemetry
instrumentation.

## [cloudQuant/backtrader-agent](https://github.com/cloudQuant/backtrader-agent)

This product. Independently installable, offline-first **agent runtime**:
host LLM agents (Claude Code, Codex, OpenCode, OpenClaw) drive typed CLI
actions over immutable content-addressed data, hash-bound approvals, a
controlled child runner, and recoverable session provenance — with a
parameter-sweep loop and a deterministic eval suite
([cloudquant.github.io/backtrader-agent](https://cloudquant.github.io/backtrader-agent/) ·
[backtrader-agent.readthedocs.io](https://backtrader-agent.readthedocs.io/)).

## [cloudQuant/fincore](https://github.com/cloudQuant/fincore)

Quantitative **performance & risk analytics** library — the maintained
continuation of the empyrical/pyfolio/alphalens stack. 150+ financial
metrics, portfolio optimization, Monte Carlo simulation, and performance
attribution across three API surfaces (frozen empyrical compatibility,
pyfolio façade, enhanced `fincore.metrics`). Version 0.3.0 (beta),
Apache 2.0, Python 3.11+.
Docs: [cloudquant.github.io/fincore](https://cloudquant.github.io/fincore/)

## How the pieces fit

```text
                    cloudQuant/backtrader (engine fork)
                                   │
        ┌──────────────┬───────────┴────────────┬──────────────┐
        │              │                        │              │
 backtrader-agent  backtrader-skills      backtrader-mcp   backtrader_web
 (agent runtime,   (author/review/test,   (MCP server,     (AI for Investor,
  host-LLM driven)  CLI product)           tool access)     web platform)
                                                        fincore
                                             (performance & risk analytics)
```

`backtrader-agent`, `backtrader-skills`, and `backtrader-mcp` share the same
canonical contracts (StrategySpec v1, run results, dataset manifests) and
are intentionally independent products — none imports or starts the others.
