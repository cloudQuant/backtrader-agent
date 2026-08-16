import json
import subprocess
import sys
from pathlib import Path

import pytest

from backtrader_agent.catalog import SnapshotCatalog
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.errors import AgentError
from backtrader_agent.scaffold import ARCHETYPES, PROFILES, ArtifactRenderer
from backtrader_agent.validator import StrategyValidator

from helpers import strategy_spec, write_price_csv


def _sized_spec_dict(sizing):
    raw = strategy_spec("ds_" + "a" * 64)
    if sizing is None:
        raw.pop("sizing", None)
    else:
        raw["sizing"] = sizing
    return raw


def _rendered_source(tmp_path, sizing, profile="python_bundle"):
    renderer = ArtifactRenderer(tmp_path / "state")
    dataset = {
        "dataset_id": "ds_" + "a" * 64,
        "manifest_hash": "a" * 64,
        "feeds": [
            {"name": "primary", "role": "execution", "columns": {"signal": "signal"}},
            {"name": "secondary", "role": "signal", "columns": {}},
        ],
    }
    raw = _sized_spec_dict(sizing)
    raw["output_profile"] = profile
    spec = StrategySpec.from_dict(raw)
    artifact = renderer.render("session-1", spec, dataset)
    sources = [
        (Path(artifact["_draft_path"]) / item["path"]).read_text(encoding="utf-8")
        for item in artifact["files"]
        if item["path"].endswith(".py")
    ]
    return "\n".join(sources), artifact


def test_all_fourteen_scaffolds_render_and_validate_without_requiring_direct_strategy_super(
    tmp_path: Path,
) -> None:
    renderer = ArtifactRenderer(tmp_path / "state")
    validator = StrategyValidator()
    dataset = {
        "dataset_id": "ds_" + "a" * 64,
        "manifest_hash": "a" * 64,
        "feeds": [
            {"name": "primary", "role": "execution", "columns": {"signal": "signal"}},
            {"name": "secondary", "role": "signal", "columns": {}},
        ],
    }

    for archetype in ARCHETYPES:
        for profile in PROFILES:
            spec = StrategySpec.from_dict(
                strategy_spec("ds_" + "a" * 64, archetype=archetype, profile=profile)
            )
            artifact = renderer.render("session-1", spec, dataset)
            report = validator.validate_artifact(artifact)
            assert report["status"] == "passed", (
                archetype,
                profile,
                report["diagnostics"],
            )
            strategy_file = next(
                item for item in artifact["files"] if item["role"] == "strategy"
            )
            source = (Path(artifact["_draft_path"]) / strategy_file["path"]).read_text(
                encoding="utf-8"
            )
            assert "class GeneratedStrategy(bt.Strategy)" in source
            assert "super().__init__()" not in source


def test_validator_rejects_dynamic_execution_but_accepts_legacy_style_strategy() -> (
    None
):
    validator = StrategyValidator()
    legacy = """
import backtrader as bt
class LegacyStrategy(bt.Strategy):
    params = (("period", 5),)
    def __init__(self):
        self.sma = bt.ind.SMA(self.data.close, period=self.p.period)
"""
    assert validator.validate_source(legacy, "strategy.py") == []

    dangerous = legacy + "\neval('1 + 1')\n"
    diagnostics = validator.validate_source(dangerous, "strategy.py")
    assert any(item["code"] == "BTAG-SEC-DYNAMIC" for item in diagnostics)


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("import os\nos.execlp('/bin/sh', 'sh')", "BTAG-SEC-CAPABILITY"),
        ("from os import execlp\nexeclp('/bin/sh', 'sh')", "BTAG-SEC-FROM-IMPORT"),
        ("from pathlib import Path\nPath('x').read_text()", "BTAG-SEC-IMPORT"),
        ("open('secret.txt').read()", "BTAG-SEC-FILESYSTEM"),
        ("reader = open\nreader('secret.txt')", "BTAG-SEC-FILESYSTEM"),
        ("getattr(object(), '__class__')", "BTAG-SEC-REFLECTION"),
        ("import socket\nsocket.socket()", "BTAG-SEC-IMPORT"),
        (
            "from strategy_helper import os\nos.execlp('/bin/sh', 'sh')",
            "BTAG-SEC-LOCAL-IMPORT",
        ),
        ("import backtrader_agent\nbacktrader_agent.cli.main()", "BTAG-SEC-IMPORT"),
        ("import backtrader as bt\nbt.os.system('id')", "BTAG-SEC-CAPABILITY"),
        (
            "import os\nvalue = os.environ.get('HOME')",
            "BTAG-SEC-ENVIRONMENT",
        ),
    ],
)
def test_validator_denies_real_capability_escape_vectors(
    payload: str, expected_code: str
) -> None:
    source = (
        "import backtrader as bt\n"
        "class EscapeStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        pass\n"
        f"{payload}\n"
    )
    diagnostics = StrategyValidator().validate_source(source, "strategy_escape.py")
    assert any(item["code"] == expected_code for item in diagnostics), diagnostics


def test_snapshot_search_is_deterministic_and_has_provenance() -> None:
    catalog = SnapshotCatalog()
    first = catalog.search("multi timeframe clock", top_k=3)
    second = catalog.search("multi timeframe clock", top_k=3)
    assert first == second
    assert first
    assert all(item["source_hash"] for item in first)
    inspected = catalog.inspect(first[0]["entry_id"])
    assert inspected["source_available"] is False


def test_single_test_source_template_golden():
    from backtrader_agent import scaffold

    src = scaffold._render_single_test_source("class Demo(bt.Strategy):\n    pass\n")
    assert "class Demo(bt.Strategy)" in src
    assert "BACKTRADER_AGENT_RESULT" in src
    assert "strategy_source" not in src  # 不残留占位符


def test_catalog_search_uses_explicit_snapshot_path(tmp_path):
    from backtrader_agent import cli

    code = cli.main(
        [
            "--state-root",
            str(tmp_path / "s"),
            "catalog",
            "search",
            "--query",
            "sma",
            "--snapshot-path",
            str(tmp_path / "snap.jsonl"),
        ]
    )
    assert code in (0, 3)  # 参数被接受;空快照允许 BTAG 领域错误,不允许用法错误(2)


@pytest.mark.parametrize(
    "sizing",
    [
        {"method": "martingale", "fixed_size": 1},
        {"method": "fixed"},
        {"method": "fixed", "fixed_size": 0},
        {"method": "fixed", "fixed_size": -5},
        {"method": "fixed", "fixed_size": "ten"},
        {"method": "fixed", "fixed_size": True},
        {"method": "fixed", "fixed_size": 1, "percent": 50},
        {"method": "percent"},
        {"method": "percent", "percent": 0},
        {"method": "percent", "percent": 101},
        {"method": "percent", "fixed_size": 1},
        "all-in",
        [],
    ],
)
def test_spec_rejects_invalid_sizing(sizing) -> None:
    with pytest.raises(AgentError) as exc:
        StrategySpec.from_dict(_sized_spec_dict(sizing))
    assert exc.value.code == "BTAG-SPEC-SIZING"


@pytest.mark.parametrize(
    ("sizing", "expected"),
    [
        (
            {"method": "fixed", "fixed_size": 100},
            {"method": "fixed", "fixed_size": 100},
        ),
        (
            {"method": "fixed", "fixed_size": 2.5},
            {"method": "fixed", "fixed_size": 2.5},
        ),
        ({"method": "percent", "percent": 95}, {"method": "percent", "percent": 95}),
        ({"method": "percent", "percent": 100}, {"method": "percent", "percent": 100}),
        (None, None),
    ],
)
def test_spec_accepts_valid_sizing(sizing, expected) -> None:
    spec = StrategySpec.from_dict(_sized_spec_dict(sizing))
    assert spec.to_dict()["sizing"] == expected


@pytest.mark.parametrize("profile", PROFILES)
def test_scaffold_renders_fixed_sizer(tmp_path, profile) -> None:
    source, artifact = _rendered_source(
        tmp_path, {"method": "fixed", "fixed_size": 100}, profile=profile
    )
    assert "cerebro.addsizer(bt.sizers.FixedSize, stake=100)" in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"


@pytest.mark.parametrize("profile", PROFILES)
def test_scaffold_renders_percent_sizer(tmp_path, profile) -> None:
    source, artifact = _rendered_source(
        tmp_path, {"method": "percent", "percent": 95}, profile=profile
    )
    assert "cerebro.addsizer(bt.sizers.PercentSizer, percents=95)" in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"


def test_spec_without_sizing_renders_no_sizer(tmp_path) -> None:
    source, artifact = _rendered_source(tmp_path, None)
    assert "addsizer" not in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("cerebro.addsizer(bt.sizers.CustomSizer(stake=1))", "BTAG-SEC-CAPABILITY"),
        ("cerebro.addsizer(bt.sizers.CustomSizer, stake=1)", "BTAG-SEC-CAPABILITY"),
        ("cerebro.addsizer(bt.sizers.FixedSize(tranches=2))", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(bt.sizers.FixedSize(5))", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(bt.sizers.FixedSize, tranches=2)", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(bt.sizers.FixedSize, 5)", "BTAG-VAL-SIZER"),
        (
            "cerebro.addsizer(bt.sizers.PercentSizer(percents=50, retint=True))",
            "BTAG-VAL-SIZER",
        ),
        ("cerebro.addsizer(bt.sizers.PercentSizer(**kwargs))", "BTAG-VAL-SIZER"),
        (
            "cerebro.addsizer(bt.sizers.PercentSizer, percents=50, retint=True)",
            "BTAG-VAL-SIZER",
        ),
        ("cerebro.addsizer(*[bt.sizers.FixedSize], tranches=2)", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(*(x for x in [bt.sizers.FixedSize]))", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(bt.sizers.FixedSize(), stake=1)", "BTAG-VAL-SIZER"),
        (
            "cls = bt.sizers.FixedSize\ncerebro.addsizer(cls, tranches=2)",
            "BTAG-VAL-SIZER",
        ),
        ("cerebro.addsizer()", "BTAG-VAL-SIZER"),
        ("cerebro.addsizer(**{'stake': 1})", "BTAG-VAL-SIZER"),
    ],
)
def test_validator_rejects_non_allowlisted_sizer_construction(
    payload: str, expected_code: str
) -> None:
    source = (
        "import backtrader as bt\n"
        "class EscapeStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        pass\n"
        f"{payload}\n"
    )
    diagnostics = StrategyValidator().validate_source(source, "strategy_escape.py")
    assert any(item["code"] == expected_code for item in diagnostics), diagnostics


def test_validator_accepts_allowlisted_sizer_construction() -> None:
    source = (
        "import backtrader as bt\n"
        "class SizerStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        pass\n"
        "cerebro.addsizer(bt.sizers.FixedSize, stake=1)\n"
        "cerebro.addsizer(bt.sizers.PercentSizer, percents=50)\n"
    )
    assert StrategyValidator().validate_source(source, "strategy_sizer.py") == []


def _timer_spec_dict(timers, cheat=None, archetype="single_data_indicator"):
    raw = strategy_spec("ds_" + "a" * 64, archetype=archetype)
    if timers is None:
        raw.pop("timers", None)
    else:
        raw["timers"] = timers
    if cheat is not None:
        raw["cheat"] = cheat
    return raw


def _rendered_timer_source(
    tmp_path,
    timers=None,
    cheat=None,
    archetype="multi_timeframe",
    profile="python_bundle",
):
    renderer = ArtifactRenderer(tmp_path / "state")
    dataset = {
        "dataset_id": "ds_" + "a" * 64,
        "manifest_hash": "a" * 64,
        "feeds": [
            {"name": "primary", "role": "execution", "columns": {}},
            {"name": "secondary", "role": "signal", "columns": {}},
        ],
    }
    raw = _timer_spec_dict(timers, cheat, archetype)
    raw["output_profile"] = profile
    spec = StrategySpec.from_dict(raw)
    artifact = renderer.render("session-1", spec, dataset)
    sources = [
        (Path(artifact["_draft_path"]) / item["path"]).read_text(encoding="utf-8")
        for item in artifact["files"]
        if item["path"].endswith(".py")
    ]
    return "\n".join(sources), artifact


def test_spec_accepts_timer_block_and_defaults_off() -> None:
    on = StrategySpec.from_dict(
        _timer_spec_dict([{"when": "session", "callback": "notify_timer"}])
    )
    assert on.to_dict()["timers"] == [{"when": "session", "callback": "notify_timer"}]
    off = StrategySpec.from_dict(_timer_spec_dict(None))
    assert off.to_dict()["timers"] is None
    assert off.to_dict()["cheat"] is None


@pytest.mark.parametrize(
    "timers",
    [
        [{"when": "lunch", "callback": "notify_timer"}],
        [{"when": "cheat", "callback": "evil_exec"}],
        [{"when": "cheat", "callback": "notify_timer", "extra": 1}],
        [{"when": "cheat"}],
        [{"when": "both"}],
        [{"callback": "notify_timer"}],
        [],
        "session",
        {"when": "session", "callback": "notify_timer"},
        [{"when": "session", "callback": "notify_timer"}] * 9,
        [42],
    ],
)
def test_spec_rejects_invalid_timers(timers) -> None:
    with pytest.raises(AgentError) as exc:
        StrategySpec.from_dict(_timer_spec_dict(timers))
    assert exc.value.code == "BTAG-SPEC-TIMERS"


@pytest.mark.parametrize(
    ("cheat", "expected"),
    [
        ({"on_open": True}, {"on_open": True, "on_close": False}),
        ({"on_close": True}, {"on_open": False, "on_close": True}),
        ({"on_open": True, "on_close": True}, {"on_open": True, "on_close": True}),
        ({"on_open": False, "on_close": False}, {"on_open": False, "on_close": False}),
    ],
)
def test_spec_accepts_valid_cheat(cheat, expected) -> None:
    spec = StrategySpec.from_dict(_timer_spec_dict(None, cheat))
    assert spec.to_dict()["cheat"] == expected


@pytest.mark.parametrize(
    "cheat",
    [
        {"on_open": "yes"},
        {"on_open": 1},
        {"on_close": True, "sneaky": True},
        {},
        [],
        "cheat",
    ],
)
def test_spec_rejects_invalid_cheat(cheat) -> None:
    with pytest.raises(AgentError) as exc:
        StrategySpec.from_dict(_timer_spec_dict(None, cheat))
    assert exc.value.code == "BTAG-SPEC-CHEAT"


def test_validator_accepts_allowlisted_timer_and_cheat_apis() -> None:
    source = (
        "import backtrader as bt\n"
        "class TimerStrategy(bt.Strategy):\n"
        "    def __init__(self):\n"
        "        self.plain = bt.Timer()\n"
        "        self.flagged = bt.Timer(cheat=False)\n"
        "        self.literal = bt.Timer(\n"
        "            when=bt.timer.SESSION_START, cheat=True\n"
        "        )\n"
        "        self.session_timer = self.add_timer(when=bt.timer.SESSION_START)\n"
        "        self.cheat_timer = self.add_timer(\n"
        "            when=bt.Timer.SESSION_END, cheat=True\n"
        "        )\n"
        "    def next(self):\n"
        "        pass\n"
        "cerebro = bt.Cerebro(stdstats=False, cheat_on_open=True)\n"
        "cerebro.broker.set_coo(True)\n"
        "cerebro.broker.set_coc(False)\n"
    )
    assert StrategyValidator().validate_source(source, "strategy_timer.py") == []


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("import threading\nthreading.Timer(1.0, print)", "BTAG-SEC-IMPORT"),
        ("import backtrader as bt\nbt.timer.WEEKDAYS", "BTAG-SEC-CAPABILITY"),
        ("import backtrader as bt\nbt.ExplosiveTimer()", "BTAG-SEC-CAPABILITY"),
    ],
)
def test_validator_rejects_unapproved_timer_usage(
    payload: str, expected_code: str
) -> None:
    source = (
        "import backtrader as bt\n"
        "class TimerEscape(bt.Strategy):\n"
        "    def next(self):\n"
        "        pass\n"
        f"{payload}\n"
    )
    diagnostics = StrategyValidator().validate_source(source, "strategy_escape.py")
    assert any(item["code"] == expected_code for item in diagnostics), diagnostics


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("self.t = bt.Timer(5)", "BTAG-VAL-TIMER"),
        ('self.t = bt.Timer(when="lunch")', "BTAG-VAL-TIMER"),
        ("self.t = bt.Timer(when=some_var)", "BTAG-VAL-TIMER"),
        ("self.t = bt.Timer(cheat=1)", "BTAG-VAL-TIMER"),
        ("self.t = bt.Timer(weekdays=[1, 2])", "BTAG-VAL-TIMER"),
        ("self.t = bt.Timer(**kwargs)", "BTAG-VAL-TIMER"),
        ("self.t = bt.Timer(*args)", "BTAG-VAL-TIMER"),
        ("self.add_timer(bt.timer.SESSION_START)", "BTAG-VAL-TIMER"),
        ("self.add_timer(when=x)", "BTAG-VAL-TIMER"),
        (
            "self.add_timer(when=bt.timer.SESSION_START, cheat=1)",
            "BTAG-VAL-TIMER",
        ),
        (
            "self.add_timer(when=bt.timer.SESSION_START, repeat=5)",
            "BTAG-VAL-TIMER",
        ),
        ("self.add_timer()", "BTAG-VAL-TIMER"),
        ("cerebro.broker.set_coc(1)", "BTAG-VAL-CHEAT"),
        ("cerebro.broker.set_coc()", "BTAG-VAL-CHEAT"),
        ("cerebro.broker.set_coo(coo=True)", "BTAG-VAL-CHEAT"),
        ("cerebro.broker.set_coc(x)", "BTAG-VAL-CHEAT"),
        ("cerebro.broker.set_coc(True, False)", "BTAG-VAL-CHEAT"),
        ("hook = cerebro.broker.set_coc", "BTAG-VAL-CHEAT"),
        ("hook = broker.set_coo", "BTAG-VAL-CHEAT"),
    ],
)
def test_validator_rejects_non_allowlisted_timer_and_cheat_construction(
    payload: str, expected_code: str
) -> None:
    source = (
        "import backtrader as bt\n"
        "class TimerGate(bt.Strategy):\n"
        "    def __init__(self):\n"
        "        pass\n"
        "    def next(self):\n"
        "        pass\n"
        f"{payload}\n"
    )
    diagnostics = StrategyValidator().validate_source(source, "strategy_escape.py")
    assert any(item["code"] == expected_code for item in diagnostics), diagnostics


@pytest.mark.parametrize("profile", PROFILES)
def test_scaffold_renders_timer_segment_only_when_present(tmp_path, profile) -> None:
    source, artifact = _rendered_timer_source(
        tmp_path,
        timers=[{"when": "cheat", "callback": "check_rebalance"}],
        profile=profile,
    )
    assert "self.add_timer(when=bt.timer.SESSION_START, cheat=True)" in source
    assert "def notify_timer(self, timer, when, *args, **kwargs):" in source
    assert "def check_rebalance(self, timer, when, *args, **kwargs):" in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"

    plain_source, _ = _rendered_timer_source(
        tmp_path / "plain", timers=None, profile=profile
    )
    assert "add_timer" not in plain_source
    assert "notify_timer" not in plain_source


def test_scaffold_renders_both_when_as_two_timers(tmp_path) -> None:
    source, artifact = _rendered_timer_source(
        tmp_path, timers=[{"when": "both", "callback": "notify_timer"}]
    )
    assert source.count("self.add_timer(") == 2
    assert "self.add_timer(when=bt.timer.SESSION_START)" in source
    assert "self.add_timer(when=bt.timer.SESSION_START, cheat=True)" in source
    assert "check_rebalance" not in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"


@pytest.mark.parametrize("profile", PROFILES)
def test_scaffold_renders_cheat_segment_only_when_present(tmp_path, profile) -> None:
    source, artifact = _rendered_timer_source(
        tmp_path, cheat={"on_open": True, "on_close": True}, profile=profile
    )
    assert "cheat_on_open=True" in source
    assert "cerebro.broker.set_coo(True)" in source
    assert "cerebro.broker.set_coc(True)" in source
    assert "def next_open(self):" in source
    assert StrategyValidator().validate_artifact(artifact)["status"] == "passed"

    plain_source, _ = _rendered_timer_source(
        tmp_path / "plain", cheat=None, profile=profile
    )
    assert "cheat_on_open" not in plain_source
    assert "set_coc" not in plain_source
    assert "set_coo" not in plain_source
    assert "next_open" not in plain_source

    open_only, _ = _rendered_timer_source(
        tmp_path / "open-only", cheat={"on_open": True}, profile=profile
    )
    assert "cerebro.broker.set_coo(True)" in open_only
    assert "set_coc" not in open_only
    assert "def next_open(self):" in open_only


def test_timers_and_cheat_together_still_validate(tmp_path) -> None:
    source, artifact = _rendered_timer_source(
        tmp_path,
        timers=[
            {"when": "session", "callback": "notify_timer"},
            {"when": "both", "callback": "check_rebalance"},
        ],
        cheat={"on_open": True, "on_close": True},
    )
    assert source.count("self.add_timer(") == 3
    report = StrategyValidator().validate_artifact(artifact)
    assert report["status"] == "passed", report["diagnostics"]


def test_real_cell_timers_fire_and_cheat_executes(tmp_path: Path) -> None:
    renderer = ArtifactRenderer(tmp_path / "state")
    dataset = {
        "dataset_id": "ds_" + "a" * 64,
        "manifest_hash": "a" * 64,
        "feeds": [{"name": "primary", "role": "execution", "columns": {}}],
    }
    spec = StrategySpec.from_dict(
        _timer_spec_dict(
            [
                {"when": "session", "callback": "notify_timer"},
                {"when": "cheat", "callback": "check_rebalance"},
            ],
            cheat={"on_open": True, "on_close": True},
        )
    )
    artifact = renderer.render("session-1", spec, dataset)
    draft = Path(artifact["_draft_path"])
    csv_path = tmp_path / "prices.csv"
    write_price_csv(csv_path, include_signal=False)
    driver = tmp_path / "driver.py"
    driver.write_text(
        f"""import json
import sys

import backtrader as bt
import pandas as pd

sys.path.insert(0, {str(draft)!r})
from strategy_{spec.module_slug} import GeneratedStrategy

frame = pd.read_csv({str(csv_path)!r}, parse_dates=["date"])
data = bt.feeds.PandasData(
    dataname=frame,
    datetime="date",
    open="open",
    high="high",
    low="low",
    close="close",
    volume="volume",
    openinterest="openinterest",
    timeframe=bt.TimeFrame.Days,
)
cerebro = bt.Cerebro(stdstats=False, cheat_on_open=True)
cerebro.broker.set_coc(True)
cerebro.adddata(data)
cerebro.addstrategy(GeneratedStrategy)
strategy = cerebro.run()[0]
print(
    "REAL_CELL="
    + json.dumps(
        {{
            "timer_count": strategy._agent_timer_count,
            "rebalance_count": strategy._agent_rebalance_count,
            "buy_count": strategy._agent_buy_count,
            "final_value": cerebro.broker.getvalue(),
        }},
        sort_keys=True,
    )
)
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(driver)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().split("REAL_CELL=", 1)[1])
    assert payload["timer_count"] >= 40
    assert payload["rebalance_count"] >= 40
    assert payload["buy_count"] > 0
    assert payload["final_value"] != 100000.0
