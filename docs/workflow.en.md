# Workflow

The P0 workflow is a two-stage write/run pipeline: nothing is written to your
workspace without a change approval, and nothing runs without a separate run
approval. Every step returns the uniform envelope
(`{"status": "ok", "result": ...}`); file-typed arguments accept inline JSON,
`@file` references, or plain paths.

## 1. Prepare the environment

```bash
backtrader-agent doctor --json
backtrader-agent backtrader check
backtrader-agent --state-root $STATE roots register --id workspace --kind workspace --writable --path /path/to/workspace
backtrader-agent --state-root $STATE roots register --id prices --kind dataset --path /path/to/offline-data
backtrader-agent --state-root $STATE roots register --id engine --kind engine --path /path/to/cloudquant-backtrader
backtrader-agent --state-root $STATE session create --session-id session-001
```

## 2. Register data

```bash
backtrader-agent --state-root $STATE data inspect --spec data-spec.json
backtrader-agent --state-root $STATE data register --session-id session-001 --spec data-spec.json
```

Six offline adapters are supported: `generic_csv`, `backtrader_csv`,
`yahoo_csv`, `mt5_csv`, `pandas`, `pandas_custom_lines`. Registration writes a
canonical CSV into the content-addressed store and emits a `DatasetManifest`.
Already registered datasets can be reused across sessions:

```bash
backtrader-agent --state-root $STATE data list
```

## 3. Validate the StrategySpec

```bash
backtrader-agent --state-root $STATE spec --session-id session-001 --approve --file strategy-spec.json
```

Seven archetypes: `single_data_indicator`, `multi_indicator_system`,
`multi_asset_allocation`, `multi_timeframe`, `pairs_spread`, `order_risk`,
`precomputed_ml` — each renderable as `single_test` or `python_bundle`.

## 4. Search the packaged snapshot and render a draft

```bash
backtrader-agent catalog search --query "multi timeframe clock" --top-k 3
backtrader-agent --state-root $STATE draft --session-id session-001 \
  --spec strategy-spec.json --dataset-manifest dataset-manifest.json
```

## 5. Validate the draft (AST only, never imported)

```bash
backtrader-agent --state-root $STATE validate \
  --artifact-manifest artifact-manifest.json --draft-root $DRAFT_ROOT \
  --session-id session-001 --dataset-hash $DATASET_HASH \
  --engine-root-id engine
```

## 6. Prepare and approve the change

```bash
backtrader-agent --state-root $STATE changes prepare --session-id session-001 \
  --draft-root $DRAFT_ROOT --files '[{"source": "...", "target": "..."}]' \
  --target-root-id workspace --validation-token $VALIDATION_TOKEN
backtrader-agent --state-root $STATE approval request --kind change \
  --subject-hash $SUBJECT --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE changes apply --manifest $CHANGE_MANIFEST \
  --change-token $CHANGE_TOKEN --idempotency-key key-1
```

## 7. Approve and run

```bash
backtrader-agent --state-root $STATE approval request --kind run \
  --subject-hash $RUN_SUBJECT --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE run --applied-artifact $APPLIED \
  --dataset-manifest dataset-manifest.json --validation-token $VALIDATION_TOKEN \
  --run-token $RUN_TOKEN --mode runonce --idempotency-key run-1
```

Modes: `runonce` and `runnext`. A transient failure (timeout) marks the
session `FAILED` with `retry_eligible=true`; recover by requesting and
granting a fresh run approval for the same effect — the new RunManifest
records a `retry_of` chain. Non-transient failures must go through `repair`
with a revised spec.

## 8. Read reports

```bash
backtrader-agent --state-root $STATE report --run-id $RUN_ID --format markdown
backtrader-agent --state-root $STATE compare --left-run-id $R1 --right-run-id $R2
backtrader-agent --state-root $STATE runs list
```

## Parameter sweep

```bash
backtrader-agent --state-root $STATE sweep prepare --session-id session-001 \
  --spec strategy-spec.json --dataset-manifest dataset-manifest.json \
  --param-grid '{"fast_period": [10, 20], "slow_period": [30, 40]}' \
  --engine-root-id engine
backtrader-agent --state-root $STATE approval request --kind sweep \
  --subject-hash $PLAN_HASH --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE sweep run --sweep-id $SWEEP_ID \
  --token $SWEEP_TOKEN --max-cells 100 --timeout-per-cell 120
backtrader-agent --state-root $STATE sweep report --sweep-id $SWEEP_ID
```

Sweep is a **run-only** capability: cells execute from private renderer-owned
drafts and never write your workspace. One sweep approval covers the whole
deterministic enumerated plan; each cell lands its own immutable
RunManifest/RunResult. The top-5 parameter priors per archetype are recorded
in the cross-session memory store.

## Recovery and state

```bash
backtrader-agent --state-root $STATE session status --session-id session-001
backtrader-agent --state-root $STATE session recover --session-id session-001
backtrader-agent --state-root $STATE doctor --audit
```

Every transition is journaled in a strictly ordered, hash-chained session
log; checkpoints are atomic; recovery accepts only a verified prefix.
`doctor --audit` reports torn journals, `RUNNING` orphans, CAS violations,
and expired-approval accumulation. Every CLI invocation is traced into
`<state>/trace/*.jsonl` (argument values are hashed, never raw).
