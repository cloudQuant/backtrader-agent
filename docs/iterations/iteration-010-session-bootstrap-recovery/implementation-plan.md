# Iteration 010 Session Bootstrap Recovery Implementation Plan

**Goal:** Safely resume a session creation interrupted after publishing its empty journal but before its manifest.

## Task 1: Capture the crash window

**Files:** `tests/test_tokens_changes_sessions.py`

- [x] Add a spawn worker that exits at the real manifest publish boundary after journal persistence.
- [x] Make the retry assertion red on the current implementation and assert final manifest/journal evidence.
- [x] Cover unsafe nonempty and symlink bootstrap journal rejection without mutation.

## Task 2: Classify bootstrap state under the existing session lock

**Files:** `src/backtrader_agent/sessions.py`

- [x] Reuse a regular empty journal when the manifest is absent.
- [x] Refuse unsafe residual journal forms with `BTAG-SESSION-BOOTSTRAP`.
- [x] Preserve complete-session idempotency, concurrent creation and transition/recovery semantics.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`, `acceptance.md`

- [x] Pass focused and existing session concurrency/regression tests.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact evidence, mark this plan complete, then repeat final convergence audit.
