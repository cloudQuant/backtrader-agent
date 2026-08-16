"""Controlled fixed-profile execution of hash-approved candidate artifacts."""

import json
import os
import re
import subprocess
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..canonical import (
    atomic_write_bytes,
    atomic_write_json,
    hash_object,
    read_json,
    sha256_bytes,
)
from ..caching import memoized
from ..data import DatasetService
from ..errors import AgentError
from ..locking import exclusive_file_lock
from ..report import normalize_metrics
from ..roots import RootRegistry
from ..sessions import SessionStore
from ..tokens import TokenAuthority, expected_bindings
from . import profiles as profiles_module
from . import reports as reports_module
from . import resume as resume_module
from .profiles import _probe_engine

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
RESULT_PREFIX = "BACKTRADER_AGENT_RESULT="
RUN_ACTION_LOCK_GRACE_SECONDS = 60
MAX_OUTPUT_BYTES = 1024 * 1024
TRUNCATION_MARKER = "[backtrader-agent: output truncated; showing the tail]\n"


def _strip_result_lines(text: str) -> str:
    """Drop the structured-result protocol lines from retained stdout.

    The ``BACKTRADER_AGENT_RESULT=`` payload is machine-consumed through the
    persisted run result; the log keeps only the child's own output.
    """

    return "\n".join(
        line for line in text.splitlines() if not line.startswith(RESULT_PREFIX)
    )


def _truncate_tail_with_marker(text: str, quota: int) -> bytes:
    """Bound encoded text to ``quota`` bytes, keeping the tail when cut.

    The end of a stream is where the failure traceback or the structured
    result appears, so truncation drops the head and prepends the truncation
    marker. The surviving tail is advanced to the next UTF-8 character
    boundary so the log file still decodes cleanly.
    """

    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= quota:
        return encoded
    marker = TRUNCATION_MARKER.encode("utf-8")
    tail = encoded[-max(0, quota - len(marker)) :]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    return marker + tail


def _retain_child_outputs(
    output_dir: Path, stdout: Optional[bytes], stderr: Optional[bytes]
) -> None:
    """Persist child stdout/stderr as ``stdout.log``/``stderr.log`` (R20).

    The protocol line is stripped from stdout; both streams are bounded by
    ``MAX_OUTPUT_BYTES`` with a truncation marker when the head was dropped.
    The writes replace any earlier attempt's logs, so a same-attempt
    re-execution keeps the latest child output. Persistence failures surface
    as ``BTAG-RUN-PERSIST`` so the effect can be retried.
    """

    try:
        stdout_text = (stdout or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr or b"").decode("utf-8", errors="replace")
        atomic_write_bytes(
            Path(output_dir) / "stdout.log",
            _truncate_tail_with_marker(
                _strip_result_lines(stdout_text), MAX_OUTPUT_BYTES
            ),
        )
        atomic_write_bytes(
            Path(output_dir) / "stderr.log",
            _truncate_tail_with_marker(stderr_text, MAX_OUTPUT_BYTES),
        )
    except (AgentError, OSError) as exc:
        raise AgentError(
            "BTAG-RUN-PERSIST",
            "child output persistence was interrupted; retry the same effect",
        ) from exc


def _execute_profile(profile: Dict[str, Any]) -> subprocess.CompletedProcess:
    """Execute one fixed controlled child-process profile.

    The pure execution core shared by ``ControlledRunner.run`` and the sweep
    cell runner: fixed argv, the minimal child environment, the wall-clock
    timeout, and the POSIX resource limits. Persistence, session effects, and
    quota checks stay with the callers.

    ``profile`` may carry an optional ``output_dir``: when present, the
    child's stdout (minus the ``BACKTRADER_AGENT_RESULT=`` protocol line) and
    stderr are retained as ``stdout.log``/``stderr.log`` in that directory,
    bounded by the output quota (R20). Partial output is retained the same
    way when the wall clock kills the child.
    """

    output_dir = profile.get("output_dir")
    try:
        completed = subprocess.run(
            profile["argv"],
            cwd=profile["cwd"],
            env=profile["env"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=profile["timeout_seconds"],
            check=False,
            shell=False,
            preexec_fn=(
                profiles_module._resource_limits(profile["timeout_seconds"])
                if os.name == "posix"
                else None
            ),
        )
    except subprocess.TimeoutExpired as exc:
        if output_dir is not None:
            _retain_child_outputs(output_dir, exc.stdout, exc.stderr)
        raise
    if output_dir is not None:
        _retain_child_outputs(output_dir, completed.stdout, completed.stderr)
    return completed


def parse_child_result(stdout_text: str) -> Dict[str, Any]:
    """Parse the structured ``BACKTRADER_AGENT_RESULT=`` payload from stdout.

    Raises ``BTAG-RUN-RESULT`` for a malformed or missing payload and
    ``BTAG-RUN-METRIC`` (from :func:`normalize_metrics`) for invalid metrics.
    """

    payload = None
    for line in stdout_text.splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX) :])
            except json.JSONDecodeError as exc:
                raise AgentError(
                    "BTAG-RUN-RESULT", "child emitted malformed structured result"
                ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
        raise AgentError("BTAG-RUN-RESULT", "child emitted no structured metrics")
    metrics = normalize_metrics(payload["metrics"])
    return {**payload, "metrics": metrics}


def build_dataset_descriptors(
    state_root: Path, dataset: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Build the controlled child-process dataset descriptors for a manifest.

    Verifies every registered CAS feed against its stored normalized hash and
    resolves transforms from the allowlisted adapter registry.
    """

    descriptors: List[Dict[str, Any]] = []
    transforms = {
        item["parameters"]["feed"]: item
        for item in dataset.get("transforms", [])
        if isinstance(item, dict)
        and isinstance(item.get("parameters"), dict)
        and isinstance(item["parameters"].get("feed"), str)
    }
    for feed in dataset.get("feeds", []):
        relative = (
            feed.get("extensions", {})
            .get("backtrader_agent", {})
            .get("cas_relative_path")
        )
        if not isinstance(relative, str):
            raise AgentError("BTAG-RUN-DATASET", "dataset has no registered CAS path")
        path = (state_root / relative).resolve(strict=True)
        try:
            path.relative_to(state_root.resolve())
        except ValueError as exc:
            raise AgentError(
                "BTAG-RUN-DATASET", "dataset CAS path escapes state root"
            ) from exc
        metadata = path.stat()
        if _dataset_feed_sha256(
            path, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns
        ) != feed.get("normalized_sha256"):
            raise AgentError("BTAG-RUN-DATASET-HASH", "dataset CAS bytes changed")
        descriptors.append(
            {
                "name": feed["name"],
                "role": feed["role"],
                "path": str(path),
                "adapter": feed["format"],
                "timeframe": feed.get("timeframe", "Days"),
                "compression": int(feed.get("compression", 1)),
                "canonical_columns": list(feed.get("canonical_columns", [])),
                "transform": transforms.get(feed["name"]),
            }
        )
    return descriptors


@memoized
def _dataset_feed_sha256(path: Path, size: int, mtime_ns: int, ctime_ns: int) -> str:
    """Read and hash a dataset CAS file once per (path, size, mtime, ctime).

    The stat identity keeps the memo fresh: a CAS file that changes within
    the process is re-read instead of returning a stale binding hash.
    ``ctime_ns`` covers an in-place tamper that preserves size and restores
    mtime via ``os.utime`` (ctime cannot be restored by an unprivileged
    writer).
    """

    # Resolved through the package namespace at call time so monkeypatched
    # package attributes are honored instead of a stale module binding.
    from . import sha256_bytes

    return sha256_bytes(path.read_bytes())


class ControlledRunner:
    MAX_OUTPUT_BYTES = MAX_OUTPUT_BYTES

    # Failure codes that admit a same-effect retry (R14). Enumerated from the
    # actual runner error paths: the wall-clock timeout is the only
    # environment-class failure the runner can observe. OS resource-limit kills
    # (RLIMIT_CPU/RLIMIT_FSIZE in profiles._resource_limits) surface as a
    # nonzero exit and are reported as BTAG-RUN-FAILED, which stays
    # non-transient: the same effect would hit the same limit again, so repair
    # (a different strategy) is the honest path. Output/result/metric codes are
    # deterministic child-output failures and are likewise non-transient.
    TRANSIENT_FAILURE_CODES = frozenset({"BTAG-RUN-TIMEOUT"})

    def __init__(
        self, roots: RootRegistry, state_root: Path, authority: TokenAuthority
    ) -> None:
        self.roots = roots
        self.state_root = Path(state_root)
        self.authority = authority
        self.action_root = self.state_root / "actions"

    @staticmethod
    def compute_run_subject(
        applied: Dict[str, Any],
        dataset: Dict[str, Any],
        validation_token: Dict[str, Any],
        *,
        mode: str,
    ) -> str:
        if mode not in {"runonce", "runnext"}:
            raise AgentError("BTAG-RUN-MODE", "mode must be runonce or runnext")
        return hash_object(
            {
                "applied_artifact_hash": applied["applied_artifact_hash"],
                "dataset_manifest_hash": dataset["manifest_hash"],
                "validation_token_id": validation_token["token_id"],
                "mode": mode,
                "profile": "controlled-runner-v1",
            }
        )

    @classmethod
    def _retry_of_reference(
        cls, session: Dict[str, Any], *, subject: str, run_id: str
    ) -> Optional[str]:
        """Return the failed run id when this run retries its transient failure.

        Detection is artifacts-only so the same manifest (and therefore the
        same ``retry_of``) is rebuilt on every idempotent replay of the retry,
        regardless of how far the session has since progressed.
        """

        artifacts = session.get("artifacts") or {}
        if (
            session.get("retry_eligible") is True
            and artifacts.get("run_failure_code") in cls.TRANSIENT_FAILURE_CODES
            and artifacts.get("run_subject_hash") == subject
        ):
            return str(artifacts.get("run_id") or run_id)
        return None

    def _verify_applied(self, applied: Dict[str, Any]) -> None:
        payload = {
            key: value
            for key, value in applied.items()
            if key not in {"applied_artifact_hash", "applied_record_hash", "status"}
        }
        if hash_object(payload) != applied.get("applied_artifact_hash"):
            raise AgentError(
                "BTAG-RUN-ARTIFACT-HASH", "applied artifact hash is invalid"
            )
        if applied.get("generated_by") != "backtrader-agent":
            raise AgentError(
                "BTAG-RUN-ORIGIN", "runner only accepts product-generated artifacts"
            )
        required = {
            "artifact_record_hash",
            "dataset_id",
            "dataset_manifest_hash",
            "session_id",
            "spec_hash",
            "validation_token_hash",
            "validation_token_id",
        }
        if any(not isinstance(applied.get(field), str) for field in required):
            raise AgentError(
                "BTAG-RUN-PROVENANCE", "applied artifact provenance is incomplete"
            )
        record = self.authority.load_bound_record(
            "applied-artifact",
            applied["session_id"],
            applied["applied_artifact_hash"],
        )
        canonical_applied = {
            key: value for key, value in applied.items() if key != "applied_record_hash"
        }
        if (
            applied.get("applied_record_hash") != record.get("record_hash")
            or record.get("applied_artifact") != canonical_applied
        ):
            raise AgentError(
                "BTAG-RUN-PROVENANCE",
                "applied artifact does not match its signed product record",
            )
        session = SessionStore(self.state_root).load(applied["session_id"])
        artifacts = session.get("artifacts", {})
        expected = {
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
        }
        if any(artifacts.get(key) != value for key, value in expected.items()):
            raise AgentError(
                "BTAG-RUN-PROVENANCE",
                "applied artifact does not match the product session checkpoint",
            )

    def _verify_registered_dataset(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            key: value for key, value in dataset.items() if key != "manifest_hash"
        }
        if hash_object(payload) != dataset.get("manifest_hash"):
            raise AgentError(
                "BTAG-RUN-DATASET-HASH", "dataset manifest hash is invalid"
            )
        if dataset.get("schema_version") != "dataset-manifest-v1":
            raise AgentError(
                "BTAG-RUN-DATASET-HASH", "dataset manifest version is invalid"
            )
        registered = DatasetService(self.roots, self.state_root).load(
            str(dataset["dataset_id"])
        )
        if registered != dataset:
            raise AgentError(
                "BTAG-RUN-DATASET-REGISTRY",
                "runner accepts only the exact registered DatasetManifest",
            )
        return registered

    def _action_path(self, key: str) -> Path:
        if not IDEMPOTENCY_RE.fullmatch(key):
            raise AgentError("BTAG-IDEMPOTENCY-KEY", "idempotency key is malformed")
        return self.action_root / f"run-{sha256_bytes(key.encode('utf-8'))}.json"

    def _action_lock_path(self, key: str) -> Path:
        return self._action_path(key).with_suffix(".lock")

    @contextmanager
    def _locked_action(self, key: str, *, timeout_seconds: int) -> Iterator[None]:
        with exclusive_file_lock(
            self._action_lock_path(key),
            error_code="BTAG-RUN-ACTION-LOCK",
            subject="controlled run action",
            timeout_seconds=float(timeout_seconds + RUN_ACTION_LOCK_GRACE_SECONDS),
        ):
            yield

    @staticmethod
    def _persist_exact_bytes(path: Path, content: bytes) -> None:
        return reports_module._persist_exact_bytes(path, content)

    @classmethod
    def _persist_exact_json(cls, path: Path, value: Dict[str, Any]) -> None:
        return reports_module._persist_exact_json(path, value)

    def _render_reports_resumable(
        self,
        run_root: Path,
        result: Dict[str, Any],
    ) -> Dict[str, str]:
        return reports_module._render_reports_resumable(self, run_root, result)

    @staticmethod
    def _verify_persisted_result(
        result: Dict[str, Any],
        *,
        run_id: str,
        mode: str,
        applied: Dict[str, Any],
        dataset: Dict[str, Any],
        validation_token: Dict[str, Any],
        run_token: Dict[str, Any],
    ) -> None:
        return reports_module._verify_persisted_result(
            result,
            run_id=run_id,
            mode=mode,
            applied=applied,
            dataset=dataset,
            validation_token=validation_token,
            run_token=run_token,
        )

    @staticmethod
    def _verify_persisted_manifest(
        run_root: Path,
        result: Dict[str, Any],
        expected: Dict[str, Any],
    ) -> None:
        return reports_module._verify_persisted_manifest(run_root, result, expected)

    @staticmethod
    def _redact_stderr(
        stderr: str,
        *,
        state_root: Path,
        entrypoint: Path,
        descriptors: List[Dict[str, Any]],
    ) -> str:
        return reports_module._redact_stderr(
            stderr,
            state_root=state_root,
            entrypoint=entrypoint,
            descriptors=descriptors,
        )

    @staticmethod
    def _begin_or_resume_session(
        sessions,
        *,
        session_id: str,
        subject: str,
        effect_id: str,
        idempotency_key: str,
        run_token: Dict[str, Any],
    ) -> None:
        return resume_module._begin_or_resume_session(
            sessions,
            session_id=session_id,
            subject=subject,
            effect_id=effect_id,
            idempotency_key=idempotency_key,
            run_token=run_token,
        )

    @classmethod
    def _finish_successful_session(
        cls,
        sessions,
        *,
        session_id: str,
        subject: str,
        effect_id: str,
        idempotency_key: str,
        run_token: Dict[str, Any],
        result: Dict[str, Any],
        report_hash: str,
    ) -> None:
        return resume_module._finish_successful_session(
            cls,
            sessions,
            session_id=session_id,
            subject=subject,
            effect_id=effect_id,
            idempotency_key=idempotency_key,
            run_token=run_token,
            result=result,
            report_hash=report_hash,
        )

    def _dataset_descriptors(self, dataset: Dict[str, Any]) -> List[Dict[str, Any]]:
        return build_dataset_descriptors(self.state_root, dataset)

    def _verify_files(self, applied: Dict[str, Any]) -> Path:
        entrypoint: Optional[Path] = None
        for item in applied.get("files", []):
            path = self.roots.resolve(
                applied["target_root_id"], item["relative_path"], require_file=True
            )
            if sha256_bytes(path.read_bytes()) != item["sha256"]:
                raise AgentError(
                    "BTAG-RUN-SOURCE-HASH", "applied source changed after approval"
                )
            if item["relative_path"] == applied.get("entrypoint"):
                entrypoint = path
        if entrypoint is None:
            raise AgentError("BTAG-RUN-ENTRYPOINT", "controlled entrypoint is absent")
        name = entrypoint.name
        profile = applied.get("profile")
        if profile == "python_bundle" and name != "run.py":
            raise AgentError("BTAG-RUN-PROFILE", "bundle entrypoint must be run.py")
        if profile == "single_test" and not name.startswith("test_"):
            raise AgentError(
                "BTAG-RUN-PROFILE", "test entrypoint must be a generated test"
            )
        return entrypoint

    @staticmethod
    def _child_environment(
        descriptors: List[Dict[str, Any]],
        mode: str,
        engine_root: Optional[Path] = None,
    ) -> Dict[str, str]:
        return profiles_module._child_environment(descriptors, mode, engine_root)

    def _resolve_engine(
        self,
        validation_token: Dict[str, Any],
    ) -> Tuple[Path, Dict[str, Any]]:
        # Resolved through the package namespace at call time so monkeypatched
        # package attributes are honored instead of a stale module binding.
        from . import inspect_engine

        bindings = validation_token.get("bindings", {})
        root_id = bindings.get("engine_root_id")
        if not isinstance(root_id, str) or not root_id:
            raise AgentError(
                "BTAG-ENGINE-BINDING",
                "validation token must bind a registered Backtrader engine root",
            )
        descriptor = inspect_engine(self.roots, str(root_id))
        if descriptor["engine_hash"] != bindings.get("engine_hash"):
            raise AgentError(
                "BTAG-ENGINE-HASH",
                "registered Backtrader engine changed after validation",
            )
        source_warning = descriptor.get("source", {}).get("warning")
        if isinstance(source_warning, str) and source_warning:
            warnings.warn(source_warning, RuntimeWarning, stacklevel=2)
        record = self.roots.get_record(str(root_id))
        root = Path(record["path"]).resolve(strict=True)
        relative_import, _attested_version = _probe_engine(
            root, self.state_root, descriptor["version"]
        )
        return root, {
            "hash": descriptor["engine_hash"],
            "kind": "registered-local",
            "root_id": descriptor["root_id"],
            "version": descriptor["version"],
            "version_file_sha256": descriptor["version_file_sha256"],
            "package_tree_sha256": descriptor["package_tree_sha256"],
            "import_relative_path": relative_import,
        }

    @staticmethod
    def _verify_execution_environment(
        validation_token: Dict[str, Any],
    ) -> Dict[str, Any]:
        return profiles_module._verify_execution_environment(validation_token)

    @staticmethod
    def _require_profile_dependencies(profile: str) -> None:
        return profiles_module._require_profile_dependencies(profile)

    def run(
        self,
        applied: Dict[str, Any],
        dataset: Dict[str, Any],
        validation_token: Dict[str, Any],
        run_token: Dict[str, Any],
        *,
        mode: str,
        idempotency_key: str,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        if timeout_seconds < 1 or timeout_seconds > 600:
            raise AgentError(
                "BTAG-RUN-TIMEOUT", "timeout must be between 1 and 600 seconds"
            )
        with self._locked_action(idempotency_key, timeout_seconds=timeout_seconds):
            return self._run_locked(
                applied,
                dataset,
                validation_token,
                run_token,
                mode=mode,
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )

    def _run_locked(
        self,
        applied: Dict[str, Any],
        dataset: Dict[str, Any],
        validation_token: Dict[str, Any],
        run_token: Dict[str, Any],
        *,
        mode: str,
        idempotency_key: str,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        if timeout_seconds < 1 or timeout_seconds > 600:
            raise AgentError(
                "BTAG-RUN-TIMEOUT", "timeout must be between 1 and 600 seconds"
            )
        self._verify_applied(applied)
        dataset = self._verify_registered_dataset(dataset)
        self.authority.verify(
            validation_token,
            kind="validation",
            subject_hash=applied["artifact_hash"],
            required_bindings=expected_bindings(
                "validation",
                artifact_record_hash=applied["artifact_record_hash"],
                dataset_hash=dataset["manifest_hash"],
                dataset_id=dataset["dataset_id"],
                session_id=applied["session_id"],
                spec_hash=applied["spec_hash"],
            ),
        )
        if (
            applied["dataset_id"] != dataset["dataset_id"]
            or applied["dataset_manifest_hash"] != dataset["manifest_hash"]
            or applied["validation_token_hash"] != hash_object(validation_token)
            or applied["validation_token_id"] != validation_token["token_id"]
        ):
            raise AgentError(
                "BTAG-RUN-PROVENANCE",
                "applied artifact, validation, and registered dataset bindings disagree",
            )
        subject = self.compute_run_subject(
            applied, dataset, validation_token, mode=mode
        )
        self.authority.verify(
            run_token,
            kind="run",
            subject_hash=subject,
            required_bindings=expected_bindings(
                "run",
                applied_artifact_hash=applied["applied_artifact_hash"],
                applied_record_hash=applied["applied_record_hash"],
                artifact_hash=applied["artifact_hash"],
                artifact_record_hash=applied["artifact_record_hash"],
                change_manifest_hash=applied["change_manifest_hash"],
                validation_token_id=validation_token["token_id"],
                validation_token_hash=hash_object(validation_token),
                dataset_hash=dataset["manifest_hash"],
                dataset_id=dataset["dataset_id"],
                mode=mode,
                session_id=applied["session_id"],
                spec_hash=applied["spec_hash"],
            ),
        )
        action_path = self._action_path(idempotency_key)
        request_hash = hash_object(
            {
                "action": "controlled-run",
                "subject_hash": subject,
                "run_token_id": run_token["token_id"],
                "timeout_seconds": timeout_seconds,
            }
        )
        effect_id = hash_object(
            {
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            }
        )
        # Attempt-distinct run id: request_hash binds the run token id, so a
        # retry under a new approval yields a different id than the failed
        # attempt while an idempotent replay of the same attempt stays stable.
        run_id = f"run-{request_hash[:20]}"
        run_root = self.state_root / "runs" / run_id
        engine_root, engine_descriptor = self._resolve_engine(validation_token)
        environment = self._verify_execution_environment(validation_token)
        self._require_profile_dependencies(str(applied.get("profile")))
        sessions = SessionStore(self.state_root)
        retry_of = self._retry_of_reference(
            sessions.load(applied["session_id"]), subject=subject, run_id=run_id
        )
        if retry_of == run_id:
            # Re-execution of the same attempt (same run token and request),
            # not a distinct retry: the chain must not self-reference.
            retry_of = None
        run_manifest: Dict[str, Any] = {
            "schema_version": "run-manifest-v1",
            "run_id": run_id,
            "artifact_hash": applied["artifact_hash"],
            "dataset_id": dataset["dataset_id"],
            "engine": engine_descriptor,
            "environment_hash": environment["environment_hash"],
            "run_profile": {
                "name": "controlled-runner-v1",
                "output_profile": applied["profile"],
                "mode": mode,
                "entrypoint": Path(applied["entrypoint"]).name,
                "timeout_seconds": timeout_seconds,
            },
            "approval_id": run_token["approval_id"],
            "extensions": {
                "backtrader_agent": {
                    "applied_artifact_hash": applied["applied_artifact_hash"],
                    "applied_record_hash": applied["applied_record_hash"],
                    "dataset_manifest_hash": dataset["manifest_hash"],
                    "validation_token_id": validation_token["token_id"],
                }
            },
        }
        if retry_of is not None:
            run_manifest["retry_of"] = retry_of
        run_manifest["manifest_hash"] = hash_object(run_manifest)
        if action_path.exists():
            recorded = read_json(action_path)
            if recorded.get("request_hash") != request_hash:
                raise AgentError(
                    "BTAG-IDEMPOTENCY-CONFLICT",
                    "idempotency key was already used for another run",
                )
            result = recorded.get("result")
            if not isinstance(result, dict):
                raise AgentError("BTAG-RUN-CONFLICT", "recorded run result is invalid")
            self._verify_persisted_result(
                result,
                run_id=run_id,
                mode=mode,
                applied=applied,
                dataset=dataset,
                validation_token=validation_token,
                run_token=run_token,
            )
            self._verify_persisted_manifest(run_root, result, run_manifest)
            report_files = self._render_reports_resumable(run_root, result)
            self._finish_successful_session(
                sessions,
                session_id=applied["session_id"],
                subject=subject,
                effect_id=effect_id,
                idempotency_key=idempotency_key,
                run_token=run_token,
                result=result,
                report_hash=report_files["report_hash"],
            )
            return result
        self.authority.consume(run_token, effect_id=effect_id)

        entrypoint = self._verify_files(applied)
        descriptors = self._dataset_descriptors(dataset)
        self._begin_or_resume_session(
            sessions,
            session_id=applied["session_id"],
            subject=subject,
            effect_id=effect_id,
            idempotency_key=idempotency_key,
            run_token=run_token,
        )
        persisted_result_path = run_root / "run-result.json"
        if persisted_result_path.exists():
            result = read_json(persisted_result_path)
            self._verify_persisted_result(
                result,
                run_id=run_id,
                mode=mode,
                applied=applied,
                dataset=dataset,
                validation_token=validation_token,
                run_token=run_token,
            )
            self._verify_persisted_manifest(run_root, result, run_manifest)
            report_files = self._render_reports_resumable(run_root, result)
            self._persist_exact_json(
                action_path,
                {
                    "schema_version": "idempotent-action-v1",
                    "request_hash": request_hash,
                    "result": result,
                },
            )
            self._finish_successful_session(
                sessions,
                session_id=applied["session_id"],
                subject=subject,
                effect_id=effect_id,
                idempotency_key=idempotency_key,
                run_token=run_token,
                result=result,
                report_hash=report_files["report_hash"],
            )
            return result
        if applied["profile"] == "python_bundle":
            argv = [sys.executable, entrypoint.name]
        else:
            argv = [
                sys.executable,
                "-m",
                "pytest",
                entrypoint.name,
                "-q",
                "-s",
                "-p",
                "no:cacheprovider",
            ]
        environment = self._child_environment(descriptors, mode, engine_root)

        def mark_failed(code: str) -> None:
            current = sessions.load(applied["session_id"])
            if current["state"] == "RUNNING":
                failed = sessions.transition(
                    applied["session_id"],
                    "FAILED",
                    "controlled-run-failed",
                    {"diagnostic": hash_object({"code": code, "subject": subject})},
                    idempotency_key=idempotency_key,
                    approval_token_id=run_token["token_id"],
                    effect_references={
                        "run_failure_code": code,
                        "run_id": run_id,
                    },
                    retry_eligible=code in self.TRANSIENT_FAILURE_CODES,
                )
                # Persist a per-attempt failure marker so the retry chain is
                # walkable: <retry manifest>.retry_of -> this run id -> its
                # journal event via event_hash -> its own retry_of, and so on.
                # Create-only: the first failure of an attempt wins; replays
                # and same-attempt re-executions never rewrite it.
                attempt_record: Dict[str, Any] = {
                    "schema_version": "run-attempt-v1",
                    "run_id": run_id,
                    "status": "failed",
                    "failure_code": code,
                    "run_subject_hash": subject,
                    "retry_of": retry_of,
                    "event_hash": failed["last_event_hash"],
                    "sequence": int(failed["last_sequence"]),
                }
                run_root.mkdir(parents=True, exist_ok=True)
                try:
                    atomic_write_json(
                        run_root / "run-attempt.json",
                        attempt_record,
                        create_only=True,
                    )
                except AgentError as exc:
                    if exc.code != "BTAG-WRITE-EXISTS":
                        raise

        started = time.monotonic()
        try:
            completed = _execute_profile(
                {
                    "argv": argv,
                    "cwd": entrypoint.parent,
                    "env": environment,
                    "timeout_seconds": timeout_seconds,
                    "output_dir": run_root,
                }
            )
        except subprocess.TimeoutExpired as exc:
            mark_failed("BTAG-RUN-TIMEOUT")
            raise AgentError(
                "BTAG-RUN-TIMEOUT", "controlled child process timed out"
            ) from exc
        duration = time.monotonic() - started
        stdout = completed.stdout
        stderr = completed.stderr
        if len(stdout) > self.MAX_OUTPUT_BYTES or len(stderr) > self.MAX_OUTPUT_BYTES:
            mark_failed("BTAG-RUN-OUTPUT")
            raise AgentError("BTAG-RUN-OUTPUT", "child output exceeded the byte quota")
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if completed.returncode != 0:
            redacted = self._redact_stderr(
                stderr_text,
                state_root=self.state_root,
                entrypoint=entrypoint,
                descriptors=descriptors,
            )
            mark_failed("BTAG-RUN-FAILED")
            raise AgentError(
                "BTAG-RUN-FAILED",
                "controlled child process failed",
                details={"returncode": completed.returncode, "stderr": redacted},
            )
        try:
            payload = parse_child_result(stdout_text)
        except AgentError as exc:
            if exc.code in {"BTAG-RUN-RESULT", "BTAG-RUN-METRIC"}:
                mark_failed(exc.code)
            raise
        metrics = payload["metrics"]

        self._persist_exact_json(run_root / "run-manifest.json", run_manifest)
        persisted_manifest = run_root / "run-manifest.json"
        result: Dict[str, Any] = {
            "schema_version": "run-result-v1",
            "run_id": run_id,
            "status": "passed",
            "metrics": metrics,
            "diagnostics": [],
            "artifacts": [
                {
                    "path": "run-manifest.json",
                    "role": "run_manifest",
                    "bytes": persisted_manifest.stat().st_size,
                    "sha256": sha256_bytes(persisted_manifest.read_bytes()),
                }
            ],
            "extensions": {
                "backtrader_agent": {
                    "mode": mode,
                    "duration_seconds": round(duration, 6),
                    "dataset_manifest_hash": dataset["manifest_hash"],
                    "applied_artifact_hash": applied["applied_artifact_hash"],
                    "manifest_hash": run_manifest["manifest_hash"],
                    "validation_token_id": validation_token["token_id"],
                    "run_token_id": run_token["token_id"],
                    "environment_policy": {
                        "home_forwarded": False,
                        "inherited_environment": False,
                    },
                    "limitations": [
                        "P0 uses a timeout- and quota-bound local child process, not an OS sandbox.",
                        "P0 does not claim verified network isolation.",
                        "Only product-generated, hash-approved candidates are executable.",
                    ],
                }
            },
        }
        result["result_hash"] = hash_object(result)
        try:
            report_files = self._render_reports_resumable(run_root, result)
        except (AgentError, OSError) as exc:
            raise AgentError(
                "BTAG-RUN-PERSIST",
                "run output persistence was interrupted; retry the same effect",
            ) from exc
        record = {
            "schema_version": "idempotent-action-v1",
            "request_hash": request_hash,
            "result": result,
        }
        self._persist_exact_json(action_path, record)
        self._finish_successful_session(
            sessions,
            session_id=applied["session_id"],
            subject=subject,
            effect_id=effect_id,
            idempotency_key=idempotency_key,
            run_token=run_token,
            result=result,
            report_hash=report_files["report_hash"],
        )
        return result
