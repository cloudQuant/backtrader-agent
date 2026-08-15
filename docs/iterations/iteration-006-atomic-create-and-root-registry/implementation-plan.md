# Iteration 006 Atomic Create and Root Registry Implementation Plan

**Goal:** Eliminate silent create-only overwrite and root-registry lost updates under local
multi-process contention.

## Task 1: Capture the unsafe persistence races

**Files:** `tests/test_persistence_concurrency.py`

- [x] Add spawn-safe publish-barrier workers for bytes/JSON create-only writes.
- [x] Add spawn-safe RootRegistry different-ID registration workers and lock diagnostics.
- [x] Demonstrate old no-clobber overwrite and lost registry update behavior.

## Task 2: Harden the shared persistence primitives

**Files:** `src/backtrader_agent/canonical.py`, `src/backtrader_agent/roots.py`

- [x] Publish create-only output with an atomic no-replace operation after staging/fsync.
- [x] Add a stable RootRegistry lock across the full register read-modify-write sequence.
- [x] Preserve existing upsert behavior and public RootRegistry contracts.

## Task 3: Verify compatibility and release

**Files:** `manifest.json`, `src/backtrader_agent/resources/distribution-manifest.json`,
`acceptance.md`

- [x] Pass A1–A5 focused tests and existing persistence regression.
- [x] Regenerate manifests and pass base, py38, py312, lint, audit, doctor and clean-wheel gates.
- [x] Record exact acceptance evidence, mark this plan complete, then begin the next audit.
