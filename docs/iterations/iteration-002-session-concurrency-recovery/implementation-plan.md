# Iteration 002 Session Concurrency and Recovery Implementation Plan

**Goal:** Serialize all same-session state operations across local processes without changing the session wire format or weakening crash recovery.

**Architecture:** A stable per-session OS lock file wraps public session operations. Unlocked helpers retain the existing journal/checkpoint algorithm so nested operations never try to acquire the same non-reentrant file lock twice.

## Task 1: Capture the current cross-process failure

**Files:** `tests/test_tokens_changes_sessions.py`

- [x] Add spawn-safe module-level workers and a barrier-driven same-session race.
- [x] Verify it fails on the current implementation by observing duplicate/competing sequence evidence or more than one apparent success.

## Task 2: Add a cross-platform session lock boundary

**Files:** `src/backtrader_agent/sessions.py`, `tests/test_tokens_changes_sessions.py`

- [x] Add stable lock-path and context-manager primitives with POSIX and Windows standard-library implementations.
- [x] Convert `create`, `load`, `transition`, `recover`, `cancel`, and `archive` to locked public methods plus unlocked helpers.
- [x] Verify A1–A6 focused tests pass.

## Task 3: Run regression and acceptance

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`, `acceptance.md`

- [x] Add a failing cache-fixture contract test, then exclude `.mypy_cache` and `.ruff_cache` consistently from manifest generation and clean-wheel source copying.
- [x] Regenerate manifests without local tool caches.
- [x] Run base, 3.8, 3.12, lint, audit, doctor, and clean-wheel acceptance gates.
- [x] Record exact outcomes in `acceptance.md`, mark this plan complete, then begin the next audit.
