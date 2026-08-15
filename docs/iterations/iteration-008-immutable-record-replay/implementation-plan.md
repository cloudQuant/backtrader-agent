# Iteration 008 Immutable Record Replay Implementation Plan

**Goal:** Convert same-content immutable write races into verified idempotent replay without
weakening create-only no-clobber protection.

## Task 1: Capture user-visible races

**Files:** `tests/test_immutable_record_concurrency.py`

- [x] Add four spawn-safe `os.link` publish-boundary races for dataset, artifact, bound record, and installer.
- [x] Demonstrate old callers surface `BTAG-WRITE-EXISTS` despite identical intended content.
- [x] Cover exact helper same/mismatch/symlink behavior and final persisted evidence.

## Task 2: Add exact replay primitive and migrate callers

**Files:** `src/backtrader_agent/canonical.py`, `data.py`, `scaffold.py`, `tokens.py`, `installer.py`

- [x] Implement canonical bytes/json create-or-verify helpers with caller-owned conflict diagnostics.
- [x] Migrate dataset, artifact, bound record, and installer apply paths.
- [x] Preserve installer preview/action reporting and existing signature/CAS contracts.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A5 focused tests and existing caller regressions.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
