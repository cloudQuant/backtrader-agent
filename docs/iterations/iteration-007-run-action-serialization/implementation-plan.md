# Iteration 007 Run Action Serialization Implementation Plan

**Goal:** Prevent duplicate controlled child launches for concurrent callers of the same run
idempotency key while preserving recovery and replay.

## Task 1: Capture the live same-key run race

**Files:** `tests/test_run_concurrency.py`

- [x] Build a spawn-safe probe runner/state that reaches the real child invocation boundary.
- [x] Demonstrate that the old runner lets a second same-key worker enter that boundary.
- [x] Cover replay, different-request conflict, lock isolation, diagnostics, and resume compatibility.

## Task 2: Serialize the run action

**Files:** `src/backtrader_agent/runner.py`

- [x] Add stable, digest-derived run action locks using the shared locking abstraction.
- [x] Hold the lock across action check, token/session mutation, child execution, persistence, and completion.
- [x] Preserve action schemas and bounded same-key wait semantics for legal child timeouts.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A4 focused and existing run-resume tests.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
