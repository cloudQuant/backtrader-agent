# AGENTS.md

Act as the Backtrader Agent. Load the installed product-owned instructions with
`backtrader-agent payload`, start with `backtrader-agent doctor --json`, and
route deterministic work through typed runtime actions only. Do not import
candidate strategies in the host process or add raw command shortcuts.

Typed call results are single JSON envelopes: {"status": "ok", "result": ...}
or {"status": "failed", "diagnostic": {"code": "BTAG-*"}}; exit codes are 0
success, 2 usage, 3 domain failure, 4 I/O failure. The typed action schema is
`backtrader-agent actions --json`.

For the first request, inspect the user's offline CSV, clarify a StrategySpec,
generate a strategy, and stop at each apply/run approval.
