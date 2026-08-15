"""Cross-process lifecycle serialization for native host adapters."""

import json
import multiprocessing
import os
import time
from pathlib import Path
from threading import BrokenBarrierError
from typing import Any

import pytest

import backtrader_agent.installer as installer_module
import backtrader_agent.locking as locking_module
from backtrader_agent.canonical import sha256_bytes
from backtrader_agent.errors import AgentError
from backtrader_agent.installer import AdapterInstaller


def _uninstall_worker(
    target_text: str,
    destination: str,
    start_barrier,
    unlink_barrier,
    outcomes,
) -> None:
    original_unlink = installer_module.Path.unlink

    def coordinated_unlink(path: Path, *args, **kwargs) -> None:
        if str(path) == destination:
            try:
                unlink_barrier.wait(timeout=2)
            except BrokenBarrierError:
                pass
        original_unlink(path, *args, **kwargs)

    installer_module.Path.unlink = coordinated_unlink
    try:
        start_barrier.wait(timeout=10)
        result = AdapterInstaller().uninstall(Path(target_text), "claude", apply=True)
        outcomes.put(("success", result["status"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - parent asserts raw legacy failure
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        installer_module.Path.unlink = original_unlink


def _run_uninstall_race(target: Path, destination: Path) -> list:
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(2)
    unlink_barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_uninstall_worker,
            args=(str(target), str(destination), start_barrier, unlink_barrier, outcomes),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _hold_apply_lock(target_text: str, host: str, acquired, release, outcomes) -> None:
    try:
        installer = AdapterInstaller()
        with installer._locked_apply(Path(target_text), host):
            acquired.set()
            release.wait(timeout=10)
    except Exception as exc:  # pragma: no cover - parent asserts worker outcome
        outcomes.put(("unexpected-error", type(exc).__name__))
    else:
        outcomes.put(("success", host))


def test_same_host_uninstall_race_has_one_committed_removal(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    AdapterInstaller().install(target, "claude", apply=True)
    destination = target / ".claude" / "agents" / "backtrader-agent.md"

    outcomes = _run_uninstall_race(target, destination)

    assert set(outcomes) == {
        ("success", "uninstalled"),
        ("agent-error", "BTAG-UNINSTALL-MANIFEST"),
    }, outcomes
    assert not destination.exists()
    assert not (target / ".backtrader-agent" / "installer" / "claude.json").exists()


def test_apply_locks_are_isolated_by_adapter_host(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    outcomes = context.Queue()
    holder = context.Process(
        target=_hold_apply_lock,
        args=(str(target), "claude", acquired, release, outcomes),
    )
    holder.start()
    assert acquired.wait(timeout=5)

    installer = AdapterInstaller()
    started = time.monotonic()
    with installer._locked_apply(target, "codex"):
        assert time.monotonic() - started < 1.0
    release.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0
    assert outcomes.get(timeout=2) == ("success", "claude")
    assert installer._lock_path(target, "claude").is_file()
    assert installer._lock_path(target, "codex").is_file()


def test_preview_does_not_create_lifecycle_state(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()
    preview = installer.install(target, "claude", apply=False)
    assert preview["status"] == "preview"
    assert not (target / ".claude").exists()
    assert not (target / ".backtrader-agent").exists()
    with pytest.raises(AgentError, match="BTAG-UNINSTALL-MANIFEST"):
        installer.uninstall(target, "claude", apply=False)
    assert not (target / ".backtrader-agent").exists()


def test_uninstall_rejects_path_escape_manifest_without_deleting_external_marker(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()
    installer.install(target, "claude", apply=True)
    adapter = target / ".claude" / "agents" / "backtrader-agent.md"
    manifest = installer._manifest_path(target, "claude")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"must-not-delete")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "adapter-install-manifest-v1",
                "host": "claude",
                "files": [
                    {
                        "relative_path": "../victim.txt",
                        "sha256": sha256_bytes(victim.read_bytes()),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AgentError) as failure:
        installer.uninstall(target, "claude", apply=True)

    assert failure.value.code == "BTAG-UNINSTALL-MANIFEST"
    assert victim.read_bytes() == b"must-not-delete"
    assert adapter.is_file()
    assert manifest.is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation can require elevated Windows privileges")
def test_uninstall_rejects_symlink_manifest_without_touching_adapter(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()
    installer.install(target, "claude", apply=True)
    adapter = target / ".claude" / "agents" / "backtrader-agent.md"
    manifest = installer._manifest_path(target, "claude")
    external_manifest = tmp_path / "external-manifest.json"
    external_manifest.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(external_manifest)

    with pytest.raises(AgentError) as failure:
        installer.uninstall(target, "claude", apply=True)

    assert failure.value.code == "BTAG-UNINSTALL-MANIFEST"
    assert manifest.is_symlink()
    assert adapter.is_file()
    assert external_manifest.is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation can require elevated Windows privileges")
def test_adapter_symlink_is_rejected_by_preview_and_uninstall(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()
    installer.install(target, "claude", apply=True)
    adapter = target / ".claude" / "agents" / "backtrader-agent.md"
    manifest = installer._manifest_path(target, "claude")
    external_adapter = tmp_path / "external-adapter.md"
    external_adapter.write_bytes(adapter.read_bytes())
    adapter.unlink()
    adapter.symlink_to(external_adapter)

    with pytest.raises(AgentError) as preview_failure:
        installer.install(target, "claude", apply=False)
    with pytest.raises(AgentError) as uninstall_failure:
        installer.uninstall(target, "claude", apply=True)

    assert preview_failure.value.code == "BTAG-INSTALL-CONFLICT"
    assert uninstall_failure.value.code == "BTAG-UNINSTALL-MODIFIED"
    assert adapter.is_symlink()
    assert manifest.is_file()
    assert external_adapter.is_file()


def test_uninstall_rejects_malformed_manifest_shapes_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()
    installer.install(target, "claude", apply=True)
    adapter = target / ".claude" / "agents" / "backtrader-agent.md"
    manifest = installer._manifest_path(target, "claude")
    valid_entry = {
        "relative_path": ".claude/agents/backtrader-agent.md",
        "sha256": sha256_bytes(adapter.read_bytes()),
    }
    payloads = (
        {"schema_version": "unknown", "host": "claude", "files": [valid_entry]},
        {"schema_version": "adapter-install-manifest-v1", "host": "codex", "files": [valid_entry]},
        {"schema_version": "adapter-install-manifest-v1", "host": "claude", "files": []},
        {
            "schema_version": "adapter-install-manifest-v1",
            "host": "claude",
            "files": [valid_entry, valid_entry],
        },
        {
            "schema_version": "adapter-install-manifest-v1",
            "host": "claude",
            "files": [{"relative_path": "unexpected.md", "sha256": "a" * 64}],
        },
        {
            "schema_version": "adapter-install-manifest-v1",
            "host": "claude",
            "files": [{**valid_entry, "sha256": "not-a-hash"}],
        },
    )

    for payload in payloads:
        manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        with pytest.raises(AgentError) as failure:
            installer.uninstall(target, "claude", apply=True)
        assert failure.value.code == "BTAG-UNINSTALL-MANIFEST"
        assert adapter.is_file()
        assert manifest.is_file()


@pytest.mark.parametrize("phase", ("prepare", "acquire", "release", "close"))
def test_apply_lock_maps_shared_lock_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    installer = AdapterInstaller()

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

    with pytest.raises(AgentError, match="BTAG-INSTALL-LOCK") as raised:
        with installer._locked_apply(target, "claude"):
            pass
    assert raised.value.code == "BTAG-INSTALL-LOCK"
