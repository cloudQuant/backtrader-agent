"""Explicit session state, append-only hash chain, and safe recovery."""

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .canonical import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    hash_object,
    read_json,
)
from .errors import AgentError
from .locking import (
    LOCK_RETRY_SECONDS as DEFAULT_LOCK_RETRY_SECONDS,
    LOCK_TIMEOUT_SECONDS as DEFAULT_LOCK_TIMEOUT_SECONDS,
    exclusive_file_lock,
)

SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$")
TERMINAL = {"COMPLETED", "CANCELLED", "ARCHIVED"}
TRANSITIONS = {
    "NEW": {"DATA_READY", "CANCELLED"},
    "DATA_READY": {"SPEC_DRAFT", "NEEDS_REVALIDATION", "CANCELLED"},
    "SPEC_DRAFT": {"SPEC_APPROVED", "CANCELLED"},
    "SPEC_APPROVED": {"SOURCES_SELECTED", "SWEEP_PREPARED", "CANCELLED"},
    "SWEEP_PREPARED": {"RUNNING", "CANCELLED"},
    "SOURCES_SELECTED": {"DRAFT_READY", "CANCELLED"},
    "DRAFT_READY": {"VALIDATED", "NEEDS_REVALIDATION", "CANCELLED"},
    "VALIDATED": {"APPLY_PREPARED", "NEEDS_REVALIDATION", "CANCELLED"},
    "APPLY_PREPARED": {"APPLIED", "NEEDS_REVALIDATION", "CANCELLED"},
    "APPLIED": {"RUN_APPROVED", "NEEDS_REVALIDATION", "CANCELLED"},
    "RUN_APPROVED": {"RUNNING", "NEEDS_REVALIDATION", "CANCELLED"},
    "RUNNING": {"PASSED", "FAILED", "PAUSED"},
    "FAILED": {"REPAIRING", "CANCELLED", "RUN_APPROVED"},
    "REPAIRING": {"DRAFT_READY", "CANCELLED"},
    "PASSED": {"REPORTED", "CANCELLED"},
    "REPORTED": {"COMPLETED", "CANCELLED"},
    "PAUSED": {"RUNNING", "NEEDS_REVALIDATION", "CANCELLED"},
    "NEEDS_REVALIDATION": {"DATA_READY", "DRAFT_READY", "CANCELLED"},
    "COMPLETED": {"ARCHIVED"},
    "CANCELLED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionStore:
    LOCK_RETRY_SECONDS = DEFAULT_LOCK_RETRY_SECONDS
    LOCK_TIMEOUT_SECONDS = DEFAULT_LOCK_TIMEOUT_SECONDS

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.sessions_root = self.state_root / "sessions"

    def _directory(self, session_id: str) -> Path:
        if not SESSION_RE.fullmatch(session_id):
            raise AgentError("BTAG-SESSION-ID", "session ID is malformed")
        return self.sessions_root / session_id

    def _manifest_path(self, session_id: str) -> Path:
        return self._directory(session_id) / "manifest.json"

    def _journal_path(self, session_id: str) -> Path:
        return self._directory(session_id) / "journal.jsonl"

    def _lock_path(self, session_id: str) -> Path:
        self._directory(session_id)
        return self.state_root / "session-locks" / f"{session_id}.lock"

    @contextmanager
    def _locked(self, session_id: str) -> Iterator[None]:
        with exclusive_file_lock(
            self._lock_path(session_id),
            error_code="BTAG-SESSION-LOCK",
            subject="session",
            retry_seconds=self.LOCK_RETRY_SECONDS,
            timeout_seconds=self.LOCK_TIMEOUT_SECONDS,
        ):
            yield

    def create(
        self, session_id: str, *, parent_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        with self._locked(session_id):
            return self._create_unlocked(
                session_id, parent_session_id=parent_session_id
            )

    def _create_unlocked(
        self, session_id: str, *, parent_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        directory = self._directory(session_id)
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            existing = read_json(manifest_path)
            if existing.get("session_id") == session_id:
                return existing
            raise AgentError(
                "BTAG-SESSION-CONFLICT", "session path contains another session"
            )
        directory.mkdir(parents=True, exist_ok=True)
        journal = directory / "journal.jsonl"
        self._ensure_empty_bootstrap_journal(journal)
        manifest: Dict[str, Any] = {
            "schema_version": "agent-session-manifest-v1",
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "product": "backtrader-agent",
            "product_version": "0.1.0",
            "state": "NEW",
            "state_revision": 0,
            "last_sequence": 0,
            "last_event_hash": "0" * 64,
            "journal": "journal.jsonl",
            "allowed_next_actions": sorted(TRANSITIONS["NEW"]),
            "artifacts": {},
            "approvals": {"apply": None, "execute": None},
            "diagnostics": [],
        }
        manifest["checkpoint_hash"] = hash_object(
            {key: value for key, value in manifest.items() if key != "checkpoint_hash"}
        )
        atomic_write_json(manifest_path, manifest, create_only=True)
        return manifest

    @staticmethod
    def _ensure_empty_bootstrap_journal(journal: Path) -> None:
        """Create or safely reuse the empty journal left before manifest publication.

        A crash after the journal fsync but before the manifest create-only publish leaves
        exactly one recoverable bootstrap state: a regular empty journal. Any other
        leftover cannot be proven to represent a NEW session and must remain untouched.
        """

        def verify_existing() -> None:
            try:
                if (
                    journal.is_symlink()
                    or not journal.is_file()
                    or journal.read_bytes() != b""
                ):
                    raise AgentError(
                        "BTAG-SESSION-BOOTSTRAP",
                        "session bootstrap journal is not a safe empty regular file",
                    )
            except AgentError:
                raise
            except OSError as exc:
                raise AgentError(
                    "BTAG-SESSION-BOOTSTRAP",
                    "session bootstrap journal could not be verified",
                ) from exc

        try:
            existing = journal.exists() or journal.is_symlink()
        except OSError as exc:
            raise AgentError(
                "BTAG-SESSION-BOOTSTRAP",
                "session bootstrap journal could not be inspected",
            ) from exc
        if existing:
            verify_existing()
            return
        try:
            atomic_write_bytes(journal, b"", create_only=True)
        except AgentError as exc:
            if exc.code != "BTAG-WRITE-EXISTS":
                raise
            verify_existing()

    def load(self, session_id: str) -> Dict[str, Any]:
        with self._locked(session_id):
            return self._load_unlocked(session_id)

    def _load_unlocked(self, session_id: str) -> Dict[str, Any]:
        path = self._manifest_path(session_id)
        if not path.exists():
            raise AgentError("BTAG-SESSION-UNKNOWN", "session does not exist")
        manifest = read_json(path)
        expected = manifest.get("checkpoint_hash")
        actual = hash_object(
            {key: value for key, value in manifest.items() if key != "checkpoint_hash"}
        )
        if expected != actual:
            raise AgentError(
                "BTAG-SESSION-CHECKPOINT", "session checkpoint hash is invalid"
            )
        return manifest

    def _append(self, session_id: str, event: Dict[str, Any]) -> None:
        journal = self._journal_path(session_id)
        data = canonical_json_bytes(event) + b"\n"
        descriptor = os.open(str(journal), os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def transition(
        self,
        session_id: str,
        to_state: str,
        action_type: str,
        input_hashes: Dict[str, str],
        *,
        idempotency_key: Optional[str] = None,
        approval_token_id: Optional[str] = None,
        effect_references: Optional[Dict[str, str]] = None,
        retry_eligible: Optional[bool] = None,
    ) -> Dict[str, Any]:
        with self._locked(session_id):
            return self._transition_unlocked(
                session_id,
                to_state,
                action_type,
                input_hashes,
                idempotency_key=idempotency_key,
                approval_token_id=approval_token_id,
                effect_references=effect_references,
                retry_eligible=retry_eligible,
            )

    def _transition_unlocked(
        self,
        session_id: str,
        to_state: str,
        action_type: str,
        input_hashes: Dict[str, str],
        *,
        idempotency_key: Optional[str] = None,
        approval_token_id: Optional[str] = None,
        effect_references: Optional[Dict[str, str]] = None,
        retry_eligible: Optional[bool] = None,
    ) -> Dict[str, Any]:
        manifest = self._load_unlocked(session_id)
        from_state = manifest["state"]
        if to_state not in TRANSITIONS.get(from_state, set()):
            raise AgentError(
                "BTAG-STATE-TRANSITION",
                "requested session state transition is not legal",
                details={"from": from_state, "to": to_state},
            )
        normalized_inputs = {
            str(key): str(value) for key, value in input_hashes.items()
        }
        effects = {
            str(key): str(value) for key, value in (effect_references or {}).items()
        }
        if from_state == "FAILED" and to_state == "RUN_APPROVED":
            self._guard_retry_transition(manifest, normalized_inputs, effects)
        sequence = int(manifest["last_sequence"]) + 1
        event: Dict[str, Any] = {
            "schema_version": "agent-event-v1",
            "session_id": session_id,
            "sequence": sequence,
            "event_type": "STATE_TRANSITION",
            "action_type": action_type,
            "from_state": from_state,
            "to_state": to_state,
            "normalized_input_hashes": dict(sorted(normalized_inputs.items())),
            "idempotency_key": idempotency_key,
            "approval_token_id": approval_token_id,
            "effect_references": dict(sorted(effects.items())),
            "previous_event_hash": manifest["last_event_hash"],
            "status": "committed",
            "timestamp": _now(),
        }
        if to_state == "FAILED":
            event["retry_eligible"] = bool(retry_eligible)
        event["event_hash"] = hash_object(event)
        self._append(session_id, event)
        manifest["state"] = to_state
        manifest["state_revision"] = int(manifest["state_revision"]) + 1
        manifest["last_sequence"] = sequence
        manifest["last_event_hash"] = event["event_hash"]
        manifest["allowed_next_actions"] = sorted(TRANSITIONS[to_state])
        manifest["artifacts"].update(effects)
        if to_state == "FAILED":
            manifest["retry_eligible"] = bool(retry_eligible)
        if approval_token_id and to_state == "APPLIED":
            manifest["approvals"]["apply"] = approval_token_id
        if approval_token_id and to_state == "RUN_APPROVED":
            manifest["approvals"]["execute"] = approval_token_id
        manifest["checkpoint_hash"] = hash_object(
            {key: value for key, value in manifest.items() if key != "checkpoint_hash"}
        )
        atomic_write_json(self._manifest_path(session_id), manifest)
        return manifest

    @staticmethod
    def _guard_retry_transition(
        manifest: Dict[str, Any],
        normalized_inputs: Dict[str, str],
        effects: Dict[str, str],
    ) -> None:
        """Gate ``FAILED → RUN_APPROVED``: transient failure plus the same subject.

        The retry flag is written by the controlled runner only for whitelisted
        transient failure codes, and the new approval must carry the run
        subject hash of the failed effect. Anything else must repair.
        """

        if not manifest.get("retry_eligible"):
            raise AgentError(
                "BTAG-STATE-TRANSITION",
                "retry requires a transient run failure of the same approved effect",
                details={"from": "FAILED", "to": "RUN_APPROVED"},
            )
        failed_subject = (manifest.get("artifacts") or {}).get("run_subject_hash")
        retry_subject = normalized_inputs.get("run_subject") or effects.get(
            "run_subject_hash"
        )
        if not failed_subject or retry_subject != failed_subject:
            raise AgentError(
                "BTAG-STATE-TRANSITION",
                "retry run subject does not match the failed approved effect",
                details={"from": "FAILED", "to": "RUN_APPROVED"},
            )

    def _parse_valid_prefix(
        self, session_id: str, data: bytes
    ) -> Tuple[List[Dict[str, Any]], int]:
        events: List[Dict[str, Any]] = []
        offset = 0
        expected_sequence = 1
        expected_previous = "0" * 64
        for line in data.splitlines(keepends=True):
            line_start = offset
            offset += len(line)
            if not line.endswith(b"\n"):
                return events, line_start
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return events, line_start
            stored_hash = event.get("event_hash")
            actual_hash = hash_object(
                {key: value for key, value in event.items() if key != "event_hash"}
            )
            if (
                event.get("session_id") != session_id
                or event.get("sequence") != expected_sequence
                or event.get("previous_event_hash") != expected_previous
                or stored_hash != actual_hash
            ):
                return events, line_start
            if event.get("to_state") not in TRANSITIONS.get(
                event.get("from_state"), set()
            ):
                return events, line_start
            if events and event.get("from_state") != events[-1].get("to_state"):
                return events, line_start
            if not events and event.get("from_state") != "NEW":
                return events, line_start
            events.append(event)
            expected_sequence += 1
            expected_previous = stored_hash
        return events, len(data)

    def recover(self, session_id: str) -> Dict[str, Any]:
        with self._locked(session_id):
            return self._recover_unlocked(session_id)

    def _recover_unlocked(self, session_id: str) -> Dict[str, Any]:
        directory = self._directory(session_id)
        journal = directory / "journal.jsonl"
        if not journal.exists():
            raise AgentError("BTAG-SESSION-JOURNAL", "session journal is missing")
        data = journal.read_bytes()
        events, valid_bytes = self._parse_valid_prefix(session_id, data)
        if valid_bytes < len(data):
            suffix = data[valid_bytes:]
            corrupt = directory / f"journal.corrupt.{time.time_ns()}.jsonl"
            atomic_write_bytes(corrupt, suffix, create_only=True)
            atomic_write_bytes(journal, data[:valid_bytes])
        previous = read_json(self._manifest_path(session_id))
        state = events[-1]["to_state"] if events else "NEW"
        last_hash = events[-1]["event_hash"] if events else "0" * 64
        artifacts: Dict[str, str] = {}
        approvals = {"apply": None, "execute": None}
        retry_eligible: Optional[bool] = None
        for event in events:
            artifacts.update(event.get("effect_references", {}))
            if event.get("to_state") == "FAILED":
                retry_eligible = bool(event.get("retry_eligible", False))
            token_id = event.get("approval_token_id")
            if token_id and event.get("to_state") == "APPLIED":
                approvals["apply"] = token_id
            if token_id and event.get("to_state") == "RUN_APPROVED":
                approvals["execute"] = token_id
        manifest: Dict[str, Any] = {
            "schema_version": "agent-session-manifest-v1",
            "session_id": session_id,
            "parent_session_id": previous.get("parent_session_id"),
            "product": "backtrader-agent",
            "product_version": "0.1.0",
            "state": state,
            "state_revision": len(events),
            "last_sequence": len(events),
            "last_event_hash": last_hash,
            "journal": "journal.jsonl",
            "allowed_next_actions": sorted(TRANSITIONS[state]),
            "artifacts": artifacts,
            "approvals": approvals,
            "diagnostics": previous.get("diagnostics", []),
        }
        if retry_eligible is not None:
            manifest["retry_eligible"] = retry_eligible
        manifest["checkpoint_hash"] = hash_object(
            {key: value for key, value in manifest.items() if key != "checkpoint_hash"}
        )
        atomic_write_json(self._manifest_path(session_id), manifest)
        if state == "RUNNING":
            manifest = self._transition_unlocked(
                session_id,
                "PAUSED",
                "session-recover",
                {"journal": last_hash},
            )
        return manifest

    def cancel(self, session_id: str) -> Dict[str, Any]:
        with self._locked(session_id):
            manifest = self._load_unlocked(session_id)
            if manifest["state"] in TERMINAL:
                raise AgentError(
                    "BTAG-STATE-TERMINAL", "terminal session cannot be cancelled"
                )
            return self._transition_unlocked(
                session_id, "CANCELLED", "session-cancel", {}
            )

    def archive(self, session_id: str) -> Dict[str, Any]:
        with self._locked(session_id):
            manifest = self._load_unlocked(session_id)
            if manifest["state"] not in {"COMPLETED", "CANCELLED"}:
                raise AgentError(
                    "BTAG-STATE-ARCHIVE",
                    "only completed or cancelled sessions may archive",
                )
            return self._transition_unlocked(
                session_id, "ARCHIVED", "session-archive", {}
            )

    def list(self) -> Dict[str, Any]:
        """Return compact summaries of every session manifest on disk plus the
        count of corrupt records skipped (R21).

        Corrupt manifests are skipped so one damaged session cannot hide the
        rest of the registry from a listing command; the skip count is
        reported so silent degradation stays visible.
        """

        if not self.sessions_root.is_dir():
            return {"sessions": [], "skipped": 0}
        summaries: List[Dict[str, Any]] = []
        skipped = 0
        for path in sorted(self.sessions_root.glob("*/manifest.json")):
            try:
                manifest = self.load(path.parent.name)
            except AgentError:
                skipped += 1
                continue
            summaries.append(
                {
                    "session_id": manifest.get("session_id"),
                    "state": manifest.get("state"),
                    "state_revision": manifest.get("state_revision"),
                    "last_sequence": manifest.get("last_sequence"),
                }
            )
        return {"sessions": summaries, "skipped": skipped}
