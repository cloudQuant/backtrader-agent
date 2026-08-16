"""Approved sweep plan execution and ranked reporting (R17/R18).

``run_sweep`` consumes a one-time ``sweep`` token and executes the sealed
plan cell by cell: every cell renders a renderer-owned private draft under
``<state>/sweeps/<sweep_id>/cells/<cell_hash>/`` with the grid parameters
injected into the spec defaults, is validated through the deterministic
validator path, and runs through the controlled-runner profile core
(``runner.execute._execute_profile``). Sweep is strictly run-only: no
workspace writes, no apply stage. Per-cell RunManifest/RunResult records
live in the cell directory; a whitelisted transient cell failure retries
once. ``sweep_report`` ranks the per-cell results by ``final_value``
descending.

Plans that predate the sealed spec/engine/environment fields are refused
(``BTAG-SWEEP-LEGACY``): a legacy sweep token's engine bindings are
form-required only, so nothing in the sealed record attests them and the
run must fail closed instead of trusting caller-supplied values.
"""

import copy
import subprocess
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .canonical import (
    create_or_verify_json,
    hash_object,
    read_json,
    sha256_bytes,
)
from .contracts import StrategySpec
from .data import DatasetService
from .engines import inspect_engine, inspect_execution_environment
from .errors import AgentError
from .locking import exclusive_file_lock
from .memory import MemoryStore
from .roots import RootRegistry
from .runner import ControlledRunner
from .runner.execute import (
    _execute_profile,
    _persist_redacted_outputs,
    build_dataset_descriptors,
    parse_child_result,
)
from .runner.profiles import (
    _child_environment,
    _probe_engine,
    _require_profile_dependencies,
)
from .scaffold import ArtifactRenderer, load_product_artifact_record
from .sessions import SessionStore
from .sweep import load_plan
from .tokens import TokenAuthority, expected_bindings
from .validator import StrategyValidator

RUN_SCHEMA_VERSION = "sweep-run-v1"
REPORT_SCHEMA_VERSION = "sweep-result-v1"
SWEEP_RUN_LOCK_GRACE_SECONDS = 60
CELL_ATTEMPT_LIMIT = 2  # one execution plus one transient-failure retry

FAILURE_DIAGNOSTIC_MESSAGES = {
    "BTAG-RUN-TIMEOUT": "controlled cell child process timed out",
    "BTAG-RUN-OUTPUT": "cell child output exceeded the byte quota",
    "BTAG-RUN-FAILED": "controlled cell child process failed",
    "BTAG-RUN-RESULT": "cell child emitted no structured metrics",
    "BTAG-RUN-METRIC": "cell child metrics are invalid",
}


def _resolve_plan_engine(
    roots: RootRegistry, state_root: Path, plan: Dict[str, Any]
) -> Tuple[Path, Dict[str, Any]]:
    """Re-verify the plan-sealed engine binding against the current registry."""

    root_id = plan["engine_root_id"]
    descriptor = inspect_engine(roots, str(root_id))
    if descriptor["engine_hash"] != plan["engine_hash"]:
        raise AgentError(
            "BTAG-SWEEP-ENGINE",
            "registered Backtrader engine changed after sweep preparation",
        )
    source_warning = descriptor.get("source", {}).get("warning")
    if isinstance(source_warning, str) and source_warning:
        warnings.warn(source_warning, RuntimeWarning, stacklevel=2)
    record = roots.get_record(str(root_id))
    root = Path(record["path"]).resolve(strict=True)
    relative_import, _attested_version = _probe_engine(
        root, state_root, descriptor["version"]
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


def _cell_spec(plan: Dict[str, Any], cell: Dict[str, Any]) -> StrategySpec:
    """Re-derive the cell spec by injecting grid overrides into the sealed spec."""

    raw = copy.deepcopy(plan["spec"])
    for parameter in raw["parameters"]:
        if parameter["name"] in cell["params"]:
            parameter["default"] = cell["params"][parameter["name"]]
    return StrategySpec.from_dict(raw)


def _cell_run_id(
    plan: Dict[str, Any],
    cell: Dict[str, Any],
    artifact: Dict[str, Any],
    dataset: Dict[str, Any],
    environment_hash: str,
    timeout_per_cell: int,
) -> str:
    """Deterministic cell run id: stable across resuming processes."""

    request_hash = hash_object(
        {
            "action": "sweep-cell-run",
            "sweep_id": plan["sweep_id"],
            "cell_hash": cell["cell_hash"],
            "artifact_hash": artifact["artifact_hash"],
            "dataset_manifest_hash": dataset["manifest_hash"],
            "environment_hash": environment_hash,
            "timeout_per_cell": timeout_per_cell,
        }
    )
    return f"run-{request_hash[:20]}"


def _cell_argv(profile: str, entrypoint_name: str) -> List[str]:
    if profile == "python_bundle":
        return [sys.executable, entrypoint_name]
    return [
        sys.executable,
        "-m",
        "pytest",
        entrypoint_name,
        "-q",
        "-s",
        "-p",
        "no:cacheprovider",
    ]


def _verify_cell_provenance(
    state_root: Path,
    plan: Dict[str, Any],
    cell: Dict[str, Any],
    artifact: Dict[str, Any],
    dataset: Dict[str, Any],
    authority: TokenAuthority,
    cell_spec: StrategySpec,
) -> None:
    """Bind the rendered cell artifact to its signed renderer-owned record."""

    record = load_product_artifact_record(
        state_root, plan["session_id"], artifact["artifact_hash"], authority
    )
    draft_path = Path(artifact["_draft_path"]).resolve(strict=True)
    expected_draft = (
        state_root.resolve() / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]
    )
    try:
        relative = draft_path.relative_to(state_root.resolve()).as_posix()
    except ValueError as exc:
        raise AgentError(
            "BTAG-SWEEP-PROVENANCE", "cell draft escapes the state root"
        ) from exc
    extension = artifact.get("extensions", {}).get("backtrader_agent", {})
    if (
        draft_path != expected_draft
        or record.get("draft_relative_path") != relative
        or record.get("spec_hash") != artifact.get("spec_hash")
        or artifact.get("spec_hash") != cell_spec.spec_hash
        or record.get("dataset_id") != dataset.get("dataset_id")
        or record.get("dataset_manifest_hash") != plan["dataset_manifest_hash"]
        or extension.get("session_id") != plan["session_id"]
    ):
        raise AgentError(
            "BTAG-SWEEP-PROVENANCE",
            "cell artifact provenance does not match the sealed sweep plan",
        )


def _cell_run_manifest(
    run_id: str,
    artifact: Dict[str, Any],
    dataset: Dict[str, Any],
    engine_descriptor: Dict[str, Any],
    environment_hash: str,
    sweep_token: Dict[str, Any],
    validation_token: Dict[str, Any],
    *,
    plan: Dict[str, Any],
    cell: Dict[str, Any],
    profile: str,
    entrypoint_name: str,
    timeout_per_cell: int,
) -> Dict[str, Any]:
    run_manifest: Dict[str, Any] = {
        "schema_version": "run-manifest-v1",
        "run_id": run_id,
        "artifact_hash": artifact["artifact_hash"],
        "dataset_id": dataset["dataset_id"],
        "engine": engine_descriptor,
        "environment_hash": environment_hash,
        "run_profile": {
            "name": "controlled-runner-v1",
            "output_profile": profile,
            "mode": "runonce",
            "entrypoint": entrypoint_name,
            "timeout_seconds": timeout_per_cell,
        },
        "approval_id": sweep_token["approval_id"],
        "extensions": {
            "backtrader_agent": {
                "dataset_manifest_hash": dataset["manifest_hash"],
                "validation_token_id": validation_token["token_id"],
                "sweep_id": plan["sweep_id"],
                "cell_hash": cell["cell_hash"],
            }
        },
    }
    run_manifest["manifest_hash"] = hash_object(run_manifest)
    return run_manifest


def _persist_passed_cell(
    cell_dir: Path,
    run_id: str,
    metrics: Dict[str, Any],
    *,
    attempts: int,
    duration_seconds: float,
    manifest: Dict[str, Any],
    dataset: Dict[str, Any],
    validation_token: Dict[str, Any],
    plan: Dict[str, Any],
    cell: Dict[str, Any],
) -> str:
    create_or_verify_json(
        cell_dir / "run-manifest.json",
        manifest,
        conflict_code="BTAG-SWEEP-CELL",
        conflict_message="cell run manifest conflicts with immutable bytes",
    )
    persisted_manifest = cell_dir / "run-manifest.json"
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
                "mode": "runonce",
                "duration_seconds": round(duration_seconds, 6),
                "attempts": attempts,
                "dataset_manifest_hash": dataset["manifest_hash"],
                "validation_token_id": validation_token["token_id"],
                "sweep_id": plan["sweep_id"],
                "cell_hash": cell["cell_hash"],
                "manifest_hash": manifest["manifest_hash"],
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
    create_or_verify_json(
        cell_dir / "run-result.json",
        result,
        conflict_code="BTAG-SWEEP-CELL",
        conflict_message="cell run result conflicts with immutable bytes",
    )
    return "passed"


def _persist_failed_cell(
    cell_dir: Path,
    *,
    context: Dict[str, Any],
    failure_code: str,
    failure_stage: str,
    diagnostics: List[Dict[str, Any]],
    run_id: Optional[str],
    validation_token_id: Optional[str],
    attempts: int,
    duration_seconds: float,
) -> str:
    result: Dict[str, Any] = {
        "schema_version": "run-result-v1",
        "run_id": run_id,
        "status": "failed",
        "metrics": {},
        "diagnostics": diagnostics,
        "artifacts": [],
        "extensions": {
            "backtrader_agent": {
                "mode": "runonce",
                "duration_seconds": round(duration_seconds, 6),
                "attempts": attempts,
                "dataset_manifest_hash": context["dataset_manifest_hash"],
                "validation_token_id": validation_token_id,
                "sweep_id": context["sweep_id"],
                "cell_hash": context["cell_hash"],
                "failure_code": failure_code,
                "failure_stage": failure_stage,
            }
        },
    }
    result["result_hash"] = hash_object(result)
    create_or_verify_json(
        cell_dir / "run-result.json",
        result,
        conflict_code="BTAG-SWEEP-CELL",
        conflict_message="cell run result conflicts with immutable bytes",
    )
    return "failed"


def _persist_attempt_marker(
    cell_dir: Path, run_id: str, subject: str, code: str, *, attempt: int
) -> None:
    """Record the first transient cell failure; replays never rewrite it."""

    create_or_verify_json(
        cell_dir / "run-attempt.json",
        {
            "schema_version": "run-attempt-v1",
            "run_id": run_id,
            "status": "failed",
            "failure_code": code,
            "run_subject_hash": subject,
            "attempt": attempt,
            "retry_of": None,
            "event_hash": None,
            "sequence": None,
        },
        conflict_code="BTAG-SWEEP-CELL",
        conflict_message="cell run attempt marker conflicts",
    )


def _verify_persisted_cell(cell_dir: Path, result: Dict[str, Any]) -> None:
    portable = {key: value for key, value in result.items() if key != "result_hash"}
    if (
        result.get("schema_version") != "run-result-v1"
        or result.get("status") not in {"passed", "failed"}
        or hash_object(portable) != result.get("result_hash")
    ):
        raise AgentError("BTAG-SWEEP-CELL", "persisted cell run result is invalid")
    if result.get("status") != "passed":
        return
    manifest_path = cell_dir / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise AgentError("BTAG-SWEEP-CELL", "cell run manifest is missing")
    manifest = read_json(manifest_path)
    if (
        manifest.get("schema_version") != "run-manifest-v1"
        or manifest.get("run_id") != result.get("run_id")
        or hash_object(
            {key: value for key, value in manifest.items() if key != "manifest_hash"}
        )
        != manifest.get("manifest_hash")
    ):
        raise AgentError("BTAG-SWEEP-CELL", "persisted cell run manifest is invalid")


def _replay_cell(
    state_root: Path, plan: Dict[str, Any], cell: Dict[str, Any]
) -> Optional[str]:
    """Return the persisted cell status when a verified result exists."""

    cell_dir = state_root / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]
    result_path = cell_dir / "run-result.json"
    if not result_path.exists():
        return None
    if result_path.is_symlink() or not result_path.is_file():
        raise AgentError("BTAG-SWEEP-CELL", "cell run result path is unsafe")
    result = read_json(result_path)
    _verify_persisted_cell(cell_dir, result)
    return str(result["status"])


def _run_cell(
    state_root: Path,
    authority: TokenAuthority,
    plan: Dict[str, Any],
    cell: Dict[str, Any],
    dataset: Dict[str, Any],
    *,
    engine_root: Path,
    engine_descriptor: Dict[str, Any],
    environment_hash: str,
    sweep_token: Dict[str, Any],
    timeout_per_cell: int,
) -> str:
    """Render, validate, and execute one sweep cell (or replay its result).

    Contained wrapper: a domain error anywhere in the pipeline (replay
    verification, rendering, provenance, token issuance, descriptor building,
    or execution) persists a failed cell record so one bad cell cannot abort
    or strand the sweep. Unexpected exceptions propagate to the run loop,
    which journals the session failure before re-raising.
    """

    try:
        return _execute_cell(
            state_root,
            authority,
            plan,
            cell,
            dataset,
            engine_root=engine_root,
            engine_descriptor=engine_descriptor,
            environment_hash=environment_hash,
            sweep_token=sweep_token,
            timeout_per_cell=timeout_per_cell,
        )
    except AgentError as exc:
        cell_dir = (
            state_root / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]
        )
        return _persist_failed_cell(
            cell_dir,
            context={
                "sweep_id": plan["sweep_id"],
                "cell_hash": cell["cell_hash"],
                "dataset_manifest_hash": dataset["manifest_hash"],
            },
            failure_code=exc.code,
            failure_stage="cell",
            diagnostics=[
                {"code": exc.code, "severity": "error", "message": exc.message}
            ],
            run_id=None,
            validation_token_id=None,
            attempts=0,
            duration_seconds=0.0,
        )


def _execute_cell(
    state_root: Path,
    authority: TokenAuthority,
    plan: Dict[str, Any],
    cell: Dict[str, Any],
    dataset: Dict[str, Any],
    *,
    engine_root: Path,
    engine_descriptor: Dict[str, Any],
    environment_hash: str,
    sweep_token: Dict[str, Any],
    timeout_per_cell: int,
) -> str:
    """Execute the per-cell pipeline without error containment."""

    cell_dir = state_root / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]
    replayed = _replay_cell(state_root, plan, cell)
    if replayed is not None:
        return replayed
    context = {
        "sweep_id": plan["sweep_id"],
        "cell_hash": cell["cell_hash"],
        "dataset_manifest_hash": dataset["manifest_hash"],
    }
    cell_spec = _cell_spec(plan, cell)
    artifact = ArtifactRenderer(state_root).render(
        plan["session_id"], cell_spec, dataset, draft_root=cell_dir
    )
    _verify_cell_provenance(
        state_root, plan, cell, artifact, dataset, authority, cell_spec
    )
    validation_report = StrategyValidator(None).validate_artifact(artifact)
    create_or_verify_json(
        cell_dir / "validation-report.json",
        validation_report,
        conflict_code="BTAG-SWEEP-CELL",
        conflict_message="cell validation report conflicts",
    )
    if validation_report["status"] != "passed":
        return _persist_failed_cell(
            cell_dir,
            context=context,
            failure_code="BTAG-SWEEP-VALIDATE",
            failure_stage="validation",
            diagnostics=validation_report["diagnostics"],
            run_id=None,
            validation_token_id=None,
            attempts=0,
            duration_seconds=0.0,
        )
    validation_token = authority.issue(
        "validation",
        artifact["artifact_hash"],
        {
            "artifact_record_hash": artifact["_artifact_record_hash"],
            "dataset_hash": dataset["manifest_hash"],
            "dataset_id": dataset["dataset_id"],
            "engine_hash": plan["engine_hash"],
            "engine_root_id": plan["engine_root_id"],
            "environment_hash": environment_hash,
            "session_id": plan["session_id"],
            "spec_hash": cell_spec.spec_hash,
            "validation_hash": validation_report["validation_hash"],
        },
        approval="validator",
    )
    entrypoint_name = artifact["extensions"]["backtrader_agent"]["entrypoint"]
    entrypoint = (cell_dir / entrypoint_name).resolve(strict=True)
    entry = next(item for item in artifact["files"] if item["path"] == entrypoint_name)
    if sha256_bytes(entrypoint.read_bytes()) != entry["sha256"]:
        raise AgentError("BTAG-SWEEP-CELL", "cell entrypoint changed after validation")
    profile = cell_spec.profile
    descriptors = build_dataset_descriptors(state_root, dataset)
    argv = _cell_argv(profile, entrypoint_name)
    environment = _child_environment(descriptors, "runonce", engine_root)
    run_id = _cell_run_id(
        plan, cell, artifact, dataset, environment_hash, timeout_per_cell
    )
    subject = hash_object(
        {
            "artifact_hash": artifact["artifact_hash"],
            "dataset_manifest_hash": dataset["manifest_hash"],
            "validation_token_id": validation_token["token_id"],
            "mode": "runonce",
            "profile": "controlled-runner-v1",
        }
    )
    attempts = 0
    total_duration = 0.0
    timeout_stdout: Optional[bytes] = None
    timeout_stderr: Optional[bytes] = None
    while True:
        attempts += 1
        started = time.monotonic()
        code: Optional[str] = None
        try:
            completed = _execute_profile(
                {
                    "argv": argv,
                    "cwd": cell_dir,
                    "env": environment,
                    "timeout_seconds": timeout_per_cell,
                    "output_dir": cell_dir,
                }
            )
        except subprocess.TimeoutExpired as exc:
            code = "BTAG-RUN-TIMEOUT"
            completed = None
            timeout_stdout, timeout_stderr = exc.stdout, exc.stderr
        total_duration += time.monotonic() - started
        if completed is not None:
            stdout = completed.stdout
            stderr = completed.stderr
            if (
                len(stdout) > ControlledRunner.MAX_OUTPUT_BYTES
                or len(stderr) > ControlledRunner.MAX_OUTPUT_BYTES
            ):
                code = "BTAG-RUN-OUTPUT"
            elif completed.returncode != 0:
                code = "BTAG-RUN-FAILED"
            else:
                try:
                    payload = parse_child_result(
                        stdout.decode("utf-8", errors="replace")
                    )
                    manifest = _cell_run_manifest(
                        run_id,
                        artifact,
                        dataset,
                        engine_descriptor,
                        environment_hash,
                        sweep_token,
                        validation_token,
                        plan=plan,
                        cell=cell,
                        profile=profile,
                        entrypoint_name=entrypoint_name,
                        timeout_per_cell=timeout_per_cell,
                    )
                    return _persist_passed_cell(
                        cell_dir,
                        run_id,
                        payload["metrics"],
                        attempts=attempts,
                        duration_seconds=total_duration,
                        manifest=manifest,
                        dataset=dataset,
                        validation_token=validation_token,
                        plan=plan,
                        cell=cell,
                    )
                except AgentError as exc:
                    if exc.code in {"BTAG-RUN-RESULT", "BTAG-RUN-METRIC"}:
                        code = exc.code
                    else:
                        raise
        if (
            attempts < CELL_ATTEMPT_LIMIT
            and code in ControlledRunner.TRANSIENT_FAILURE_CODES
        ):
            _persist_attempt_marker(
                cell_dir, run_id, subject, str(code), attempt=attempts
            )
            continue
        _persist_redacted_outputs(
            cell_dir,
            completed.stdout if completed is not None else timeout_stdout,
            completed.stderr if completed is not None else timeout_stderr,
            state_root=state_root,
            entrypoint=entrypoint,
            descriptors=descriptors,
        )
        return _persist_failed_cell(
            cell_dir,
            context=context,
            failure_code=str(code),
            failure_stage="execution",
            diagnostics=[
                {
                    "code": code,
                    "severity": "error",
                    "message": FAILURE_DIAGNOSTIC_MESSAGES.get(
                        code, "controlled cell execution failed"
                    ),
                }
            ],
            run_id=run_id,
            validation_token_id=validation_token["token_id"],
            attempts=attempts,
            duration_seconds=total_duration,
        )


def _mark_sweep_failed(
    sessions: SessionStore,
    plan: Dict[str, Any],
    sweep_id: str,
    completed: int,
    failed: int,
    *,
    code: str,
) -> None:
    """Journal ``RUNNING -> FAILED`` before an uncontainable cell error re-raises.

    Mirrors the controlled runner's ``mark_failed``: the failure is committed
    to the session journal with ``retry_eligible=False`` so an interrupted
    sweep never silently strands in RUNNING with its one-time token consumed.
    """

    sessions.transition(
        plan["session_id"],
        "FAILED",
        "sweep-failed",
        {"sweep": plan["plan_hash"]},
        effect_references={
            "sweep_id": sweep_id,
            "sweep_plan_hash": plan["plan_hash"],
            "cells_completed": str(completed),
            "cells_failed": str(failed),
            "cells_pending": str(len(plan["cells"]) - completed - failed),
            "run_failure_code": code,
        },
        retry_eligible=False,
    )


@contextmanager
def _locked_sweep(
    state_root: Path, sweep_id: str, *, timeout_per_cell: int
) -> Iterator[None]:
    lock_path = (
        state_root
        / "actions"
        / f"sweep-run-{sha256_bytes(sweep_id.encode('utf-8'))}.lock"
    )
    with exclusive_file_lock(
        lock_path,
        error_code="BTAG-SWEEP-LOCK",
        subject="sweep run",
        timeout_seconds=float(timeout_per_cell + SWEEP_RUN_LOCK_GRACE_SECONDS),
    ):
        yield


def _preflight(
    state_root: Path,
    roots: RootRegistry,
    authority: TokenAuthority,
    plan: Dict[str, Any],
    token: Dict[str, Any],
) -> Tuple[Path, Dict[str, Any], Dict[str, Any], str]:
    """Fail closed on every binding mismatch before the token is consumed."""

    if (
        not isinstance(plan.get("spec"), dict)
        or not isinstance(plan.get("engine_root_id"), str)
        or not plan["engine_root_id"]
        or not isinstance(plan.get("engine_hash"), str)
        or not isinstance(plan.get("environment_hash"), str)
    ):
        raise AgentError(
            "BTAG-SWEEP-LEGACY",
            "sweep plan predates the sealed spec/engine/environment fields "
            "and cannot be executed; re-prepare the sweep",
        )
    if hash_object(plan["spec"]) != plan["spec_hash"]:
        raise AgentError(
            "BTAG-SWEEP-PLAN", "sweep plan spec does not match its sealed hash"
        )
    sessions = SessionStore(state_root)
    session = sessions.load(plan["session_id"])
    state_name = session.get("state")
    if state_name not in {"SWEEP_PREPARED", "RUNNING", "PAUSED"}:
        raise AgentError(
            "BTAG-SWEEP-SESSION",
            "session is not ready for sweep execution",
            details={"state": state_name},
        )
    artifacts = session.get("artifacts", {})
    if (
        artifacts.get("sweep_id") != plan["sweep_id"]
        or artifacts.get("sweep_plan_hash") != plan["plan_hash"]
    ):
        raise AgentError(
            "BTAG-SWEEP-SESSION", "session does not reference this sweep plan"
        )
    authority.verify(
        token,
        kind="sweep",
        subject_hash=plan["plan_hash"],
        required_bindings=expected_bindings(
            "sweep",
            session_id=plan["session_id"],
            sweep_plan_hash=plan["plan_hash"],
            dataset_manifest_hash=plan["dataset_manifest_hash"],
            environment_hash=plan["environment_hash"],
            engine_hash=plan["engine_hash"],
            engine_root_id=plan["engine_root_id"],
            spec_hash=plan["spec_hash"],
        ),
    )
    engine_root, engine_descriptor = _resolve_plan_engine(roots, state_root, plan)
    environment = inspect_execution_environment()
    if environment.get("environment_hash") != plan["environment_hash"]:
        raise AgentError(
            "BTAG-SWEEP-ENVIRONMENT",
            "Python execution environment changed after sweep preparation",
        )
    dataset_id = artifacts.get("dataset_id")
    if not isinstance(dataset_id, str):
        raise AgentError(
            "BTAG-SWEEP-SESSION", "session does not reference a registered dataset"
        )
    dataset = DatasetService(roots, state_root).load(dataset_id)
    if dataset.get("manifest_hash") != plan["dataset_manifest_hash"]:
        raise AgentError(
            "BTAG-SWEEP-DATASET", "registered dataset does not match the sweep plan"
        )
    _require_profile_dependencies(str(plan["spec"].get("output_profile")))
    return engine_root, engine_descriptor, dataset, state_name


def run_sweep(
    state: Path,
    roots: RootRegistry,
    authority: TokenAuthority,
    sweep_id: str,
    token: Dict[str, Any],
    max_cells: int = 100,
    timeout_per_cell: int = 120,
) -> Dict[str, Any]:
    """Execute an approved SweepPlan cell by cell through the controlled runner.

    The one-time sweep token is verified against the pinned sweep binding
    shape and consumed exactly once; an interrupted run resumes by replaying
    verified per-cell results under the same token and effect. A whitelisted
    transient cell failure retries once; a cell that fails anywhere in its
    pipeline with a domain error is persisted as a failed cell record and the
    remaining cells still complete. On completion the top-5 ranked passed
    cells are recorded as cross-session parameter priors for the plan
    archetype (R22); a priors write failure journals FAILED before re-raising
    so the sweep never reports success with the priors record missing. An
    unexpected in-process exception journals the session FAILED before
    re-raising so the sweep never silently strands in RUNNING.
    """

    state_root = Path(state)
    if not isinstance(roots, RootRegistry):
        raise AgentError("BTAG-SWEEP-ROOTS", "roots must be a RootRegistry")
    if not isinstance(authority, TokenAuthority):
        raise AgentError("BTAG-SWEEP-AUTHORITY", "authority must be a TokenAuthority")
    if (
        isinstance(max_cells, bool)
        or not isinstance(max_cells, int)
        or not 1 <= max_cells <= 1000
    ):
        raise AgentError("BTAG-SWEEP-BOUNDS", "max_cells must be between 1 and 1000")
    if (
        isinstance(timeout_per_cell, bool)
        or not isinstance(timeout_per_cell, int)
        or not 1 <= timeout_per_cell <= 600
    ):
        raise AgentError(
            "BTAG-SWEEP-TIMEOUT",
            "timeout_per_cell must be between 1 and 600 seconds",
        )
    plan = load_plan(state_root, sweep_id)
    engine_root, engine_descriptor, dataset, state_name = _preflight(
        state_root, roots, authority, plan, token
    )
    request_hash = hash_object(
        {
            "action": "sweep-run",
            "sweep_id": sweep_id,
            "sweep_token_id": token["token_id"],
            "max_cells": max_cells,
            "timeout_per_cell": timeout_per_cell,
        }
    )
    effect_id = hash_object({"sweep_id": sweep_id, "request_hash": request_hash})
    with _locked_sweep(state_root, sweep_id, timeout_per_cell=timeout_per_cell):
        authority.consume(token, effect_id=effect_id)
        sessions = SessionStore(state_root)
        if state_name in {"SWEEP_PREPARED", "PAUSED"}:
            sessions.transition(
                plan["session_id"],
                "RUNNING",
                "sweep",
                {
                    "spec": plan["spec_hash"],
                    "dataset": plan["dataset_manifest_hash"],
                    "sweep": plan["plan_hash"],
                },
                approval_token_id=token["token_id"],
                effect_references={
                    "sweep_id": sweep_id,
                    "sweep_plan_hash": plan["plan_hash"],
                },
            )
        cells = plan["cells"]
        attempted = cells[:max_cells]
        completed = 0
        failed = 0
        for cell in attempted:
            try:
                status = _run_cell(
                    state_root,
                    authority,
                    plan,
                    cell,
                    dataset,
                    engine_root=engine_root,
                    engine_descriptor=engine_descriptor,
                    environment_hash=plan["environment_hash"],
                    sweep_token=token,
                    timeout_per_cell=timeout_per_cell,
                )
            except AgentError as exc:
                # Containment itself failed (e.g. the tampered cell directory
                # rejected the failure record): journal and fail closed.
                _mark_sweep_failed(
                    sessions, plan, sweep_id, completed, failed, code=exc.code
                )
                raise
            except Exception:
                # Unexpected in-process failure: never strand the session in
                # RUNNING with the token already consumed.
                _mark_sweep_failed(
                    sessions,
                    plan,
                    sweep_id,
                    completed,
                    failed,
                    code="BTAG-SWEEP-CRASH",
                )
                raise
            if status == "passed":
                completed += 1
            else:
                failed += 1
        try:
            _record_priors(state_root, plan)
        except AgentError as exc:
            # The cross-session priors effect is part of a complete sweep:
            # journal FAILED (retryable via a replayed run) instead of ever
            # reporting success with the priors record missing.
            _mark_sweep_failed(
                sessions, plan, sweep_id, completed, failed, code=exc.code
            )
            raise
        except Exception:
            # Unexpected in-process priors failure: never strand the session.
            _mark_sweep_failed(
                sessions,
                plan,
                sweep_id,
                completed,
                failed,
                code="BTAG-SWEEP-CRASH",
            )
            raise
        pending = len(cells) - len(attempted)
        final_state = "PASSED" if failed == 0 else "FAILED"
        effects = {
            "sweep_id": sweep_id,
            "sweep_plan_hash": plan["plan_hash"],
            "cells_completed": str(completed),
            "cells_failed": str(failed),
            "cells_pending": str(pending),
        }
        if final_state == "FAILED":
            effects["run_failure_code"] = "BTAG-SWEEP-CELL"
        sessions.transition(
            plan["session_id"],
            final_state,
            "sweep-complete",
            {"sweep": plan["plan_hash"]},
            effect_references=effects,
            retry_eligible=False,
        )
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "sweep_id": sweep_id,
            "session_id": plan["session_id"],
            "cells_total": len(cells),
            "cells_completed": completed,
            "cells_failed": failed,
            "cells_skipped": pending,
        }


def _load_cell_result(
    state_root: Path, plan: Dict[str, Any], cell: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    cell_dir = state_root / "sweeps" / plan["sweep_id"] / "cells" / cell["cell_hash"]
    result_path = cell_dir / "run-result.json"
    if not result_path.exists():
        return None
    if result_path.is_symlink() or not result_path.is_file():
        raise AgentError("BTAG-SWEEP-RESULT", "cell run result path is unsafe")
    try:
        result = read_json(result_path)
    except AgentError as exc:
        raise AgentError("BTAG-SWEEP-RESULT", "cell run result is corrupt") from exc
    portable = {key: value for key, value in result.items() if key != "result_hash"}
    if (
        result.get("schema_version") != "run-result-v1"
        or result.get("status") not in {"passed", "failed"}
        or hash_object(portable) != result.get("result_hash")
    ):
        raise AgentError(
            "BTAG-SWEEP-RESULT", "cell run result hash or status is invalid"
        )
    if result.get("status") == "passed":
        manifest_path = cell_dir / "run-manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise AgentError("BTAG-SWEEP-RESULT", "cell run manifest is missing")
        manifest = read_json(manifest_path)
        if (
            manifest.get("schema_version") != "run-manifest-v1"
            or manifest.get("run_id") != result.get("run_id")
            or hash_object(
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "manifest_hash"
                }
            )
            != manifest.get("manifest_hash")
        ):
            raise AgentError("BTAG-SWEEP-RESULT", "cell run manifest is invalid")
    return result


def _record_priors(state_root: Path, plan: Dict[str, Any]) -> None:
    """Persist the sweep's top-5 ranked passed cells as parameter priors (R22).

    Every passed cell's verified run result contributes its params and
    ``final_value`` to the cross-session memory store under the plan
    archetype; the store merges, deduplicates, and keeps the best 5 by
    ranking. A sweep with no passed cells records nothing.
    """

    entries: List[Dict[str, Any]] = []
    for cell in plan["cells"]:
        result = _load_cell_result(state_root, plan, cell)
        if result is None or result.get("status") != "passed":
            continue
        metrics = result.get("metrics")
        entries.append(
            {
                "sweep_id": plan["sweep_id"],
                "cell_id": cell["cell_id"],
                "params": cell["params"],
                # A missing/non-finite final_value is rejected as a contained
                # domain failure by ``record_priors`` instead of escaping here.
                "final_value": (
                    metrics.get("final_value") if isinstance(metrics, dict) else None
                ),
            }
        )
    if not entries:
        return
    archetype = plan["spec"].get("archetype")
    if not isinstance(archetype, str) or not archetype:
        raise AgentError(
            "BTAG-SWEEP-ARCHETYPE", "sweep plan spec declares no archetype"
        )
    MemoryStore(state_root).record_priors(archetype, entries)


def sweep_report(state: Path, sweep_id: str) -> Dict[str, Any]:
    """Rank the per-cell sweep results by ``final_value`` descending.

    Cells without a persisted result report as ``pending``; failed cells keep
    their plan order after every passed cell. Tampered persisted results are
    rejected with ``BTAG-SWEEP-RESULT``.
    """

    state_root = Path(state)
    plan = load_plan(state_root, sweep_id)
    cells: List[Dict[str, Any]] = []
    completed = 0
    failed = 0
    pending = 0
    for cell in plan["cells"]:
        result = _load_cell_result(state_root, plan, cell)
        entry: Dict[str, Any] = {
            "cell_id": cell["cell_id"],
            "cell_hash": cell["cell_hash"],
            "params": cell["params"],
        }
        if result is None:
            pending += 1
            entry.update(
                {
                    "status": "pending",
                    "run_id": None,
                    "metrics": None,
                    "failure_code": None,
                    "diagnostics": [],
                }
            )
        elif result.get("status") == "passed":
            completed += 1
            entry.update(
                {
                    "status": "passed",
                    "run_id": result["run_id"],
                    "metrics": result["metrics"],
                    "failure_code": None,
                    "diagnostics": [],
                }
            )
        else:
            failed += 1
            entry.update(
                {
                    "status": "failed",
                    "run_id": result.get("run_id"),
                    "metrics": None,
                    "failure_code": result.get("extensions", {})
                    .get("backtrader_agent", {})
                    .get("failure_code"),
                    "diagnostics": result.get("diagnostics", []),
                }
            )
        cells.append(entry)
    cells.sort(
        key=lambda item: (
            0 if item["status"] == "passed" else 1,
            -(item["metrics"]["final_value"]) if item["status"] == "passed" else 0,
        )
    )
    report: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "sweep_id": sweep_id,
        "plan_hash": plan["plan_hash"],
        "session_id": plan["session_id"],
        "cells_completed": completed,
        "cells_failed": failed,
        "cells_pending": pending,
        "cells": cells,
    }
    report["report_hash"] = hash_object(report)
    return report
