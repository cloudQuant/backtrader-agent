import json
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
from jsonschema import Draft202012Validator

import backtrader_agent
from backtrader_agent import engines
from backtrader_agent.audit import IndependenceAuditor
from backtrader_agent.changes import ChangeManager
from backtrader_agent.catalog import SnapshotCatalog
from backtrader_agent.canonical import hash_object
from backtrader_agent.contracts import ARCHETYPES, StrategySpec
from backtrader_agent.data import DatasetService
from backtrader_agent.doctor import diagnose
from backtrader_agent.engines import inspect_engine, inspect_execution_environment
from backtrader_agent.errors import AgentError
from backtrader_agent.installer import AdapterInstaller
from backtrader_agent.roots import RootRegistry
from backtrader_agent.report import compare_metrics, normalize_extended_metrics
from backtrader_agent.runner import ControlledRunner
from backtrader_agent.runner.execute import parse_child_result
from backtrader_agent.scaffold import ArtifactRenderer
from backtrader_agent.sessions import SessionStore
from backtrader_agent.tokens import TokenAuthority
from backtrader_agent.validator import StrategyValidator

from helpers import (
    data_spec,
    resolve_acceptance_engine_root,
    strategy_spec,
    write_adapter_price_csv,
    write_price_csv,
)

PACKAGE_ROOT = Path(backtrader_agent.__file__).resolve().parent
PRODUCT_ROOT = PACKAGE_ROOT.parents[1]
CONTRACT_ROOT = PACKAGE_ROOT / "resources/contracts"
BACKTRADER_ROOT = resolve_acceptance_engine_root(PRODUCT_ROOT)


def validate_contract(name: str, value: dict) -> None:
    schema = json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def test_controlled_child_environment_does_not_forward_home() -> None:
    environment = ControlledRunner._child_environment([], "runonce")
    assert "HOME" not in environment
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print('present' if 'HOME' in os.environ else 'absent')",
        ],
        check=True,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.stdout.strip() == "absent"


def _registered_copied_engine(tmp_path: Path) -> tuple:
    engine_root = tmp_path / "engine"
    shutil.copytree(BACKTRADER_ROOT / "backtrader", engine_root / "backtrader")
    roots = RootRegistry(tmp_path / "state")
    roots.register("engine", engine_root, writable=False, kind="engine")
    return roots, engine_root


def test_inspect_engine_hashes_all_regular_package_members(tmp_path: Path) -> None:
    roots, engine_root = _registered_copied_engine(tmp_path)
    before = inspect_engine(roots, "engine")
    assert before["source"]["status"] == "warning"

    target = engine_root / "backtrader" / "cerebro.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# test mutation\n", encoding="utf-8")

    after = inspect_engine(roots, "engine")
    assert before["package_tree_sha256"] != after["package_tree_sha256"]
    assert before["package_file_count"] == after["package_file_count"]
    assert before["engine_hash"] != after["engine_hash"]


def test_inspect_engine_rejects_symlinked_package_member(tmp_path: Path) -> None:
    roots, engine_root = _registered_copied_engine(tmp_path)
    (engine_root / "backtrader" / "linked.py").symlink_to(
        engine_root / "backtrader" / "__init__.py"
    )

    with pytest.raises(AgentError, match="BTAG-ENGINE-SYMLINK"):
        inspect_engine(roots, "engine")


def test_execution_environment_is_versioned_and_hash_bound() -> None:
    descriptor = engines.inspect_execution_environment()
    evidence = {key: value for key, value in descriptor.items() if key != "environment_hash"}

    assert descriptor["schema_version"] == "execution-environment-v1"
    assert descriptor["environment_hash"] == hash_object(evidence)
    assert {
        "python_executable",
        "python_version",
        "python_implementation",
        "platform",
    }.issubset(descriptor)


ADAPTER_BY_ARCHETYPE = {
    "single_data_indicator": "generic_csv",
    "multi_indicator_system": "backtrader_csv",
    "multi_asset_allocation": "yahoo_csv",
    "multi_timeframe": "mt5_csv",
    "pairs_spread": "pandas",
    "order_risk": "generic_csv",
    "precomputed_ml": "pandas_custom_lines",
}


def _matrix_data_spec(archetype: str) -> dict:
    value = data_spec()
    primary = value["feeds"][0]
    adapter = ADAPTER_BY_ARCHETYPE[archetype]
    primary["format"] = adapter
    primary["columns"] = {"signal": "signal"} if adapter == "pandas_custom_lines" else {}
    primary.pop("datetime_format", None)
    if archetype == "multi_timeframe":
        primary["timeframe"] = "Minutes"
        primary["delimiter"] = "\t"
    if archetype in {"multi_asset_allocation", "multi_timeframe", "pairs_spread"}:
        secondary = {
            **primary,
            "columns": dict(primary["columns"]),
            "feed_id": "secondary",
            "name": "secondary",
            "role": "signal" if archetype != "pairs_spread" else "hedge",
            "relative_path": "secondary.csv",
        }
        value["feeds"].append(secondary)
    if archetype == "multi_timeframe":
        value["transforms"] = [
            {
                "profile_id": "resample",
                "parameters": {
                    "feed": "secondary",
                    "timeframe": "Days",
                    "compression": 1,
                },
            }
        ]
    return value


def _execute_matrix_mode(
    root: Path,
    profile: str,
    archetype: str,
    mode: str,
    *,
    engine_root: Path = BACKTRADER_ROOT,
    environment_hash: Optional[str] = None,
    before_run: Optional[Callable[[], None]] = None,
    run_context: Optional[Dict[str, Any]] = None,
) -> tuple:
    workspace = root / "workspace"
    input_root = root / "input"
    workspace.mkdir(parents=True)
    input_root.mkdir(parents=True)
    adapter = ADAPTER_BY_ARCHETYPE[archetype]
    write_adapter_price_csv(input_root / "prices.csv", adapter, rows=40)
    write_adapter_price_csv(
        input_root / "secondary.csv",
        adapter,
        rows=40,
        price_offset=7.0,
    )
    state = workspace / ".backtrader-agent"

    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    roots.register("input", input_root, writable=False, kind="dataset")
    roots.register("engine", engine_root, writable=False, kind="engine")
    engine = inspect_engine(roots, "engine")
    sessions = SessionStore(state)
    session_id = f"session-{mode}"
    sessions.create(session_id)
    dataset_input = _matrix_data_spec(archetype)
    dataset = DatasetService(roots, state).register(dataset_input)
    sessions.transition(
        session_id,
        "DATA_READY",
        "dataset-register",
        {"dataset": dataset["manifest_hash"]},
        effect_references={
            "dataset_id": dataset["dataset_id"],
            "dataset_manifest_hash": dataset["manifest_hash"],
        },
    )
    raw_spec = strategy_spec(
        dataset["dataset_id"],
        profile=profile,
        archetype=archetype,
    )
    raw_spec["feeds"] = [{"name": feed["name"], "role": feed["role"]} for feed in dataset["feeds"]]
    spec = StrategySpec.from_dict(raw_spec)
    sessions.transition(
        session_id,
        "SPEC_DRAFT",
        "spec-draft",
        {"spec": spec.spec_hash},
        effect_references={"spec_hash": spec.spec_hash},
    )
    sessions.transition(
        session_id,
        "SPEC_APPROVED",
        "spec-approve",
        {"spec": spec.spec_hash},
        effect_references={"approved_spec_hash": spec.spec_hash},
    )
    sources = SnapshotCatalog().search(archetype.replace("_", " "), top_k=1)
    assert sources
    sessions.transition(
        session_id,
        "SOURCES_SELECTED",
        "sources-select",
        {"catalog": sources[0]["source_hash"]},
        effect_references={"selected_source_hash": sources[0]["source_hash"]},
    )
    artifact = ArtifactRenderer(state).render(session_id, spec, dataset)
    sessions.transition(
        session_id,
        "DRAFT_READY",
        "draft-render",
        {"artifact": artifact["artifact_hash"]},
        effect_references={"artifact_hash": artifact["artifact_hash"]},
    )
    assert {
        "schema_version",
        "artifact_id",
        "spec_hash",
        "dataset_id",
        "output_profile",
        "files",
        "artifact_hash",
    }.issubset(artifact)
    validate_contract(
        "artifact-manifest-v1.schema.json",
        {key: value for key, value in artifact.items() if not key.startswith("_")},
    )

    authority = TokenAuthority(state)
    validator = StrategyValidator(authority)
    validation_report = validator.validate_artifact(
        artifact,
        bindings={
            "dataset_hash": dataset["manifest_hash"],
            "engine_hash": engine["engine_hash"],
            "engine_root_id": "engine",
            "environment_hash": environment_hash or inspect_execution_environment()["environment_hash"],
        },
        approval="validator",
        session_id=session_id,
    )
    assert validation_report["status"] == "passed"
    sessions.transition(
        session_id,
        "VALIDATED",
        "strategy-validate",
        {"validation": validation_report["validation_hash"]},
        effect_references={
            "artifact_record_hash": validation_report["validation_token"]["bindings"][
                "artifact_record_hash"
            ],
            "validation_hash": validation_report["validation_hash"],
            "validation_token_hash": hash_object(validation_report["validation_token"]),
            "validation_token_id": validation_report["validation_token"]["token_id"],
        },
    )
    assert {
        "schema_version",
        "validation_id",
        "artifact_hash",
        "dataset_id",
        "status",
        "diagnostics",
        "evidence",
        "validation_hash",
    }.issubset(validation_report)
    validate_contract("validation-report-v1.schema.json", validation_report)
    validation_token = validation_report["validation_token"]

    changes = ChangeManager(roots, state, authority)
    prepared = changes.prepare(
        session_id=session_id,
        draft_root=Path(artifact["_draft_path"]),
        files=[
            {
                "source": item["path"],
                "target": f"strategies/generated/e2e/{mode}/{item['path']}",
            }
            for item in artifact["files"]
        ],
        target_root_id="workspace",
        validation_token=validation_token,
    )
    change_request = authority.prepare_approval(
        "change",
        prepared["manifest_hash"],
        {
            "artifact_hash": prepared["artifact_hash"],
            "artifact_record_hash": prepared["artifact_record_hash"],
            "change_manifest_hash": prepared["manifest_hash"],
            "dataset_hash": prepared["dataset_manifest_hash"],
            "dataset_id": prepared["dataset_id"],
            "spec_hash": prepared["spec_hash"],
            "validation_token_hash": prepared["validation_token_hash"],
            "validation_token_id": validation_token["token_id"],
            "session_id": session_id,
        },
    )
    change_token = authority.grant_approval(
        change_request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    applied = changes.apply(prepared, change_token, idempotency_key=f"e2e-apply-{mode}")
    assert sessions.load(session_id)["state"] == "APPLIED"

    run_subject = ControlledRunner.compute_run_subject(
        applied,
        dataset,
        validation_token,
        mode=mode,
    )
    run_request = authority.prepare_approval(
        "run",
        run_subject,
        {
            "applied_artifact_hash": applied["applied_artifact_hash"],
            "applied_record_hash": applied["applied_record_hash"],
            "artifact_hash": applied["artifact_hash"],
            "artifact_record_hash": applied["artifact_record_hash"],
            "change_manifest_hash": applied["change_manifest_hash"],
            "validation_token_id": validation_token["token_id"],
            "validation_token_hash": hash_object(validation_token),
            "dataset_hash": dataset["manifest_hash"],
            "dataset_id": dataset["dataset_id"],
            "mode": mode,
            "session_id": session_id,
            "spec_hash": applied["spec_hash"],
        },
    )
    run_token = authority.grant_approval(
        run_request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    assert sessions.load(session_id)["state"] == "RUN_APPROVED"
    if run_context is not None:
        run_context.update(
            {
                "authority": authority,
                "run_token": run_token,
                "state": state,
            }
        )
    if before_run is not None:
        before_run()
    result = ControlledRunner(roots, state, authority).run(
        applied,
        dataset,
        validation_token,
        run_token,
        mode=mode,
        idempotency_key=f"e2e-run-{mode}",
        timeout_seconds=30,
    )
    assert result["status"] == "passed"
    assert sessions.load(session_id)["state"] == "COMPLETED"
    assert result["metrics"]["final_value"] is not None
    assert result["metrics"]["return_rate"] == pytest.approx(
        (result["metrics"]["final_value"] / 100000.0 - 1.0) * 100.0
    )
    assert isinstance(result["artifacts"], list)
    assert {
        "schema_version",
        "run_id",
        "status",
        "metrics",
        "diagnostics",
        "artifacts",
        "result_hash",
    }.issubset(result)
    validate_contract("run-result-v1.schema.json", result)
    run_manifest = json.loads(
        (state / "runs" / result["run_id"] / "run-manifest.json").read_text(encoding="utf-8")
    )
    assert {
        "schema_version",
        "run_id",
        "artifact_hash",
        "dataset_id",
        "engine",
        "environment_hash",
        "run_profile",
        "approval_id",
        "manifest_hash",
    }.issubset(run_manifest)
    validate_contract("run-manifest-v1.schema.json", run_manifest)
    assert run_manifest["engine"]["root_id"] == "engine"
    assert run_manifest["engine"]["hash"] == engine["engine_hash"]
    assert run_manifest["engine"]["version"] == engine["version"]
    assert run_manifest["engine"]["package_tree_sha256"] == engine["package_tree_sha256"]
    assert run_manifest["engine"]["import_relative_path"] == "backtrader/__init__.py"
    assert (state / "runs" / result["run_id"] / "report.md").is_file()
    assert all(set(item) == {"path", "role", "bytes", "sha256"} for item in result["artifacts"])

    repeated = ControlledRunner(roots, state, authority).run(
        applied,
        dataset,
        validation_token,
        run_token,
        mode=mode,
        idempotency_key=f"e2e-run-{mode}",
        timeout_seconds=30,
    )
    assert repeated == result
    return result, dataset, run_manifest, sources[0]


def test_run_rejects_environment_change_before_token_consumption(tmp_path: Path) -> None:
    context: Dict[str, Any] = {}

    with pytest.raises(AgentError, match="BTAG-ENVIRONMENT-HASH"):
        _execute_matrix_mode(
            tmp_path,
            "python_bundle",
            "single_data_indicator",
            "runonce",
            environment_hash="e" * 64,
            run_context=context,
        )

    context["authority"].require_issued(context["run_token"])
    assert not list((context["state"] / "runs").rglob("run-result.json"))


def test_run_rejects_engine_tree_mutation_before_token_consumption(tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    shutil.copytree(BACKTRADER_ROOT / "backtrader", engine_root / "backtrader")
    context: Dict[str, Any] = {}

    def mutate_engine() -> None:
        target = engine_root / "backtrader" / "cerebro.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# test mutation\n", encoding="utf-8")

    with pytest.raises(AgentError, match="BTAG-ENGINE-HASH"):
        _execute_matrix_mode(
            tmp_path / "run",
            "python_bundle",
            "single_data_indicator",
            "runonce",
            engine_root=engine_root,
            before_run=mutate_engine,
            run_context=context,
        )

    context["authority"].require_issued(context["run_token"])
    assert not list((context["state"] / "runs").rglob("run-result.json"))


def test_run_preflight_rejects_missing_profile_dependency_before_token_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context: Dict[str, Any] = {}
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "pandas" else original_find_spec(name),
    )

    with pytest.raises(AgentError, match="BTAG-RUN-DEPENDENCY") as failure:
        _execute_matrix_mode(
            tmp_path,
            "python_bundle",
            "single_data_indicator",
            "runonce",
            run_context=context,
        )

    assert failure.value.details == {"profile": "python_bundle", "missing": ["pandas"]}
    context["authority"].require_issued(context["run_token"])
    assert not list((context["state"] / "runs").rglob("run-result.json"))


def test_doctor_distinguishes_execution_readiness(tmp_path: Path) -> None:
    state = tmp_path / "state"
    without_engine = diagnose(state_root=state)
    assert without_engine["execution_ready"] is False
    assert without_engine["execution_profiles"]["python_bundle"]["ready"] is True

    roots = RootRegistry(state)
    roots.register("engine", BACKTRADER_ROOT, writable=False, kind="engine")
    with_engine = diagnose(state_root=state)
    assert with_engine["execution_ready"] is True
    assert with_engine["engines"][0]["source"]["status"] == "verified"


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
@pytest.mark.parametrize("profile", ["python_bundle", "single_test"])
def test_controlled_end_to_end_run_and_report(
    tmp_path: Path,
    profile: str,
    archetype: str,
) -> None:
    modes = {}
    datasets = {}
    manifests = {}
    source = None
    for mode in ("runonce", "runnext"):
        result, dataset, run_manifest, source = _execute_matrix_mode(
            tmp_path / mode,
            profile,
            archetype,
            mode,
        )
        modes[mode] = result
        datasets[mode] = dataset
        manifests[mode] = run_manifest
    comparison = compare_metrics(modes["runonce"]["metrics"], modes["runnext"]["metrics"])
    assert comparison["status"] == "passed", comparison
    assert datasets["runonce"]["semantic_hash"] == datasets["runnext"]["semantic_hash"]
    assert manifests["runonce"]["run_profile"]["mode"] == "runonce"
    assert manifests["runnext"]["run_profile"]["mode"] == "runnext"

    evidence_root = os.environ.get("BACKTRADER_AGENT_ACCEPTANCE_EVIDENCE_DIR")
    if evidence_root:
        evidence = {
            "schema_version": "acceptance-cell-v1",
            "cell_id": f"{archetype}:{profile}",
            "archetype": archetype,
            "profile": profile,
            "status": "passed",
            "modes": {
                mode: {
                    "result_hash": modes[mode]["result_hash"],
                    "run_manifest_hash": manifests[mode]["manifest_hash"],
                }
                for mode in ("runonce", "runnext")
            },
            "comparison": comparison,
            "data": {
                "formats": sorted({feed["format"] for feed in datasets["runonce"]["feeds"]}),
                "feed_count": len(datasets["runonce"]["feeds"]),
                "transforms": datasets["runonce"]["transforms"],
                "custom_lines": sorted(
                    {
                        line
                        for feed in datasets["runonce"]["feeds"]
                        for line in feed.get("canonical_columns", [])[7:]
                    }
                ),
            },
            "source": {
                "entry_id": source["entry_id"],
                "source_hash": source["source_hash"],
            },
        }
        path = Path(evidence_root) / f"{archetype}--{profile}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")


REQUIRED_SCALARS = {
    "bar_num",
    "buy_count",
    "sell_count",
    "win_count",
    "loss_count",
    "trade_num",
    "final_value",
    "sharpe_ratio",
    "annual_return",
    "max_drawdown",
    "return_rate",
}

EXTENDED_METRIC_FIELDS = {
    "trade_analyzer",
    "sqn",
    "calmar",
    "vwr",
    "gross_leverage",
    "positions_value",
}

TRADE_ANALYZER_SUBSET_FIELDS = {
    "profit_factor",
    "avg_holding_bars",
    "max_consecutive_wins",
    "max_consecutive_losses",
}


def test_eleven_scalars_still_required_in_schema() -> None:
    schema = json.loads(
        (CONTRACT_ROOT / "run-result-v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["$defs"]["Metrics"]["required"]) == REQUIRED_SCALARS
    assert "extended_metrics" not in schema["required"]
    extended = schema["$defs"].get("ExtendedMetrics")
    assert extended is not None
    assert EXTENDED_METRIC_FIELDS <= set(extended["properties"])
    assert extended["type"] == ["object", "null"]
    trade_analyzer = schema["$defs"].get("TradeAnalyzerSubset")
    assert trade_analyzer is not None
    assert TRADE_ANALYZER_SUBSET_FIELDS <= set(trade_analyzer["properties"])


def test_run_result_extended_metrics_from_real_cell(tmp_path: Path) -> None:
    result, _, _, _ = _execute_matrix_mode(
        tmp_path,
        "python_bundle",
        "single_data_indicator",
        "runonce",
    )
    assert "extended_metrics" in result
    em = result["extended_metrics"]
    assert em is not None
    assert set(em) == EXTENDED_METRIC_FIELDS
    assert "sqn" in em and "calmar" in em
    assert set(em["trade_analyzer"]) == TRADE_ANALYZER_SUBSET_FIELDS
    assert REQUIRED_SCALARS <= set(result["metrics"])
    validate_contract("run-result-v1.schema.json", result)


def test_missing_extended_analyzer_does_not_fail_the_run(tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    shutil.copytree(BACKTRADER_ROOT / "backtrader", engine_root / "backtrader")
    init_path = engine_root / "backtrader" / "analyzers" / "__init__.py"
    init_path.write_text(
        init_path.read_text(encoding="utf-8").replace(
            "from .sqn import *\n", ""
        ),
        encoding="utf-8",
    )
    (engine_root / "backtrader" / "analyzers" / "sqn.py").unlink()

    result, _, _, _ = _execute_matrix_mode(
        tmp_path / "run",
        "python_bundle",
        "single_data_indicator",
        "runonce",
        engine_root=engine_root,
    )
    assert result["status"] == "passed"
    em = result["extended_metrics"]
    assert em is not None
    assert set(em) == EXTENDED_METRIC_FIELDS
    assert em["sqn"] is None
    assert em["gross_leverage"] is not None
    assert em["positions_value"] is not None
    validate_contract("run-result-v1.schema.json", result)


def test_runtime_error_in_extended_analyzer_does_not_fail_the_run(
    tmp_path: Path,
) -> None:
    engine_root = tmp_path / "engine"
    shutil.copytree(BACKTRADER_ROOT / "backtrader", engine_root / "backtrader")
    sqn_path = engine_root / "backtrader" / "analyzers" / "sqn.py"
    source = sqn_path.read_text(encoding="utf-8")
    poisoned = source.replace(
        "def stop(self):",
        "def stop(self):\n        raise RuntimeError('poisoned analyzer stop')",
        1,
    )
    assert poisoned != source
    sqn_path.write_text(poisoned, encoding="utf-8")

    result, _, _, _ = _execute_matrix_mode(
        tmp_path / "run",
        "python_bundle",
        "single_data_indicator",
        "runonce",
        engine_root=engine_root,
    )
    assert result["status"] == "passed"
    em = result["extended_metrics"]
    assert em is not None
    assert set(em) == EXTENDED_METRIC_FIELDS
    assert em["sqn"] is None
    assert em["gross_leverage"] is not None
    assert em["positions_value"] is not None
    validate_contract("run-result-v1.schema.json", result)


def test_extended_metrics_normalization_nulls_non_finite_values() -> None:
    with pytest.warns(RuntimeWarning):
        normalized = normalize_extended_metrics(
            {
                "trade_analyzer": {
                    "profit_factor": 1.5,
                    "avg_holding_bars": 2,
                    "max_consecutive_wins": 3,
                    "max_consecutive_losses": float("nan"),
                },
                "sqn": 4.2,
                "calmar": None,
                "vwr": float("inf"),
                "gross_leverage": "not-a-number",
                "positions_value": 1234.5,
            }
        )
    assert normalized is not None
    assert normalized["trade_analyzer"]["profit_factor"] == 1.5
    assert normalized["trade_analyzer"]["avg_holding_bars"] == 2.0
    assert normalized["trade_analyzer"]["max_consecutive_wins"] == 3.0
    assert normalized["trade_analyzer"]["max_consecutive_losses"] is None
    assert normalized["sqn"] == 4.2
    assert normalized["calmar"] is None
    assert normalized["vwr"] is None
    assert normalized["gross_leverage"] is None
    assert normalized["positions_value"] == 1234.5


def test_extended_metrics_normalization_tolerates_missing_analyzers() -> None:
    assert normalize_extended_metrics(None) is None
    with pytest.warns(RuntimeWarning):
        assert normalize_extended_metrics("garbage") is None
    normalized = normalize_extended_metrics({})
    assert set(normalized) == EXTENDED_METRIC_FIELDS
    assert normalized["trade_analyzer"] is None
    assert normalized["sqn"] is None


def test_parse_child_result_sets_null_extended_metrics_when_absent() -> None:
    payload = "BACKTRADER_AGENT_RESULT=" + json.dumps(
        {
            "metrics": {
                "bar_num": 0,
                "buy_count": 0,
                "sell_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "trade_num": 0,
                "final_value": 100000.0,
                "sharpe_ratio": None,
                "annual_return": None,
                "max_drawdown": 0.0,
                "return_rate": 0.0,
            }
        },
        sort_keys=True,
    )
    result = parse_child_result(payload)
    assert result["extended_metrics"] is None
    assert REQUIRED_SCALARS <= set(result["metrics"])


def test_executable_validation_requires_signed_product_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    input_root = tmp_path / "input"
    workspace.mkdir()
    input_root.mkdir()
    write_price_csv(input_root / "prices.csv")
    state = workspace / ".backtrader-agent"
    roots = RootRegistry(state)
    roots.register("input", input_root, writable=False, kind="dataset")
    sessions = SessionStore(state)
    session_id = "provenance-1"
    sessions.create(session_id)
    dataset = DatasetService(roots, state).register(data_spec())
    sessions.transition(
        session_id,
        "DATA_READY",
        "dataset-register",
        {"dataset": dataset["manifest_hash"]},
        effect_references={
            "dataset_id": dataset["dataset_id"],
            "dataset_manifest_hash": dataset["manifest_hash"],
        },
    )
    spec = StrategySpec.from_dict(strategy_spec(dataset["dataset_id"]))
    sessions.transition(
        session_id,
        "SPEC_DRAFT",
        "spec-draft",
        {"spec": spec.spec_hash},
        effect_references={"spec_hash": spec.spec_hash},
    )
    sessions.transition(
        session_id,
        "SPEC_APPROVED",
        "spec-approve",
        {"spec": spec.spec_hash},
        effect_references={"approved_spec_hash": spec.spec_hash},
    )
    sessions.transition(session_id, "SOURCES_SELECTED", "sources-select", {"catalog": "fixture"})
    artifact = ArtifactRenderer(state).render(session_id, spec, dataset)
    sessions.transition(
        session_id,
        "DRAFT_READY",
        "draft-render",
        {"artifact": artifact["artifact_hash"]},
        effect_references={"artifact_hash": artifact["artifact_hash"]},
    )
    authority = TokenAuthority(state)
    validator = StrategyValidator(authority)
    bindings = {
        "dataset_hash": dataset["manifest_hash"],
        "engine_hash": "e" * 64,
        "engine_root_id": "engine",
        "environment_hash": "test-environment",
    }
    assert (
        validator.validate_artifact(
            artifact,
            bindings=bindings,
            approval="validator",
            session_id=session_id,
        )["status"]
        == "passed"
    )

    forged = {
        key: value
        for key, value in artifact.items()
        if key not in {"artifact_hash", "_artifact_record_hash"}
    }
    forged["artifact_id"] = "artifact-forged"
    forged["artifact_hash"] = hash_object(
        {
            key: value
            for key, value in forged.items()
            if not key.startswith("_") and key != "artifact_hash"
        }
    )
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-MISSING"):
        validator.validate_artifact(
            forged,
            bindings=bindings,
            approval="validator",
            session_id=session_id,
        )

    external_draft = tmp_path / "external-draft"
    shutil.copytree(Path(artifact["_draft_path"]), external_draft)
    outside = {**artifact, "_draft_path": str(external_draft)}
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-BINDING"):
        validator.validate_artifact(
            outside,
            bindings=bindings,
            approval="validator",
            session_id=session_id,
        )

    sessions.create("provenance-2", parent_session_id=session_id)
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-MISSING"):
        validator.validate_artifact(
            artifact,
            bindings=bindings,
            approval="validator",
            session_id="provenance-2",
        )

    record_path = (
        state / "sessions" / session_id / "artifacts" / f"{artifact['artifact_hash']}.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["dataset_id"] = "ds_" + "f" * 64
    record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-SIGNATURE"):
        validator.validate_artifact(
            artifact,
            bindings=bindings,
            approval="validator",
            session_id=session_id,
        )


def test_run_approval_is_bound_to_the_session_applied_artifact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    sessions = SessionStore(state)
    session_id = "run-binding"
    sessions.create(session_id)
    artifact_hash = "a" * 64
    artifact_record_hash = "r" * 64
    spec_hash = "b" * 64
    dataset_id = "ds_" + "d" * 64
    dataset_hash = "d" * 64
    validation_token_id = "tok-validation"
    validation_token_hash = "v" * 64
    change_manifest_hash = "c" * 64
    transitions = (
        (
            "DATA_READY",
            "dataset-register",
            {"dataset_id": dataset_id, "dataset_manifest_hash": dataset_hash},
        ),
        ("SPEC_DRAFT", "spec-draft", {"spec_hash": spec_hash}),
        ("SPEC_APPROVED", "spec-approve", {"approved_spec_hash": spec_hash}),
        ("SOURCES_SELECTED", "sources-select", {}),
        ("DRAFT_READY", "draft-render", {"artifact_hash": artifact_hash}),
        (
            "VALIDATED",
            "strategy-validate",
            {
                "artifact_record_hash": artifact_record_hash,
                "validation_hash": "e" * 64,
                "validation_token_hash": validation_token_hash,
                "validation_token_id": validation_token_id,
            },
        ),
        (
            "APPLY_PREPARED",
            "changes-prepare",
            {"change_manifest_hash": change_manifest_hash},
        ),
    )
    for state_name, action, effects in transitions:
        sessions.transition(
            session_id,
            state_name,
            action,
            {"input": hash_object({"state": state_name})},
            effect_references=effects,
        )

    authority = TokenAuthority(state)
    applied_portable = {
        "schema_version": "applied-artifact-v1",
        "applied_artifact_id": "applied-fixture",
        "generated_by": "backtrader-agent",
        "session_id": session_id,
        "target_root_id": "workspace",
        "profile": "single_test",
        "entrypoint": "test_fixture.py",
        "artifact_hash": artifact_hash,
        "artifact_record_hash": artifact_record_hash,
        "spec_hash": spec_hash,
        "dataset_id": dataset_id,
        "dataset_manifest_hash": dataset_hash,
        "change_manifest_hash": change_manifest_hash,
        "validation_token_id": validation_token_id,
        "validation_token_hash": validation_token_hash,
        "approval_id": "approval-change",
        "files": [],
    }
    applied_portable["applied_artifact_hash"] = hash_object(applied_portable)
    applied = {**applied_portable, "status": "applied"}
    applied_record = authority.store_bound_record(
        "applied-artifact",
        session_id,
        applied["applied_artifact_hash"],
        {"applied_artifact": applied},
    )
    sessions.transition(
        session_id,
        "APPLIED",
        "changes-apply",
        {"applied": applied["applied_artifact_hash"]},
        effect_references={
            "applied_artifact_hash": applied["applied_artifact_hash"],
            "applied_record_hash": applied_record["record_hash"],
        },
    )
    mode = "runonce"
    subject = hash_object(
        {
            "applied_artifact_hash": applied["applied_artifact_hash"],
            "dataset_manifest_hash": dataset_hash,
            "validation_token_id": validation_token_id,
            "mode": mode,
            "profile": "controlled-runner-v1",
        }
    )
    bindings = {
        "applied_artifact_hash": applied["applied_artifact_hash"],
        "applied_record_hash": applied_record["record_hash"],
        "artifact_hash": artifact_hash,
        "artifact_record_hash": artifact_record_hash,
        "change_manifest_hash": change_manifest_hash,
        "dataset_hash": dataset_hash,
        "dataset_id": dataset_id,
        "mode": mode,
        "session_id": session_id,
        "spec_hash": spec_hash,
        "validation_token_hash": validation_token_hash,
        "validation_token_id": validation_token_id,
    }
    with pytest.raises(AgentError, match="BTAG-RUN-MODE"):
        authority.prepare_approval(
            "run",
            hash_object(
                {
                    "applied_artifact_hash": applied["applied_artifact_hash"],
                    "dataset_manifest_hash": dataset_hash,
                    "validation_token_id": validation_token_id,
                    "mode": "arbitrary",
                    "profile": "controlled-runner-v1",
                }
            ),
            {**bindings, "mode": "arbitrary"},
        )
    for field in (
        "applied_artifact_hash",
        "applied_record_hash",
        "artifact_record_hash",
        "change_manifest_hash",
        "validation_token_hash",
        "validation_token_id",
    ):
        forged = {**bindings, field: "f" * 64}
        if field == "validation_token_id":
            forged[field] = "tok-forged"
        with pytest.raises((AgentError, KeyError), match="BTAG-(APPROVAL|RECORD)"):
            authority.prepare_approval("run", subject, forged)

    request = authority.prepare_approval("run", subject, bindings)
    record_path = (
        state
        / "sessions"
        / session_id
        / "records"
        / "applied-artifact"
        / f"{applied['applied_artifact_hash']}.json"
    )
    record_bytes = record_path.read_bytes()
    forged_record = json.loads(record_bytes)
    forged_record["artifact_record_hash"] = "f" * 64
    record_path.write_text(json.dumps(forged_record, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-SIGNATURE"):
        authority.grant_approval(
            request["request_id"],
            approver="local-user",
            confirmed=True,
        )
    record_path.write_bytes(record_bytes)

    request = authority.prepare_approval("run", subject, bindings)
    grant = authority.grant_approval(
        request["request_id"],
        approver="local-user",
        confirmed=True,
    )
    assert grant["token"]["bindings"] == bindings
    assert sessions.load(session_id)["state"] == "RUN_APPROVED"


def test_installer_is_create_only_idempotent_and_independence_audit_passes(
    tmp_path: Path,
) -> None:
    installer = AdapterInstaller()
    expected = {
        "claude": ".claude/agents/backtrader-agent.md",
        "codex": ".codex/agents/backtrader-agent.toml",
        "opencode": ".opencode/agents/backtrader-agent.md",
        "openclaw": ".openclaw/workspaces/backtrader-agent/AGENTS.md",
    }
    targets = {}
    results = {}
    for host, relative in expected.items():
        target = tmp_path / (f"{host} target" if host == "openclaw" else f"{host}-target")
        target.mkdir()
        targets[host] = target
        preview = installer.install(target, host, apply=False)
        assert preview["status"] == "preview"
        results[host] = installer.install(target, host, apply=True)
        repeated = installer.install(target, host, apply=True)
        assert results[host]["status"] == "installed"
        assert repeated["status"] == "unchanged"
        assert (target / relative).is_file()
        assert installer.uninstall(target, host, apply=False)["status"] == "preview"

    canonical_pairs = (
        (
            PRODUCT_ROOT / "adapters/claude-code/backtrader-agent.md",
            PACKAGE_ROOT / "resources/adapters/claude-code/backtrader-agent.md",
        ),
        (
            PRODUCT_ROOT / "adapters/codex/backtrader-agent.toml",
            PACKAGE_ROOT / "resources/adapters/codex/backtrader-agent.toml",
        ),
        (
            PRODUCT_ROOT / "adapters/opencode/backtrader-agent.md",
            PACKAGE_ROOT / "resources/adapters/opencode/backtrader-agent.md",
        ),
        (
            PRODUCT_ROOT / "adapters/openclaw/workspace/AGENTS.md",
            PACKAGE_ROOT / "resources/adapters/openclaw/workspace/AGENTS.md",
        ),
        (
            PRODUCT_ROOT / "adapters/openclaw/workspace/IDENTITY.md",
            PACKAGE_ROOT / "resources/adapters/openclaw/workspace/IDENTITY.md",
        ),
        (
            PRODUCT_ROOT / "adapters/openclaw/workspace/README.md",
            PACKAGE_ROOT / "resources/adapters/openclaw/workspace/README.md",
        ),
        (
            PRODUCT_ROOT / "adapters/openclaw/workspace/registration-manifest.template.json",
            PACKAGE_ROOT
            / "resources/adapters/openclaw/workspace/registration-manifest.template.json",
        ),
    )
    for source, packaged in canonical_pairs:
        assert source.read_bytes() == packaged.read_bytes()
    assert (PRODUCT_ROOT / "SKILL.md").read_bytes() == (
        PACKAGE_ROOT / "resources/agent-payload.md"
    ).read_bytes()

    openclaw_target = targets["openclaw"]
    openclaw = results["openclaw"]
    workspace = openclaw_target / ".openclaw/workspaces/backtrader-agent"
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / "IDENTITY.md").is_file()
    assert (workspace / "registration-manifest.json").is_file()
    assert not (openclaw_target / ".openclaw/agents/backtrader-agent/agent.json").exists()
    registration = json.loads(
        (workspace / "registration-manifest.json").read_text(encoding="utf-8")
    )
    registration_argv = shlex.split(registration["registration_command"])
    assert registration_argv[registration_argv.index("--workspace") + 1] == str(workspace)
    assert shlex.split(registration["invocation_command"])[-1] == registration["first_request"]
    assert openclaw["manual_registration"]["command"] == registration["registration_command"]
    assert openclaw["manual_registration"]["invoke"] == registration["invocation_command"]
    assert openclaw["manual_registration"]["executed"] is False
    assert registration_argv[:4] == ["openclaw", "agents", "add", "backtrader-agent"]
    assert registration_argv[-1] == "--non-interactive"
    assert openclaw["manual_registration"]["verify"] == "openclaw agents list"

    for host, relative in expected.items():
        removed = installer.uninstall(targets[host], host, apply=True)
        assert removed["status"] == "uninstalled"
        assert not (targets[host] / relative).exists()

    product_root = Path(__file__).resolve().parents[1]
    report = IndependenceAuditor(product_root).audit()
    assert report["status"] == "passed", report
