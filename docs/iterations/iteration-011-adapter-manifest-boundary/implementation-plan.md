# Iteration 011 Adapter Manifest Boundary Implementation Plan

**Goal:** Make adapter install/uninstall treat manifest content and symlinked paths as untrusted target state.

## Task 1: Capture unsafe target-state cases

**Files:** `tests/test_installer_concurrency.py`, `tests/test_runner_installer_audit.py`

- [x] Add the legacy path-traversal manifest red proof with a target-external victim marker.
- [x] Add manifest structure/symlink and same-content adapter symlink behavior tests.
- [x] Preserve existing real spawn lifecycle race coverage.

## Task 2: Validate before planning removals

**Files:** `src/backtrader_agent/installer.py`

- [x] Add strict host allowlist manifest parsing and stable malformed-manifest diagnostics.
- [x] Make install preview and uninstall reject symlink/nonregular paths before file reads/unlinks.
- [x] Preserve valid install, repeat, interrupted-uninstall and lifecycle-lock behavior.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`, `acceptance.md`

- [x] Pass focused installer, concurrency and existing adapter regression tests.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record evidence, mark the plan complete, then resume final convergence audit.
