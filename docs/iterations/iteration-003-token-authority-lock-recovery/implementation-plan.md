# Iteration 003 TokenAuthority Lock Recovery Implementation Plan

**Goal:** Make token secret bootstrap and approval mutations process-safe and crash-recoverable using the same stable OS-lock semantics as sessions.

## Task 1: Capture the unsafe behavior

**Files:** `tests/test_token_concurrency.py`, `tests/test_tokens_changes_sessions.py`

- [x] Add spawn-safe secret-bootstrap and approval-lock workers.
- [x] Demonstrate the old secret create-only race and legacy approval lock blockage.

## Task 2: Centralize durable file locking

**Files:** `src/backtrader_agent/locking.py`, `src/backtrader_agent/sessions.py`, `src/backtrader_agent/tokens.py`

- [x] Implement cross-platform stable OS file locking with caller-owned diagnostic codes.
- [x] Migrate SessionStore without changing its lock error contract.
- [x] Use a secret lock for bootstrap and a stable per-request approval lock without unlink-on-release.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`, `acceptance.md`

- [x] Pass A1–A6 focused tests and existing approval/session regression.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor, and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
