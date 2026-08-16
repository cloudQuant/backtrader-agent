import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backtrader_agent.catalog import EXPECTED_COUNTS, SnapshotCatalog
from backtrader_agent.errors import AgentError

EXPECTED_ASSET_SHA256 = "30973a10bd434e7935aa5b45577a5d5de0221a58b53a4c00a8124006438c5828"
EXPECTED_REGISTRY_SHA256 = "2f1ac2d7e103498d825b2cf7115f85c34a2a7f940223142b292af5b79e161808"
EXPECTED_INDICATOR_COUNT = 417
EXTRACTOR = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "extract_indicator_registry.py"
)


def _indicator_registry_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "backtrader_agent"
        / "resources"
        / "catalog"
        / "indicator-registry-v1.json"
    )


def test_packaged_catalog_has_full_verified_metadata_and_fourteen_templates() -> None:
    catalog = SnapshotCatalog()
    assert catalog.manifest["counts"] == EXPECTED_COUNTS
    assert catalog.manifest["entry_count"] == 1155
    assert len(catalog.templates()) == 14
    assert len(catalog.templates(archetype="pairs_spread", profile="python_bundle")) == 1
    results = catalog.search(
        "moving average trend",
        archetype="single_data_indicator",
        profile="single_test",
        top_k=3,
    )
    assert len(results) == 3
    assert all(result["source_available"] is False for result in results)
    assert catalog.inspect(results[0]["entry_id"])["source_available"] is False

    snapshot = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "backtrader_agent"
        / "resources"
        / "catalog"
        / "corpus-v1.jsonl"
    )
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == EXPECTED_ASSET_SHA256


def test_source_attached_refresh_rebuilds_both_adapters_without_mutating_source(
    tmp_path: Path,
) -> None:
    functional = tmp_path / "functional"
    packages = tmp_path / "packages"
    output = tmp_path / "state" / "source-catalog.jsonl"
    test_dir = functional / "trend"
    package_dir = packages / "trend" / "0001_example"
    test_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    test_path = test_dir / "test_0001_example.py"
    strategy_path = package_dir / "strategy_example.py"
    test_path.write_text(
        "import backtrader as bt\nclass Example(bt.Strategy):\n    pass\n",
        encoding="utf-8",
    )
    strategy_path.write_text(
        "import backtrader as bt\nclass Example(bt.Strategy):\n    pass\n",
        encoding="utf-8",
    )
    config_path = package_dir / "config.yaml"
    run_path = package_dir / "run.py"
    config_path.write_text("period: 5\n", encoding="utf-8")
    run_path.write_text("from strategy_example import Example\n", encoding="utf-8")
    inputs = (test_path, strategy_path, config_path, run_path)
    before = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in inputs
    }

    manifest = SnapshotCatalog.refresh_source_attached(
        functional,
        packages,
        output,
        require_verified_counts=False,
    )
    assert manifest["mode"] == "source-attached"
    assert manifest["counts"] == {
        "functional_tests": 1,
        "strategy_packages": 1,
        "mapped": 1,
    }
    rebuilt = SnapshotCatalog(snapshot_path=output)
    assert rebuilt.inspect("trend/0001_example")["source_available"] is True
    repeated_output = tmp_path / "state" / "source-catalog-repeated.jsonl"
    repeated = SnapshotCatalog.refresh_source_attached(
        functional,
        packages,
        repeated_output,
        require_verified_counts=False,
    )
    assert repeated["snapshot_hash"] == manifest["snapshot_hash"]
    assert repeated_output.read_bytes() == output.read_bytes()
    after = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in inputs
    }
    assert after == before

    with pytest.raises(AgentError) as error:
        SnapshotCatalog.refresh_source_attached(
            functional,
            packages,
            functional / "forbidden-output.jsonl",
            require_verified_counts=False,
        )
    assert error.value.code == "BTAG-CATALOG-OUTPUT"


def test_source_attached_verified_baseline_gate_rejects_partial_corpora(
    tmp_path: Path,
) -> None:
    functional = tmp_path / "functional"
    packages = tmp_path / "packages"
    functional.mkdir()
    packages.mkdir()
    with pytest.raises(AgentError) as error:
        SnapshotCatalog.refresh_source_attached(
            functional,
            packages,
            tmp_path / "catalog.jsonl",
        )
    assert error.value.code == "BTAG-CATALOG-COUNTS"


def test_indicator_registry_packaged_and_searchable() -> None:
    registry_path = _indicator_registry_path()
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "indicator-registry-v1"
    assert data["mode"] == "snapshot"
    assert data["counts"] == {
        "core_modules": 56,
        "contrib_modules": 207,
        "indicators": EXPECTED_INDICATOR_COUNT,
    }
    entries = data["indicators"]
    assert len(entries) == EXPECTED_INDICATOR_COUNT
    assert all(entry["schema_version"] == "indicator-entry-v1" for entry in entries)
    assert all(entry["source_available"] is False for entry in entries)
    assert any(
        entry["class_name"] == "Sma" or entry["module"].endswith("sma")
        for entry in entries
    )
    assert (
        hashlib.sha256(registry_path.read_bytes()).hexdigest()
        == EXPECTED_REGISTRY_SHA256
    )

    catalog = SnapshotCatalog()
    hits = catalog.search_indicators("bollinger", top_k=3)
    assert hits and all("bollinger" in hit["class_name"].lower() for hit in hits)
    assert all(hit["source_available"] is False for hit in hits)
    assert hits == catalog.search_indicators("bollinger", top_k=3)
    assert hits[0]["class_name"] == "BollingerBands"
    assert hits[0]["param_names"] == ["period", "devfactor", "movav"]
    assert hits[0]["module"] == "backtrader.indicators.bollinger"

    sma_hits = catalog.search_indicators("sma", top_k=8)
    assert all("sma" in hit["class_name"].lower() for hit in sma_hits[:5])
    assert any(hit["class_name"] == "MovingAverageSimple" for hit in sma_hits[5:])


def test_catalog_search_indicator_kind_via_cli() -> None:
    from backtrader_agent.cli import build_parser, dispatch

    value = dispatch(
        build_parser().parse_args(
            [
                "catalog",
                "search",
                "--kind",
                "indicator",
                "--query",
                "bollinger",
                "--top-k",
                "3",
            ]
        )
    )
    assert value["results"]
    assert all("bollinger" in hit["class_name"].lower() for hit in value["results"])

    with pytest.raises(AgentError) as error:
        dispatch(
            build_parser().parse_args(
                [
                    "catalog",
                    "search",
                    "--kind",
                    "indicator",
                    "--query",
                    "x",
                    "--archetype",
                    "pairs_spread",
                ]
            )
        )
    assert error.value.code == "BTAG-CATALOG-KIND"


def _write_import_poisoned_indicators(source: Path) -> None:
    indicators = source / "backtrader" / "indicators"
    contrib = indicators / "contrib"
    contrib.mkdir(parents=True)
    (indicators / "__init__.py").write_text(
        "raise RuntimeError('the registry extractor must never import fork source')\n",
        encoding="utf-8",
    )
    (indicators / "bollinger.py").write_text(
        "raise RuntimeError('never import')\n"
        "class Indicator:\n"
        "    pass\n"
        "class BollingerBands(Indicator):\n"
        "    params = (('period', 20), ('devfactor', 2.0), ('movav', 1))\n"
        "class BollingerBandsPct(BollingerBands):\n"
        "    lines = ('pctbands',)\n",
        encoding="utf-8",
    )
    (contrib / "bb_squeeze_indicator.py").write_text(
        "raise RuntimeError('never import')\n"
        "class BBandsSqueeze(BollingerBands):\n"
        "    params = (('bb_period', 20),)\n",
        encoding="utf-8",
    )


def _run_extractor(
    tmp_path: Path,
    *,
    root: Path,
    output: Path,
    state: Path,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            "--root",
            str(root),
            "--output",
            str(output),
            "--state-root",
            str(state),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_extract_indicator_registry_script_is_read_only_and_deterministic(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_import_poisoned_indicators(source)
    before = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }
    for path in sorted(source.rglob("*.py")):
        path.chmod(0o444)

    output_a = tmp_path / "registry-a.json"
    completed = _run_extractor(
        tmp_path, root=source, output=output_a, state=tmp_path / "state"
    )
    assert completed.returncode == 0, completed.stderr

    output_b = tmp_path / "registry-b.json"
    repeated = _run_extractor(
        tmp_path, root=source, output=output_b, state=tmp_path / "state"
    )
    assert repeated.returncode == 0, repeated.stderr
    assert output_b.read_bytes() == output_a.read_bytes()

    data = json.loads(output_a.read_text(encoding="utf-8"))
    assert data["schema_version"] == "indicator-registry-v1"
    assert data["mode"] == "snapshot"
    assert data["counts"] == {
        "core_modules": 1,
        "contrib_modules": 1,
        "indicators": 3,
    }
    by_id = {entry["entry_id"]: entry for entry in data["indicators"]}
    assert by_id["backtrader.indicators.bollinger:BollingerBands"]["param_names"] == [
        "period",
        "devfactor",
        "movav",
    ]
    assert by_id["backtrader.indicators.bollinger:BollingerBandsPct"][
        "param_names"
    ] == []
    assert by_id[
        "backtrader.indicators.contrib.bb_squeeze_indicator:BBandsSqueeze"
    ]["param_names"] == ["bb_period"]
    assert all(entry["source_available"] is False for entry in data["indicators"])

    after = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_extract_indicator_registry_script_reads_env_and_registered_engine_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_import_poisoned_indicators(source)

    env_output = tmp_path / "env-registry.json"
    completed = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--output", str(env_output)],
        cwd=tmp_path,
        env={
            "BACKTRADER_AGENT_INDICATOR_ROOT": str(source),
            "PATH": os.environ.get("PATH", ""),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(env_output.read_text(encoding="utf-8"))["counts"][
        "indicators"
    ] == 3

    state = tmp_path / "state"
    state.mkdir()
    (state / "roots.json").write_text(
        json.dumps(
            {
                "schema_version": "root-registry-v1",
                "roots": {
                    "engine": {
                        "path": str(source),
                        "writable": False,
                        "kind": "engine",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    root_output = tmp_path / "root-registry.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            "--output",
            str(root_output),
            "--state-root",
            str(state),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(root_output.read_text(encoding="utf-8"))["counts"][
        "indicators"
    ] == 3


def test_extract_indicator_registry_script_skips_with_explanation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "registry.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            "--output",
            str(output),
            "--state-root",
            str(tmp_path / "state"),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0
    assert "skip" in completed.stdout.lower()
    assert "BACKTRADER_AGENT_INDICATOR_ROOT" in completed.stdout
    assert not output.exists()
