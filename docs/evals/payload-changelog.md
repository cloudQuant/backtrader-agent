# Payload changelog

Every content change to `src/backtrader_agent/resources/agent-payload.md`
(and its byte-identical mirror `SKILL.md`) MUST:

1. bump the `version` line in the payload,
2. update `EXPECTED_PAYLOAD_SHA256` in `tests/test_payload_contract.py`,
3. add an entry here recording the motivation and the corresponding eval
   baseline.

## 13.0.2 — 2026-08-16

**Motivation.** Empirical correction driven by the deterministic eval harness
(Task 10): two recovery-table rows described recoveries the runtime rejects
from the states the rows are reached in, which would strand a host following
the table. The scripted-host tasks assert the verified paths and are the
executable ground truth.

**Changes.**

- BTAG-TOKEN-EXPIRED: "run `validate` again for a fresh validation token"
  fails from APPLY_PREPARED with BTAG-PROVENANCE-BINDING (validation requires
  DRAFT_READY, and a fresh token would also break the prepare session
  evidence). The row now matches the Error-handling section exactly: a failed
  or stale token requires a new validation/approval cycle; from
  APPLY_PREPARED, re-run the full approval cycle (`changes prepare` →
  `approval request` → `approval grant` → `changes apply`).
- BTAG-CHANGE-PREIMAGE: "prepare a fresh change set" fails with
  BTAG-CHANGE-SESSION (the session is APPLY_PREPARED and bound to the first
  manifest hash). The row now says: stop and report the external
  modification; optionally restore the expected preimage and re-apply the
  same manifest under the same idempotency key; never overwrite and never
  prepare a fresh change set against a tampered target silently.
- Payload contract test: version regex pinned to 13.0.2.

**Eval baseline.** Task 10 suite: 23/23 scripted-host tasks pass
(`python scripts/run_evals.py`), including
`inject-expired-token` (BTAG-TOKEN-EXPIRED + full-cycle recovery) and
`inject-preimage` (BTAG-CHANGE-PREIMAGE + restore-and-reapply recovery).
This entry pins the golden SHA-256
`ddaa0ee19c75cbbc5d054e038bfd7d9fb62f96108b99e0e81e0283be9bcb1df4`.

## 13.0.1 — 2026-08-16

**Motivation.** Post-review correction of the 13.0.0 compression rules: the
"safe to summarize" list named artifacts whose full bodies have no typed
re-fetch command (approval grants, the prepared change manifest, the
applied-artifact record), and it contradicted the "never drop" list, which
names pending approval grants. A host that summarized a pending one-time
change/run token or a manifest a later step still consumes would strand the
pipeline.

**Changes.**

- Compression rules: grants removed from the summarizable list (covered by
  the consumed/expired-token rule); the prepared change manifest is
  summarizable only after `changes apply` consumes it; the DatasetManifest,
  the validation token, and the applied-artifact record only after the run
  completes; the approved StrategySpec only after `draft` renders. Re-fetch
  claims now name only the commands that actually exist (`runs list`,
  `report`, `data list`, `session status`), and the never-drop list names
  pending approval requests and their grants explicitly.
- Worked trace steps 5-6: step 6 now passes the "full spec result from
  step 5" to `draft --spec`, and step 5 states that the result is the full
  approved StrategySpec whose spec_hash field is the approved hash.
- Payload contract test: removed the dead `--help` escape in the menu-row
  scan (the regex cannot match tokens that start with a hyphen) and pinned
  the version regex to 13.0.1.

**Eval baseline.** None yet (unchanged from 13.0.0). This entry pins the
golden SHA-256
`1a260b64e214fde0b4d7e4eb4bcfbf41cfff6a54676ca9959a42aa68218c46e1`.

## 13.0.0 — 2026-08-16

**Motivation.** R12/R13: the previous payload routed intents to bare action
names with zero executable examples and no failure manual, so hosts could not
drive the full typed pipeline without reading source. Phase 0 (Tasks 1–3)
changed the CLI surface (success envelope `{"status": "ok", "result": ...}`,
exit codes 0/2/3/4, inline-JSON/@file arguments) and the payload predated all
of it.

**Changes.**

- Added the `version: "13.0.0"` header with the bump/hash/changelog
  discipline.
- Added a Protocol section documenting the success/failure envelope, exit
  codes, inline-JSON/@file argument rules (including the two inline-only
  exceptions), and the fixed `--state-root` convention.
- Added a 14-step worked trace covering doctor → roots register → session
  create → data list/inspect/register → spec --approve → draft → validate →
  changes prepare → approval request/grant (change) → changes apply →
  run-subject → approval request/grant (run) → run → report, with minimal
  DataSpec/StrategySpec JSON and per-step field handoffs. Every command was
  executed verbatim against the current CLI before being recorded.
- Added the BTAG recovery table: CLI input/IO/JSON, token/approval expiry and
  consumption, session state transition/terminal/journal recovery, change
  preimage/source-hash/rollback/idempotency, run timeout/failed/subject
  mismatch/unknown, data/spec/validation/report diagnostics.
- Added context compression rules (hash/token-pinned artifacts may be
  summarized and re-fetched; draft paths, unconsumed tokens, pending request
  ids, idempotency keys, and next-command JSON must never be dropped) and the
  NW dataset-reuse rule (`data list` first; only new data goes through
  `data register`).
- Mirrored the rewrite byte-for-byte to the repository-root `SKILL.md` and
  added `cli.PAYLOAD_PATH` so the dispatch payload branch and the contract
  tests share one path.

**Eval baseline.** None yet — the R9 scripted-host harness and the R11 LLM
gate land in Tasks 9–11. This entry pinned golden SHA-256
`6eeac4ed5282ec7a9ddd132e565d7522ead6ae6c7e3f8dd8f3c32a672706ac23`
(superseded by 13.0.1); baseline pass@1/pass@3 scores will be recorded here
when the LLM gate first runs.
