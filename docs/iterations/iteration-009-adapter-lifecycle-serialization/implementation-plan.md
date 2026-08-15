# Iteration 009 Adapter Lifecycle Serialization Implementation Plan

**Goal:** Serialize apply-time adapter installation and removal per external target and host.

## Task 1: Capture lifecycle races

**Files:** `tests/test_installer_concurrency.py`

- [x] Add a spawn-safe concurrent uninstall race synchronized at the real first unlink boundary.
- [x] Demonstrate the old raw `FileNotFoundError` outcome and assert final target evidence.
- [x] Cover same/different host lock behavior, diagnostics, and preview zero-write.

## Task 2: Add apply lifecycle locking

**Files:** `src/backtrader_agent/installer.py`

- [x] Add stable target/host lock helpers using the shared locking abstraction.
- [x] Refactor apply install/uninstall to recheck and mutate only inside the shared lock.
- [x] Preserve preview and manifest/hash safety contracts.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A4 focused and existing installer regressions.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin final convergence audit.
