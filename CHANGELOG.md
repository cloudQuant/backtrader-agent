# Changelog

All notable changes to `backtrader-agent` are documented here. The product
follows the offline-first, deterministic, independence-strict contract
described in [README.md](README.md) and [SECURITY.md](SECURITY.md).

## [0.2.0] - 2026-08-16

Iteration 013: agentic engineering (tool-surface contract, eval-first
verification, observability, memory) plus quant capability expansion
(parameter sweep, sizers, timers/cheat, extended metrics, indicator
registry).

### Breaking

- **Spec-hash compatibility.** The canonical `StrategySpec` now always
  includes the `timers` and `cheat` fields, so every previously computed
  `spec_hash` changes. Pre-1.0 breakage is accepted: re-approve affected
  specs (the legacy input aliases are unaffected).

### Security

- Executable validation now derives its engine and interpreter evidence from a
  registered read-only engine root. Caller-supplied engine/environment hashes
  are no longer accepted, and every run rechecks the engine package tree,
  execution environment, and profile dependencies before consuming its run
  approval.
- Sweep is a run-only capability with a strictly smaller authorization
  surface than apply+run; a single `sweep` approval token covers only the
  bound, deterministic enumerated plan. Red tests cover forged plans, token
  replay, and cross-session reuse.

### Added

- **Unified success envelope.** Every successful invocation prints
  `{"status": "ok", "result": ...}`; failures print
  `{"status": "failed", "diagnostic": {...}}`; all `--json` output is always
  `json.loads`-parseable.
- **Exit-code contract.** `0` success, `2` usage error, `3` BTAG domain
  failure, `4` OS I/O failure (`BTAG-CLI-IO`); OSError no longer masquerades
  as `BTAG-CLI-INPUT`.
- **Machine-readable action schema.** `backtrader-agent actions --json`
  reflects every typed subcommand; the same content ships as the packaged
  `actions-v1.json` resource validated against `actions-v1.schema.json`.
- **Inline JSON inputs.** All `--*-file` arguments accept a file path, an
  inline JSON object string, or `@file` reference.
- **Deterministic eval harness.** `scripts/run_evals.py` drives 23
  scripted-host tasks (7 archetype pipelines, 6 adapter registrations,
  failure injection, idempotent replay) with hash/schema/exit-code-only
  graders; it is the default CI gate. `scripts/eval_llm_loop.py` adds an
  opt-in LLM-in-the-loop pass@1/pass@3 gate that never runs in CI.
- **Parameter sweep.** `sweep prepare` (immutable `SweepPlan` with
  deterministic cell enumeration and bounds checking), `approval
  request --kind sweep` + `approval grant --confirm`, `sweep run`
  (`--max-cells`, `--timeout-per-cell`, per-cell RunManifest/RunResult), and
  `sweep report` (`sweep-result-v1`, passed cells ranked by `final_value`
  descending). Sweep v1 sweeps numeric parameters only.
- **Transient retry.** A run failing with the whitelisted transient code
  `BTAG-RUN-TIMEOUT` marks the session `retry_eligible`; `FAILED →
  RUN_APPROVED` re-runs the same approved effect and records the `retry_of`
  chain. Non-transient failures, changed effects, and terminal sessions must
  `repair`.
- **Host observability.** Every CLI invocation appends a trace line to
  `<state>/trace/<session-id>.jsonl` or `<state>/trace/global.jsonl`
  (command, argument hashes, elapsed time, exit code); controlled runs keep
  child `stdout.log`/`stderr.log` on success as well as failure.
- **State audit.** `doctor --audit` / `--audit-deep` report structured
  diagnostics for torn journals, orphaned `RUNNING` sessions, CAS violations,
  stale approvals, and trace/memory health; listing commands report skip
  counts.
- **Cross-session memory.** `memory list`/`memory note` manage the schema-
  bound `memory/datasets.json` and `memory/params.json` stores (dataset
  notes and top-5 sweep parameter priors per archetype).
- **Extended metrics.** Optional `RunResult.extended_metrics` block:
  TradeAnalyzer subset, SQN, Calmar, VWR, GrossLeverage, PositionsValue. The
  11 required scalars are unchanged; a missing analyzer normalizes to `null`
  sub-items.
- **Sizers.** `sizing: {method: fixed, fixed_size: n}` and
  `{method: percent, percent: p}` render `cerebro.addsizer` assemblies; the
  validator allowlists the two sizer classes under restricted construction.
- **Indicator registry.** Packaged `indicator-registry-v1.json` (module,
  class, and parameter names extracted statically from the fork corpus,
  `source_available=false`); `catalog search --kind indicator` searches it.
- **Timers/cheat.** Optional `timers`/`cheat` spec blocks (default off)
  render `self.add_timer` assemblies and the fork's
  `cheat_on_open`/`set_coo`/`set_coc` broker APIs under literal-argument
  validator gates.
- Listing commands: `data list`, `session list`, `runs list`, and
  `engine --list` enumerate registered datasets, sessions, run results, and
  engine roots with validity status.
- CI workflow (`.github/workflows/ci.yml`) running unit tests, the
  independence audit, doctor, the deterministic eval suite, manifest
  freshness, and the acceptance matrix across Python 3.9/3.11/3.12.
- `examples/` with an offline CSV, `DataSpec`, `StrategySpec`, and a walkthrough.
- `SECURITY.md`, `CONTRIBUTING.md`, and this changelog.
- `.gitignore` now ignores the `.backtrader-agent/` runtime state root.

### Changed

- Controlled execution extras now use the direct
  `cloudQuant/backtrader` Git dependency. Added `backtrader check|ensure`,
  source-aware doctor output, missing-only bootstrap, and warnings for an
  existing Backtrader installation that is not verifiably from that repository.
- Packaging now declares MIT consistently with `LICENSE`, exposes explicit
  `backtest`, `single-test`, and `test` extras, and CI verifies Python
  3.8/3.9/3.11/3.12 from that install contract. The full acceptance matrix runs
  once on Python 3.12 after the matrix succeeds.
- The example walkthrough creates its session before registering data.
- Single-source registries: the 7 archetypes and 6 adapters are defined in
  exactly one place (`archetypes.py`/`adapters.py`); the `canonical_csv_v1`
  allowlist inconsistency is gone.
- Cache discipline: in-process memoization of engine-tree/probe/feed hashes;
  catalog verifies one manifest-level snapshot hash per call; no cross-process
  caching of security-sensitive hashes.
- `runner.py`/`changes.py` split into <400-line submodules; dead code removed
  (`doctor --json` effective, `catalog refresh` snapshot consumed by
  search/inspect, stale `build/lib/` cleaned).
- Documented renderer scope: `sizing` (fixed/percent) and `timers`/`cheat`
  are now functional in the limited documented forms. The `entry`, `exit`,
  and `risk` fields remain validated and recorded in the spec hash but are
  **not** translated into executable logic. See
  [references/current-fork-rules.md](references/current-fork-rules.md).

### Fixed

- Distribution manifests were stale: the root `manifest.json` omitted
  `LICENSE` and `.gitignore`, and the package
  `resources/distribution-manifest.json` drifted after source edits. Added
  `scripts/build_manifest.py` as the single regeneration entrypoint and a CI
  check that committed manifests match a fresh build.
- `test_source_distribution_manifest_covers_every_file` counted `.git/`
  internals in a git checkout, failing `file_count`. The exclusion set now
  includes `.git`.
- The acceptance engine root defaulted to the repository grandparent, which is
  rarely a valid Backtrader source root, so all 14 end-to-end cells failed in a
  fresh checkout. Engine roots are now resolved automatically (env var, sibling
  `backtrader`/`back_trader` checkouts, then the installed `backtrader`
  package) with actionable guidance when none is found.
- The end-to-end test hardcoded the engine version `1.3.0`, failing against any
  other Backtrader. It now asserts the run manifest records the descriptor's
  actual version, making the suite portable.

## [0.1.0] - 2026-07-31

Initial P0 release: independent, offline-first Backtrader strategy-authoring
agent runtime. See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for the
implemented scope, verification evidence, and deferred work.
