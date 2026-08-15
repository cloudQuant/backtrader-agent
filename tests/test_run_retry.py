"""R14: transient run failures admit a same-effect retry; everything else must repair.

The retry contract:

- ``FAILED → RUN_APPROVED`` is legal only when the failed run's code is in
  ``ControlledRunner.TRANSIENT_FAILURE_CODES`` (recorded as ``retry_eligible``
  on the FAILED manifest) and the new approval carries the same run subject.
- A non-transient failure, a changed effect, and terminal sessions all reject
  the retry transition with ``BTAG-STATE-TRANSITION``.
- The retry run's RunManifest records ``retry_of`` pointing at the failed
  attempt's run id, which is attempt-distinct (derived from the run
  request, i.e. the run token). Every failed attempt persists a
  ``run-attempt.json`` marker linking its journal event, so the chain is
  walkable: ``retry_of`` -> attempt marker -> journal event -> ...
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from backtrader_agent.canonical import hash_object, read_json
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry
from backtrader_agent.runner import ControlledRunner
from backtrader_agent.sessions import SessionStore, TRANSITIONS
from backtrader_agent.tokens import TokenAuthority


class _ProbeAuthority:
    """Minimal authority double; mirrors tests/test_run_concurrency.py."""

    def verify(self, token: Dict[str, Any], **_kwargs: Any) -> Dict[str, Any]:
        return token

    def consume(self, token: Dict[str, Any], *, effect_id: str) -> Dict[str, Any]:
        return {
            "state": "CONSUMED",
            "effect_id": effect_id,
            "token_id": token["token_id"],
        }


class _ProbeRunner(ControlledRunner):
    """Probe runner whose real child behavior is chosen by PROBE_BEHAVIOR."""

    def __init__(self, state_root: Path, behavior: str) -> None:
        super().__init__(RootRegistry(state_root), state_root, _ProbeAuthority())
        self.behavior = behavior

    def _verify_applied(self, applied: Dict[str, Any]) -> None:
        del applied

    def _verify_registered_dataset(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        return dataset

    def _resolve_engine(self, validation_token: Dict[str, Any]):
        del validation_token
        return self.state_root, {
            "root_id": "probe",
            "hash": "e" * 64,
            "version": "probe",
        }

    def _verify_execution_environment(
        self, validation_token: Dict[str, Any]
    ) -> Dict[str, Any]:
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

    def _child_environment(
        self, descriptors, mode: str, engine_root: Path
    ) -> Dict[str, str]:
        del descriptors, mode, engine_root
        return {"PATH": os.environ.get("PATH", ""), "PROBE_BEHAVIOR": self.behavior}


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
    dataset = {
        "dataset_id": applied["dataset_id"],
        "manifest_hash": "e" * 64,
        "feeds": [],
    }
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
    payload = "BACKTRADER_AGENT_RESULT=" + json.dumps(
        {"metrics": metrics}, sort_keys=True
    )
    script = "\n".join(
        (
            "import json",
            "import os",
            "import sys",
            "import time",
            "",
            "behavior = os.environ.get('PROBE_BEHAVIOR', 'pass')",
            "if behavior == 'hang':",
            "    time.sleep(3600)",
            "    sys.exit(1)  # pragma: no cover - never reached before the wall timeout",
            "if behavior == 'exit':",
            "    sys.exit(1)",
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
        sessions.transition(
            "session-probe", state, state.lower(), {"probe": state}, **kwargs
        )


def _probe_subject() -> str:
    applied, dataset, validation_token, _run_token = _probe_inputs()
    return ControlledRunner.compute_run_subject(
        applied, dataset, validation_token, mode="runonce"
    )


def _drive_to_failed(
    store: SessionStore,
    session_id: str,
    *,
    subject: str,
    code: str = "BTAG-RUN-TIMEOUT",
    retry_eligible: Optional[bool] = None,
) -> Dict[str, Any]:
    """Drive a fixture session to FAILED; ``retry_eligible=None`` omits the flag."""
    store.create(session_id)
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
        kwargs = {"approval_token_id": "tok-run"} if state == "RUN_APPROVED" else {}
        store.transition(session_id, state, state.lower(), {"probe": state}, **kwargs)
    store.transition(
        session_id,
        "RUNNING",
        "controlled-run-start",
        {"run_subject": subject},
        idempotency_key="probe-run",
        approval_token_id="tok-run",
        effect_references={"run_subject_hash": subject, "run_effect_id": "e" * 64},
    )
    kwargs: Dict[str, Any] = {
        "idempotency_key": "probe-run",
        "approval_token_id": "tok-run",
        "effect_references": {
            "run_failure_code": code,
            "run_id": f"run-{subject[:20]}",
        },
    }
    if retry_eligible is not None:
        kwargs["retry_eligible"] = retry_eligible
    return store.transition(
        session_id,
        "FAILED",
        "controlled-run-failed",
        {"diagnostic": hash_object({"code": code, "subject": subject})},
        **kwargs,
    )


def _retry_transition(
    store: SessionStore,
    session_id: str,
    *,
    subject: str,
) -> Dict[str, Any]:
    return store.transition(
        session_id,
        "RUN_APPROVED",
        "run-approve",
        {"approval_request": "a" * 64, "run_subject": subject},
        approval_token_id="tok-run",
        effect_references={"run_approval_id": "approval-retry"},
    )


def test_transient_failure_allows_same_effect_retry_transition(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(store, "session-retry-ok", subject=subject, retry_eligible=True)

    manifest = _retry_transition(store, "session-retry-ok", subject=subject)

    assert manifest["state"] == "RUN_APPROVED"
    assert manifest["allowed_next_actions"] == sorted(TRANSITIONS["RUN_APPROVED"])


def test_non_transient_failure_rejects_retry(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(
        store,
        "session-retry-nontransient",
        subject=subject,
        code="BTAG-RUN-FAILED",
        retry_eligible=False,
    )

    with pytest.raises(AgentError) as raised:
        _retry_transition(store, "session-retry-nontransient", subject=subject)

    assert raised.value.code == "BTAG-STATE-TRANSITION"
    assert store.load("session-retry-nontransient")["state"] == "FAILED"


def test_failure_without_retry_flag_fails_closed(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(store, "session-retry-unflagged", subject=subject)

    with pytest.raises(AgentError, match="BTAG-STATE-TRANSITION"):
        _retry_transition(store, "session-retry-unflagged", subject=subject)

    assert store.load("session-retry-unflagged")["state"] == "FAILED"


def test_changed_effect_rejects_retry(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(
        store, "session-retry-changed", subject=subject, retry_eligible=True
    )

    with pytest.raises(AgentError) as raised:
        _retry_transition(store, "session-retry-changed", subject="9" * 64)

    assert raised.value.code == "BTAG-STATE-TRANSITION"
    assert store.load("session-retry-changed")["state"] == "FAILED"


def test_archived_session_never_retries(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(
        store, "session-retry-archived", subject=subject, retry_eligible=True
    )
    store.cancel("session-retry-archived")
    store.archive("session-retry-archived")

    with pytest.raises(AgentError, match="BTAG-STATE-TRANSITION"):
        _retry_transition(store, "session-retry-archived", subject=subject)

    assert store.load("session-retry-archived")["state"] == "ARCHIVED"


def test_recovered_failed_session_preserves_retry_eligibility(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    subject = "s" * 64
    _drive_to_failed(
        store, "session-retry-recover", subject=subject, retry_eligible=True
    )

    recovered = store.recover("session-retry-recover")

    assert recovered["state"] == "FAILED"
    assert recovered["retry_eligible"] is True
    manifest = _retry_transition(store, "session-retry-recover", subject=subject)
    assert manifest["state"] == "RUN_APPROVED"


def _failed_approval_fixture(tmp_path: Path, *, retry_eligible: bool):
    """FAILED session whose applied-artifact record passes real approval checks."""
    state = tmp_path / "state"
    authority = TokenAuthority(state)
    applied, dataset, validation_token, run_token = _probe_inputs()
    record = authority.store_bound_record(
        "applied-artifact",
        applied["session_id"],
        applied["applied_artifact_hash"],
        {"applied_artifact": applied},
    )
    applied = {**applied, "applied_record_hash": record["record_hash"]}
    subject = ControlledRunner.compute_run_subject(
        applied, dataset, validation_token, mode="runonce"
    )
    store = SessionStore(state)
    store.create(applied["session_id"])
    for state_name in (
        "DATA_READY",
        "SPEC_DRAFT",
        "SPEC_APPROVED",
        "SOURCES_SELECTED",
        "DRAFT_READY",
        "VALIDATED",
        "APPLY_PREPARED",
    ):
        store.transition(
            applied["session_id"], state_name, state_name.lower(), {"probe": state_name}
        )
    store.transition(
        applied["session_id"],
        "APPLIED",
        "changes-apply",
        {"applied": applied["applied_artifact_hash"]},
        approval_token_id="tok-change",
        effect_references={
            "applied_artifact_hash": applied["applied_artifact_hash"],
            "applied_record_hash": applied["applied_record_hash"],
            "artifact_hash": applied["artifact_hash"],
            "artifact_record_hash": applied["artifact_record_hash"],
            "approved_spec_hash": applied["spec_hash"],
            "change_manifest_hash": applied["change_manifest_hash"],
            "dataset_id": applied["dataset_id"],
            "dataset_manifest_hash": applied["dataset_manifest_hash"],
            "validation_token_hash": applied["validation_token_hash"],
            "validation_token_id": applied["validation_token_id"],
        },
    )
    store.transition(
        applied["session_id"],
        "RUN_APPROVED",
        "run-approve",
        {"run_subject": subject},
        approval_token_id="tok-run",
    )
    store.transition(
        applied["session_id"],
        "RUNNING",
        "controlled-run-start",
        {"run_subject": subject},
        idempotency_key="probe-run",
        approval_token_id="tok-run",
        effect_references={"run_subject_hash": subject, "run_effect_id": "e" * 64},
    )
    store.transition(
        applied["session_id"],
        "FAILED",
        "controlled-run-failed",
        {"diagnostic": hash_object({"code": "BTAG-RUN-TIMEOUT", "subject": subject})},
        idempotency_key="probe-run",
        approval_token_id="tok-run",
        effect_references={
            "run_failure_code": "BTAG-RUN-TIMEOUT",
            "run_id": f"run-{subject[:20]}",
        },
        retry_eligible=retry_eligible,
    )
    bindings = {
        "applied_artifact_hash": applied["applied_artifact_hash"],
        "applied_record_hash": applied["applied_record_hash"],
        "artifact_hash": applied["artifact_hash"],
        "artifact_record_hash": applied["artifact_record_hash"],
        "change_manifest_hash": applied["change_manifest_hash"],
        "dataset_hash": dataset["manifest_hash"],
        "dataset_id": dataset["dataset_id"],
        "mode": "runonce",
        "session_id": applied["session_id"],
        "spec_hash": applied["spec_hash"],
        "validation_token_hash": applied["validation_token_hash"],
        "validation_token_id": validation_token["token_id"],
    }
    return (
        authority,
        store,
        subject,
        bindings,
        applied,
        dataset,
        validation_token,
        run_token,
    )


def test_failed_session_prepares_and_grants_same_effect_run_retry(
    tmp_path: Path,
) -> None:
    authority, store, subject, bindings, *_rest = _failed_approval_fixture(
        tmp_path, retry_eligible=True
    )

    request = authority.prepare_approval("run", subject, bindings)
    granted = authority.grant_approval(
        request["request_id"], approver="local-user", confirmed=True
    )

    assert granted["token"]["subject_hash"] == subject
    assert store.load("session-probe")["state"] == "RUN_APPROVED"


def test_non_transient_failed_session_cannot_prepare_run_approval(
    tmp_path: Path,
) -> None:
    authority, _store, subject, bindings, *_rest = _failed_approval_fixture(
        tmp_path, retry_eligible=False
    )

    with pytest.raises(AgentError, match="BTAG-APPROVAL-SESSION"):
        authority.prepare_approval("run", subject, bindings)


def test_timeout_failure_then_same_effect_retry_passes_with_retry_of(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    _prepare_probe_state(state)
    applied, dataset, validation_token, run_token = _probe_inputs()
    subject = _probe_subject()

    with pytest.raises(AgentError, match="BTAG-RUN-TIMEOUT") as raised:
        _ProbeRunner(state, "hang").run(
            applied,
            dataset,
            validation_token,
            run_token,
            mode="runonce",
            idempotency_key="probe-run",
            timeout_seconds=1,
        )
    assert raised.value.code == "BTAG-RUN-TIMEOUT"

    store = SessionStore(state)
    failed = store.load("session-probe")
    assert failed["state"] == "FAILED"
    assert failed["retry_eligible"] is True
    assert failed["artifacts"]["run_failure_code"] == "BTAG-RUN-TIMEOUT"
    first_run_id = failed["artifacts"]["run_id"]

    # The failed attempt leaves a walkable marker linking its journal event.
    first_attempt = read_json(state / "runs" / first_run_id / "run-attempt.json")
    assert first_attempt["schema_version"] == "run-attempt-v1"
    assert first_attempt["run_id"] == first_run_id
    assert first_attempt["status"] == "failed"
    assert first_attempt["failure_code"] == "BTAG-RUN-TIMEOUT"
    assert first_attempt["run_subject_hash"] == subject
    assert first_attempt["retry_of"] is None
    assert first_attempt["event_hash"] == failed["last_event_hash"]
    assert first_attempt["sequence"] == failed["last_sequence"]

    manifest = _retry_transition(store, "session-probe", subject=subject)
    assert manifest["state"] == "RUN_APPROVED"

    retry_token = {
        "token_id": "run-probe-retry",
        "approval_id": "approval-probe-retry",
    }
    result = _ProbeRunner(state, "pass").run(
        applied,
        dataset,
        validation_token,
        retry_token,
        mode="runonce",
        idempotency_key="probe-run",
        timeout_seconds=15,
    )

    assert result["status"] == "passed"
    run_manifest = read_json(state / "runs" / result["run_id"] / "run-manifest.json")
    assert run_manifest["run_id"] == result["run_id"]
    assert run_manifest["retry_of"] == first_run_id
    assert run_manifest["retry_of"] != run_manifest["run_id"]
    assert store.load("session-probe")["state"] == "COMPLETED"

    # The same attempt replays from the recorded action instead of launching
    # another child: behavior "exit" would fail any real child run.
    replay = _ProbeRunner(state, "exit").run(
        applied,
        dataset,
        validation_token,
        retry_token,
        mode="runonce",
        idempotency_key="probe-run",
        timeout_seconds=15,
    )
    assert replay == result
    assert store.load("session-probe")["state"] == "COMPLETED"


def test_same_attempt_reexecution_does_not_self_reference(tmp_path: Path) -> None:
    """Re-running the same token/key/request is the same attempt, not a chain link."""
    state = tmp_path / "state"
    _prepare_probe_state(state)
    applied, dataset, validation_token, run_token = _probe_inputs()
    subject = _probe_subject()

    with pytest.raises(AgentError, match="BTAG-RUN-TIMEOUT"):
        _ProbeRunner(state, "hang").run(
            applied,
            dataset,
            validation_token,
            run_token,
            mode="runonce",
            idempotency_key="probe-run",
            timeout_seconds=1,
        )

    store = SessionStore(state)
    first_run_id = store.load("session-probe")["artifacts"]["run_id"]
    _retry_transition(store, "session-probe", subject=subject)

    result = _ProbeRunner(state, "pass").run(
        applied,
        dataset,
        validation_token,
        run_token,
        mode="runonce",
        idempotency_key="probe-run",
        timeout_seconds=1,
    )

    assert result["status"] == "passed"
    run_manifest = read_json(state / "runs" / result["run_id"] / "run-manifest.json")
    assert run_manifest["run_id"] == first_run_id
    assert "retry_of" not in run_manifest
    # The first-failure marker is preserved next to the eventual success.
    assert (state / "runs" / first_run_id / "run-attempt.json").is_file()
    assert store.load("session-probe")["state"] == "COMPLETED"


def test_non_transient_run_failure_is_not_retry_eligible(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _prepare_probe_state(state)
    applied, dataset, validation_token, run_token = _probe_inputs()
    subject = _probe_subject()

    with pytest.raises(AgentError, match="BTAG-RUN-FAILED") as raised:
        _ProbeRunner(state, "exit").run(
            applied,
            dataset,
            validation_token,
            run_token,
            mode="runonce",
            idempotency_key="probe-run",
            timeout_seconds=15,
        )
    assert raised.value.code == "BTAG-RUN-FAILED"

    store = SessionStore(state)
    failed = store.load("session-probe")
    assert failed["state"] == "FAILED"
    assert failed["retry_eligible"] is False
    assert failed["artifacts"]["run_failure_code"] == "BTAG-RUN-FAILED"

    # Non-transient failures leave the same walkable attempt marker.
    first_run_id = failed["artifacts"]["run_id"]
    attempt = read_json(state / "runs" / first_run_id / "run-attempt.json")
    assert attempt["run_id"] == first_run_id
    assert attempt["failure_code"] == "BTAG-RUN-FAILED"
    assert attempt["retry_of"] is None

    with pytest.raises(AgentError, match="BTAG-STATE-TRANSITION"):
        _retry_transition(store, "session-probe", subject=subject)

    assert store.load("session-probe")["state"] == "FAILED"
