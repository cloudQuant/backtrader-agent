# Changelog

All notable changes to `backtrader-agent` are documented here. The product
follows the offline-first, deterministic, independence-strict contract
described in [README.md](README.md) and [SECURITY.md](SECURITY.md).

## [Unreleased]

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

### Added

- Listing commands: `data list`, `session list`, `runs list`, and
  `engine --list` enumerate registered datasets, sessions, run results, and
  engine roots with validity status.
- `doctor` now reports registered engine roots and a hint when none is
  registered.
- CI workflow (`.github/workflows/ci.yml`) running unit tests, the independence
  audit, doctor, manifest freshness, and the acceptance matrix across Python
  3.9/3.11/3.12.
- `examples/` with an offline CSV, `DataSpec`, `StrategySpec`, and a walkthrough.
- `SECURITY.md`, `CONTRIBUTING.md`, and this changelog.
- `.gitignore` now ignores the `.backtrader-agent/` runtime state root.

### Changed

- Documented renderer scope: the P0 renderer maps a `StrategySpec` to one of
  seven fixed archetype templates parameterized by `archetype`,
  `output_profile`, and numeric defaults. The `entry`, `exit`, `sizing`, and
  `risk` fields are validated and recorded in the spec hash but are not
  translated into executable logic. See
  [references/current-fork-rules.md](references/current-fork-rules.md).

## [0.1.0] - 2026-07-31

Initial P0 release: independent, offline-first Backtrader strategy-authoring
agent runtime. See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) for the
implemented scope, verification evidence, and deferred work.
