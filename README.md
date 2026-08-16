# backtrader-agent

**English** | [**中文**](#-中文文档)

`backtrader-agent` is an independently installable, offline-first Backtrader
strategy-authoring agent runtime. It registers local data into immutable
content-addressed storage, validates canonical strategy specifications, renders
14 current-fork scaffolds, statically reviews candidates without importing
them, gates writes and runs with separate hash-bound approvals, executes only a
fixed child-process profile, and records recoverable session provenance. Its
acceptance matrix executes every scaffold in both `runonce` and `runnext` mode
and compares the normalized metrics.

It does not import, start, inspect, or depend on another Backtrader AI product.
It also does not embed a model SDK or require a model API key.

## Adapter, payload, and runtime are different layers

- A **native host adapter** is a tiny discovery/activation file in the host's
  own format. It contains no validation, data, state, or runner implementation.
- The packaged **agent payload** (`backtrader-agent payload`) provides persona,
  routing, lifecycle, and safety instructions.
- The installed **Python runtime** (`backtrader_agent`) implements typed actions,
  contracts, CAS, validator, approvals, writer, child runner, reports, and
  journal recovery.

The installer never presents one generic `.agents/skills` directory as four
different hosts. Each host receives its own native adapter.

## Install the Python distribution

From this directory, using any supported Python 3.8+ virtual environment:

```bash
# Base runtime: offline data, contracts, validation, sessions, and doctor.
python -m pip install .

# Controlled Backtrader execution (installs cloudQuant/backtrader and pandas).
python -m pip install '.[backtest]'

backtrader-agent backtrader check
backtrader-agent doctor --json
backtrader-agent payload
```

The base runtime has no mandatory third-party dependency. Install the
`backtest` extra before controlled execution; generated `single_test` profiles
also need the `single-test` extra (or the full `test` extra). Executable
validation accepts a registered read-only `--engine-root-id` only: the engine
and environment hashes are derived by the runtime and cannot be supplied as
CLI values.

The execution extras declare `backtrader` directly from
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader), rather than
a generic PyPI version range. `backtrader-agent backtrader check` and
`doctor --json` report its source evidence. If the package is missing, run
`backtrader-agent backtrader ensure` to install it into the current interpreter;
the controlled-run preflight performs the same missing-only bootstrap. An
existing package whose source is different or cannot be verified produces a
warning and is never silently replaced.

The examples below assume the environment containing `backtrader-agent` is
active. Conda, `venv`, and equivalent isolated Python environments are all
supported.

## Install one native host adapter

All installs are preview-first, create-only, hash recorded, and idempotent.
An existing modified adapter is never overwritten. Replace
`/path/to/project` with the host project/workspace root.

### Claude Code

```bash
backtrader-agent install --target /path/to/project --host claude --preview
backtrader-agent install --target /path/to/project --host claude --apply
```

Creates `.claude/agents/backtrader-agent.md`.

Verify and invoke:

```text
1. Start a new Claude Code session in /path/to/project and open /agents.
2. Confirm backtrader-agent is listed.
3. First request:
   Use the backtrader-agent subagent to inspect my offline CSV, clarify a
   StrategySpec, generate a strategy, and stop at each apply/run approval.
```

### Codex

```bash
backtrader-agent install --target /path/to/project --host codex --preview
backtrader-agent install --target /path/to/project --host codex --apply
```

Creates `.codex/agents/backtrader-agent.toml`.

Verify and invoke:

```text
1. Start a new Codex task rooted at /path/to/project.
2. Ask Codex to list or spawn the project agent named backtrader-agent.
3. First request:
   Spawn the backtrader-agent for my offline CSV, clarify a StrategySpec,
   generate a strategy, and stop at each apply/run approval.
```

### OpenCode

```bash
backtrader-agent install --target /path/to/project --host opencode --preview
backtrader-agent install --target /path/to/project --host opencode --apply
```

Creates `.opencode/agents/backtrader-agent.md`.

Verify and invoke:

```bash
cd /path/to/project
opencode agent list
opencode run --agent backtrader-agent \
  'Inspect my offline CSV, clarify a StrategySpec, generate a strategy, and stop at each apply/run approval.'
```

### OpenClaw

```bash
backtrader-agent install --target /path/to/project --host openclaw --preview
backtrader-agent install --target /path/to/project --host openclaw --apply
```

Creates an independent `.openclaw/workspaces/backtrader-agent/` workspace with
`AGENTS.md`, `IDENTITY.md`, a payload guide, and a registration manifest. It
does **not** claim that a project-local `agent.json` is discoverable and it does
not invoke the external OpenClaw CLI.

After reviewing the generated absolute workspace path, the user must explicitly
register and verify it with the official native commands printed by the
installer:

```bash
openclaw agents add backtrader-agent \
  --workspace '/absolute/path/to/openclaw-workspace' \
  --non-interactive
openclaw agents list
openclaw agent --agent backtrader-agent \
  --message 'Inspect my offline CSV, clarify a StrategySpec, generate a strategy, and stop at each apply/run approval.'
```

The generated registration manifest uses shell-safe quoting for the exact
workspace path; review and run its `registration_command` and
`invocation_command` instead of manually reconstructing them.

Install this Python distribution into that workspace's Python environment; the
workspace adapter does not duplicate product logic.

For exact manifest-driven removal, run:

```bash
backtrader-agent install --target /path/to/project --host codex --uninstall
```

Removal stops if an installed adapter was modified. For OpenClaw, filesystem
uninstall does not claim to unregister an already registered external agent;
manage that registration explicitly with the installed OpenClaw version.

## P0 workflow

Use a dedicated state root, normally `<workspace>/.backtrader-agent`, and add
only that narrow runtime directory to the target repository's ignore file.

```bash
backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id workspace --kind workspace --writable --path /path/to/workspace

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id prices --kind dataset --path /path/to/offline-data

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id engine --kind engine --path /path/to/cloudquant-backtrader

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  engine --root-id engine

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  session create --session-id session-001

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  data inspect --spec data-spec.json

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  data register --session-id session-001 --spec data-spec.json
```

DataSpec names a registered `root_id` and a relative path. The resolver rejects
absolute paths, `..`, symlink escape, devices, unsupported formats, changing
files, invalid timestamps, non-finite numbers, invalid OHLC, and quota
violations. Registration writes a canonical UTF-8 CSV to
`data/sha256/<prefix>/<normalized-hash>.csv` and emits a canonical
`DatasetManifest` with:

```text
schema_version, dataset_id=ds_<64 hex semantic hash>, spec_hash,
semantic_hash, manifest_hash, feeds, master_feed, alignment, status,
diagnostics, transforms, provenance, extensions
```

Every `--*-file` argument (DataSpec, StrategySpec, token JSON, report JSON,
and similar) accepts three equivalent forms: a plain file path, an inline
JSON object string, or `@path/to/file.json` to load JSON from a file.
`backtrader-agent actions --json` emits the machine-readable schema of every
typed subcommand (name, type, required/optional, choices, defaults); the same
content ships as the packaged `actions-v1.json` resource, validated against
`actions-v1.schema.json`. Every successful invocation prints
`{"status": "ok", "result": ...}` on stdout; failures print
`{"status": "failed", "diagnostic": {...}}` and exit with `2` (usage error),
`3` (BTAG domain failure), or `4` (OS I/O failure such as a full disk or a
permission error); success exits `0`. All `--json` output is always parseable
by `json.loads`.

The six declared offline adapters are `generic_csv`, `backtrader_csv`,
`yahoo_csv`, `mt5_csv`, `pandas`, and `pandas_custom_lines`. Registration parses
each adapter's native offline text shape into the immutable canonical CAS.
Controlled execution then uses the corresponding `GenericCSVData`,
`BacktraderCSVData`, offline `YahooFinanceCSVData`, controlled MT5,
`PandasData`, or product-owned extended `PandasData` assembly path. The two
Pandas adapters accept only already materialized tabular text-not pickle or
arbitrary Python objects. `resample` and `replay` are typed transforms with an
explicit feed, target timeframe, and compression; the runner routes them only
through `Cerebro.resampledata` or `Cerebro.replaydata`.

Validate a canonical StrategySpec, search the package-owned snapshot, and
render a private draft:

```bash
backtrader-agent --state-root /path/to/state spec \
  --session-id session-001 --approve --file strategy-spec.json
backtrader-agent catalog search --query "multi timeframe clock" --top-k 3
backtrader-agent --state-root /path/to/state draft \
  --session-id session-001 \
  --spec strategy-spec.json \
  --dataset-manifest dataset-manifest.json
```

The installed catalog owns two separate assets:

- `corpus-v1.jsonl` contains 1,155 immutable metadata records covering the
  verified 1,152 functional tests, 1,035 three-file packages, and 1,032
  mappings. These records contain hashes and relative provenance, not strategy
  source; every bundled record therefore has `source_available=false`.
- `snapshot.jsonl` contains the 14 current-fork template entries: seven
  archetypes by `single_test` and `python_bundle`. Template selection remains
  available independently of corpus search.

When the original two corpora are explicitly mounted read-only, the runtime
can rebuild a source-attached snapshot without importing, executing, or
modifying them:

```bash
backtrader-agent --state-root /path/to/state roots register \
  --id functional --kind dataset \
  --path /absolute/backtrader/tests/functional/strategies
backtrader-agent --state-root /path/to/state roots register \
  --id packages --kind dataset \
  --path /absolute/back_trader/strategies
backtrader-agent --state-root /path/to/state catalog refresh \
  --functional-root-id functional --package-root-id packages
```

The default baseline gate requires exactly 1,152/1,035/1,032. Use
`--allow-count-drift` only for an intentionally different corpus. The generated
snapshot stays in private Agent state, outside both source roots; the
package-owned snapshot remains unchanged.

Canonical StrategySpec output uses:

```text
spec_version='strategy-spec-v1', name, slug, category, archetype,
output_profile, dataset_id, feeds, parameters, entry, exit, sizing, timers,
cheat, risk, run_modes, allowed_imports
```

The seven archetypes are `single_data_indicator`,
`multi_indicator_system`, `multi_asset_allocation`, `multi_timeframe`,
`pairs_spread`, `order_risk`, and `precomputed_ml`; both `single_test` and
`python_bundle` profiles are renderable. Legacy input aliases
`single_indicator`, `multi_indicator`, `multi_asset`, `schema_version`,
`profile`, and `execution_modes` are accepted but never emitted.

> **Renderer scope:** the P0 renderer is a deterministic scaffold selector.
> It maps a StrategySpec to one of the seven fixed archetype templates,
> parameterized only by `archetype`, `output_profile`, and numeric parameter
> defaults (e.g. `fast_period` / `slow_period`). The `entry`, `exit`, and
> `risk` fields are validated and recorded in the spec hash, but are **not**
> translated into executable logic — `next()` behavior comes entirely from the
> chosen archetype. The `sizing` field is functional in a limited way:
> `{method: fixed, fixed_size: n}` renders
> `cerebro.addsizer(bt.sizers.FixedSize, stake=n)` and
> `{method: percent, percent: p}` renders
> `cerebro.addsizer(bt.sizers.PercentSizer, percents=p)`; omit the field (or
> set it to `null`) to keep Backtrader's default sizer. The `timers` field is
> functional in a limited way: `[{when: session|cheat|both, callback}]` renders
> a fixed `self.add_timer(when=bt.timer.SESSION_START[, cheat=True])` assembly
> (`both` schedules one timer in each window), and the allowlisted callbacks
> (`notify_timer`, `check_rebalance`) render as fixed deterministic hooks that
> only count firings. The `cheat` flags map to the fork's broker API:
> `{on_open: true}` renders `bt.Cerebro(..., cheat_on_open=True)` plus
> `cerebro.broker.set_coo(True)` and a `next_open` that mirrors the archetype
> signal at the open, and `{on_close: true}` renders
> `cerebro.broker.set_coc(True)` so market orders execute at their creation
> bar's close. Omit both fields (or set them to `null`) to keep the
> pre-timer/cheat render. To change trade logic, pick a different archetype
> or revise parameters. See
> [references/current-fork-rules.md](references/current-fork-rules.md).

Validation uses Python AST only. It never imports a candidate into the host
process. Imports, `os` access, Backtrader APIs, local strategy symbols, and
environment keys use exact capability allowlists. It rejects dynamic execution,
reflection, filesystem access, process/network libraries, product-runtime
transduction, live stores, path traversal, and non-allowlisted dependencies. A
direct `bt.Strategy` subclass is intentionally **not** required to call
`super().__init__()` on this fork; a custom parent or cooperative mixin must
still satisfy its MRO.

The write/run sequence is deliberately two-stage:

1. Rendering creates a private, locally signed provenance record bound to the
   exact session, approved spec, registered dataset manifest, draft directory,
   artifact manifest, and generated bytes. `validate --engine-root-id engine`
   accepts executable artifacts only when that renderer-owned record and the
   session checkpoint agree, then emits a validation report and token bound to
   the provenance record, artifact, dataset, environment, exact engine hash,
   and engine root ID.
2. `changes prepare` records exact source/target bytes, diff, expected preimage
   hash, renderer-owned draft path, artifact provenance, and the complete
   validation-token hash in an immutable locally signed prepared-change record.
   It does not write the target.
3. `approval request` persists a `PENDING` change request; a distinct local
   `approval grant --confirm` re-authenticates that signed record and the current
   session checkpoint before it persists and issues a one-time `change` token.
   `changes apply` ignores caller-supplied draft paths, loads the signed draft,
   consumes the token, checks every preimage, and uses a staged transaction
   journal with verified rollback.
4. A successful apply creates an immutable locally signed applied-artifact
   record. A separate request and local grant re-authenticates that record and
   issues a one-time `run` token bound to the applied/artifact/change records,
   full validation token, dataset, mode, environment, and engine.
5. `run` re-hashes all inputs and launches only `run.py` or the generated test
   through a fixed argv with `shell=False`, a minimal environment with no
   forwarded `HOME`, timeout, resource limits, and output quota. Before strategy
   execution, the same child environment imports `backtrader` and proves its
   resolved `__init__.py` and version belong to the approved engine root; the
   relative import path is recorded in `RunManifest`.

Reusing the same idempotency key returns its recorded result. A different key
is a different effect and cannot replay a consumed token.

After a run, reports and comparisons are addressed only by private immutable
run IDs:

```bash
backtrader-agent --state-root /path/to/state report \
  --run-id run-0123456789abcdef0123 --format markdown
backtrader-agent --state-root /path/to/state compare \
  --left-run-id run-0123456789abcdef0123 \
  --right-run-id run-fedcba9876543210fedc
```

The repair action never accepts a source patch. It requires a structured failed
ValidationReport/RunResult plus a revised StrategySpec, transitions the failed
session through `REPAIRING`, and deterministically re-renders a new owned draft.
The old artifact and action approvals cannot authorize the new bytes:

```bash
backtrader-agent --state-root /path/to/state repair \
  --session-id session-001 \
  --spec revised-strategy-spec.json \
  --dataset-manifest dataset-manifest.json \
  --failure-report failed-run-result.json
```

Use `backtrader-agent --help` and each subcommand's `--help` for exact typed
arguments. There is no `--command`, `--shell`, arbitrary callable, arbitrary
pytest target, or arbitrary output action.

## Sweep and transient retry

A parameter sweep is a run-only capability with a strictly smaller
authorization surface than apply+run: `sweep run` renders renderer-owned
private drafts cell by cell and never writes the user workspace through the
two-stage apply flow. Prepare an immutable plan, approve the whole enumerated
grid once, run it, and rank the per-cell results:

```bash
backtrader-agent --state-root /path/to/state sweep prepare \
  --session-id session-001 \
  --spec strategy-spec.json \
  --dataset-manifest dataset-manifest.json \
  --param-grid '{"fast_period": [5, 10], "slow_period": [15, 20]}' \
  --engine-root-id engine

# plan result contains sweep_id and plan_hash; approve the whole plan once
backtrader-agent --state-root /path/to/state approval request \
  --kind sweep --subject-hash <plan_hash> --bindings '<plan-bindings-json>'
backtrader-agent --state-root /path/to/state approval grant \
  --request-id <request_id> --approver local-user --confirm

backtrader-agent --state-root /path/to/state sweep run \
  --sweep-id sweep_<64-hex> --token '<grant-token-json>'

backtrader-agent --state-root /path/to/state sweep report \
  --sweep-id sweep_<64-hex>
```

`--param-grid` is a JSON object mapping spec parameter names to non-empty
numeric value lists; the cartesian product enumerates one deterministic cell
per combination. Values outside the spec's declared `minimum`/`maximum` are
rejected, and the plan is bound to the approved spec, dataset manifest,
engine, and environment hashes. `--max-cells` and `--timeout-per-cell` bound
the run; the sweep approval token covers only that plan and cannot be
replayed or reused across sessions. Each cell writes its own immutable
`RunManifest`/`RunResult`; `sweep report` returns a `sweep-result-v1` ranking
of passed cells by `final_value` descending (failed cells listed after), and
`compare` accepts any two cell run IDs. Sweep v1 sweeps numeric parameters
only: no genetic or Bayesian optimization, and `entry`/`exit`/`risk` remain
untranslated.

A failed run with the whitelisted transient failure `BTAG-RUN-TIMEOUT` is
`retry_eligible`: the session may move `FAILED → RUN_APPROVED` and re-run the
*same* approved effect without a new validation or apply cycle. The new run
approval must carry the failed run-subject hash, and the new `RunManifest`
records its `retry_of` chain. Non-transient failures (including OS
resource-limit kills, which would hit the same limit again), changed
effects, and terminal sessions cannot use this path and must go through
`repair`.

## Observability and memory

Every CLI invocation is traced to an append-only JSONL file under the state
root: session-scoped calls go to `<state>/trace/<session-id>.jsonl` and all
other calls to `<state>/trace/global.jsonl`. Each line records the command,
argument hashes (never secrets or absolute target paths), elapsed time, exit
code, and session context. Every controlled run keeps its child
`stdout.log`/`stderr.log` in the run directory on success as well as on
failure (truncated to the output quota, with a truncation marker when the
head or tail was dropped).

`doctor --audit` performs a read-only state-root health audit and appends
structured diagnostics: torn or corrupt journals, orphaned `RUNNING`
sessions, CAS hash violations, stale approval pile-ups, and trace/memory
directory health, each with a status and a repair hint. `--audit-deep` adds
full per-file CAS hash verification. Listing commands report a skip count
whenever they skip a corrupt record.

The cross-session memory store keeps two lightweight, schema-bound JSON
stores under `<state>/memory/`: `datasets.json` (dataset_id → registration
time, last use, and host notes) and `params.json` (archetype → top-5 sweep
parameter priors, written when a sweep completes). Every write is atomic
under a stable lock, and a tampered or corrupt store is rejected on load
rather than trusted. Manage it with `memory list` (`--datasets` or
`--params [--archetype ...]`) and `memory note --dataset-id ... --note ...`.

## Sessions and recovery

```bash
backtrader-agent --state-root /path/to/state session create --session-id session-001
backtrader-agent --state-root /path/to/state session status --session-id session-001
backtrader-agent --state-root /path/to/state session recover --session-id session-001
```

Every transition has a strictly increasing sequence, previous-event hash, event
hash, normalized input hashes, action, state pair, token/effect references, and
timestamp. Data registration, spec approval, draft, validation, prepare/apply,
run approval, execution, reporting, and completion all advance this state
machine; they are not isolated from `session` commands. Checkpoints are atomic.
Recovery accepts only a verified journal
prefix, isolates a malformed suffix, and moves an interrupted `RUNNING` session
to `PAUSED`. Terminal sessions do not silently reactivate.

## Reports and provenance

Each successful bundle run writes immutable `RunManifest`, `RunResult`,
Markdown, and HTML artifacts under the private run root. Metrics are:

`bar_num`, `buy_count`, `sell_count`, `win_count`, `loss_count`, `trade_num`,
`final_value`, `sharpe_ratio`, `annual_return`, `max_drawdown`, and
`return_rate`.

`sharpe_ratio` and `annual_return` may be `null`; NaN, Infinity, and any other
missing metric fail. Comparison uses exact integer/status/hash semantics and
`rel_tol=1e-7`, `abs_tol=1e-9` for floats.

`RunResult` also carries an optional `extended_metrics` block with the
TradeAnalyzer subset (profit factor, average holding length, win/loss
streaks), `sqn` (System Quality Number), `calmar`, `vwr` (variability-
weighted return), `gross_leverage`, and `positions_value`. The 11 scalars
above stay required and unchanged; a missing analyzer or a malformed block
normalizes to `null` sub-items instead of failing an otherwise healthy run,
and the schema is versioned through `$defs`.

## Verification

```bash
python -m pytest tests -q -p no:cacheprovider

python scripts/audit_independence.py

python scripts/run_evals.py

python scripts/run_acceptance.py

python scripts/doctor.py
```

`scripts/run_evals.py` runs the deterministic scripted-host eval suite: 23
tasks under `tests/evals/` that drive the full typed pipeline per the agent
payload — all 7 archetypes, 6 adapter registrations, failure injection
(expired token, preimage mismatch, unapproved run, corrupt journal), and
idempotent replay — with graders that assert only exit codes, schemas, and
hashes (no LLM judging). It is the default CI gate. The opt-in LLM loop
(`scripts/eval_llm_loop.py`, needs the `eval` extra and an API key, never
run by CI) measures pass@1/pass@3 for the same tasks through a real host
LLM; see [docs/evals/payload-changelog.md](docs/evals/payload-changelog.md).

The acceptance matrix and the end-to-end runner need a CloudQuant Backtrader
engine root: a directory from
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader) containing
`backtrader/__init__.py` and `backtrader/version.py` (a source checkout, or the
installed `site-packages` directory). It is resolved automatically by checking,
in order: the `BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT` environment variable,
sibling `backtrader` / `back_trader` source checkouts next to this repo, and the
installed `backtrader` package. If auto-resolution fails, set it explicitly:

```bash
export BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT=/path/to/cloudquant-backtrader
```

`backtrader-agent doctor --json` reports registered engine roots, the installed
Backtrader source status, and a hint when no engine is registered. It does not
replace an existing non-CloudQuant package.

The tests build a wheel in a temporary copy and prove that the seven public
schemas, AgentSessionManifest/AgentEvent schema, ComparisonProfile, snapshot,
corpus manifest, and agent payload are present in the wheel. They also verify
the exact full-snapshot SHA-256 and import/search it from a clean temporary
site outside this repository, without either sibling AI product.

`run_acceptance.py` writes and checks structured evidence for all 14
archetype/profile cells. Each cell contains separate `runonce` and `runnext`
result/manifest hashes, their normalized comparison, source provenance, and
the data shape used. Before running the fixed tests, it builds a wheel from a
temporary source copy, installs that wheel into a clean target, and executes
from a separate working directory whose import path excludes the source
checkout. The report records the wheel SHA-256, installed package origin, clean
`sys.path`, and `source_checkout_absent` attestation. The gate requires exact
coverage of all six adapters, multi-feed scenarios, typed multi-timeframe
transformation, and precomputed custom lines. Crash/resume and failure/repair
run against the same clean installation as independent gates rather than being
inferred from a successful matrix run.

Sibling absence is mandatory in the default command: acceptance fails if either
`backtrader_mcp` or `backtrader_skills` is importable in the clean runtime.

## Honest P0 limits

- Offline local files only: no download, database, WebSocket, API key, live
  broker/store, or real order.
- The controlled child process is defense in depth, not a container or OS
  sandbox. Network isolation is not claimed as OS-verified.
- Only candidates with an authenticated renderer-owned provenance record and
  matching session/spec/dataset/artifact approvals may run. Unknown third-party
  strategies are static-review-only.
- Snapshot search is lexical and deterministic over all 1,155 packaged metadata
  records. No embeddings, original corpus source, or hidden sibling checkout
  is required.
- The renderer provides functional scaffolds, not automatic optimization or
  profitability claims. Sweep v1 enumerates bounded numeric parameter grids
  deterministically; it is not a genetic or Bayesian optimizer.
- Fresh master/dev orchestration is not automated in this compact P0; register
  and run each engine as a separately approved profile before comparison.
- Pandas inputs must be materialized to canonical CSV outside this runtime;
  arbitrary DataFrame objects and pickle are rejected by design.

See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for implemented scope,
verification evidence, migration impact, and deferred items.

---

# 📖 中文文档

[**English**](#backtrader-agent) | **中文**

---

`backtrader-agent` 是一个可独立安装、离线优先的 Backtrader 策略编写 agent 运行时。
它把本地数据登记进不可变的内容寻址存储，校验规范策略规格（StrategySpec），渲染
当前 fork 的 14 个脚手架，静态审查候选项而不导入它，用各自独立的哈希绑定审批来把关
写入与运行，只执行固定的子进程 profile，并记录可恢复的会话溯源。其验收矩阵会把每个
脚手架在 `runonce` 和 `runnext` 两种模式下都执行一遍，并比较归一化指标。

它不会导入、启动、检查或依赖另一个 Backtrader AI 产品，也不嵌入 model SDK 或要求
model API key。

## adapter、payload 和运行时是不同层次

- **原生宿主 adapter** 是宿主自身格式下的一个极小发现 / 激活文件。它不含任何校验、
  数据、状态或 runner 实现。
- 打包的 **agent payload**（`backtrader-agent payload`）提供 persona、路由、生命周期
  和安全指令。
- 已安装的 **Python 运行时**（`backtrader_agent`）实现 typed 动作、契约、CAS、
  validator、审批、writer、child runner、报告和日志恢复。

安装器绝不会把同一个通用 `.agents/skills` 目录冒充成四个不同宿主。每个宿主拿到的是
各自的原生 adapter。

## 安装 Python 分发

在本目录下，使用任意受支持的 Python 3.8+ 虚拟环境：

```bash
# 基础运行时：离线数据、契约、校验、会话和 doctor。
python -m pip install .

# 受控 Backtrader 执行（安装 cloudQuant/backtrader 和 pandas）。
python -m pip install '.[backtest]'

backtrader-agent backtrader check
backtrader-agent doctor --json
backtrader-agent payload
```

基础运行时没有强制的第三方依赖。受控执行前请安装 `backtest` extra；生成的
`single_test` profile 还需要 `single-test` extra（或完整的 `test` extra）。可执行校验
只接受已注册、只读的 `--engine-root-id`：engine 和环境哈希由运行时派生，不能作为 CLI
参数传入。

执行 extra 会把 `backtrader` 直接声明为
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader)，而不是接受泛化的 PyPI
版本范围。`backtrader-agent backtrader check` 与 `doctor --json` 会报告来源证据。若当前
解释器缺少该包，可运行 `backtrader-agent backtrader ensure` 安装；受控运行的预检也只会在
缺失时执行相同补齐。已有包若来源不同或无法验证，会输出警告，但绝不会被静默替换。

下方示例假设包含 `backtrader-agent` 的环境已激活。Conda、`venv` 及等价的隔离
Python 环境都受支持。

## 安装一个原生宿主 adapter

所有安装都是 preview-first、create-only、记录哈希且幂等的。既有的、被修改过的
adapter 永不被覆盖。把 `/path/to/project` 替换为宿主项目 / 工作区根目录。

### Claude Code

```bash
backtrader-agent install --target /path/to/project --host claude --preview
backtrader-agent install --target /path/to/project --host claude --apply
```

创建 `.claude/agents/backtrader-agent.md`。

验证并调用：

```text
1. Start a new Claude Code session in /path/to/project and open /agents.
2. Confirm backtrader-agent is listed.
3. First request:
   Use the backtrader-agent subagent to inspect my offline CSV, clarify a
   StrategySpec, generate a strategy, and stop at each apply/run approval.
```

### Codex

```bash
backtrader-agent install --target /path/to/project --host codex --preview
backtrader-agent install --target /path/to/project --host codex --apply
```

创建 `.codex/agents/backtrader-agent.toml`。

验证并调用：

```text
1. Start a new Codex task rooted at /path/to/project.
2. Ask Codex to list or spawn the project agent named backtrader-agent.
3. First request:
   Spawn the backtrader-agent for my offline CSV, clarify a StrategySpec,
   generate a strategy, and stop at each apply/run approval.
```

### OpenCode

```bash
backtrader-agent install --target /path/to/project --host opencode --preview
backtrader-agent install --target /path/to/project --host opencode --apply
```

创建 `.opencode/agents/backtrader-agent.md`。

验证并调用：

```bash
cd /path/to/project
opencode agent list
opencode run --agent backtrader-agent \
  'Inspect my offline CSV, clarify a StrategySpec, generate a strategy, and stop at each apply/run approval.'
```

### OpenClaw

```bash
backtrader-agent install --target /path/to/project --host openclaw --preview
backtrader-agent install --target /path/to/project --host openclaw --apply
```

创建一个独立的 `.openclaw/workspaces/backtrader-agent/` 工作区，含 `AGENTS.md`、
`IDENTITY.md`、payload 指南和注册清单。它**不**声称项目本地的 `agent.json` 可被发现，
也不调用外部 OpenClaw CLI。

审查生成的绝对工作区路径后，用户必须用安装器打印的官方原生命令显式注册并验证它：

```bash
openclaw agents add backtrader-agent \
  --workspace '/absolute/path/to/openclaw-workspace' \
  --non-interactive
openclaw agents list
openclaw agent --agent backtrader-agent \
  --message 'Inspect my offline CSV, clarify a StrategySpec, generate a strategy, and stop at each apply/run approval.'
```

生成的注册清单对确切的工作区路径使用 shell 安全的引号；请审查并运行其中的
`registration_command` 和 `invocation_command`，而不是手动重建。

请把本 Python 分发安装进该工作区的 Python 环境；工作区 adapter 不重复产品逻辑。

基于清单的精确卸载：

```bash
backtrader-agent install --target /path/to/project --host codex --uninstall
```

若已安装的 adapter 被修改过，卸载会停止。对 OpenClaw，文件系统卸载不声称会取消注册
已注册的外部 agent；请用已安装的 OpenClaw 版本显式管理该注册。

## P0 工作流

使用专用 state root，通常为 `<workspace>/.backtrader-agent`，并只把这一个窄运行时目
录加入目标仓库的忽略文件。

```bash
backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id workspace --kind workspace --writable --path /path/to/workspace

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id prices --kind dataset --path /path/to/offline-data

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  roots register --id engine --kind engine --path /path/to/cloudquant-backtrader

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  engine --root-id engine

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  session create --session-id session-001

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  data inspect --spec data-spec.json

backtrader-agent --state-root /path/to/workspace/.backtrader-agent \
  data register --session-id session-001 --spec data-spec.json
```

DataSpec 命名一个已注册的 `root_id` 和一个相对路径。解析器会拒绝绝对路径、`..`、
符号链接逃逸、设备、不支持的格式、变化中的文件、无效时间戳、非有限数字、无效 OHLC
和配额违规。注册把规范 UTF-8 CSV 写到
`data/sha256/<prefix>/<normalized-hash>.csv`，并发出规范的 `DatasetManifest`：

```text
schema_version, dataset_id=ds_<64 hex semantic hash>, spec_hash,
semantic_hash, manifest_hash, feeds, master_feed, alignment, status,
diagnostics, transforms, provenance, extensions
```

所有 `--*-file` 类参数（DataSpec、StrategySpec、token JSON、report JSON 等）都接受
三种等价形式：纯文件路径、内联 JSON 对象字符串、或 `@path/to/file.json` 从文件加载
JSON。`backtrader-agent actions --json` 输出全部 typed 子命令的机器可读 schema（名称、
类型、required/optional、choices、默认值）；同一内容作为打包资源 `actions-v1.json` 随
wheel 分发，并可被 `actions-v1.schema.json` 校验。每次成功调用在 stdout 打印
`{"status": "ok", "result": ...}`；失败打印 `{"status": "failed", "diagnostic": {...}}`
并以 `2`（用法错误）、`3`（BTAG 领域失败）或 `4`（OS I/O 失败，如磁盘满或权限错误）
退出；成功退出 `0`。所有 `--json` 输出始终可被 `json.loads` 解析。

声明的六个离线 adapter 是 `generic_csv`、`backtrader_csv`、`yahoo_csv`、`mt5_csv`、
`pandas` 和 `pandas_custom_lines`。注册把每个 adapter 的原生离线文本形态解析成不可变
的规范 CAS。受控执行随后使用对应的 `GenericCSVData`、`BacktraderCSVData`、离线
`YahooFinanceCSVData`、受控 MT5、`PandasData` 或产品自有的扩展 `PandasData` 装配路
径。两个 Pandas adapter 只接受已物化的表格文本——不接受 pickle 或任意 Python 对象。
`resample` 和 `replay` 是带显式 feed、目标 timeframe 和 compression 的 typed
transform；runner 只通过 `Cerebro.resampledata` 或 `Cerebro.replaydata` 路由它们。

校验规范 StrategySpec、搜索包内快照并渲染私有草稿：

```bash
backtrader-agent --state-root /path/to/state spec \
  --session-id session-001 --approve --file strategy-spec.json
backtrader-agent catalog search --query "multi timeframe clock" --top-k 3
backtrader-agent --state-root /path/to/state draft \
  --session-id session-001 \
  --spec strategy-spec.json \
  --dataset-manifest dataset-manifest.json
```

已安装的 catalog 拥有两份独立资产：

- `corpus-v1.jsonl` 含 1,155 条不可变元数据记录，覆盖已验证的 1,152 个功能测试、
  1,035 个三文件包和 1,032 个映射。这些记录只含哈希和相对溯源，不含策略源码；因此每
  条内置记录的 `source_available=false`。
- `snapshot.jsonl` 含当前 fork 的 14 条模板条目：七个 archetype × `single_test` 和
  `python_bundle`。模板选择独立于语料搜索可用。

当原始两个语料被显式以只读挂载时，运行时可在不导入、执行或修改它们的前提下重建带
源码的快照：

```bash
backtrader-agent --state-root /path/to/state roots register \
  --id functional --kind dataset \
  --path /absolute/backtrader/tests/functional/strategies
backtrader-agent --state-root /path/to/state roots register \
  --id packages --kind dataset \
  --path /absolute/back_trader/strategies
backtrader-agent --state-root /path/to/state catalog refresh \
  --functional-root-id functional --package-root-id packages
```

默认基线门禁要求恰好 1,152/1,035/1,032。只有针对刻意不同的语料时才用
`--allow-count-drift`。生成的快照留在私有 Agent 状态内，位于两个 source root 之外；
包内快照保持不变。

规范 StrategySpec 输出使用：

```text
spec_version='strategy-spec-v1', name, slug, category, archetype,
output_profile, dataset_id, feeds, parameters, entry, exit, sizing, timers,
cheat, risk, run_modes, allowed_imports
```

七个 archetype 是 `single_data_indicator`、`multi_indicator_system`、
`multi_asset_allocation`、`multi_timeframe`、`pairs_spread`、`order_risk` 和
`precomputed_ml`；`single_test` 和 `python_bundle` 两种 profile 均可渲染。遗留输入
别名 `single_indicator`、`multi_indicator`、`multi_asset`、`schema_version`、
`profile` 和 `execution_modes` 被接受但从不输出。

> **Renderer 范围：**P0 renderer 是确定性的脚手架选择器。它把 StrategySpec 映射到
> 七个固定 archetype 模板之一，仅由 `archetype`、`output_profile` 和数值参数默认值
> （如 `fast_period` / `slow_period`）参数化。`entry`、`exit`、`risk` 字段会被校验
> 并记入 spec 哈希，但**不会**被翻译成可执行逻辑——`next()` 的行为完全来自所选
> archetype。`sizing` 字段已有限落地：`{method: fixed, fixed_size: n}` 渲染为
> `cerebro.addsizer(bt.sizers.FixedSize, stake=n)`，`{method: percent, percent: p}`
> 渲染为 `cerebro.addsizer(bt.sizers.PercentSizer, percents=p)`；省略该字段（或置为
> `null`）则保持 Backtrader 默认 sizer。`timers` 字段已有限落地：
> `[{when: session|cheat|both, callback}]` 渲染为固定的
> `self.add_timer(when=bt.timer.SESSION_START[, cheat=True])` 装配（`both` 在两种
> 窗口各注册一个 timer），白名单回调（`notify_timer`、`check_rebalance`）渲染为只
> 计数的确定性固定钩子。`cheat` 标志映射到 fork 的 broker API：`{on_open: true}`
> 渲染 `bt.Cerebro(..., cheat_on_open=True)` + `cerebro.broker.set_coo(True)` 并在
> 开盘复现 archetype 信号的 `next_open`，`{on_close: true}` 渲染
> `cerebro.broker.set_coc(True)` 使市价单以创建 bar 的收盘价成交。省略这两个字段
> （或置为 `null`）则保持 timer/cheat 之前的渲染。要改变交易逻辑，请换一个 archetype
> 或调整参数。见 [references/current-fork-rules.md](references/current-fork-rules.md)。

校验仅用 Python AST，绝不把候选项导入宿主进程。import、`os` 访问、Backtrader API、
本地策略符号和环境键使用精确的能力白名单。它拒绝动态执行、反射、文件系统访问、进程
/网络库、产品运行时传导、实盘 store、路径穿越和非白名单依赖。本 fork 上直接的
`bt.Strategy` 子类**不**要求调用 `super().__init__()`；自定义父类或协作式 mixin 仍
须满足其 MRO。

写入 / 运行序列刻意分为两段：

1. 渲染创建一个私有的、本地签名的溯源记录，绑定到确切的会话、已批准 spec、已注册数
   据集 manifest、草稿目录、artifact manifest 和生成字节。`validate
   --engine-root-id engine` 只有在该 renderer 拥有的记录与会话 checkpoint 一致时才接
   受可执行 artifact，随后发出绑定到溯源记录、artifact、数据集、环境、确切 engine 哈
   希和 engine root ID 的校验报告与令牌。
2. `changes prepare` 把确切的源 / 目标字节、diff、预期原像哈希、renderer 拥有的草稿
   路径、artifact 溯源和完整校验令牌哈希记录到一条不可变、本地签名的 prepared-change
   记录中。它不写入 target。
3. `approval request` 持久化一条 `PENDING` change 请求；另一个独立的本地
   `approval grant --confirm` 在持久化并签发一次性 `change` token 之前，重新认证该签
   名记录和当前会话 checkpoint。`changes apply` 忽略调用方提供的草稿路径，加载已签名
   草稿，消费 token，检查每个原像，并使用带校验回滚的暂存事务日志。
4. 成功的 apply 创建一条不可变、本地签名的 applied-artifact 记录。另一次单独的请求与
   本地 grant 重新认证该记录，并签发一次性 `run` token，绑定到 applied/artifact/
   change 记录、完整校验令牌、数据集、mode、环境和 engine。
5. `run` 重新哈希所有输入，并只通过固定 argv 启动 `run.py` 或生成的测试，使用
   `shell=False`、不转发 `HOME` 的最小环境、超时、资源限制和输出配额。在策略执行前，
   同一子环境导入 `backtrader` 并证明其解析到的 `__init__.py` 和版本属于已批准的
   engine root；相对导入路径记录在 `RunManifest` 中。

复用同一幂等键会返回其已记录结果。不同键是不同效果，不能重放已消费的 token。

运行后，报告和比较只能通过私有不可变 run ID 寻址：

```bash
backtrader-agent --state-root /path/to/state report \
  --run-id run-0123456789abcdef0123 --format markdown
backtrader-agent --state-root /path/to/state compare \
  --left-run-id run-0123456789abcdef0123 \
  --right-run-id run-fedcba9876543210fedc
```

repair 动作绝不接受源码补丁。它要求结构化的失败 ValidationReport/RunResult 加上一
份修订后的 StrategySpec，把失败会话转入 `REPAIRING`，并确定性地重新渲染一条新的自有
草稿。旧 artifact 和动作审批无法授权新字节：

```bash
backtrader-agent --state-root /path/to/state repair \
  --session-id session-001 \
  --spec revised-strategy-spec.json \
  --dataset-manifest dataset-manifest.json \
  --failure-report failed-run-result.json
```

用 `backtrader-agent --help` 和各子命令的 `--help` 查看确切的 typed 参数。没有
`--command`、`--shell`、任意 callable、任意 pytest 目标或任意输出动作。

## Sweep 与瞬态重试

参数 sweep 是 run-only 能力，授权面严格小于 apply+run：`sweep run` 逐 cell 渲染
renderer 拥有的私有草稿，从不通过两段式 apply 流程写入用户 workspace。先生成不可变
计划，对整张枚举网格审批一次，再运行并按 cell 结果排名：

```bash
backtrader-agent --state-root /path/to/state sweep prepare \
  --session-id session-001 \
  --spec strategy-spec.json \
  --dataset-manifest dataset-manifest.json \
  --param-grid '{"fast_period": [5, 10], "slow_period": [15, 20]}' \
  --engine-root-id engine

# 计划结果含 sweep_id 与 plan_hash；对整张计划审批一次
backtrader-agent --state-root /path/to/state approval request \
  --kind sweep --subject-hash <plan_hash> --bindings '<plan-bindings-json>'
backtrader-agent --state-root /path/to/state approval grant \
  --request-id <request_id> --approver local-user --confirm

backtrader-agent --state-root /path/to/state sweep run \
  --sweep-id sweep_<64-hex> --token '<grant-token-json>'

backtrader-agent --state-root /path/to/state sweep report \
  --sweep-id sweep_<64-hex>
```

`--param-grid` 是把 spec 参数名映射到非空数值列表的 JSON 对象；笛卡尔积按确定顺序
展开为逐 cell 的一个组合。落在 spec 声明 `minimum`/`maximum` 之外的值被拒绝，计划
绑定到已批准 spec、数据集 manifest、engine 与环境哈希。`--max-cells` 与
`--timeout-per-cell` 约束执行；sweep 审批 token 只覆盖该计划，不可重放或跨会话使用。
每个 cell 写入独立的不可变 `RunManifest`/`RunResult`；`sweep report` 返回
`sweep-result-v1`，通过的 cell 按 `final_value` 降序排名（失败的 cell 排在其后），
`compare` 可比较任意两个 cell run ID。sweep v1 只扫数值参数：不做遗传或贝叶斯优化，
`entry`/`exit`/`risk` 继续不翻译。

失败 run 若命中白名单瞬态失败码 `BTAG-RUN-TIMEOUT`，则 `retry_eligible`：会话可从
`FAILED → RUN_APPROVED`，对**同一**已批准 effect 重新运行，无需新的校验或 apply
周期。新的 run 审批必须携带失败 run 的 subject hash，新的 `RunManifest` 记录
`retry_of` 链。非瞬态失败（包括 OS 资源限制击杀——同一 effect 会再次撞上同一限制）、
effect 变化和终态会话不能走这条路径，必须走 `repair`。

## 可观测性与记忆

每次 CLI 调用都会追加追踪到 state root 下的 append-only JSONL 文件：会话内调用写入
`<state>/trace/<session-id>.jsonl`，其余调用写入 `<state>/trace/global.jsonl`。每行
记录命令、参数哈希（不含 secret 与绝对 target 路径）、耗时、退出码和会话上下文。每次
受控运行在成功与失败路径都把子进程 `stdout.log`/`stderr.log` 保留在 run 目录（按输出
配额截断，丢弃头部或尾部时带截断标记）。

`doctor --audit` 对 state root 做只读健康审计并追加结构化诊断：损坏/撕裂 journal、
`RUNNING` 孤儿会话、CAS 对象哈希违规、过期审批堆积、trace/记忆目录健康，每项带状态
与修复提示。`--audit-deep` 追加逐文件 CAS 哈希全量校验。listing 命令在跳过损坏记录时
同时报告跳过计数。

跨会话记忆存储维护 `<state>/memory/` 下两个轻量、带 schema 绑定的 JSON 存储：
`datasets.json`（dataset_id → 注册时间、最近使用、宿主笔记）与 `params.json`
（archetype → sweep 产出的 top-5 参数先验，sweep 完成时写入）。每次写入都在稳定锁下
原子完成，被篡改或损坏的存储加载时被拒绝而非被信任。用 `memory list`（`--datasets`
或 `--params [--archetype ...]`）与 `memory note --dataset-id ... --note ...` 管理。

## 会话与恢复

```bash
backtrader-agent --state-root /path/to/state session create --session-id session-001
backtrader-agent --state-root /path/to/state session status --session-id session-001
backtrader-agent --state-root /path/to/state session recover --session-id session-001
```

每次转换都有严格递增的序列号、前一事件哈希、事件哈希、归一化输入哈希、动作、状态
对、token/effect 引用和时间戳。数据登记、spec 审批、草稿、校验、prepare/apply、
run 审批、执行、报告和完成都会推进这个状态机；它们与 `session` 命令并不隔离。
checkpoint 是原子的。恢复只接受已校验的日志前缀，隔离畸形后缀，并把中断的
`RUNNING` 会话移到 `PAUSED`。终态会话不会静默重新激活。

## 报告与溯源

每次成功的 bundle 运行都会在私有 run root 下写入不可变的 `RunManifest`、
`RunResult`、Markdown 和 HTML artifact。指标为：

`bar_num`、`buy_count`、`sell_count`、`win_count`、`loss_count`、`trade_num`、
`final_value`、`sharpe_ratio`、`annual_return`、`max_drawdown` 和 `return_rate`。

`sharpe_ratio` 和 `annual_return` 可为 `null`；NaN、Infinity 及任何其他缺失指标都会
失败。比较使用精确的整数 / 状态 / 哈希语义，浮点数用 `rel_tol=1e-7`、
`abs_tol=1e-9`。

`RunResult` 还携带可选 `extended_metrics` 区块：TradeAnalyzer 子集（profit factor、
平均持仓时长、连赢连亏）、`sqn`（System Quality Number）、`calmar`、`vwr`
（variability-weighted return）、`gross_leverage` 与 `positions_value`。上面 11 个
标量保持 required 不变；分析器缺失或区块畸形时归一化为 `null` 子项，不会让健康的
运行失败；schema 通过 `$defs` 版本化扩展。

## 验证

```bash
python -m pytest tests -q -p no:cacheprovider

python scripts/audit_independence.py

python scripts/run_evals.py

python scripts/run_acceptance.py

python scripts/doctor.py
```

`scripts/run_evals.py` 运行确定性的 scripted-host eval 套件：`tests/evals/` 下 23 个
任务按 agent payload 驱动完整 typed 管线——全部 7 个 archetype、6 个 adapter 注册、
失败注入（过期 token、preimage 不符、未批准 run、损坏 journal）与幂等重放——grader 只
断言 exit code、schema 和哈希（不用 LLM 评判）。它是默认 CI 门。opt-in LLM 环
（`scripts/eval_llm_loop.py`，需要 `eval` extra 和 API key，CI 从不运行）用真实宿主
LLM 对同一任务集测量 pass@1/pass@3；见
[docs/evals/payload-changelog.md](docs/evals/payload-changelog.md)。

验收矩阵和端到端 runner 需要一个 CloudQuant Backtrader engine root：即
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader) 中包含
`backtrader/__init__.py` 和 `backtrader/version.py` 的目录（源码检出，或已安装的
`site-packages` 目录）。它会按以下顺序自动解析：`BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT`
环境变量、与本仓库同级的 `backtrader` / `back_trader` 源码检出、以及已安装的
`backtrader` 包。若自动解析失败，请显式设置：

```bash
export BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT=/path/to/cloudquant-backtrader
```

`backtrader-agent doctor --json` 会报告已注册的 engine root、已安装 Backtrader 的来源状态，
并在未注册时给出提示；它不会替换已有的非 CloudQuant 包。

测试在临时副本中构建 wheel，并证明七个公共 schema、AgentSessionManifest/AgentEvent
schema、ComparisonProfile、快照、语料 manifest 和 agent payload 都在 wheel 中。它们
还验证完整快照的确切 SHA-256，并从本仓库之外的干净临时 site 导入 / 搜索它，且没有任
何 sibling AI 产品。

`run_acceptance.py` 为全部 14 个 archetype/profile 单元写入并检查结构化证据。每个单
元含独立的 `runonce` 和 `runnext` 结果 / manifest 哈希、其归一化比较、源溯源和所用
数据形态。在运行固定测试前，它从临时源副本构建 wheel，把该 wheel 装进干净目标，并从
另一个工作目录执行，该目录的导入路径排除源码检出。报告记录 wheel SHA-256、已安装包
来源、干净的 `sys.path` 和 `source_checkout_absent` 证明。门禁要求精确覆盖全部六个
adapter、多 feed 场景、typed 多 timeframe 转换和 precomputed 自定义 line。
crash/resume 和 failure/repair 针对同一干净安装作为独立门禁运行，而非从成功的矩阵运
行推断。

sibling 缺失在默认命令中是强制的：若干净运行时可导入 `backtrader_mcp` 或
`backtrader_skills` 中的任何一个，验收失败。

## 如实说明 P0 的限制

- 仅离线本地文件：无下载、数据库、WebSocket、API key、实盘 broker/store 或真实订
  单。
- 受控子进程是纵深防御，不是容器或 OS 沙箱。网络隔离不以 OS 验证自居。
- 只有带已认证 renderer 拥有溯源记录且会话/spec/dataset/artifact 审批一致的候选项才
  能运行。未知第三方策略仅做静态审查。
- 快照搜索是对全部 1,155 条内置元数据记录的词法、确定性搜索。不需要 embedding、原
  始语料源或隐藏 sibling 检出。
- renderer 提供功能性脚手架，不提供自动优化或盈利保证。Sweep v1 确定性枚举有界的
  数值参数网格；它不是遗传或贝叶斯优化器。
- 本紧凑 P0 不自动编排全新 master/dev；比较前请把每个 engine 作为单独已批准 profile
  注册并运行。
- Pandas 输入必须在本运行时之外物化为规范 CSV；任意 DataFrame 对象和 pickle 按设计
  被拒绝。

实现范围、验证证据、迁移影响和延后项见
[IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md)。
