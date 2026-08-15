"""Cross-process run-action serialization regression coverage."""

import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any, Dict

import pytest

import backtrader_agent.locking as locking_module
from backtrader_agent.canonical import hash_object
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry
from backtrader_agent.runner import ControlledRunner
from backtrader_agent.sessions import SessionStore


class _ProbeAuthority:
    """Minimal authority double; the test exercises the runner's mutable tail."""

    def verify(self, token: Dict[str, Any], **_kwargs: Any) -> Dict[str, Any]:
        return token

    def consume(self, token: Dict[str, Any], *, effect_id: str) -> Dict[str, Any]:
        return {"state": "CONSUMED", "effect_id": effect_id, "token_id": token["token_id"]}


class _ProbeRunner(ControlledRunner):
    """Drive the production run tail into a deliberately held real child process."""

    def __init__(self, state_root: Path, started_root: Path, release_path: Path) -> None:
        super().__init__(RootRegistry(state_root), state_root, _ProbeAuthority())
        self.started_root = Path(started_root)
        self.release_path = Path(release_path)

    def _verify_applied(self, applied: Dict[str, Any]) -> None:
        del applied

    def _verify_registered_dataset(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        return dataset

    def _resolve_engine(self, validation_token: Dict[str, Any]):
        del validation_token
        return self.state_root, {"root_id": "probe", "hash": "e" * 64, "version": "probe"}

    def _verify_execution_environment(self, validation_token: Dict[str, Any]) -> Dict[str, Any]:
        del validation_token
        return {"environment_hash": "f" * 64}

    @staticmethod
    def _require_profile_dependencies(profile: str) -> None:
        del profile

    def _verify_files(self, applied: Dict[str, Any]) -> Path:
        del applied
        return self.state_root / "probe-artifact" / "run.py"

    @staticmethod
    def _dataset_descriptors(dataset: Dict[str, Any]):
        del dataset
        return []

    def _child_environment(self, descriptors, mode: str, engine_root: Path) -> Dict[str, str]:
        del descriptors, mode, engine_root
        return {
            "PATH": os.environ.get("PATH", ""),
            "PROBE_STARTED_ROOT": str(self.started_root),
            "PROBE_RELEASE_PATH": str(self.release_path),
        }


def _probe_inputs() -> tuple:
    validation_token = {"token_id": "validation-probe"}
    applied = {
        "session_id": "session-probe",
        "applied_artifact_hash": "a" * 64,
        "applied_record_hash": "b" * 64,
        "artifact_hash": "c" * 64,
        "artifact_record_hash": "d" * 64,
        "dataset_id": "ds_" + "e" * 64,
        "dataset_manifest_hash": "e" * 64,
        "validation_token_hash": hash_object(validation_token),
        "validation_token_id": "validation-probe",
        "change_manifest_hash": "g" * 64,
        "spec_hash": "h" * 64,
        "profile": "python_bundle",
        "entrypoint": "run.py",
    }
    dataset = {"dataset_id": applied["dataset_id"], "manifest_hash": "e" * 64, "feeds": []}
    run_token = {"token_id": "run-probe", "approval_id": "approval-probe"}
    return applied, dataset, validation_token, run_token


def _write_probe_script(state_root: Path) -> None:
    metrics = {
        "bar_num": 1,
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
    payload = "BACKTRADER_AGENT_RESULT=" + json.dumps({"metrics": metrics}, sort_keys=True)
    script = "\n".join(
        (
            "import os",
            "import time",
            "from pathlib import Path",
            "",
            "started = Path(os.environ['PROBE_STARTED_ROOT'])",
            "started.mkdir(parents=True, exist_ok=True)",
            "(started / f'child-{os.getpid()}').write_text('started', encoding='utf-8')",
            "release = Path(os.environ['PROBE_RELEASE_PATH'])",
            "while not release.exists():",
            "    time.sleep(0.01)",
            f"print({payload!r})",
            "",
        )
    )
    artifact = state_root / "probe-artifact"
    artifact.mkdir(parents=True)
    (artifact / "run.py").write_text(script, encoding="utf-8")


def _prepare_probe_state(state_root: Path) -> None:
    _write_probe_script(state_root)
    sessions = SessionStore(state_root)
    sessions.create("session-probe")
    for state in (
        "DATA_READY",
        "SPEC_DRAFT",
        "SPEC_APPROVED",
        "SOURCES_SELECTED",
        "DRAFT_READY",
        "VALIDATED",
        "APPLY_PREPARED",
        "APPLIED",
        "RUN_APPROVED",
    ):
        kwargs = {"approval_token_id": "run-probe"} if state == "RUN_APPROVED" else {}
        sessions.transition("session-probe", state, state.lower(), {"probe": state}, **kwargs)


def _run_probe_worker(
    state_text: str,
    started_text: str,
    release_text: str,
    idempotency_key: str,
    outcomes,
) -> None:
    state_root = Path(state_text)
    try:
        result = _ProbeRunner(state_root, Path(started_text), Path(release_text)).run(
            *_probe_inputs(),
            mode="runonce",
            idempotency_key=idempotency_key,
            timeout_seconds=15,
        )
    except Exception as exc:  # pragma: no cover - parent asserts serialized outcome
        outcomes.put(("error", type(exc).__name__, str(exc)))
    else:
        outcomes.put(("success", result["result_hash"]))


def _wait_for_child_count(started_root: Path, expected: int, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if len(list(started_root.glob("child-*"))) >= expected:
            return
        time.sleep(0.02)
    raise AssertionError(f"expected {expected} controlled child starts")


def _probe_process(context, state_root: Path, started_root: Path, release_path: Path, outcomes):
    return context.Process(
        target=_run_probe_worker,
        args=(
            str(state_root),
            str(started_root),
            str(release_path),
            "same-run-action",
            outcomes,
        ),
    )


def _hold_action_lock(
    state_text: str,
    idempotency_key: str,
    acquired,
    release,
    outcomes,
) -> None:
    try:
        runner = ControlledRunner(RootRegistry(Path(state_text)), Path(state_text), _ProbeAuthority())
        with runner._locked_action(idempotency_key, timeout_seconds=1):
            acquired.set()
            release.wait(timeout=10)
    except Exception as exc:  # pragma: no cover - parent asserts worker outcome
        outcomes.put(("error", type(exc).__name__, str(exc)))
    else:
        outcomes.put(("success", idempotency_key))


def test_same_run_action_key_launches_only_one_child_across_processes(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    started_root = tmp_path / "started"
    release_path = tmp_path / "release"
    _prepare_probe_state(state_root)
    context = mp.get_context("spawn")
    outcomes = context.Queue()
    first = _probe_process(context, state_root, started_root, release_path, outcomes)
    second = _probe_process(context, state_root, started_root, release_path, outcomes)

    first_error = None
    first.start()
    try:
        _wait_for_child_count(started_root, 1)
        second.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(list(started_root.glob("child-*"))) == 1:
            time.sleep(0.02)
        assert len(list(started_root.glob("child-*"))) == 1
    except AssertionError as exc:
        first_error = exc
    finally:
        release_path.write_text("release", encoding="utf-8")
        first.join(timeout=20)
        if second.pid is not None:
            second.join(timeout=20)

    if first_error is not None:
        first_outcome = outcomes.get(timeout=2)
        raise AssertionError(f"{first_error}; first worker outcome={first_outcome}")

    assert first.exitcode == 0
    assert second.exitcode == 0
    results = [outcomes.get(timeout=2) for _ in range(2)]
    assert [item[0] for item in results] == ["success", "success"], results
    assert results[0][1] == results[1][1]
    assert SessionStore(state_root).load("session-probe")["state"] == "COMPLETED"


def test_same_completed_run_key_replays_and_rejects_a_different_request(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    started_root = tmp_path / "started"
    release_path = tmp_path / "release"
    _prepare_probe_state(state_root)
    release_path.write_text("release", encoding="utf-8")
    first = _ProbeRunner(state_root, started_root, release_path).run(
        *_probe_inputs(),
        mode="runonce",
        idempotency_key="completed-run-action",
        timeout_seconds=15,
    )
    replay = _ProbeRunner(state_root, started_root, release_path).run(
        *_probe_inputs(),
        mode="runonce",
        idempotency_key="completed-run-action",
        timeout_seconds=15,
    )
    assert replay == first
    assert len(list(started_root.glob("child-*"))) == 1

    applied, dataset, validation_token, run_token = _probe_inputs()
    conflicting_token = {**run_token, "token_id": "run-probe-conflict"}
    with pytest.raises(AgentError, match="BTAG-IDEMPOTENCY-CONFLICT"):
        _ProbeRunner(state_root, started_root, release_path).run(
            applied,
            dataset,
            validation_token,
            conflicting_token,
            mode="runonce",
            idempotency_key="completed-run-action",
            timeout_seconds=15,
        )
    assert len(list(started_root.glob("child-*"))) == 1


def test_different_run_action_keys_are_lock_isolated(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    context = mp.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    outcomes = context.Queue()
    holder = context.Process(
        target=_hold_action_lock,
        args=(str(state_root), "first-run-action", acquired, release, outcomes),
    )
    holder.start()
    assert acquired.wait(timeout=5)
    runner = ControlledRunner(RootRegistry(state_root), state_root, _ProbeAuthority())
    started = time.monotonic()
    with runner._locked_action("second-run-action", timeout_seconds=1):
        assert time.monotonic() - started < 1.0
    release.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0
    assert outcomes.get(timeout=2) == ("success", "first-run-action")
    assert runner._action_lock_path("first-run-action").is_file()
    assert runner._action_lock_path("second-run-action").is_file()


def test_run_action_lock_uses_stable_error_code_and_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    runner = ControlledRunner(RootRegistry(state_root), state_root, _ProbeAuthority())
    lock_path = runner._action_lock_path("diagnostic-run-action")

    actual_open = locking_module.os.open
    actual_close = locking_module.os.close

    def fail_open(*_args: Any, **_kwargs: Any) -> int:
        raise OSError("open failure")

    monkeypatch.setattr(locking_module.os, "open", fail_open)
    with pytest.raises(AgentError, match="BTAG-RUN-ACTION-LOCK"):
        with runner._locked_action("diagnostic-run-action", timeout_seconds=1):
            pass
    monkeypatch.setattr(locking_module.os, "open", actual_open)

    closed = []

    def close_after_recording(descriptor: int) -> None:
        closed.append(descriptor)
        actual_close(descriptor)

    monkeypatch.setattr(locking_module.os, "close", close_after_recording)
    with runner._locked_action("diagnostic-run-action", timeout_seconds=1):
        pass
    assert closed
    assert lock_path.is_file()



@pytest.mark.parametrize("phase", ("prepare", "acquire", "release", "close"))
def test_run_action_lock_maps_shared_lock_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    state_root = tmp_path / "state"
    runner = ControlledRunner(RootRegistry(state_root), state_root, _ProbeAuthority())

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(f"{phase} failure")

    if phase == "prepare":
        monkeypatch.setattr(locking_module, "_prepare_windows_lock", fail)
    elif phase == "acquire":
        monkeypatch.setattr(locking_module, "_acquire_file_lock", fail)
    elif phase == "release":
        monkeypatch.setattr(locking_module, "_release_file_lock", fail)
    else:
        actual_close = locking_module.os.close

        def close_after_cleanup(descriptor: int) -> None:
            actual_close(descriptor)
            fail()

        monkeypatch.setattr(locking_module.os, "close", close_after_cleanup)

    with pytest.raises(AgentError, match="BTAG-RUN-ACTION-LOCK") as raised:
        with runner._locked_action("diagnostic-run-action", timeout_seconds=1):
            pass
    assert raised.value.code == "BTAG-RUN-ACTION-LOCK"
