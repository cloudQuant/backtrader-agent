# Capabilities

Iteration 013 (v0.2.0) added the agentic-engineering and quant-capability
layers below. The core offline pipeline (data → spec → draft → validate →
approve → run → report) is unchanged.

## Tool-surface contract (for host LLMs)

- **Uniform envelope** — success is always `{"status": "ok", "result": ...}`,
  failure `{"status": "failed", "diagnostic": {"code": "BTAG-*", ...}}`.
- **Exit codes** — `0` success, `2` usage, `3` BTAG domain failure, `4` OS
  I/O failure (`BTAG-CLI-IO`). Disk-full is no longer misreported as an input
  parse error.
- **Machine-readable action schema** — `backtrader-agent actions --json`
  enumerates every subcommand with typed parameters, packaged with the wheel,
  so host adapters can generate tool definitions instead of parsing `--help`.
- **Inline JSON arguments** — every file-typed argument accepts inline JSON,
  `@file`, or a plain path.

## Eval-first verification

- **Deterministic scripted-host suite** — 23 eval tasks (7 full archetype
  pipelines, 6 adapter registrations, idempotent replay, 6 failure
  injections) drive the real CLI as a scripted host; graders are purely
  deterministic. Runs in CI on every push.
- **Opt-in LLM-in-the-loop gate** — with `BACKTRADER_AGENT_EVAL_API_KEY`
  configured, `scripts/eval_llm_loop.py` measures pass@1/pass@3 with a real
  host LLM over the same task set. Never runs in CI, never a runtime
  dependency.
- **Payload versioning** — the agent payload carries a version and a pinned
  SHA-256 golden test; every change is recorded in
  `docs/evals/payload-changelog.md` with its eval baseline.

## Parameter sweep / optimization loop

- `sweep prepare` expands a declared parameter grid into an immutable,
  sealed SweepPlan (bounds come from the spec's `minimum`/`maximum`).
- `approval request --kind sweep` + `grant` issue a one-time sweep token
  bound to the plan hash, session, dataset, engine, and environment.
- `sweep run` executes each cell from private renderer-owned drafts through
  the controlled runner — a **run-only** capability that never writes your
  workspace. `--max-cells` and `--timeout-per-cell` bound the run; cell-level
  transient failures retry once.
- `sweep report` ranks cells by `final_value` and records the top-5
  parameter priors per archetype into the memory store.

## Transient-failure retry

A timeout-class failure marks the session `FAILED` with
`retry_eligible=true`; a fresh run approval for the same effect resumes it
(`FAILED → RUN_APPROVED`), and the new RunManifest records a `retry_of`
chain. Non-transient failures still require `repair` with a revised spec.

## Extended metrics

RunResult keeps the 11 required scalars and adds an optional
`extended_metrics` block: TradeAnalyzer subset (profit factor, average
holding bars, consecutive wins/losses), SQN, Calmar, VWR, GrossLeverage,
PositionsValue. Analyzer errors or missing analyzers degrade that field to
`null` — never a run failure.

## Sizers

The spec's `sizing` block is now functional:
`{method: fixed|percent, fixed_size|percent}` renders
`cerebro.addsizer(bt.sizers.FixedSize, stake=n)` /
`PercentSizer(percents=p)` into every archetype. `entry`, `exit`, and `risk`
remain validated-but-not-translated (honest boundary).

## Timers and cheat modes

Optional `timers` (`{when: session|cheat|both, callback}`) and `cheat`
(`{on_open|on_close}`) blocks render as `self.add_timer(...)` /
`cerebro.broker.set_coo(...)` / `set_coc(...)` segments. The validator gates
timer construction and broker cheat calls to literal allowlisted forms.

## Indicator registry

`catalog search --kind indicator` searches a packaged
`indicator-registry-v1.json` (417 classes across 56 core + 207 contrib
modules) extracted offline from the engine source — pure metadata,
`source_available=false`, never imported at runtime.

## Observability

- **Invocation tracing** — every CLI call is appended to
  `<state>/trace/<session-id>.jsonl` (or `global.jsonl`) with hashed
  arguments, duration, and exit code; failures are traced too.
- **Child output retention** — every controlled run keeps
  `stdout.log`/`stderr.log` (failure paths redact to the tail-2000
  discipline).
- **`doctor --audit`** — read-only state-root health: torn journals,
  `RUNNING` orphans, CAS violations, expired-approval accumulation,
  trace/memory health. `--audit-deep` adds full per-file hashing.

## Cross-session memory

`<state>/memory/datasets.json` and `params.json` store per-dataset notes and
sweep-derived parameter priors (atomic writes, schema-validated, hash
sealed). The payload instructs hosts to reuse registered datasets via
`data list` before re-registering.
