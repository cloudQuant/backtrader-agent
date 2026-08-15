import errno
import json
import multiprocessing
import os
from pathlib import Path
from threading import BrokenBarrierError

import pytest

import backtrader_agent.canonical as canonical_module
import backtrader_agent.locking as locking_module
import backtrader_agent.roots as roots_module
from backtrader_agent.canonical import atomic_write_bytes, atomic_write_json
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry


def _create_only_worker(
    destination: str,
    content: bytes,
    start_barrier,
    publish_barrier,
    outcomes,
) -> None:
    original_replace = canonical_module.os.replace

    def _coordinated_replace(source, target):
        if str(target) == destination:
            try:
                publish_barrier.wait(timeout=2)
            except BrokenBarrierError:
                pass
        return original_replace(source, target)

    canonical_module.os.replace = _coordinated_replace
    try:
        start_barrier.wait(timeout=10)
        atomic_write_bytes(Path(destination), content, create_only=True)
        outcomes.put(("success", content.decode("ascii")))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        canonical_module.os.replace = original_replace


def _run_create_only_race(destination: Path, contents: tuple) -> list:
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(len(contents))
    publish_barrier = context.Barrier(len(contents))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_create_only_worker,
            args=(str(destination), content, start_barrier, publish_barrier, outcomes),
        )
        for content in contents
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _root_register_worker(
    state_root: str,
    root_id: str,
    root_path: str,
    start_barrier,
    write_barrier,
    outcomes,
) -> None:
    original_write = roots_module.atomic_write_json

    def _coordinated_write(path, value, *, create_only=False):
        if Path(path).name == "roots.json":
            try:
                write_barrier.wait(timeout=2)
            except BrokenBarrierError:
                pass
        return original_write(path, value, create_only=create_only)

    roots_module.atomic_write_json = _coordinated_write
    try:
        start_barrier.wait(timeout=10)
        result = RootRegistry(Path(state_root)).register(
            root_id,
            Path(root_path),
            writable=True,
            kind="workspace",
        )
        outcomes.put(("success", result["root_id"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        roots_module.atomic_write_json = original_write


def _run_root_register_race(state_root: Path, registrations: tuple) -> list:
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(len(registrations))
    write_barrier = context.Barrier(len(registrations))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_root_register_worker,
            args=(str(state_root), root_id, str(root_path), start_barrier, write_barrier, outcomes),
        )
        for root_id, root_path in registrations
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def test_create_only_publish_never_overwrites_a_competing_payload(tmp_path: Path) -> None:
    destination = tmp_path / "state" / "immutable.bin"
    contents = (b"first-payload", b"second-payload")

    outcomes = _run_create_only_race(destination, contents)

    assert [outcome[0] for outcome in outcomes].count("success") == 1
    assert [outcome for outcome in outcomes if outcome[0] == "agent-error"] == [
        ("agent-error", "BTAG-WRITE-EXISTS")
    ]
    assert destination.read_bytes() in contents
    assert not list(destination.parent.glob(f".{destination.name}.*"))


def test_create_only_json_inherits_no_clobber_and_replace_remains_supported(tmp_path: Path) -> None:
    destination = tmp_path / "state" / "record.json"
    first = {"value": "first"}
    second = {"value": "second"}

    atomic_write_json(destination, first, create_only=True)
    with pytest.raises(AgentError) as failure:
        atomic_write_json(destination, second, create_only=True)
    assert failure.value.code == "BTAG-WRITE-EXISTS"
    assert json.loads(destination.read_text(encoding="utf-8")) == first
    atomic_write_json(destination, second)
    assert json.loads(destination.read_text(encoding="utf-8")) == second


def test_root_registry_register_keeps_concurrent_distinct_ids(tmp_path: Path) -> None:
    state = tmp_path / "state"
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    outcomes = _run_root_register_race(state, (("left", left), ("right", right)))

    assert set(outcomes) == {("success", "left"), ("success", "right")}
    registry = RootRegistry(state)
    assert {item["root_id"] for item in registry.list()} == {"left", "right"}
    assert registry._lock_path().is_file()


def test_root_registry_same_id_remains_idempotent_and_conflicting_rebind_is_rejected(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = RootRegistry(state)

    first_result = registry.register("workspace", first, writable=True, kind="workspace")
    assert registry.register("workspace", first, writable=True, kind="workspace") == first_result
    with pytest.raises(AgentError) as failure:
        registry.register("workspace", second, writable=True, kind="workspace")
    assert failure.value.code == "BTAG-ROOT-CONFLICT"


def test_root_registry_lock_open_error_has_stable_diagnostic(tmp_path: Path, monkeypatch) -> None:
    registry = RootRegistry(tmp_path / "state")

    def _open_error(*_args, **_kwargs):
        raise OSError(errno.EACCES, "injected root lock open failure")

    monkeypatch.setattr(locking_module.os, "open", _open_error)
    with pytest.raises(AgentError) as failure:
        with registry._locked():
            pass
    assert failure.value.code == "BTAG-ROOT-LOCK"


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_root_registry_lock_acquire_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    registry = RootRegistry(tmp_path / "state")
    original_close = locking_module.os.close
    closed_descriptors = []

    def _acquire_error(_descriptor, _operation):
        raise OSError(errno.EIO, "injected root lock acquire failure")

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _acquire_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with registry._locked():
            pass
    assert failure.value.code == "BTAG-ROOT-LOCK"
    assert len(closed_descriptors) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_root_registry_lock_release_error_closes_descriptor_and_maps_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    import fcntl

    registry = RootRegistry(tmp_path / "state")
    original_flock = fcntl.flock
    original_close = locking_module.os.close
    closed_descriptors = []

    def _release_error(descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "injected root lock release failure")
        return original_flock(descriptor, operation)

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _release_error)
    monkeypatch.setattr(locking_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with registry._locked():
            pass
    assert failure.value.code == "BTAG-ROOT-LOCK"
    assert len(closed_descriptors) == 1
