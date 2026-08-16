# backtrader-agent

`backtrader-agent` is an independently installable, offline-first Backtrader
strategy-authoring **agent runtime**. Host LLM agents (Claude Code, Codex,
OpenCode, OpenClaw) drive it through a typed CLI: it registers local CSV data
into an immutable content-addressed store, validates canonical strategy
specifications, renders 14 strategy scaffolds (7 archetypes × 2 output
profiles), statically reviews candidates without importing them, gates writes
and runs with separate hash-bound approvals, executes only a fixed
child-process profile, and records recoverable session provenance.

It does not import, start, inspect, or depend on another Backtrader AI
product. It also does not embed a model SDK or require a model API key.

## Three layers

- **Native host adapters** — tiny discovery files in each host's own format
  (Claude Code / Codex / OpenCode / OpenClaw). No logic: they only point at
  the payload and the installed runtime.
- **Agent payload** (`backtrader-agent payload`) — the versioned persona,
  routing, lifecycle, and safety instructions (with a worked trace and a
  BTAG error-recovery table).
- **Python runtime** (`backtrader_agent`) — typed actions, contracts,
  content-addressed storage, validator, approvals, writer, controlled child
  runner, reports, and journal recovery.

## Install

```bash
# Base runtime: offline data, contracts, validation, sessions, doctor.
python -m pip install .

# Controlled Backtrader execution (installs cloudQuant/backtrader + pandas).
python -m pip install '.[backtest]'

backtrader-agent backtrader check
backtrader-agent doctor --json
backtrader-agent payload
```

Python 3.8+ required. The base runtime has no mandatory third-party
dependency.

## Install one native host adapter

```bash
backtrader-agent install --target /path/to/project --host claude --preview
backtrader-agent install --target /path/to/project --host claude --apply
```

Supported hosts: `claude`, `codex`, `opencode`, `openclaw`. All installs are
preview-first, create-only, hash recorded, and idempotent.

## Quick start

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

Then follow the [workflow](workflow.md) page through draft → validate →
approval → run → report.

## Output contract

Every successful invocation prints `{"status": "ok", "result": ...}`; every
failure prints `{"status": "failed", "diagnostic": {"code": "BTAG-*", ...}}`.
Exit codes: `0` success, `2` usage, `3` BTAG domain failure, `4` OS I/O
failure. `backtrader-agent actions --json` emits the machine-readable action
schema so host adapters can generate tool definitions.

## The engine

The only accepted Backtrader runtime is the
[cloudQuant/backtrader](https://github.com/cloudQuant/backtrader) fork,
verified by source evidence at registration and re-proven inside the child
environment before every controlled run.

## Honest boundaries

- Offline local files only: no download, database, WebSocket, API key, live
  broker/store, or real order.
- The controlled child process is defense in depth, not a container or OS
  sandbox.
- The renderer provides functional scaffolds, not automatic optimization or
  profitability claims. The sweep action performs bounded parameter
  enumeration over your declared grid.
- `entry`, `exit`, and `risk` fields are validated and recorded in the spec
  hash but are not translated into executable logic; `sizing`
  (fixed/percent) is rendered.

See the [ecosystem](ecosystem.md) page for the sibling cloudQuant products.
