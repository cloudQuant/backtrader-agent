"""Single-source registry of the seven P0 strategy archetypes (R6).

Every archetype enumeration, template source, and allowed-parameter list in
contracts.py / scaffold.py / catalog.py is derived from this module, so the
seven P0 values and their rendered strategy bodies are defined exactly once.
"""

from typing import Dict, FrozenSet, NamedTuple, Tuple


class ArchetypeSpec(NamedTuple):
    contract_value: str
    template: Tuple[str, str]
    allowed_params: Tuple[str, ...]


ARCHETYPE_SPECS: Dict[str, ArchetypeSpec] = {
    "single_data_indicator": ArchetypeSpec(
        contract_value="single_data_indicator",
        template=(
            """        self.fast = bt.ind.SMA(self.data.close, period=self.p.fast_period)
        self.slow = bt.ind.SMA(self.data.close, period=self.p.slow_period)""",
            """        if not self.position and self.fast[0] > self.slow[0]:
            self.buy(size=1)
        elif self.position and self.fast[0] < self.slow[0]:
            self.close()""",
        ),
        allowed_params=("fast_period", "slow_period"),
    ),
    "multi_indicator_system": ArchetypeSpec(
        contract_value="multi_indicator_system",
        template=(
            """        self.fast = bt.ind.EMA(self.data.close, period=self.p.fast_period)
        self.slow = bt.ind.SMA(self.data.close, period=self.p.slow_period)
        self.rsi = bt.ind.RSI(self.data.close, period=max(2, self.p.fast_period))""",
            """        if not self.position and self.fast[0] > self.slow[0] and self.rsi[0] < 70:
            self.buy(size=1)
        elif self.position and (self.fast[0] < self.slow[0] or self.rsi[0] > 75):
            self.close()""",
        ),
        allowed_params=("fast_period", "slow_period"),
    ),
    "multi_asset_allocation": ArchetypeSpec(
        contract_value="multi_asset_allocation",
        template=(
            """        self.execution_data = self.datas[0]
        self.signal_data = self.datas[1] if len(self.datas) > 1 else self.datas[0]
        self.signal_sma = bt.ind.SMA(self.signal_data.close, period=self.p.fast_period)""",
            """        if not self.getposition(self.execution_data) and self.signal_data.close[0] > self.signal_sma[0]:
            self.buy(data=self.execution_data, size=1)
        elif self.getposition(self.execution_data) and self.signal_data.close[0] < self.signal_sma[0]:
            self.close(data=self.execution_data)""",
        ),
        allowed_params=("fast_period",),
    ),
    "multi_timeframe": ArchetypeSpec(
        contract_value="multi_timeframe",
        template=(
            """        self.execution_data = self.datas[0]
        self.higher_timeframe = self.datas[1] if len(self.datas) > 1 else self.datas[0]
        self.higher_sma = bt.ind.SMA(self.higher_timeframe.close, period=self.p.fast_period)""",
            """        if not self.position and self.higher_timeframe.close[0] > self.higher_sma[0]:
            self.buy(data=self.execution_data, size=1)
        elif self.position and self.higher_timeframe.close[0] < self.higher_sma[0]:
            self.close(data=self.execution_data)""",
        ),
        allowed_params=("fast_period",),
    ),
    "pairs_spread": ArchetypeSpec(
        contract_value="pairs_spread",
        template=(
            """        self.leg_a = self.datas[0]
        self.leg_b = self.datas[1] if len(self.datas) > 1 else self.datas[0]
        self.spread = self.leg_a.close - self.leg_b.close
        self.spread_mean = bt.ind.SMA(self.spread, period=self.p.fast_period)""",
            """        deviation = self.spread[0] - self.spread_mean[0]
        if not self.getposition(self.leg_a) and deviation < 0:
            self.buy(data=self.leg_a, size=1)
            if self.leg_b is not self.leg_a:
                self.sell(data=self.leg_b, size=1)
        elif self.getposition(self.leg_a) and deviation >= 0:
            self.close(data=self.leg_a)
            if self.leg_b is not self.leg_a:
                self.close(data=self.leg_b)""",
        ),
        allowed_params=("fast_period",),
    ),
    "order_risk": ArchetypeSpec(
        contract_value="order_risk",
        template=(
            """        self.signal = bt.ind.SMA(self.data.close, period=self.p.fast_period)
        self.atr = bt.ind.ATR(self.data, period=max(2, self.p.fast_period))
        self.entry_price = None""",
            """        if not self.position and self.data.close[0] > self.signal[0]:
            self.buy(size=1)
            self.entry_price = float(self.data.close[0])
        elif self.position:
            stop = self.entry_price - 2.0 * float(self.atr[0])
            if self.data.close[0] < self.signal[0] or self.data.close[0] <= stop:
                self.close()
                self.entry_price = None""",
        ),
        allowed_params=("fast_period",),
    ),
    "precomputed_ml": ArchetypeSpec(
        contract_value="precomputed_ml",
        template=(
            """        if not hasattr(self.data, "signal"):
            raise RuntimeError("registered dataset must expose the precomputed signal line")
        self.model_signal = self.data.signal""",
            """        if not self.position and self.model_signal[0] > 0:
            self.buy(size=1)
        elif self.position and self.model_signal[0] <= 0:
            self.close()""",
        ),
        allowed_params=(),
    ),
}

ARCHETYPE_IDS: FrozenSet[str] = frozenset(ARCHETYPE_SPECS)
