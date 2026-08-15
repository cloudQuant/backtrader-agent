# Examples

This directory holds a minimal, self-contained end-to-end fixture: an offline
OHLCV CSV, the `DataSpec` that registers it, and a canonical `StrategySpec`
that turns it into a single-data SMA-crossover scaffold. Copy these into your
own workspace and adjust paths; do not register files inside this repository.

## Files

- `prices.csv` — 12 daily OHLCV bars in the `generic_csv` native shape.
- `data-spec.json` — a `DataSpec` naming `root_id: input` and
  `relative_path: prices.csv`.
- `strategy-spec.json` — a `single_data_indicator` / `python_bundle` spec.
  Its `dataset_id` is a placeholder (`ds_0000…`); replace it with the real id
  returned by `data register` before approving the spec.

## Walkthrough

Assume a dedicated state root and a registered
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader) engine root.
The engine root is a directory containing `backtrader/__init__.py` and
`backtrader/version.py` (a source checkout, or the installed `site-packages`
directory). `backtrader-agent backtrader check` verifies the current interpreter
and `backtrader-agent doctor --json` reports registered engine roots.

```bash
STATE=/path/to/workspace/.backtrader-agent
EXAMPLES=/path/to/backtrader-agent/examples

# 1. Register the engine and the directory holding prices.csv as opaque roots.
backtrader-agent --state-root "$STATE" roots register \
  --id engine --kind engine --path /path/to/cloudquant-backtrader
backtrader-agent --state-root "$STATE" roots register \
  --id input --kind dataset --path "$EXAMPLES"

# 2. Create the session before any command refers to session-001.
backtrader-agent --state-root "$STATE" session create --session-id session-001

# 3. Inspect and register the offline data; capture dataset_id.
backtrader-agent --state-root "$STATE" data inspect --spec "$EXAMPLES/data-spec.json"
backtrader-agent --state-root "$STATE" data register \
  --session-id session-001 --spec "$EXAMPLES/data-spec.json"

# 4. Put the returned dataset_id into strategy-spec.json, then approve the spec.
backtrader-agent --state-root "$STATE" spec \
  --session-id session-001 --approve --file strategy-spec.json

# 5. Render, validate, prepare, approve, apply, approve, run.
backtrader-agent --state-root "$STATE" draft \
  --session-id session-001 --spec strategy-spec.json \
  --dataset-manifest <dataset-manifest-from-step-2>
# ...continue with validate / changes prepare / approval / changes apply /
#    approval / run, each stopping for explicit approval as documented in the
#    top-level README.
```

See the top-level [README.md](../README.md) for the full required workflow and
the two-stage approval model. See [references/current-fork-rules.md](../references/current-fork-rules.md)
for what the renderer does and does not translate from the spec.
