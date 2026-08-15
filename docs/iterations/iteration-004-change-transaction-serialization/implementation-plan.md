# Iteration 004 Change Transaction Serialization Implementation Plan

**Goal:** Serialize all mutable apply work per target root so a live transaction cannot be
mistaken for a crashed transaction by another process.

## Task 1: Capture the concurrency failure

**Files:** `tests/test_change_concurrency.py`, `tests/test_tokens_changes_sessions.py`

- [x] Add spawn-safe workers and a deterministic live-`APPLYING` overlap fixture.
- [x] Demonstrate that the old implementation can roll back a live transaction or return an
  unsafe concurrent outcome.
- [x] Add root lock naming/error/descriptor and same-root stale-preimage coverage.

## Task 2: Serialize mutable apply work

**Files:** `src/backtrader_agent/changes.py`

- [x] Add a digest-derived stable target-root lock path using the shared lock primitive.
- [x] Hold that lock across the idempotency, consume, transaction, action-record and session
  mutation boundary without changing public schemas.
- [x] Preserve crashed-transaction recovery once the prior process no longer owns the OS lock.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A5 focused tests and existing change/token/session regression.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
