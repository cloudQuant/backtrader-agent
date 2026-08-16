# Changelog

The authoritative change history lives in the repository's
[`CHANGELOG.md`](https://github.com/cloudQuant/backtrader-agent/blob/master/CHANGELOG.md).
This page summarizes the current release.

## 0.2.0 — 2026-08-16 (iteration 013)

Agentic engineering + quant capability expansion.

### Breaking

- **Spec-hash compatibility.** The canonical `StrategySpec` now always
  includes the `timers` and `cheat` fields, so every previously computed
  `spec_hash` changes. Pre-1.0 breakage is accepted: re-approve affected
  specs (legacy input aliases are unaffected).

### Security

- Executable validation derives engine and interpreter evidence from a
  registered read-only engine root; caller-supplied engine/environment
  hashes are no longer accepted.
- Sweep is a run-only capability with a strictly smaller authorization
  surface than apply+run; one sweep token covers only the bound,
  deterministic enumerated plan.

### Added

- **Tool-surface contract** — unified `{"status": "ok", ...}` /
  `{"status": "failed", ...}` envelope, exit-code matrix
  (0/2/3/4), `actions --json` machine-readable schema, inline-JSON
  arguments, honest `BTAG-CLI-IO` labeling.
- **Eval-first verification** — 23-task deterministic scripted-host suite
  in CI, opt-in LLM-in-the-loop gate, payload versioning with golden hash
  and changelog.
- **Parameter sweep** — `sweep prepare / run / report` with sealed
  SweepPlans, a dedicated approval kind, per-cell controlled runs, and
  ranked reports.
- **Transient-failure retry** — same-effect retry after timeout-class
  failures with a `retry_of` chain.
- **Extended metrics** — optional TradeAnalyzer/SQN/Calmar/VWR/
  GrossLeverage/PositionsValue block alongside the 11 required scalars.
- **Sizers** — functional `sizing` (fixed/percent) rendering.
- **Timers & cheat modes** — optional timer/cheat spec blocks with
  literal-form validator gates.
- **Indicator registry** — packaged 417-class metadata registry with
  `catalog search --kind indicator`.
- **Observability** — per-invocation traces, child stdout/stderr retention,
  `doctor --audit` state health.
- **Cross-session memory** — dataset notes and sweep parameter priors.
- **Engineering health** — single-source archetype/adapter registries,
  per-process hash caching, module splits, dead-code cleanup.
