"""R22: cross-session memory store for dataset notes and parameter priors.

The memory contract:

- ``MemoryStore`` persists ``<state>/memory/datasets.json`` (dataset_id ->
  registered_at, last_used_at, host note) and ``<state>/memory/params.json``
  (archetype -> top-5 sweep-derived parameter priors) with atomic writes, a
  ``schema_version``, and a ``hash`` binding; a tampered store is rejected on
  load with ``AgentError``.
- ``sweep_run.run_sweep`` records the top-5 ranked passed cells as parameter
  priors for the plan archetype on completion. The write is best-effort: a
  poisoned or unwritable memory store warns on stderr and never fails the
  sweep or its session.
"""

import json
from pathlib import Path

import pytest

from backtrader_agent import memory, sweep_run
from backtrader_agent.canonical import hash_object
from backtrader_agent.cli import build_parser, dispatch
from backtrader_agent.errors import AgentError
from backtrader_agent.sessions import SessionStore
from test_sweep import _make_approved_sweep


@pytest.fixture
def make_approved_sweep(tmp_path: Path):
    """Run-ready sweep in the brief's shape: (state, roots, authority, sweep_id, token)."""
    state, roots, authority, plan, token = _make_approved_sweep(tmp_path)
    return state, roots, authority, plan["sweep_id"], token


def _prior_cell(sweep_id: str, fast: int, slow: int, final_value: float) -> dict:
    return {
        "sweep_id": sweep_id,
        "cell_id": "cell_"
        + hash_object(
            {
                "sweep_id": sweep_id,
                "params": {"fast_period": fast, "slow_period": slow},
            }
        )[:16],
        "params": {"fast_period": fast, "slow_period": slow},
        "final_value": final_value,
    }


def test_memory_store_roundtrip_and_tamper(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    store.note_dataset("ds_x", "daily bars, works well with sma")
    assert store.datasets()["ds_x"]["note"] == "daily bars, works well with sma"
    # 篡改后加载拒绝 (tampered stores are rejected on load)
    p = tmp_path / "state" / "memory" / "datasets.json"
    payload = json.loads(p.read_text())
    payload["ds_x"]["note"] = "hacked"
    p.write_text(json.dumps(payload))
    with pytest.raises(AgentError):
        store.datasets()


def test_sweep_writes_param_priors(tmp_path: Path, make_approved_sweep) -> None:
    state, roots, authority, sweep_id, token = make_approved_sweep
    sweep_run.run_sweep(state, roots, authority, sweep_id, token)
    priors = memory.MemoryStore(state).param_priors("single_data_indicator")
    assert priors and "fast_period" in priors[0]["params"]
    # Ranked top-5 by final_value descending, bound to this sweep.
    assert len(priors) == 4
    finals = [prior["final_value"] for prior in priors]
    assert finals == sorted(finals, reverse=True)
    assert all(prior["sweep_id"] == sweep_id for prior in priors)


def test_record_priors_keeps_top_five_ranked(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    cells = [
        _prior_cell("sweep_a", fast=index, slow=20, final_value=float(index))
        for index in range(1, 8)
    ]
    store.record_priors("single_data_indicator", cells)

    priors = store.param_priors("single_data_indicator")
    assert [prior["params"]["fast_period"] for prior in priors] == [7, 6, 5, 4, 3]
    assert [prior["final_value"] for prior in priors] == [7.0, 6.0, 5.0, 4.0, 3.0]
    assert all(prior["recorded_at"].endswith("Z") for prior in priors)


def test_record_priors_merges_and_deduplicates_across_sweeps(
    tmp_path: Path,
) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    store.record_priors(
        "multi_timeframe",
        [
            _prior_cell("sweep_a", 1, 10, 1.0),
            _prior_cell("sweep_a", 2, 10, 3.0),
            _prior_cell("sweep_a", 3, 10, 2.0),
        ],
    )
    store.record_priors(
        "multi_timeframe",
        [
            # Same params as sweep_a's (1, 10): the higher final_value wins.
            _prior_cell("sweep_b", 1, 10, 4.0),
            _prior_cell("sweep_b", 4, 10, 0.5),
        ],
    )

    priors = store.param_priors("multi_timeframe")
    assert [prior["params"]["fast_period"] for prior in priors] == [1, 2, 3, 4]
    assert priors[0]["final_value"] == 4.0
    assert priors[0]["sweep_id"] == "sweep_b"


def test_param_priors_unknown_archetype_is_empty(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    assert store.param_priors("single_data_indicator") == []


def test_params_store_tamper_rejected(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    store.record_priors("single_data_indicator", [_prior_cell("sweep_a", 5, 20, 1.0)])
    path = tmp_path / "state" / "memory" / "params.json"
    payload = json.loads(path.read_text())
    payload["single_data_indicator"][0]["final_value"] = 999.0
    path.write_text(json.dumps(payload))

    with pytest.raises(AgentError) as raised:
        store.param_priors("single_data_indicator")
    assert raised.value.code == "BTAG-MEMORY-HASH"


def test_datasets_store_is_hash_bound_and_schema_versioned(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    store.note_dataset("ds_x", "note one")
    path = tmp_path / "state" / "memory" / "datasets.json"
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == "memory-datasets-v1"
    assert payload["hash"] == hash_object(
        {key: value for key, value in payload.items() if key != "hash"}
    )


def test_note_dataset_preserves_registered_at_and_bumps_last_used(
    tmp_path: Path,
) -> None:
    store = memory.MemoryStore(tmp_path / "state")
    store.note_dataset("ds_x", "first")
    first = store.datasets()["ds_x"]
    assert first["registered_at"] == first["last_used_at"]
    assert first["registered_at"].endswith("Z")

    store.note_dataset("ds_x", "second")
    second = store.datasets()["ds_x"]
    assert second["registered_at"] == first["registered_at"]
    assert second["last_used_at"] >= first["last_used_at"]
    assert second["note"] == "second"


def test_memory_input_validation(tmp_path: Path) -> None:
    store = memory.MemoryStore(tmp_path / "state")

    with pytest.raises(AgentError) as raised:
        store.note_dataset("", "note")
    assert raised.value.code == "BTAG-MEMORY-INPUT"

    with pytest.raises(AgentError) as raised:
        store.note_dataset("hash", "note")  # reserved meta key
    assert raised.value.code == "BTAG-MEMORY-INPUT"

    with pytest.raises(AgentError) as raised:
        store.note_dataset("ds_x", "")
    assert raised.value.code == "BTAG-MEMORY-INPUT"

    with pytest.raises(AgentError) as raised:
        store.record_priors("single_data_indicator", [])
    assert raised.value.code == "BTAG-MEMORY-INPUT"

    with pytest.raises(AgentError) as raised:
        store.record_priors(
            "single_data_indicator", [{"params": {}, "final_value": 1.0}]
        )
    assert raised.value.code == "BTAG-MEMORY-PRIOR"

    with pytest.raises(AgentError) as raised:
        store.record_priors(
            "single_data_indicator",
            [{"params": {"fast_period": 5}, "final_value": float("nan")}],
        )
    assert raised.value.code == "BTAG-MEMORY-PRIOR"


def test_sweep_priors_failure_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )

    def boom(self, archetype, cells):
        raise AgentError("BTAG-MEMORY-LOCK", "simulated memory failure")

    monkeypatch.setattr(memory.MemoryStore, "record_priors", boom)
    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    # The memory store is a convenience outside the sweep's deliverables: a
    # priors write failure must not fail the cells or the session.
    assert result["cells_completed"] == 1
    assert result["cells_failed"] == 0
    assert SessionStore(state).load("session-001")["state"] == "PASSED"
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert captured.out == ""


def test_sweep_poisoned_memory_store_is_best_effort(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    state, roots, authority, plan, token = _make_approved_sweep(
        tmp_path, grid={"fast_period": [5]}
    )
    # A tampered params.json (e.g. from an unrelated session) must not break
    # this sweep: the poisoned store is rejected on load, warned about, and
    # the run completes normally.
    params_path = state / "memory" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps({"schema_version": "memory-params-v1", "hash": "f" * 64}),
        encoding="utf-8",
    )

    result = sweep_run.run_sweep(state, roots, authority, plan["sweep_id"], token)

    assert result["cells_completed"] == 1
    assert result["cells_failed"] == 0
    assert SessionStore(state).load("session-001")["state"] == "PASSED"
    assert "BTAG-MEMORY-HASH" in capsys.readouterr().err


def _call(*arguments: str):
    return dispatch(build_parser().parse_args(list(arguments)))


def test_cli_memory_note_and_list_workflow(tmp_path: Path) -> None:
    common = ("--state-root", str(tmp_path / "state"))

    noted = _call(
        *common, "memory", "note", "--dataset-id", "ds_x", "--note", "daily bars"
    )
    assert noted == {"dataset_id": "ds_x", "note": "daily bars", "status": "recorded"}

    listed = _call(*common, "memory", "list", "--datasets")
    assert listed["datasets"]["ds_x"]["note"] == "daily bars"
    assert _call(*common, "memory", "list")["datasets"]["ds_x"]["note"] == "daily bars"

    memory.MemoryStore(tmp_path / "state").record_priors(
        "single_data_indicator", [_prior_cell("sweep_a", 5, 20, 2.0)]
    )
    params = _call(*common, "memory", "list", "--params")
    assert params["priors"]["single_data_indicator"][0]["params"] == {
        "fast_period": 5,
        "slow_period": 20,
    }
    filtered = _call(
        *common,
        "memory",
        "list",
        "--params",
        "--archetype",
        "single_data_indicator",
    )
    assert filtered["priors"] == params["priors"]["single_data_indicator"]

    with pytest.raises(AgentError) as raised:
        _call(*common, "memory", "list", "--archetype", "single_data_indicator")
    assert raised.value.code == "BTAG-MEMORY-INPUT"
