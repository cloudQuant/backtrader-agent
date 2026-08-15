# Iteration 005 Global Idempotency Serialization Implementation Plan

**Goal:** Make a ChangeManager idempotency key a state-root-wide serialization boundary before
any target-root mutation occurs.

## Task 1: Capture cross-root key reuse

**Files:** `tests/test_change_concurrency.py`

- [x] Add spawn-safe workers for distinct roots that deliberately reuse one key.
- [x] Demonstrate that the old implementation lets the second worker reach target replacement.
- [x] Add action-key path isolation and lock diagnostic coverage.

## Task 2: Serialize global action identity

**Files:** `src/backtrader_agent/changes.py`

- [x] Add digest-derived stable action-key lock paths with a distinct diagnostic code.
- [x] Acquire action-key lock before target-root lock for the mutable apply tail.
- [x] Ensure a conflicting key is rejected before token consume and target mutation.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A4 focused tests and existing regression.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
