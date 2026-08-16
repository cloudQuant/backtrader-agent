from pathlib import Path

import pytest

from backtrader_agent.catalog import SnapshotCatalog
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.errors import AgentError
from backtrader_agent.scaffold import ARCHETYPES, PROFILES, ArtifactRenderer
from backtrader_agent.validator import StrategyValidator

from helpers import strategy_spec


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
