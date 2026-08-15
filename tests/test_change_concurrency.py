import errno
import json
import multiprocessing
import os
from pathlib import Path

import pytest

import backtrader_agent.locking as locking_module
from backtrader_agent.canonical import sha256_bytes
from backtrader_agent.changes import ChangeManager
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry
from backtrader_agent.tokens import TokenAuthority
from test_tokens_changes_sessions import _validated_product_artifact


def _approval_bindings(change: dict) -> dict:
    return {
        "artifact_hash": change["artifact_hash"],
        "artifact_record_hash": change["artifact_record_hash"],
        "change_manifest_hash": change["manifest_hash"],
        "dataset_hash": change["dataset_manifest_hash"],
        "dataset_id": change["dataset_id"],
        "session_id": change["session_id"],
        "spec_hash": change["spec_hash"],
        "validation_token_hash": change["validation_token_hash"],
        "validation_token_id": change["validation_token_id"],
    }


def _prepare_authorized_change(
    workspace: Path,
    session_id: str,
    *,
    target: str,
    target_root_id: str = "workspace",
) -> tuple:
    state = workspace / ".backtrader-agent"
    artifact, validation, roots, authority = _validated_product_artifact(
        workspace,
        session_id,
        profile="single_test",
    )
    manager = ChangeManager(roots, state, authority)
    change = manager.prepare(
        session_id=session_id,
        draft_root=Path(artifact["_draft_path"]),
        files=[{"source": artifact["files"][0]["path"], "target": target}],
        target_root_id=target_root_id,
        validation_token=validation,
    )
    request = authority.prepare_approval(
        "change",
        change["manifest_hash"],
        _approval_bindings(change),
    )
    token = authority.grant_approval(
        request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    return state, roots, authority, manager, change, token


def _apply_worker(
    state_root: str,
    change: dict,
    token: dict,
    idempotency_key: str,
    *,
    pause_after_applying: bool,
    applying_started,
    allow_replace,
    second_started,
    live_rollback_seen,
    outcomes,
) -> None:
    state = Path(state_root)
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    original_replace = manager._replace_target
    original_rollback = manager._rollback_transaction

    if pause_after_applying:

        def _pause_before_replace(index: int, target: Path, staged: Path, create_only: bool) -> None:
            applying_started.set()
            if not allow_replace.wait(timeout=20):
                raise RuntimeError("test did not release the first apply worker")
            original_replace(index, target, staged, create_only)

        manager._replace_target = _pause_before_replace
    else:

        def _observe_live_rollback(transaction_path, transaction, resolved_by_path) -> None:
            if transaction.get("state") == "APPLYING":
                live_rollback_seen.set()
            original_rollback(transaction_path, transaction, resolved_by_path)

        manager._rollback_transaction = _observe_live_rollback

    try:
        if not pause_after_applying:
            second_started.set()
        result = manager.apply(change, token, idempotency_key=idempotency_key)
        outcomes.put(
            (
                "success",
                result["applied_artifact_hash"],
                result["applied_record_hash"],
            )
        )
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_live_apply_race(state: Path, change: dict, token: dict, idempotency_key: str) -> tuple:
    context = multiprocessing.get_context("spawn")
    applying_started = context.Event()
    allow_replace = context.Event()
    second_started = context.Event()
    live_rollback_seen = context.Event()
    outcomes = context.Queue()
    first = context.Process(
        target=_apply_worker,
        args=(str(state), change, token, idempotency_key),
        kwargs={
            "pause_after_applying": True,
            "applying_started": applying_started,
            "allow_replace": allow_replace,
            "second_started": second_started,
            "live_rollback_seen": live_rollback_seen,
            "outcomes": outcomes,
        },
    )
    second = context.Process(
        target=_apply_worker,
        args=(str(state), change, token, idempotency_key),
        kwargs={
            "pause_after_applying": False,
            "applying_started": applying_started,
            "allow_replace": allow_replace,
            "second_started": second_started,
            "live_rollback_seen": live_rollback_seen,
            "outcomes": outcomes,
        },
    )
    first.start()
    assert applying_started.wait(timeout=10)
    second.start()
    assert second_started.wait(timeout=10)
    live_rollback = live_rollback_seen.wait(timeout=2)
    allow_replace.set()
    for process in (first, second):
        process.join(timeout=20)
        assert process.exitcode == 0
    return live_rollback, [outcomes.get(timeout=5) for _ in range(2)]


def _change_lock_worker(state_root: str, root_id: str, entered_barrier, outcomes) -> None:
    state = Path(state_root)
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    try:
        with manager._locked_target_root(root_id):
            entered_barrier.wait(timeout=10)
        outcomes.put(("success", root_id))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_distinct_change_lock_race(state: Path, root_ids: tuple) -> list:
    context = multiprocessing.get_context("spawn")
    entered_barrier = context.Barrier(len(root_ids))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_change_lock_worker,
            args=(str(state), root_id, entered_barrier, outcomes),
        )
        for root_id in root_ids
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _cross_root_key_worker(
    state_root: str,
    change: dict,
    token: dict,
    idempotency_key: str,
    *,
    pause_before_replace: bool,
    first_replacement,
    allow_first_replace,
    second_replacement,
    second_started,
    outcomes,
) -> None:
    state = Path(state_root)
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    original_replace = manager._replace_target

    def _coordinated_replace(index: int, target: Path, staged: Path, create_only: bool) -> None:
        if pause_before_replace:
            first_replacement.set()
            if not allow_first_replace.wait(timeout=20):
                raise RuntimeError("test did not release the first key worker")
        else:
            second_replacement.set()
        original_replace(index, target, staged, create_only)

    manager._replace_target = _coordinated_replace
    try:
        if not pause_before_replace:
            second_started.set()
        result = manager.apply(change, token, idempotency_key=idempotency_key)
        outcomes.put(("success", result["session_id"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_cross_root_key_race(
    state: Path,
    first_change: dict,
    first_token: dict,
    second_change: dict,
    second_token: dict,
    idempotency_key: str,
) -> tuple:
    context = multiprocessing.get_context("spawn")
    first_replacement = context.Event()
    allow_first_replace = context.Event()
    second_replacement = context.Event()
    second_started = context.Event()
    outcomes = context.Queue()
    first = context.Process(
        target=_cross_root_key_worker,
        args=(str(state), first_change, first_token, idempotency_key),
        kwargs={
            "pause_before_replace": True,
            "first_replacement": first_replacement,
            "allow_first_replace": allow_first_replace,
            "second_replacement": second_replacement,
            "second_started": second_started,
            "outcomes": outcomes,
        },
    )
    second = context.Process(
        target=_cross_root_key_worker,
        args=(str(state), second_change, second_token, idempotency_key),
        kwargs={
            "pause_before_replace": False,
            "first_replacement": first_replacement,
            "allow_first_replace": allow_first_replace,
            "second_replacement": second_replacement,
            "second_started": second_started,
            "outcomes": outcomes,
        },
    )
    first.start()
    assert first_replacement.wait(timeout=10)
    second.start()
    assert second_started.wait(timeout=10)
    reached_second_target = second_replacement.wait(timeout=2)
    allow_first_replace.set()
    for process in (first, second):
        process.join(timeout=20)
        assert process.exitcode == 0
    return reached_second_target, [outcomes.get(timeout=5) for _ in range(2)]


def _action_lock_worker(state_root: str, key: str, entered_barrier, outcomes) -> None:
    state = Path(state_root)
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    try:
        with manager._locked_action_key(key):
            entered_barrier.wait(timeout=10)
        outcomes.put(("success", key))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_distinct_action_lock_race(state: Path, keys: tuple) -> list:
    context = multiprocessing.get_context("spawn")
    entered_barrier = context.Barrier(len(keys))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_action_lock_worker,
            args=(str(state), key, entered_barrier, outcomes),
        )
        for key in keys
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def test_live_apply_is_not_rolled_back_by_a_competing_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state, _roots, _authority, manager, change, token = _prepare_authorized_change(
        workspace,
        "concurrent-apply",
        target="generated/concurrent.py",
    )

    live_rollback, outcomes = _run_live_apply_race(state, change, token, "concurrent-apply")

    assert not live_rollback
    assert [outcome[0] for outcome in outcomes] == ["success", "success"]
    assert len({outcome[1:] for outcome in outcomes}) == 1
    target = workspace / "generated/concurrent.py"
    assert sha256_bytes(target.read_bytes()) == change["changes"][0]["source_hash"]
    transaction = json.loads(
        (state / "transactions" / change["change_id"] / "transaction.json").read_text(
            encoding="utf-8"
        )
    )
    assert transaction["state"] == "COMMITTED"
    action_path = manager._action_path("concurrent-apply")
    assert action_path.is_file()
    assert list(action_path.parent.glob("*.json")) == [action_path]


def test_same_idempotency_key_cannot_mutate_a_second_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    left = tmp_path / "left"
    right = tmp_path / "right"
    workspace.mkdir()
    left.mkdir()
    right.mkdir()
    state = workspace / ".backtrader-agent"
    roots = RootRegistry(state)
    roots.register("left", left, writable=True, kind="workspace")
    roots.register("right", right, writable=True, kind="workspace")
    state, _roots, _authority, first, first_change, first_token = _prepare_authorized_change(
        workspace,
        "idempotency-left",
        target="generated/left.py",
        target_root_id="left",
    )
    _state, _roots, _authority, _second, second_change, second_token = _prepare_authorized_change(
        workspace,
        "idempotency-right",
        target="generated/right.py",
        target_root_id="right",
    )

    reached_second_target, outcomes = _run_cross_root_key_race(
        state,
        first_change,
        first_token,
        second_change,
        second_token,
        "global-key-race",
    )

    assert not reached_second_target
    assert ("success", "idempotency-left") in outcomes
    assert ("agent-error", "BTAG-IDEMPOTENCY-CONFLICT") in outcomes
    first_target = left / "generated/left.py"
    second_target = right / "generated/right.py"
    assert sha256_bytes(first_target.read_bytes()) == first_change["changes"][0]["source_hash"]
    assert not second_target.exists()
    second_approval = json.loads(
        (state / "approvals" / f"{second_token['approval_request_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert second_approval["state"] == "ISSUED"
    assert first._action_path("global-key-race").is_file()


def test_same_root_rechecks_preimage_after_another_change_commits(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _state, _roots, _authority, first, first_change, first_token = _prepare_authorized_change(
        workspace,
        "first-change",
        target="generated/shared.py",
    )
    _state, _roots, _authority, second, second_change, second_token = _prepare_authorized_change(
        workspace,
        "second-change",
        target="generated/shared.py",
    )

    first.apply(first_change, first_token, idempotency_key="first-change-apply")
    with pytest.raises(AgentError) as failure:
        second.apply(second_change, second_token, idempotency_key="second-change-apply")
    assert failure.value.code == "BTAG-CHANGE-PREIMAGE"
    target = workspace / "generated/shared.py"
    assert sha256_bytes(target.read_bytes()) == first_change["changes"][0]["source_hash"]


def test_change_locks_are_isolated_by_root_id(tmp_path: Path) -> None:
    state = tmp_path / "state"
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    roots = RootRegistry(state)
    roots.register("left", left, writable=True, kind="workspace")
    roots.register("right", right, writable=True, kind="workspace")
    manager = ChangeManager(roots, state, TokenAuthority(state))

    outcomes = _run_distinct_change_lock_race(state, ("left", "right"))

    assert set(outcomes) == {("success", "left"), ("success", "right")}
    paths = [manager._target_root_lock_path(root_id) for root_id in ("left", "right")]
    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)


def test_action_locks_are_isolated_by_idempotency_key(tmp_path: Path) -> None:
    state = tmp_path / "state"
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    keys = ("action-key-left", "action-key-right")

    outcomes = _run_distinct_action_lock_race(state, keys)

    assert set(outcomes) == {("success", key) for key in keys}
    paths = [manager._action_lock_path(key) for key in keys]
    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)


def test_change_lock_open_error_has_stable_diagnostic(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    manager = ChangeManager(roots, state, TokenAuthority(state))

    def _open_error(*_args, **_kwargs):
        raise OSError(errno.EACCES, "injected change lock open failure")

    monkeypatch.setattr(locking_module.os, "open", _open_error)
    with pytest.raises(AgentError) as failure:
        with manager._locked_target_root("workspace"):
            pass
    assert failure.value.code == "BTAG-CHANGE-LOCK"


def test_action_lock_open_error_has_stable_diagnostic(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))

    def _open_error(*_args, **_kwargs):
        raise OSError(errno.EACCES, "injected action lock open failure")

    monkeypatch.setattr(locking_module.os, "open", _open_error)
    with pytest.raises(AgentError) as failure:
        with manager._locked_action_key("action-key-open"):
            pass
    assert failure.value.code == "BTAG-CHANGE-ACTION-LOCK"


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_action_lock_acquire_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    state = tmp_path / "state"
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    original_close = locking_module.os.close
    closed_descriptors = []

    def _acquire_error(_descriptor, _operation):
        raise OSError(errno.EIO, "injected action lock acquire failure")

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _acquire_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with manager._locked_action_key("action-key-acquire"):
            pass
    assert failure.value.code == "BTAG-CHANGE-ACTION-LOCK"
    assert len(closed_descriptors) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_action_lock_release_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    state = tmp_path / "state"
    manager = ChangeManager(RootRegistry(state), state, TokenAuthority(state))
    original_flock = fcntl.flock
    original_close = locking_module.os.close
    closed_descriptors = []

    def _release_error(descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "injected action lock release failure")
        return original_flock(descriptor, operation)

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _release_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with manager._locked_action_key("action-key-release"):
            pass
    assert failure.value.code == "BTAG-CHANGE-ACTION-LOCK"
    assert len(closed_descriptors) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_change_lock_acquire_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    manager = ChangeManager(roots, state, TokenAuthority(state))
    original_close = locking_module.os.close
    closed_descriptors = []

    def _acquire_error(_descriptor, _operation):
        raise OSError(errno.EIO, "injected change lock acquire failure")

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _acquire_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with manager._locked_target_root("workspace"):
            pass
    assert failure.value.code == "BTAG-CHANGE-LOCK"
    assert len(closed_descriptors) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_change_lock_release_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    manager = ChangeManager(roots, state, TokenAuthority(state))
    original_flock = fcntl.flock
    original_close = locking_module.os.close
    closed_descriptors = []

    def _release_error(descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "injected change lock release failure")
        return original_flock(descriptor, operation)

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _release_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with manager._locked_target_root("workspace"):
            pass
    assert failure.value.code == "BTAG-CHANGE-LOCK"
    assert len(closed_descriptors) == 1
