import errno
import multiprocessing
import time
from pathlib import Path
from threading import BrokenBarrierError

import pytest

import backtrader_agent.locking as locking_module
import backtrader_agent.tokens as tokens_module
from backtrader_agent.errors import AgentError
from backtrader_agent.tokens import TokenAuthority


def _secret_bootstrap_worker(
    state_root: str,
    start_barrier,
    write_barrier,
    outcomes,
) -> None:
    original_write = tokens_module.atomic_write_bytes

    def _coordinated_secret_write(path, data, *, create_only=False):
        if Path(path).name == "token-secret.key" and create_only:
            try:
                write_barrier.wait(timeout=1)
            except BrokenBarrierError:
                # A correct exclusive lock admits one first-time writer, which
                # intentionally breaks this test-only synchronization barrier.
                pass
        return original_write(path, data, create_only=create_only)

    tokens_module.atomic_write_bytes = _coordinated_secret_write
    try:
        start_barrier.wait(timeout=10)
        outcomes.put(("success", TokenAuthority(Path(state_root))._secret().hex()))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        tokens_module.atomic_write_bytes = original_write


def _run_secret_bootstrap_race(state_root: Path, workers: int = 8):
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(workers)
    write_barrier = context.Barrier(workers)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_secret_bootstrap_worker,
            args=(str(state_root), start_barrier, write_barrier, outcomes),
        )
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _approval_lock_worker(state_root: str, request_id: str, start_barrier, outcomes) -> None:
    authority = TokenAuthority(Path(state_root))
    try:
        start_barrier.wait(timeout=10)
        with authority._approval_lock(request_id):
            time.sleep(0.2)
        outcomes.put(("success", request_id))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_approval_lock_race(state_root: Path, request_id: str):
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_approval_lock_worker,
            args=(str(state_root), request_id, start_barrier, outcomes),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _independent_approval_lock_worker(
    state_root: str,
    request_id: str,
    entered_barrier,
    outcomes,
) -> None:
    authority = TokenAuthority(Path(state_root))
    try:
        with authority._approval_lock(request_id):
            entered_barrier.wait(timeout=10)
        outcomes.put(("success", request_id))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_independent_approval_lock_race(state_root: Path, request_ids: tuple):
    context = multiprocessing.get_context("spawn")
    entered_barrier = context.Barrier(len(request_ids))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_independent_approval_lock_worker,
            args=(str(state_root), request_id, entered_barrier, outcomes),
        )
        for request_id in request_ids
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def test_secret_bootstrap_is_linearized_across_processes(tmp_path: Path) -> None:
    state = tmp_path / "state"

    outcomes = _run_secret_bootstrap_race(state)

    assert [outcome[0] for outcome in outcomes] == ["success"] * len(outcomes)
    assert len({outcome[1] for outcome in outcomes}) == 1
    secret = (state / "token-secret.key").read_bytes()
    assert len(secret) == 32
    assert secret.hex() == outcomes[0][1]


def test_existing_secret_is_reused_and_invalid_secret_is_rejected(tmp_path: Path) -> None:
    authority = TokenAuthority(tmp_path / "state")
    authority.state_root.mkdir(parents=True)
    expected = b"s" * 32
    authority.secret_path.write_bytes(expected)

    assert authority._secret() == expected
    authority.secret_path.write_bytes(b"invalid")
    with pytest.raises(AgentError) as failure:
        authority._secret()
    assert failure.value.code == "BTAG-TOKEN-SECRET"


def test_approval_lock_recovers_legacy_lock_path(tmp_path: Path) -> None:
    authority = TokenAuthority(tmp_path / "state")
    request_id = "aprq-" + "a" * 24
    legacy_lock = authority.approval_root / f"{request_id}.lock"
    legacy_lock.parent.mkdir(parents=True)
    legacy_lock.write_bytes(b"")

    with authority._approval_lock(request_id):
        assert legacy_lock.is_file()

    assert legacy_lock.is_file()


def test_approval_lock_waits_for_normal_competition(tmp_path: Path) -> None:
    state = tmp_path / "state"
    request_id = "aprq-" + "b" * 24

    outcomes = _run_approval_lock_race(state, request_id)

    assert outcomes == [("success", request_id)] * len(outcomes)


def test_approval_locks_are_isolated_by_request_id(tmp_path: Path) -> None:
    state = tmp_path / "state"
    request_ids = ("aprq-" + "c" * 24, "aprq-" + "d" * 24)
    authority = TokenAuthority(state)

    outcomes = _run_independent_approval_lock_race(state, request_ids)

    assert set(outcomes) == {("success", request_id) for request_id in request_ids}
    lock_paths = [authority.approval_root / f"{request_id}.lock" for request_id in request_ids]
    assert lock_paths[0] != lock_paths[1]
    assert all(path.is_file() for path in lock_paths)


def test_approval_lock_open_error_has_stable_diagnostic(tmp_path: Path, monkeypatch) -> None:
    authority = TokenAuthority(tmp_path / "state")
    request_id = "aprq-" + "e" * 24

    def _open_error(*_args, **_kwargs):
        raise OSError(errno.EACCES, "injected lock open failure")

    monkeypatch.setattr(locking_module.os, "open", _open_error)
    with pytest.raises(AgentError) as failure:
        with authority._approval_lock(request_id):
            pass
    assert failure.value.code == "BTAG-APPROVAL-LOCK"
