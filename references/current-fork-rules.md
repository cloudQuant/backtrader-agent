# Current-fork authoring rules

- A direct `bt.Strategy` subclass may read `self.p`, `self.datas`, broker, and
  data aliases in its own `__init__` without calling `super().__init__()`. The
  current fork initializes these before dispatching the user initializer.
- A custom parent strategy or cooperative mixin may own initialization; follow
  that class's MRO contract and call `super()` where required.
- Multi-data and multi-timeframe indicators must be bound to their actual input
  feed/clock. Do not silently align with forward-fill.
- Build indicators in `__init__`; trade in `next`. Avoid future indexing and
  incomplete higher-timeframe bars.
- Use only offline registered data and analyzers included in the generated
  profile. Live stores/brokers are outside P0.

## Renderer scope (what the spec does and does not drive)

The P0 renderer is a deterministic scaffold selector, not a free-form code
generator. It translates a StrategySpec into one of seven fixed archetype
templates, parameterized only by the declared `archetype`, `output_profile`,
and numeric parameter defaults (notably `fast_period` / `slow_period`).

The spec fields `entry`, `exit`, and `risk` are **contract and documentation
only**: they are validated for presence and shape and recorded in the immutable
spec hash, but they are **not translated into executable strategy logic**. The
`sizing` field is functional in a limited way: `{method: fixed, fixed_size: n}`
and `{method: percent, percent: p}` render a fixed
`cerebro.addsizer(bt.sizers.FixedSize, stake=n)` /
`cerebro.addsizer(bt.sizers.PercentSizer, percents=p)` assembly, and entry
orders delegate their stake to that sizer; without a `sizing` block (or with
`null`) the strategy keeps the fork's default `FixedSize(stake=1)`. The actual
`next()` signal logic comes entirely from the chosen archetype template. An
autonomous patch synthesizer that turns natural-language entry/exit rules into
code is deferred (see IMPLEMENTATION_REPORT.md). To change trade logic today,
select a different `archetype` or revise parameters; do not expect prose
`entry`/`exit` text to alter the generated `next()`.

