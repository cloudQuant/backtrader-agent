"""R15: sweep prepare enumerates a bounded parameter grid into an immutable SweepPlan.

The prepare contract:

- ``prepare_sweep`` cartesian-expands the grid in sorted parameter-name order;
  every cell value must fall inside the spec-declared ``minimum``/``maximum``
  bounds (violation -> ``BTAG-SWEEP-BOUNDS``) and every grid key must be a
  declared numeric spec parameter (``BTAG-SWEEP-PARAM``).
- Each cell carries a deterministic ``cell_hash`` binding the spec hash and
  the cell parameters; the plan carries a ``plan_hash`` over all other fields
  and lives at ``<state>/sweeps/sweep_<64hex>/sweep-plan.json``.
- ``load_plan`` re-verifies the plan hash and each cell hash and rejects any
  tampering with ``BTAG-SWEEP-PLAN``.
- The session journal records the prepare as a ``sweep`` action event and the
  session lands in ``SWEEP_PREPARED``.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from backtrader_agent import sweep, sweep_run
from backtrader_agent.canonical import hash_object, read_json
from backtrader_agent.cli import build_parser, dispatch
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.data import DatasetService
from backtrader_agent.engines import inspect_engine, inspect_execution_environment
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry
from backtrader_agent.sessions import SessionStore
from backtrader_agent.tokens import TokenAuthority, expected_bindings

from helpers import (
    data_spec,
    dump_json,
    resolve_acceptance_engine_root,
    strategy_spec,
    write_price_csv,
)

DATASET_ID = "ds_" + "d" * 64


def _is_within(base: Path, path: Path) -> bool:
    """Python 3.8-compatible containment check (Path.is_relative_to is 3.9+)."""

    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def _register_engine(state: Path) -> str:
    RootRegistry(state).register(
        "engine",
        resolve_acceptance_engine_root(Path(__file__).resolve().parents[1]),
        writable=False,
        kind="engine",
    )
    return "engine"


def _call(*arguments: str):
    return dispatch(build_parser().parse_args(list(arguments)))


def _sweep_strategy_spec(dataset_id: str) -> dict:
    raw = strategy_spec(dataset_id, profile="python_bundle")
    raw["parameters"] = {
        "fast_period": {"type": "integer", "default": 5, "minimum": 2, "maximum": 200},
        "slow_period": {"type": "integer", "default": 12, "minimum": 3, "maximum": 400},
    }
    return raw


def _dataset_manifest() -> dict:
    manifest = data_spec()
    manifest["dataset_id"] = DATASET_ID
    manifest["manifest_hash"] = hash_object(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    return manifest


def _drive_approved_session(
    state: Path,
    session_id: str,
    spec: StrategySpec,
    dataset: Dict[str, Any],
) -> SessionStore:
    store = SessionStore(state)
    store.create(session_id)
    store.transition(
        session_id,
        "DATA_READY",
        "dataset-register",
        {"dataset": dataset["manifest_hash"]},
        effect_references={
            "dataset_id": dataset["dataset_id"],
            "dataset_manifest_hash": dataset["manifest_hash"],
        },
    )
    store.transition(
        session_id,
        "SPEC_DRAFT",
        "spec-draft",
        {"spec": spec.spec_hash},
        effect_references={"spec_hash": spec.spec_hash},
    )
    store.transition(
        session_id,
        "SPEC_APPROVED",
        "spec-approve",
        {"spec": spec.spec_hash},
        effect_references={"approved_spec_hash": spec.spec_hash},
    )
    return store


def _prepared_plan(
    state: Path,
    session_id: str = "session-001",
    *,
    grid: Dict[str, Any] = None,
) -> Dict[str, Any]:
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    engine_root_id = _register_engine(state)
    if not (state / "sessions" / session_id / "manifest.json").exists():
        _drive_approved_session(state, session_id, spec, dataset)
    return sweep.prepare_sweep(
        state,
        session_id,
        spec,
        dataset,
        (
            grid
            if grid is not None
            else {"fast_period": [10, 20], "slow_period": [30, 40]}
        ),
        engine_root_id=engine_root_id,
    )


def test_sweep_prepare_enumerates_grid(tmp_path: Path) -> None:
    state = tmp_path / "state"
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    engine_root_id = _register_engine(state)
    _drive_approved_session(state, "session-001", spec, dataset)

    plan = sweep.prepare_sweep(
        state,
        "session-001",
        spec,
        dataset,
        {"fast_period": [10, 20], "slow_period": [30, 40]},
        engine_root_id=engine_root_id,
    )

    assert plan["schema_version"] == "sweep-plan-v1"
    assert plan["sweep_id"].startswith("sweep_")
    assert len(plan["sweep_id"]) == 6 + 64
    assert plan["session_id"] == "session-001"
    assert plan["spec_hash"] == spec.spec_hash
    assert plan["dataset_manifest_hash"] == dataset["manifest_hash"]
    engine = inspect_engine(RootRegistry(state), engine_root_id)
    environment = inspect_execution_environment()
    assert plan["engine_root_id"] == engine_root_id
    assert plan["engine_hash"] == engine["engine_hash"]
    assert plan["environment_hash"] == environment["environment_hash"]
    assert len(plan["cells"]) == 4
    # Deterministic sorted-key order: fast_period varies slowest.
    assert [cell["params"] for cell in plan["cells"]] == [
        {"fast_period": 10, "slow_period": 30},
        {"fast_period": 10, "slow_period": 40},
        {"fast_period": 20, "slow_period": 30},
        {"fast_period": 20, "slow_period": 40},
    ]
    hashes = {cell["cell_hash"] for cell in plan["cells"]}
    assert len(hashes) == 4
    for cell in plan["cells"]:
        assert cell["cell_id"] == "cell_" + cell["cell_hash"][:16]
        assert cell["cell_hash"] == hash_object(
            {"spec_hash": spec.spec_hash, "params": cell["params"]}
        )
    assert plan["plan_hash"] == hash_object(
        {key: value for key, value in plan.items() if key != "plan_hash"}
    )

    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"
    assert path.is_file()
    assert not path.is_symlink()
    assert read_json(path) == plan
    assert sweep.load_plan(state, plan["sweep_id"]) == plan

    session = SessionStore(state).load("session-001")
    assert session["state"] == "SWEEP_PREPARED"
    assert session["artifacts"]["sweep_id"] == plan["sweep_id"]
    assert session["artifacts"]["sweep_plan_hash"] == plan["plan_hash"]


def test_sweep_prepare_is_deterministic_across_states(tmp_path: Path) -> None:
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    grid = {"fast_period": [10, 20], "slow_period": [30, 40]}

    first = tmp_path / "first"
    second = tmp_path / "second"
    _register_engine(first)
    _register_engine(second)
    _drive_approved_session(first, "session-001", spec, dataset)
    _drive_approved_session(second, "session-001", spec, dataset)
    plan_a = sweep.prepare_sweep(
        first, "session-001", spec, dataset, grid, engine_root_id="engine"
    )
    plan_b = sweep.prepare_sweep(
        second, "session-001", spec, dataset, grid, engine_root_id="engine"
    )

    assert plan_a == plan_b
    assert plan_a["sweep_id"] == plan_b["sweep_id"]


def test_sweep_prepare_rejects_out_of_bounds(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with pytest.raises(AgentError) as above:
        _prepared_plan(state, grid={"fast_period": [999999]})
    assert above.value.code == "BTAG-SWEEP-BOUNDS"

    with pytest.raises(AgentError) as below:
        _prepared_plan(state, grid={"slow_period": [1]})
    assert below.value.code == "BTAG-SWEEP-BOUNDS"


def test_sweep_prepare_rejects_unknown_param_key(tmp_path: Path) -> None:
    with pytest.raises(AgentError) as raised:
        _prepared_plan(
            tmp_path / "state", grid={"fast_period": [10], "unknown_period": [5]}
        )
    assert raised.value.code == "BTAG-SWEEP-PARAM"


def test_sweep_prepare_rejects_unbounded_spec_param(tmp_path: Path) -> None:
    state = tmp_path / "state"
    raw = strategy_spec(DATASET_ID)  # helpers declare minimum only, no maximum
    spec = StrategySpec.from_dict(raw)
    dataset = _dataset_manifest()
    _register_engine(state)
    _drive_approved_session(state, "session-001", spec, dataset)

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state,
            "session-001",
            spec,
            dataset,
            {"fast_period": [10, 20]},
            engine_root_id="engine",
        )
    assert raised.value.code == "BTAG-SWEEP-BOUNDS"


def test_sweep_prepare_rejects_non_numeric_spec_param(tmp_path: Path) -> None:
    state = tmp_path / "state"
    raw = _sweep_strategy_spec(DATASET_ID)
    raw["parameters"]["verbose"] = {"type": "boolean", "default": False}
    spec = StrategySpec.from_dict(raw)
    dataset = _dataset_manifest()
    _register_engine(state)
    _drive_approved_session(state, "session-001", spec, dataset)

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state,
            "session-001",
            spec,
            dataset,
            {"verbose": [True]},
            engine_root_id="engine",
        )
    assert raised.value.code == "BTAG-SWEEP-PARAM"


@pytest.mark.parametrize(
    "grid",
    [
        {},
        {"fast_period": []},
        {"fast_period": [10.5]},  # non-integral value for an integer parameter
        {"fast_period": ["fast"]},
        {"fast_period": [True]},
        {"fast_period": [10, 10]},  # duplicate values yield duplicate cells
        ["not", "a", "dict"],
    ],
)
def test_sweep_prepare_rejects_malformed_grid(tmp_path: Path, grid: Any) -> None:
    with pytest.raises(AgentError) as raised:
        _prepared_plan(tmp_path / "state", grid=grid)
    assert raised.value.code == "BTAG-SWEEP-GRID"


def test_sweep_plan_tamper_rejected_on_load(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cells"][0]["params"]["fast_period"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"

    # A doctored plan_hash must fail too.
    path.write_text(json.dumps(plan), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["plan_hash"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"

    # A plan moved to another sweep directory must not load.
    foreign = state / "sweeps" / ("sweep_" + "f" * 64) / "sweep-plan.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, "sweep_" + "f" * 64)
    assert raised.value.code == "BTAG-SWEEP-PLAN"


def test_sweep_plan_binds_engine_and_environment(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"

    # An environment_hash swap with a recomputed plan_hash must still reject:
    # the sweep id is content-derived over the sealed fields.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["environment_hash"] = "f" * 64
    payload["plan_hash"] = hash_object(
        {key: value for key, value in payload.items() if key != "plan_hash"}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"

    path.write_text(json.dumps(plan), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["engine_hash"] = "e" * 64
    payload["plan_hash"] = hash_object(
        {key: value for key, value in payload.items() if key != "plan_hash"}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"


def test_load_plan_rejects_non_dict_cell_with_recomputed_hash(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cells"][0] = "tampered"
    payload["plan_hash"] = hash_object(
        {key: value for key, value in payload.items() if key != "plan_hash"}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"


def test_load_plan_rejects_corrupt_or_non_object_plan(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"

    path.write_text("{definitely not json", encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"

    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(AgentError) as raised:
        sweep.load_plan(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-PLAN"


def test_load_plan_rejects_missing_and_malformed_ids(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with pytest.raises(AgentError) as missing:
        sweep.load_plan(state, "sweep_" + "e" * 64)
    assert missing.value.code == "BTAG-SWEEP-PLAN"

    with pytest.raises(AgentError) as malformed:
        sweep.load_plan(state, "sweep_nope")
    assert malformed.value.code == "BTAG-SWEEP-PLAN"


def test_sweep_prepare_requires_approved_session(tmp_path: Path) -> None:
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    state = tmp_path / "state"
    _register_engine(state)

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state,
            "session-001",
            spec,
            dataset,
            {"fast_period": [10]},
            engine_root_id="engine",
        )
    assert raised.value.code == "BTAG-SESSION-UNKNOWN"

    # A session whose approved spec differs must reject the sweep spec.
    store = SessionStore(state)
    store.create("session-001")
    store.transition(
        "session-001",
        "DATA_READY",
        "dataset-register",
        {"dataset": dataset["manifest_hash"]},
        effect_references={
            "dataset_id": dataset["dataset_id"],
            "dataset_manifest_hash": dataset["manifest_hash"],
        },
    )
    store.transition(
        "session-001",
        "SPEC_DRAFT",
        "spec-draft",
        {"spec": spec.spec_hash},
        effect_references={"spec_hash": spec.spec_hash},
    )
    other = StrategySpec.from_dict(_sweep_strategy_spec("ds_" + "c" * 64))
    store.transition(
        "session-001",
        "SPEC_APPROVED",
        "spec-approve",
        {"spec": other.spec_hash},
        effect_references={"approved_spec_hash": other.spec_hash},
    )
    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state,
            "session-001",
            spec,
            dataset,
            {"fast_period": [10]},
            engine_root_id="engine",
        )
    assert raised.value.code == "BTAG-SWEEP-SESSION"


def test_sweep_prepare_rejects_mismatched_dataset(tmp_path: Path) -> None:
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    state = tmp_path / "state"
    _drive_approved_session(state, "session-001", spec, dataset)

    # A dataset manifest bound to a different dataset id must reject.
    other = dict(dataset)
    other["dataset_id"] = "ds_" + "c" * 64
    other["manifest_hash"] = hash_object(
        {key: value for key, value in other.items() if key != "manifest_hash"}
    )
    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state,
            "session-001",
            spec,
            other,
            {"fast_period": [10]},
            engine_root_id="engine",
        )
    assert raised.value.code == "BTAG-SWEEP-DATASET"


def test_sweep_prepare_is_idempotent_replay(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)

    replay = _prepared_plan(state)

    assert replay == plan
    journal = state / "sessions" / "session-001" / "journal.jsonl"
    events = [
        json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    sweep_events = [event for event in events if event.get("action_type") == "sweep"]
    assert len(sweep_events) == 1
    assert sweep_events[0]["to_state"] == "SWEEP_PREPARED"
    assert sweep_events[0]["effect_references"]["sweep_id"] == plan["sweep_id"]


def test_cli_sweep_prepare_workflow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inputs = tmp_path / "inputs"
    workspace.mkdir()
    inputs.mkdir()
    write_price_csv(inputs / "prices.csv", rows=40)
    state = workspace / ".backtrader-agent"
    common = ("--state-root", str(state))

    _call(
        *common,
        "roots",
        "register",
        "--id",
        "workspace",
        "--path",
        str(workspace),
        "--kind",
        "workspace",
        "--writable",
    )
    _call(
        *common,
        "roots",
        "register",
        "--id",
        "input",
        "--path",
        str(inputs),
        "--kind",
        "dataset",
    )
    _call(
        *common,
        "roots",
        "register",
        "--id",
        "engine",
        "--path",
        str(resolve_acceptance_engine_root(Path(__file__).resolve().parents[1])),
        "--kind",
        "engine",
    )
    _call(*common, "session", "create", "--session-id", "sweep-1")

    data_spec_path = dump_json(tmp_path / "data-spec.json", data_spec())
    dataset = _call(
        *common,
        "data",
        "register",
        "--session-id",
        "sweep-1",
        "--spec",
        str(data_spec_path),
    )
    spec_path = dump_json(
        tmp_path / "strategy-spec.json", _sweep_strategy_spec(dataset["dataset_id"])
    )
    _call(
        *common,
        "spec",
        "--session-id",
        "sweep-1",
        "--approve",
        "--file",
        str(spec_path),
    )

    plan = _call(
        *common,
        "sweep",
        "prepare",
        "--session-id",
        "sweep-1",
        "--spec",
        str(spec_path),
        "--dataset-manifest",
        json.dumps(dataset),
        "--param-grid",
        json.dumps({"fast_period": [10, 20], "slow_period": [30, 40]}),
        "--engine-root-id",
        "engine",
    )
    assert plan["schema_version"] == "sweep-plan-v1"
    assert plan["engine_root_id"] == "engine"
    assert len(plan["cells"]) == 4

    session = _call(*common, "session", "status", "--session-id", "sweep-1")
    assert session["state"] == "SWEEP_PREPARED"
    journal = state / "sessions" / "sweep-1" / "journal.jsonl"
    events = [
        json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    sweep_events = [event for event in events if event.get("action_type") == "sweep"]
    assert len(sweep_events) == 1
    assert sweep_events[0]["to_state"] == "SWEEP_PREPARED"
    assert sweep_events[0]["effect_references"]["sweep_id"] == plan["sweep_id"]


# --- R16: one-time sweep approval tokens -------------------------------------


def _sweep_bindings(plan: Dict[str, Any]) -> Dict[str, str]:
    return {
        "session_id": plan["session_id"],
        "sweep_plan_hash": plan["plan_hash"],
        "dataset_manifest_hash": plan["dataset_manifest_hash"],
        "environment_hash": plan["environment_hash"],
        "engine_hash": plan["engine_hash"],
        "engine_root_id": plan["engine_root_id"],
        "spec_hash": plan["spec_hash"],
    }


def test_sweep_approval_roundtrip(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    authority = TokenAuthority(state)

    request = authority.prepare_approval(
        "sweep", plan["plan_hash"], _sweep_bindings(plan)
    )
    assert request["kind"] == "sweep"
    assert request["state"] == "PENDING"
    assert request["subject_hash"] == plan["plan_hash"]
    grant = authority.grant_approval(
        request["request_id"], approver="me", confirmed=True
    )
    token = grant["token"]
    assert token["kind"] == "sweep"
    assert token["bindings"] == _sweep_bindings(plan)

    authority.verify(
        token,
        kind="sweep",
        subject_hash=plan["plan_hash"],
        required_bindings=expected_bindings("sweep", **_sweep_bindings(plan)),
    )
    # First consumption succeeds; a replay under a different effect rejects.
    authority.consume(token, effect_id="a" * 64)
    with pytest.raises(AgentError) as replayed:
        authority.consume(token, effect_id="b" * 64)
    assert replayed.value.code == "BTAG-TOKEN-CONSUMED"


def test_sweep_token_replay_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    authority = TokenAuthority(state)
    request = authority.prepare_approval(
        "sweep", plan["plan_hash"], _sweep_bindings(plan)
    )
    token = authority.grant_approval(
        request["request_id"], approver="me", confirmed=True
    )["token"]

    # The sweep token cannot be verified as another action kind.
    with pytest.raises(AgentError) as reused:
        authority.verify(token, kind="run", subject_hash=plan["plan_hash"])
    assert reused.value.code == "BTAG-TOKEN-KIND"

    # The same token cannot act on another sweep plan.
    with pytest.raises(AgentError) as foreign:
        authority.verify(token, kind="sweep", subject_hash="0" * 64)
    assert foreign.value.code == "BTAG-TOKEN-BINDING"

    # Consumption is one-time: same effect replays, a new effect rejects.
    authority.consume(token, effect_id="c" * 64)
    record = authority.consume(token, effect_id="c" * 64)
    assert record["state"] == "CONSUMED"
    with pytest.raises(AgentError) as replayed:
        authority.consume(token, effect_id="d" * 64)
    assert replayed.value.code == "BTAG-TOKEN-CONSUMED"


def test_sweep_token_cross_session_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state, session_id="session-001")
    authority = TokenAuthority(state)

    # An unknown session cannot approve any sweep plan.
    with pytest.raises(AgentError) as unknown:
        authority.prepare_approval(
            "sweep",
            plan["plan_hash"],
            {**_sweep_bindings(plan), "session_id": "session-404"},
        )
    assert unknown.value.code == "BTAG-SESSION-UNKNOWN"

    # A different session holding its own sweep plan must not approve this one.
    _prepared_plan(state, session_id="session-002")
    with pytest.raises(AgentError) as cross:
        authority.prepare_approval(
            "sweep",
            plan["plan_hash"],
            {**_sweep_bindings(plan), "session_id": "session-002"},
        )
    assert cross.value.code == "BTAG-APPROVAL-BINDING"


def test_sweep_kind_missing_bindings_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    authority = TokenAuthority(state)

    with pytest.raises(AgentError) as missing:
        authority.prepare_approval(
            "sweep", plan["plan_hash"], {"session_id": "session-001"}
        )
    assert missing.value.code == "BTAG-TOKEN-BINDING"

    # A binding value that contradicts the signed plan must reject.
    tampered = {**_sweep_bindings(plan), "spec_hash": "0" * 64}
    with pytest.raises(AgentError) as tamper:
        authority.prepare_approval("sweep", plan["plan_hash"], tampered)
    assert tamper.value.code == "BTAG-APPROVAL-BINDING"


def test_sweep_approval_binds_legacy_plan_fields_only(tmp_path: Path) -> None:
    """Legacy v1 plans (predating engine/environment fields) stay approvable.

    The approval validator derives what is bindable from the CURRENT plan
    record only: fields the plan does not carry are not assumed present.
    """

    state = tmp_path / "state"
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    _register_engine(state)
    _drive_approved_session(state, "legacy-1", spec, dataset)

    # Build a plan the way an old prepare_sweep would have: no engine or
    # environment bindings, session and dataset fields intact.
    cell_hash = hash_object(
        {"spec_hash": spec.spec_hash, "params": {"fast_period": 10}}
    )
    payload = {
        "schema_version": "sweep-plan-v1",
        "session_id": "legacy-1",
        "spec_hash": spec.spec_hash,
        "dataset_manifest_hash": dataset["manifest_hash"],
        "cells": [
            {
                "cell_id": "cell_" + cell_hash[:16],
                "params": {"fast_period": 10},
                "cell_hash": cell_hash,
            }
        ],
    }
    legacy = dict(payload)
    legacy["sweep_id"] = "sweep_" + hash_object(payload)
    legacy["plan_hash"] = hash_object(legacy)
    legacy_dir = state / "sweeps" / legacy["sweep_id"]
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "sweep-plan.json").write_text(json.dumps(legacy), encoding="utf-8")
    SessionStore(state).transition(
        "legacy-1",
        "SWEEP_PREPARED",
        "sweep",
        {"spec": spec.spec_hash, "dataset": dataset["manifest_hash"]},
        effect_references={
            "sweep_id": legacy["sweep_id"],
            "sweep_plan_hash": legacy["plan_hash"],
        },
    )

    authority = TokenAuthority(state)
    bindings = {
        "session_id": "legacy-1",
        "sweep_plan_hash": legacy["plan_hash"],
        "dataset_manifest_hash": legacy["dataset_manifest_hash"],
        "environment_hash": "e" * 64,
        "engine_hash": "f" * 64,
        "engine_root_id": "engine",
        "spec_hash": legacy["spec_hash"],
    }
    request = authority.prepare_approval("sweep", legacy["plan_hash"], bindings)
    token = authority.grant_approval(
        request["request_id"], approver="me", confirmed=True
    )["token"]
    assert token["kind"] == "sweep"
    assert token["bindings"]["sweep_plan_hash"] == legacy["plan_hash"]
    authority.verify(
        token,
        kind="sweep",
        subject_hash=legacy["plan_hash"],
    )


def test_cli_sweep_approval_request_and_grant(tmp_path: Path) -> None:
    state = tmp_path / "state"
    plan = _prepared_plan(state)
    common = ("--state-root", str(state))

    request = _call(
        *common,
        "approval",
        "request",
        "--kind",
        "sweep",
        "--subject-hash",
        plan["plan_hash"],
        "--bindings",
        json.dumps(_sweep_bindings(plan)),
    )
    assert request["kind"] == "sweep"
    assert request["state"] == "PENDING"

    grant = _call(
        *common,
        "approval",
        "grant",
        "--request-id",
        request["request_id"],
        "--approver",
        "local-user",
        "--confirm",
    )
    assert grant["token"]["kind"] == "sweep"
    assert grant["token"]["bindings"]["sweep_plan_hash"] == plan["plan_hash"]


# --- R17/R18: sweep run and report --------------------------------------------


def _make_approved_sweep(
    tmp_path: Path,
    *,
    grid: Optional[Dict[str, Any]] = None,
    session_id: str = "session-001",
    engine_root: Optional[Path] = None,
) -> Tuple[Path, RootRegistry, TokenAuthority, Dict[str, Any], Dict[str, Any]]:
    """Run-ready sweep setup: registered roots, a CAS dataset, an approved
    spec, a prepared SweepPlan, and a granted one-time sweep token."""
    workspace = tmp_path / "workspace"
    inputs = tmp_path / "inputs"
    workspace.mkdir()
    inputs.mkdir()
    write_price_csv(inputs / "prices.csv", rows=40)
    state = workspace / ".backtrader-agent"
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    roots.register("input", inputs, writable=False, kind="dataset")
    if engine_root is None:
        _register_engine(state)
    else:
        roots.register("engine", engine_root, writable=False, kind="engine")
    dataset = DatasetService(roots, state).register(data_spec())
    spec = StrategySpec.from_dict(_sweep_strategy_spec(dataset["dataset_id"]))
    sessions = SessionStore(state)
    sessions.create(session_id)
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
    plan = sweep.prepare_sweep(
        state,
        session_id,
        spec,
        dataset,
        (
            grid
            if grid is not None
            else {"fast_period": [5, 10], "slow_period": [15, 20]}
        ),
        engine_root_id="engine",
    )
    authority = TokenAuthority(state)
    request = authority.prepare_approval(
        "sweep", plan["plan_hash"], _sweep_bindings(plan)
    )
    token = authority.grant_approval(
        request["request_id"], approver="me", confirmed=True
    )["token"]
    return state, roots, authority, plan, token


def _cell_dir(state: Path, plan: Dict[str, Any], cell: Dict[str, Any]) -> Path:
    return state / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]


def _journal_events(state: Path, session_id: str) -> list:
    journal = state / "sessions" / session_id / "journal.jsonl"
    return [
        json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
    ]


def test_sweep_run_two_by_two(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state, roots, authority, plan, token = _make_approved_sweep(tmp_path)
    before = {str(path) for path in workspace.rglob("*")}

    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["schema_version"] == "sweep-run-v1"
    assert result["sweep_id"] == plan["sweep_id"]
    assert result["cells_total"] == 4
    assert result["cells_completed"] == 4
    assert result["cells_failed"] == 0
    assert result["cells_skipped"] == 0

    report = sweep_run.sweep_report(state, plan["sweep_id"])
    assert report["schema_version"] == "sweep-result-v1"
    assert report["sweep_id"] == plan["sweep_id"]
    assert report["plan_hash"] == plan["plan_hash"]
    assert report["cells_completed"] == 4
    assert report["cells_failed"] == 0
    assert report["cells_pending"] == 0
    assert [cell["status"] for cell in report["cells"]] == ["passed"] * 4
    finals = [cell["metrics"]["final_value"] for cell in report["cells"]]
    assert finals == sorted(finals, reverse=True)  # ranked by final_value desc
    assert report["report_hash"] == hash_object(
        {key: value for key, value in report.items() if key != "report_hash"}
    )

    # Every cell renders a private draft under state/sweeps and persists its
    # own RunManifest/RunResult without touching the workspace.
    for cell in plan["cells"]:
        cell_dir = _cell_dir(state, plan, cell)
        assert (cell_dir / "artifact-manifest.json").is_file()
        assert (cell_dir / "run.py").is_file()
        assert (cell_dir / "run-manifest.json").is_file()
        assert (cell_dir / "run-result.json").is_file()
        # R20: sweep cells retain child output through the shared execution core.
        assert (cell_dir / "stdout.log").is_file()
        assert (cell_dir / "stderr.log").is_file()
        assert "BACKTRADER_AGENT_RESULT=" not in (cell_dir / "stdout.log").read_text(
            encoding="utf-8"
        )
    new_files = {str(path) for path in workspace.rglob("*")} - before
    assert new_files, "the run must persist per-cell records"
    assert all(_is_within(state, Path(path)) for path in new_files)

    # Journal records the sweep start (RUNNING) and sweep-complete events.
    session = SessionStore(state).load("session-001")
    assert session["state"] == "PASSED"
    sweep_events = [
        event
        for event in _journal_events(state, "session-001")
        if event.get("action_type") in {"sweep", "sweep-complete"}
    ]
    assert [event["to_state"] for event in sweep_events] == [
        "SWEEP_PREPARED",
        "RUNNING",
        "PASSED",
    ]
    assert sweep_events[-1]["action_type"] == "sweep-complete"
    assert sweep_events[-1]["effect_references"]["cells_completed"] == "4"


def test_sweep_run_respects_max_cells(tmp_path: Path) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(tmp_path)

    result = sweep_run.run_sweep(
        state, roots, authority, plan["sweep_id"], token, max_cells=2
    )

    assert result["cells_completed"] == 2
    assert result["cells_skipped"] == 2
    report = sweep_run.sweep_report(state, plan["sweep_id"])
    assert report["cells_completed"] == 2
    assert report["cells_pending"] == 2
    assert [cell["status"] for cell in report["cells"][:2]] == ["passed", "passed"]
    assert [cell["status"] for cell in report["cells"][2:]] == ["pending", "pending"]
    assert SessionStore(state).load("session-001")["state"] == "PASSED"


def test_sweep_run_refuses_legacy_plan_without_engine_fields(tmp_path: Path) -> None:
    """Legacy plans predating engine/environment/spec fields fail closed.

    A legacy plan can still receive a sweep token (bindings are form-required
    only, Task 14), but the run must never trust the caller-supplied
    unattested engine/environment values: the sealed plan is the only
    attestation the run may execute from.
    """
    state = tmp_path / "state"
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    _register_engine(state)
    _drive_approved_session(state, "legacy-1", spec, dataset)

    cell_hash = hash_object(
        {"spec_hash": spec.spec_hash, "params": {"fast_period": 10}}
    )
    payload = {
        "schema_version": "sweep-plan-v1",
        "session_id": "legacy-1",
        "spec_hash": spec.spec_hash,
        "dataset_manifest_hash": dataset["manifest_hash"],
        "cells": [
            {
                "cell_id": "cell_" + cell_hash[:16],
                "params": {"fast_period": 10},
                "cell_hash": cell_hash,
            }
        ],
    }
    legacy = dict(payload)
    legacy["sweep_id"] = "sweep_" + hash_object(payload)
    legacy["plan_hash"] = hash_object(legacy)
    legacy_dir = state / "sweeps" / legacy["sweep_id"]
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "sweep-plan.json").write_text(json.dumps(legacy), encoding="utf-8")
    SessionStore(state).transition(
        "legacy-1",
        "SWEEP_PREPARED",
        "sweep",
        {"spec": spec.spec_hash, "dataset": dataset["manifest_hash"]},
        effect_references={
            "sweep_id": legacy["sweep_id"],
            "sweep_plan_hash": legacy["plan_hash"],
        },
    )
    authority = TokenAuthority(state)
    bindings = {
        "session_id": "legacy-1",
        "sweep_plan_hash": legacy["plan_hash"],
        "dataset_manifest_hash": legacy["dataset_manifest_hash"],
        "environment_hash": "e" * 64,
        "engine_hash": "f" * 64,
        "engine_root_id": "engine",
        "spec_hash": legacy["spec_hash"],
    }
    request = authority.prepare_approval("sweep", legacy["plan_hash"], bindings)
    token = authority.grant_approval(
        request["request_id"], approver="me", confirmed=True
    )["token"]

    with pytest.raises(AgentError) as raised:
        sweep_run.run_sweep(
            state, RootRegistry(state), authority, legacy["sweep_id"], token
        )
    assert raised.value.code == "BTAG-SWEEP-LEGACY"
    authority.require_issued(token)  # the refusal must not consume the token


def test_sweep_run_reverifies_engine_and_environment(tmp_path: Path) -> None:
    acceptance_root = resolve_acceptance_engine_root(
        Path(__file__).resolve().parents[1]
    )
    engine_copy = tmp_path / "engine"
    shutil.copytree(acceptance_root / "backtrader", engine_copy / "backtrader")
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, engine_root=engine_copy
    )

    # A mutated engine tree must reject the run before the token is consumed.
    target = engine_copy / "backtrader" / "cerebro.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# mutation\n", encoding="utf-8"
    )
    with pytest.raises(AgentError) as raised:
        sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)
    assert raised.value.code == "BTAG-SWEEP-ENGINE"
    authority.require_issued(token)


def test_sweep_run_reverifies_execution_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(tmp_path)
    monkeypatch.setattr(
        sweep_run,
        "inspect_execution_environment",
        lambda: {
            "schema_version": "execution-environment-v1",
            "environment_hash": "e" * 64,
        },
    )

    with pytest.raises(AgentError) as raised:
        sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)
    assert raised.value.code == "BTAG-SWEEP-ENVIRONMENT"
    authority.require_issued(token)


def test_sweep_run_replays_completed_cells_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5, 10]}
    )
    real_execute = sweep_run._execute_profile
    calls = {"count": 0}

    def crash_on_second(profile):
        calls["count"] += 1
        if calls["count"] == 2:
            # A process-level kill (SIGINT/SIGKILL) journals nothing: the
            # session stays RUNNING and recover() forces PAUSED for resume.
            raise KeyboardInterrupt
        return real_execute(profile)

    monkeypatch.setattr(sweep_run, "_execute_profile", crash_on_second)
    with pytest.raises(KeyboardInterrupt):
        sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)
    assert SessionStore(state).load("session-001")["state"] == "RUNNING"
    first_result_path = _cell_dir(state, plan, plan["cells"][0]) / "run-result.json"
    first_result = read_json(first_result_path)

    # Resuming with the same one-time token and identical arguments replays
    # the completed cell byte-identically and finishes the remaining cell.
    monkeypatch.setattr(sweep_run, "_execute_profile", real_execute)
    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)
    assert result["cells_completed"] == 2
    assert result["cells_failed"] == 0
    assert read_json(first_result_path) == first_result
    assert SessionStore(state).load("session-001")["state"] == "PASSED"


def test_sweep_cell_transient_failure_retries_once_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )
    real_execute = sweep_run._execute_profile
    calls = {"count": 0}

    def flaky(profile):
        calls["count"] += 1
        if calls["count"] == 1:
            raise subprocess.TimeoutExpired(profile["argv"], profile["timeout_seconds"])
        return real_execute(profile)

    monkeypatch.setattr(sweep_run, "_execute_profile", flaky)
    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["cells_completed"] == 1
    assert result["cells_failed"] == 0
    assert calls["count"] == 2
    cell_dir = _cell_dir(state, plan, plan["cells"][0])
    attempt = read_json(cell_dir / "run-attempt.json")
    assert attempt["schema_version"] == "run-attempt-v1"
    assert attempt["status"] == "failed"
    assert attempt["failure_code"] == "BTAG-RUN-TIMEOUT"
    persisted = read_json(cell_dir / "run-result.json")
    assert persisted["status"] == "passed"
    assert persisted["extensions"]["backtrader_agent"]["attempts"] == 2
    assert SessionStore(state).load("session-001")["state"] == "PASSED"


def test_sweep_cell_timeout_lands_redacted_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wall-clock-killed cell keeps what it printed before the kill: the
    redacted failure logs carry the retained partial streams (R20)."""
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )
    partial = b"PROBE-TIMEOUT-PARTIAL /tmp/fake-path\n"
    monkeypatch.setattr(
        sweep_run,
        "_execute_profile",
        lambda profile: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(
                profile["argv"],
                profile["timeout_seconds"],
                output=partial,
                stderr=partial,
            )
        ),
    )

    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["cells_failed"] == 1
    cell_dir = _cell_dir(state, plan, plan["cells"][0])
    stdout_log = (cell_dir / "stdout.log").read_text(encoding="utf-8")
    stderr_log = (cell_dir / "stderr.log").read_text(encoding="utf-8")
    assert "PROBE-TIMEOUT-PARTIAL" in stdout_log
    assert "PROBE-TIMEOUT-PARTIAL" in stderr_log
    assert "/tmp/fake-path" not in stdout_log
    assert "/tmp/fake-path" not in stderr_log
    assert len(stdout_log.encode("utf-8")) <= 2000
    assert len(stderr_log.encode("utf-8")) <= 2000


def test_sweep_cell_persistent_transient_failure_fails_sweep_and_report_rejects_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )
    monkeypatch.setattr(
        sweep_run,
        "_execute_profile",
        lambda profile: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(profile["argv"], profile["timeout_seconds"])
        ),
    )

    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["cells_completed"] == 0
    assert result["cells_failed"] == 1
    session = SessionStore(state).load("session-001")
    assert session["state"] == "FAILED"
    assert session["retry_eligible"] is False
    report = sweep_run.sweep_report(state, plan["sweep_id"])
    failed = [cell for cell in report["cells"] if cell["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["metrics"] is None
    assert failed[0]["failure_code"] == "BTAG-RUN-TIMEOUT"
    cell_dir = _cell_dir(state, plan, plan["cells"][0])
    attempt = read_json(cell_dir / "run-attempt.json")
    assert attempt["failure_code"] == "BTAG-RUN-TIMEOUT"
    # R20: a failed cell still lands its logs (empty redacted tails here —
    # the monkeypatched timeout never produced child output).
    assert (cell_dir / "stdout.log").is_file()
    assert (cell_dir / "stderr.log").is_file()

    # A tampered persisted cell result must be rejected by the report.
    result_path = cell_dir / "run-result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "run-result-v1",
                "run_id": "run-tampered",
                "status": "passed",
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AgentError) as raised:
        sweep_run.sweep_report(state, plan["sweep_id"])
    assert raised.value.code == "BTAG-SWEEP-RESULT"


def test_sweep_cell_failure_does_not_corrupt_other_cells(tmp_path: Path) -> None:
    """One bad cell (a tampered cell draft) must not abort or strand the sweep.

    The render of the tampered cell fails with a domain error; the cell is
    persisted as failed and the remaining cells still complete.
    """
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5, 10]}
    )
    tampered = plan["cells"][0]
    tampered_dir = _cell_dir(state, plan, tampered)
    tampered_dir.mkdir(parents=True)
    (tampered_dir / "artifact-manifest.json").write_text("tampered", encoding="utf-8")

    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["cells_completed"] == 1
    assert result["cells_failed"] == 1
    failed_record = read_json(tampered_dir / "run-result.json")
    assert failed_record["status"] == "failed"
    assert (
        failed_record["extensions"]["backtrader_agent"]["failure_code"]
        == "BTAG-DRAFT-MANIFEST"
    )
    report = sweep_run.sweep_report(state, plan["sweep_id"])
    assert [cell["status"] for cell in report["cells"]] == ["passed", "failed"]
    assert report["cells_completed"] == 1
    assert report["cells_failed"] == 1
    # The sweep completed (journaled), it did not silently strand in RUNNING.
    assert SessionStore(state).load("session-001")["state"] == "FAILED"


def test_sweep_unexpected_cell_exception_journals_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected in-process cell exception journals FAILED before re-raising.

    A non-AgentError exception cannot be contained as a per-cell failure, so
    the sweep must never leave the session stranded in RUNNING with the
    one-time token already consumed.
    """
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )
    monkeypatch.setattr(
        sweep_run,
        "_execute_profile",
        lambda profile: (_ for _ in ()).throw(OSError("simulated child exec failure")),
    )

    with pytest.raises(OSError):
        sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    session = SessionStore(state).load("session-001")
    assert session["state"] == "FAILED"
    assert session["retry_eligible"] is False
    failed_events = [
        event
        for event in _journal_events(state, "session-001")
        if event["to_state"] == "FAILED"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["action_type"] == "sweep-failed"
    assert (
        failed_events[0]["effect_references"]["run_failure_code"] == "BTAG-SWEEP-CRASH"
    )
    assert failed_events[0]["effect_references"]["sweep_id"] == plan["sweep_id"]
    assert failed_events[0]["effect_references"]["cells_pending"] == "1"


def test_cli_sweep_run_two_by_two(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inputs = tmp_path / "inputs"
    workspace.mkdir()
    inputs.mkdir()
    write_price_csv(inputs / "prices.csv", rows=40)
    state = workspace / ".backtrader-agent"
    common = ("--state-root", str(state))

    _call(
        *common,
        "roots",
        "register",
        "--id",
        "workspace",
        "--path",
        str(workspace),
        "--kind",
        "workspace",
        "--writable",
    )
    _call(
        *common,
        "roots",
        "register",
        "--id",
        "input",
        "--path",
        str(inputs),
        "--kind",
        "dataset",
    )
    _call(
        *common,
        "roots",
        "register",
        "--id",
        "engine",
        "--path",
        str(resolve_acceptance_engine_root(Path(__file__).resolve().parents[1])),
        "--kind",
        "engine",
    )
    _call(*common, "session", "create", "--session-id", "sweep-1")
    data_spec_path = dump_json(tmp_path / "data-spec.json", data_spec())
    dataset = _call(
        *common,
        "data",
        "register",
        "--session-id",
        "sweep-1",
        "--spec",
        str(data_spec_path),
    )
    spec_path = dump_json(
        tmp_path / "strategy-spec.json", _sweep_strategy_spec(dataset["dataset_id"])
    )
    _call(
        *common,
        "spec",
        "--session-id",
        "sweep-1",
        "--approve",
        "--file",
        str(spec_path),
    )
    plan = _call(
        *common,
        "sweep",
        "prepare",
        "--session-id",
        "sweep-1",
        "--spec",
        str(spec_path),
        "--dataset-manifest",
        json.dumps(dataset),
        "--param-grid",
        json.dumps({"fast_period": [5, 10], "slow_period": [15, 20]}),
        "--engine-root-id",
        "engine",
    )
    request = _call(
        *common,
        "approval",
        "request",
        "--kind",
        "sweep",
        "--subject-hash",
        plan["plan_hash"],
        "--bindings",
        json.dumps(_sweep_bindings(plan)),
    )
    grant = _call(
        *common,
        "approval",
        "grant",
        "--request-id",
        request["request_id"],
        "--approver",
        "local-user",
        "--confirm",
    )
    before = {str(path) for path in workspace.rglob("*")}

    result = _call(
        *common,
        "sweep",
        "run",
        "--sweep-id",
        plan["sweep_id"],
        "--token",
        json.dumps(grant["token"]),
    )
    assert result["cells_completed"] == 4
    assert result["cells_failed"] == 0

    report = _call(*common, "sweep", "report", "--sweep-id", plan["sweep_id"])
    assert report["schema_version"] == "sweep-result-v1"
    finals = [cell["metrics"]["final_value"] for cell in report["cells"]]
    assert finals == sorted(finals, reverse=True)

    # Sweep is run-only: the workspace gains no files outside the state root.
    new_files = {str(path) for path in workspace.rglob("*")} - before
    assert all(_is_within(state, Path(path)) for path in new_files)
    session = _call(*common, "session", "status", "--session-id", "sweep-1")
    assert session["state"] == "PASSED"
