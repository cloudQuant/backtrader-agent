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
from pathlib import Path
from typing import Any, Dict

import pytest

from backtrader_agent import sweep
from backtrader_agent.canonical import hash_object, read_json
from backtrader_agent.cli import build_parser, dispatch
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.errors import AgentError
from backtrader_agent.sessions import SessionStore

from helpers import data_spec, dump_json, strategy_spec, write_price_csv

DATASET_ID = "ds_" + "d" * 64


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
    )


def test_sweep_prepare_enumerates_grid(tmp_path: Path) -> None:
    state = tmp_path / "state"
    spec = StrategySpec.from_dict(_sweep_strategy_spec(DATASET_ID))
    dataset = _dataset_manifest()
    _drive_approved_session(state, "session-001", spec, dataset)

    plan = sweep.prepare_sweep(
        state,
        "session-001",
        spec,
        dataset,
        {"fast_period": [10, 20], "slow_period": [30, 40]},
    )

    assert plan["schema_version"] == "sweep-plan-v1"
    assert plan["sweep_id"].startswith("sweep_")
    assert len(plan["sweep_id"]) == 6 + 64
    assert plan["session_id"] == "session-001"
    assert plan["spec_hash"] == spec.spec_hash
    assert plan["dataset_manifest_hash"] == dataset["manifest_hash"]
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
    _drive_approved_session(first, "session-001", spec, dataset)
    _drive_approved_session(second, "session-001", spec, dataset)
    plan_a = sweep.prepare_sweep(first, "session-001", spec, dataset, grid)
    plan_b = sweep.prepare_sweep(second, "session-001", spec, dataset, grid)

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
    _drive_approved_session(state, "session-001", spec, dataset)

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(
            state, "session-001", spec, dataset, {"fast_period": [10, 20]}
        )
    assert raised.value.code == "BTAG-SWEEP-BOUNDS"


def test_sweep_prepare_rejects_non_numeric_spec_param(tmp_path: Path) -> None:
    state = tmp_path / "state"
    raw = _sweep_strategy_spec(DATASET_ID)
    raw["parameters"]["verbose"] = {"type": "boolean", "default": False}
    spec = StrategySpec.from_dict(raw)
    dataset = _dataset_manifest()
    _drive_approved_session(state, "session-001", spec, dataset)

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(state, "session-001", spec, dataset, {"verbose": [True]})
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

    with pytest.raises(AgentError) as raised:
        sweep.prepare_sweep(state, "session-001", spec, dataset, {"fast_period": [10]})
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
        sweep.prepare_sweep(state, "session-001", spec, dataset, {"fast_period": [10]})
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
        sweep.prepare_sweep(state, "session-001", spec, other, {"fast_period": [10]})
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
    )
    assert plan["schema_version"] == "sweep-plan-v1"
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
