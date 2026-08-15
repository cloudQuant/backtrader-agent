import errno
import json
import multiprocessing
import os
from pathlib import Path

import pytest

import backtrader_agent.sessions as sessions_module
from backtrader_agent.canonical import atomic_write_json, hash_object, sha256_bytes
from backtrader_agent.changes import ChangeManager
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry
from backtrader_agent.scaffold import ArtifactRenderer
from backtrader_agent.sessions import SessionStore
from backtrader_agent.tokens import TokenAuthority
from backtrader_agent.validator import StrategyValidator

from helpers import strategy_spec


def _same_session_transition_worker(
    state_root: str,
    session_id: str,
    worker_id: int,
    barrier,
    outcomes,
) -> None:
    store = SessionStore(Path(state_root))
    try:
        barrier.wait(timeout=10)
        store.transition(
            session_id,
            "DATA_READY",
            "multiprocess-race",
            {"worker": str(worker_id)},
        )
        outcomes.put(("success", worker_id))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_same_session_race(state_root: Path, session_id: str, workers: int = 8):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(workers)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_same_session_transition_worker,
            args=(str(state_root), session_id, worker_id, barrier, outcomes),
        )
        for worker_id in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _same_session_create_worker(
    state_root: str,
    session_id: str,
    barrier,
    outcomes,
) -> None:
    store = SessionStore(Path(state_root))
    try:
        barrier.wait(timeout=10)
        manifest = store.create(session_id)
        outcomes.put(("success", manifest["checkpoint_hash"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_same_session_create_race(state_root: Path, session_id: str, workers: int = 8):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(workers)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_same_session_create_worker,
            args=(str(state_root), session_id, barrier, outcomes),
        )
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _crash_before_session_manifest_worker(state_root: str, session_id: str) -> None:
    original_write = sessions_module.atomic_write_json

    def _crash_before_manifest(path, value, *, create_only=False):
        if Path(path).name == "manifest.json" and create_only:
            os._exit(79)
        return original_write(path, value, create_only=create_only)

    sessions_module.atomic_write_json = _crash_before_manifest
    SessionStore(Path(state_root)).create(session_id)
    os._exit(80)  # pragma: no cover - the manifest boundary must terminate first


def _recover_or_transition_worker(
    state_root: str,
    session_id: str,
    operation: str,
    barrier,
    outcomes,
) -> None:
    store = SessionStore(Path(state_root))
    try:
        barrier.wait(timeout=10)
        if operation == "recover":
            manifest = store.recover(session_id)
        else:
            manifest = store.transition(
                session_id,
                "PASSED",
                "multiprocess-run-complete",
                {"operation": operation},
            )
        outcomes.put((operation, "success", manifest["state"]))
    except AgentError as exc:
        outcomes.put((operation, "agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put((operation, "unexpected-error", type(exc).__name__))


def _run_recover_transition_race(state_root: Path, session_id: str):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_recover_or_transition_worker,
            args=(str(state_root), session_id, operation, barrier, outcomes),
        )
        for operation in ("recover", "transition")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return {outcome[0]: outcome[1:] for outcome in (outcomes.get(timeout=5) for _ in processes)}


def _distinct_session_lock_worker(
    state_root: str,
    session_id: str,
    barrier,
    outcomes,
) -> None:
    store = SessionStore(Path(state_root))
    try:
        with store._locked(session_id):
            barrier.wait(timeout=10)
        outcomes.put(("success", session_id))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        outcomes.put(("unexpected-error", type(exc).__name__))


def _run_distinct_session_lock_race(state_root: Path, session_ids: tuple):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(session_ids))
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_distinct_session_lock_worker,
            args=(str(state_root), session_id, barrier, outcomes),
        )
        for session_id in session_ids
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def _journal_events(state_root: Path, session_id: str):
    journal = state_root / "sessions" / session_id / "journal.jsonl"
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


def _assert_valid_journal_chain(events) -> None:
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    previous_hash = "0" * 64
    for event in events:
        assert event["previous_event_hash"] == previous_hash
        assert event["event_hash"] == hash_object(
            {key: value for key, value in event.items() if key != "event_hash"}
        )
        previous_hash = event["event_hash"]


def _validated_product_artifact(
    workspace: Path,
    session_id: str,
    *,
    profile: str,
) -> tuple:
    state = workspace / ".backtrader-agent"
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    authority = TokenAuthority(state)
    sessions = SessionStore(state)
    sessions.create(session_id)
    dataset = {
        "dataset_id": "ds_" + "d" * 64,
        "manifest_hash": "d" * 64,
        "feeds": [
            {
                "name": "primary",
                "role": "execution",
                "canonical_columns": [
                    "datetime",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "openinterest",
                ],
            }
        ],
    }
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
    spec = StrategySpec.from_dict(strategy_spec(dataset["dataset_id"], profile=profile))
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
    report = StrategyValidator(authority).validate_artifact(
        artifact,
        bindings={
            "dataset_hash": dataset["manifest_hash"],
            "engine_hash": "e" * 64,
            "engine_root_id": "engine",
            "environment_hash": "test-environment",
        },
        approval="validator",
        session_id=session_id,
    )
    validation = report["validation_token"]
    sessions.transition(
        session_id,
        "VALIDATED",
        "strategy-validate",
        {"validation": report["validation_hash"]},
        effect_references={
            "artifact_record_hash": validation["bindings"]["artifact_record_hash"],
            "validation_hash": report["validation_hash"],
            "validation_token_hash": hash_object(validation),
            "validation_token_id": validation["token_id"],
        },
    )
    return artifact, validation, roots, authority


def _advance_to_validated(store: SessionStore, session_id: str) -> None:
    store.create(session_id)
    transitions = (
        ("DATA_READY", "dataset-register"),
        ("SPEC_DRAFT", "spec-draft"),
        ("SPEC_APPROVED", "spec-approve"),
        ("SOURCES_SELECTED", "sources-select"),
        ("DRAFT_READY", "draft-render"),
        ("VALIDATED", "strategy-validate"),
    )
    for state, action in transitions:
        store.transition(session_id, state, action, {action: hash_object(action)})


def _advance_to_running(store: SessionStore, session_id: str) -> None:
    _advance_to_validated(store, session_id)
    transitions = (
        ("APPLY_PREPARED", "change-plan"),
        ("APPLIED", "change-apply"),
        ("RUN_APPROVED", "run-approve"),
        ("RUNNING", "run-start"),
    )
    for state, action in transitions:
        store.transition(session_id, state, action, {action: hash_object(action)})


def test_validation_token_requires_engine_root_binding(tmp_path: Path) -> None:
    authority = TokenAuthority(tmp_path)
    bindings = {
        "artifact_record_hash": "a" * 64,
        "dataset_hash": "d" * 64,
        "dataset_id": "ds_" + "d" * 64,
        "engine_hash": "e" * 64,
        "environment_hash": "f" * 64,
        "session_id": "session-1",
        "spec_hash": "s" * 64,
    }

    with pytest.raises(AgentError, match="BTAG-TOKEN-BINDING") as failure:
        authority.issue_validation("a" * 64, bindings)
    assert failure.value.details == {"missing": ["engine_root_id"]}


def test_same_session_transitions_are_linearized_across_processes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = SessionStore(state)
    store.create("session-race")

    outcomes = _run_same_session_race(state, "session-race")

    assert [outcome[0] for outcome in outcomes].count("success") == 1
    assert all(outcome[0] in {"success", "agent-error"} for outcome in outcomes)
    assert all(
        outcome[0] == "success" or outcome[1] == "BTAG-STATE-TRANSITION"
        for outcome in outcomes
    )
    journal = state / "sessions/session-race/journal.jsonl"
    events = _journal_events(state, "session-race")
    _assert_valid_journal_chain(events)
    assert [event["sequence"] for event in events] == [1]
    assert store.load("session-race")["last_event_hash"] == events[-1]["event_hash"]
    assert not list(journal.parent.glob("journal.corrupt.*.jsonl"))


def test_same_session_create_is_idempotent_across_processes(tmp_path: Path) -> None:
    state = tmp_path / "state"

    outcomes = _run_same_session_create_race(state, "session-create-race")

    assert [outcome[0] for outcome in outcomes] == ["success"] * len(outcomes)
    assert len({outcome[1] for outcome in outcomes}) == 1
    store = SessionStore(state)
    manifest = store.load("session-create-race")
    assert manifest["state"] == "NEW"
    assert manifest["last_sequence"] == 0
    assert (state / "sessions/session-create-race/journal.jsonl").read_bytes() == b""


def test_session_create_recovers_after_manifest_publish_crash(tmp_path: Path) -> None:
    state = tmp_path / "state"
    session_id = "session-bootstrap-crash"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_before_session_manifest_worker,
        args=(str(state), session_id),
    )

    process.start()
    process.join(timeout=20)

    assert process.exitcode == 79
    directory = state / "sessions" / session_id
    journal = directory / "journal.jsonl"
    assert journal.read_bytes() == b""
    assert not (directory / "manifest.json").exists()

    manifest = SessionStore(state).create(session_id)

    assert manifest["state"] == "NEW"
    assert manifest["last_sequence"] == 0
    assert manifest["last_event_hash"] == "0" * 64
    assert SessionStore(state).load(session_id) == manifest
    assert journal.read_bytes() == b""


def test_session_create_refuses_nonempty_bootstrap_journal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    session_id = "session-bootstrap-reject"
    directory = state / "sessions" / session_id
    directory.mkdir(parents=True)
    journal = directory / "journal.jsonl"
    journal_content = b"unexpected event\n"
    journal.write_bytes(journal_content)

    with pytest.raises(AgentError) as failure:
        SessionStore(state).create(session_id)

    assert failure.value.code == "BTAG-SESSION-BOOTSTRAP"
    assert journal.read_bytes() == journal_content
    assert not (directory / "manifest.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation can require elevated Windows privileges")
def test_session_create_refuses_symlink_bootstrap_journal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    session_id = "session-bootstrap-symlink"
    directory = state / "sessions" / session_id
    directory.mkdir(parents=True)
    target = tmp_path / "outside-journal.jsonl"
    target.write_bytes(b"")
    journal = directory / "journal.jsonl"
    journal.symlink_to(target)

    with pytest.raises(AgentError) as failure:
        SessionStore(state).create(session_id)

    assert failure.value.code == "BTAG-SESSION-BOOTSTRAP"
    assert journal.is_symlink()
    assert target.read_bytes() == b""
    assert not (directory / "manifest.json").exists()


def test_recover_and_transition_are_linearized_across_processes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = SessionStore(state)
    _advance_to_running(store, "session-recover-race")

    outcomes = _run_recover_transition_race(state, "session-recover-race")

    assert outcomes["recover"][0] == "success"
    assert outcomes["transition"][0] in {"success", "agent-error"}
    events = _journal_events(state, "session-recover-race")
    _assert_valid_journal_chain(events)
    manifest = store.load("session-recover-race")
    assert manifest["last_sequence"] == len(events)
    assert manifest["last_event_hash"] == events[-1]["event_hash"]
    assert not list((state / "sessions/session-recover-race").glob("journal.corrupt.*.jsonl"))
    if outcomes["transition"][0] == "success":
        assert manifest["state"] == "PASSED"
        assert events[-1]["to_state"] == "PASSED"
    else:
        assert outcomes["transition"] == ("agent-error", "BTAG-STATE-TRANSITION")
        assert manifest["state"] == "PAUSED"
        assert events[-1]["action_type"] == "session-recover"


def test_distinct_session_locks_do_not_serialize_processes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    session_ids = ("session-left", "session-right")
    store = SessionStore(state)

    outcomes = _run_distinct_session_lock_race(state, session_ids)

    assert set(outcomes) == {("success", session_id) for session_id in session_ids}
    lock_paths = [store._lock_path(session_id) for session_id in session_ids]
    assert lock_paths[0] != lock_paths[1]
    assert all(path.is_file() for path in lock_paths)


def test_session_lock_open_error_has_stable_diagnostic(tmp_path: Path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "state")

    def _open_error(*_args, **_kwargs):
        raise OSError(errno.EACCES, "injected lock open failure")

    monkeypatch.setattr(sessions_module.os, "open", _open_error)
    with pytest.raises(AgentError) as failure:
        with store._locked("session-lock-open"):
            pass
    assert failure.value.code == "BTAG-SESSION-LOCK"


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_session_lock_acquire_error_is_closed_and_diagnostic(tmp_path: Path, monkeypatch) -> None:
    import fcntl

    store = SessionStore(tmp_path / "state")
    original_close = sessions_module.os.close
    closed_descriptors = []

    def _acquire_error(_descriptor, _operation):
        raise OSError(errno.EIO, "injected lock acquire failure")

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _acquire_error)
    monkeypatch.setattr(sessions_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with store._locked("session-lock-acquire"):
            pass
    assert failure.value.code == "BTAG-SESSION-LOCK"
    assert len(closed_descriptors) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock error mapping is covered on POSIX")
def test_session_lock_release_error_is_closed_and_diagnostic(tmp_path: Path, monkeypatch) -> None:
    import fcntl

    store = SessionStore(tmp_path / "state")
    original_flock = fcntl.flock
    original_close = sessions_module.os.close
    closed_descriptors = []

    def _release_error(descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "injected lock release failure")
        return original_flock(descriptor, operation)

    def _tracked_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", _release_error)
    monkeypatch.setattr(sessions_module.os, "close", _tracked_close)
    with pytest.raises(AgentError) as failure:
        with store._locked("session-lock-release"):
            pass
    assert failure.value.code == "BTAG-SESSION-LOCK"
    assert len(closed_descriptors) == 1


def test_hash_bound_tokens_and_expected_hash_make_apply_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = workspace / ".backtrader-agent"
    artifact, validation, roots, authority = _validated_product_artifact(
        workspace,
        "session-1",
        profile="single_test",
    )
    manager = ChangeManager(roots, state, authority)
    source = artifact["files"][0]["path"]
    change = manager.prepare(
        session_id="session-1",
        draft_root=Path(artifact["_draft_path"]),
        files=[
            {
                "source": source,
                "target": "strategies/generated/demo/test_strategy.py",
            }
        ],
        target_root_id="workspace",
        validation_token=validation,
    )
    request = authority.prepare_approval(
        "change",
        change["manifest_hash"],
        {
            "artifact_hash": change["artifact_hash"],
            "artifact_record_hash": change["artifact_record_hash"],
            "change_manifest_hash": change["manifest_hash"],
            "dataset_hash": change["dataset_manifest_hash"],
            "dataset_id": change["dataset_id"],
            "spec_hash": change["spec_hash"],
            "validation_token_hash": change["validation_token_hash"],
            "validation_token_id": validation["token_id"],
            "session_id": "session-1",
        },
    )
    grant = authority.grant_approval(
        request["request_id"],
        approver="local-user",
        confirmed=True,
    )
    change_token = grant["token"]

    first = manager.apply(change, change_token, idempotency_key="apply-1")
    second = manager.apply(change, change_token, idempotency_key="apply-1")
    assert first == second
    target = workspace / "strategies/generated/demo/test_strategy.py"
    assert sha256_bytes(target.read_bytes()) == change["changes"][0]["source_hash"]
    approval_record = json.loads(
        (state / "approvals" / f"{request['request_id']}.json").read_text(encoding="utf-8")
    )
    assert approval_record["state"] == "CONSUMED"
    assert approval_record["token"] == change_token

    action_path = manager._action_path("apply-1")
    canonical_action = json.loads(action_path.read_text(encoding="utf-8"))
    victim_store = SessionStore(state)
    _advance_to_validated(victim_store, "cache-victim")
    victim_store.transition(
        "cache-victim",
        "APPLY_PREPARED",
        "changes-prepare",
        {"change_manifest": "f" * 64},
        effect_references={"change_manifest_hash": "f" * 64},
    )
    cross_session_action = json.loads(json.dumps(canonical_action))
    cross_session_action["result"]["session_id"] = "cache-victim"
    atomic_write_json(action_path, cross_session_action)
    with pytest.raises(AgentError, match="BTAG-(PROVENANCE-SIGNATURE|IDEMPOTENCY-RECORD)"):
        manager.apply(change, change_token, idempotency_key="apply-1")
    assert victim_store.load("cache-victim")["state"] == "APPLY_PREPARED"

    resigned_payload = {
        key: value for key, value in cross_session_action.items() if key != "signature"
    }
    resigned_payload["record_hash"] = hash_object(
        {key: value for key, value in resigned_payload.items() if key != "record_hash"}
    )
    resigned_cross_session_action = {
        **resigned_payload,
        "signature": authority.sign_product_record(resigned_payload),
    }
    atomic_write_json(action_path, resigned_cross_session_action)
    with pytest.raises(AgentError, match="BTAG-IDEMPOTENCY-RESULT"):
        manager.apply(change, change_token, idempotency_key="apply-1")
    assert victim_store.load("cache-victim")["state"] == "APPLY_PREPARED"

    atomic_write_json(action_path, canonical_action)
    applied_record_path = (
        state
        / "sessions"
        / "session-1"
        / "records"
        / "applied-artifact"
        / f"{first['applied_artifact_hash']}.json"
    )
    canonical_applied_record = json.loads(applied_record_path.read_text(encoding="utf-8"))
    tampered_applied_record = json.loads(json.dumps(canonical_applied_record))
    tampered_applied_record["applied_artifact"]["spec_hash"] = "0" * 64
    atomic_write_json(applied_record_path, tampered_applied_record)
    with pytest.raises(AgentError, match="BTAG-PROVENANCE-SIGNATURE"):
        manager.apply(change, change_token, idempotency_key="apply-1")
    atomic_write_json(applied_record_path, canonical_applied_record)

    with pytest.raises(AgentError, match="BTAG-TOKEN-CONSUMED"):
        manager.apply(change, change_token, idempotency_key="apply-2")

    with pytest.raises(AgentError, match="BTAG-TOKEN-KIND"):
        authority.verify(
            change_token,
            kind="run",
            subject_hash=change["manifest_hash"],
        )
    with pytest.raises(AgentError, match="BTAG-TOKEN-BINDING"):
        authority.verify(validation, kind="validation", subject_hash="0" * 64)


def test_change_approval_and_apply_reject_forged_manifest_external_draft_and_cross_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = workspace / ".backtrader-agent"
    artifact, validation, roots, authority = _validated_product_artifact(
        workspace,
        "secure-change",
        profile="single_test",
    )
    manager = ChangeManager(roots, state, authority)
    source = artifact["files"][0]["path"]
    prepared = manager.prepare(
        session_id="secure-change",
        draft_root=Path(artifact["_draft_path"]),
        files=[{"source": source, "target": "generated/test_secure.py"}],
        target_root_id="workspace",
        validation_token=validation,
    )
    bindings = {
        "artifact_hash": prepared["artifact_hash"],
        "artifact_record_hash": prepared["artifact_record_hash"],
        "change_manifest_hash": prepared["manifest_hash"],
        "dataset_hash": prepared["dataset_manifest_hash"],
        "dataset_id": prepared["dataset_id"],
        "session_id": prepared["session_id"],
        "spec_hash": prepared["spec_hash"],
        "validation_token_hash": prepared["validation_token_hash"],
        "validation_token_id": prepared["validation_token_id"],
    }

    forged = {
        key: value
        for key, value in prepared.items()
        if not key.startswith("_") and key != "manifest_hash"
    }
    external = tmp_path / "external-draft"
    external.mkdir()
    forged_source = external / source
    forged_source.parent.mkdir(parents=True, exist_ok=True)
    forged_source.write_text("UNVALIDATED = True\n", encoding="utf-8")
    forged["changes"] = [
        {
            **forged["changes"][0],
            "source_hash": sha256_bytes(forged_source.read_bytes()),
            "size_bytes": len(forged_source.read_bytes()),
        }
    ]
    forged["manifest_hash"] = hash_object(forged)
    forged["_draft_path"] = str(external)
    forged_bindings = {
        **bindings,
        "change_manifest_hash": forged["manifest_hash"],
    }
    with pytest.raises(AgentError, match="BTAG-RECORD-MISSING"):
        authority.prepare_approval("change", forged["manifest_hash"], forged_bindings)

    cross_session_bindings = {**bindings, "session_id": "another-session"}
    with pytest.raises(AgentError, match="BTAG-(SESSION-UNKNOWN|RECORD-MISSING)"):
        authority.prepare_approval("change", prepared["manifest_hash"], cross_session_bindings)

    request = authority.prepare_approval("change", prepared["manifest_hash"], bindings)
    token = authority.grant_approval(
        request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    external_source = external / source
    external_source.write_text("UNVALIDATED = 'different bytes'\n", encoding="utf-8")
    result = manager.apply(
        {**prepared, "_draft_path": str(external)},
        token,
        idempotency_key="secure-apply",
    )
    target = workspace / "generated/test_secure.py"
    signed_source = Path(artifact["_draft_path"]) / source
    assert target.read_bytes() == signed_source.read_bytes()
    assert target.read_bytes() != external_source.read_bytes()
    assert result["applied_record_hash"]


def test_multifile_apply_rolls_back_second_file_failure_and_can_resume_same_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_target = workspace / "generated/first.py"
    second_target = workspace / "generated/second.py"
    first_target.parent.mkdir(parents=True)
    first_target.write_text("OLD_FIRST = 1\n", encoding="utf-8")
    second_target.write_text("OLD_SECOND = 1\n", encoding="utf-8")
    artifact, validation, roots, authority = _validated_product_artifact(
        workspace,
        "session-txn",
        profile="python_bundle",
    )
    state = workspace / ".backtrader-agent"
    manager = ChangeManager(roots, state, authority)
    sources = [item["path"] for item in artifact["files"] if item["path"].endswith(".py")]
    expected_first = (Path(artifact["_draft_path"]) / sources[0]).read_text(encoding="utf-8")
    expected_second = (Path(artifact["_draft_path"]) / sources[1]).read_text(encoding="utf-8")
    change = manager.prepare(
        session_id="session-txn",
        draft_root=Path(artifact["_draft_path"]),
        files=[
            {"source": sources[0], "target": "generated/first.py"},
            {"source": sources[1], "target": "generated/second.py"},
        ],
        target_root_id="workspace",
        validation_token=validation,
    )
    request = authority.prepare_approval(
        "change",
        change["manifest_hash"],
        {
            "artifact_hash": change["artifact_hash"],
            "artifact_record_hash": change["artifact_record_hash"],
            "change_manifest_hash": change["manifest_hash"],
            "dataset_hash": change["dataset_manifest_hash"],
            "dataset_id": change["dataset_id"],
            "spec_hash": change["spec_hash"],
            "validation_token_hash": change["validation_token_hash"],
            "validation_token_id": validation["token_id"],
            "session_id": "session-txn",
        },
    )
    token = authority.grant_approval(request["request_id"], approver="local-user", confirmed=True)[
        "token"
    ]

    original_replace = manager._replace_target

    def fail_second(index: int, target: Path, staged: Path, create_only: bool) -> None:
        if index == 1:
            raise OSError("injected second-file failure")
        original_replace(index, target, staged, create_only)

    monkeypatch.setattr(manager, "_replace_target", fail_second)
    with pytest.raises(AgentError, match="BTAG-CHANGE-TRANSACTION"):
        manager.apply(change, token, idempotency_key="txn-apply")
    assert first_target.read_text(encoding="utf-8") == "OLD_FIRST = 1\n"
    assert second_target.read_text(encoding="utf-8") == "OLD_SECOND = 1\n"
    transaction = json.loads(
        (state / "transactions" / change["change_id"] / "transaction.json").read_text(
            encoding="utf-8"
        )
    )
    assert transaction["state"] == "ROLLED_BACK"

    monkeypatch.setattr(manager, "_replace_target", original_replace)
    result = manager.apply(change, token, idempotency_key="txn-apply")
    assert result["status"] == "applied"
    assert first_target.read_text(encoding="utf-8") == expected_first
    assert second_target.read_text(encoding="utf-8") == expected_second


def test_session_journal_hash_chain_recovers_valid_prefix_and_isolates_tail(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    session = store.create("session-1")
    assert session["state"] == "NEW"
    store.transition("session-1", "DATA_READY", "dataset-register", {"dataset": "d" * 64})
    store.transition("session-1", "SPEC_DRAFT", "spec-draft", {"spec": "s" * 64})

    journal = tmp_path / "state" / "sessions" / "session-1" / "journal.jsonl"
    with journal.open("ab") as handle:
        handle.write(b'{"partial":')

    recovered = store.recover("session-1")
    assert recovered["state"] == "SPEC_DRAFT"
    assert recovered["last_sequence"] == 2
    assert list(journal.parent.glob("journal.corrupt.*.jsonl"))

    with pytest.raises(AgentError, match="BTAG-STATE-TRANSITION"):
        store.transition("session-1", "RUNNING", "illegal", {})


def test_interrupted_running_session_recovers_to_paused_with_effects(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = SessionStore(state)
    _advance_to_validated(store, "session-crash")
    store.transition(
        "session-crash",
        "APPLY_PREPARED",
        "changes-prepare",
        {"change": "c" * 64},
        effect_references={"change_manifest_hash": "c" * 64},
    )
    store.transition(
        "session-crash",
        "APPLIED",
        "changes-apply",
        {"applied": "a" * 64},
        approval_token_id="tok-change",
        effect_references={"applied_artifact_hash": "a" * 64},
    )
    store.transition(
        "session-crash",
        "RUN_APPROVED",
        "run-approve",
        {"run": "r" * 64},
        approval_token_id="tok-run",
    )
    store.transition(
        "session-crash",
        "RUNNING",
        "controlled-run-start",
        {"run": "r" * 64},
    )

    journal = state / "sessions/session-crash/journal.jsonl"
    with journal.open("ab") as handle:
        handle.write(b'{"partial":')
    recovered = store.recover("session-crash")
    assert recovered["state"] == "PAUSED"
    assert recovered["artifacts"]["change_manifest_hash"] == "c" * 64
    assert recovered["artifacts"]["applied_artifact_hash"] == "a" * 64
    assert recovered["approvals"] == {"apply": "tok-change", "execute": "tok-run"}
