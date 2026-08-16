"""Run-result listing, persistence, verification, and redaction."""

import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..canonical import (
    atomic_write_bytes,
    atomic_write_json,
    hash_object,
    read_json,
)
from ..errors import AgentError
from ..report import ReportRenderer


def list_runs(state_root: Path) -> Dict[str, Any]:
    """Return compact summaries of every persisted run result plus the count
    of corrupt records skipped (R21).

    Corrupt result records are skipped so one bad run cannot hide the rest;
    the skip count keeps that degradation visible to listing commands.
    """

    runs_root = Path(state_root) / "runs"
    if not runs_root.is_dir():
        return {"runs": [], "skipped": 0}
    summaries: List[Dict[str, Any]] = []
    skipped = 0
    for path in sorted(runs_root.glob("*/run-result.json")):
        try:
            result = read_json(path)
        except (OSError, ValueError, AgentError):
            skipped += 1
            continue
        if result.get("schema_version") != "run-result-v1":
            skipped += 1
            continue
        metrics = result.get("metrics", {})
        summaries.append(
            {
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "final_value": metrics.get("final_value"),
                "trade_num": metrics.get("trade_num"),
                "result_hash": result.get("result_hash"),
            }
        )
    return {"runs": summaries, "skipped": skipped}


def _persist_exact_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise AgentError(
                "BTAG-RUN-CONFLICT",
                "persisted run artifact conflicts with the approved effect",
            )
        return
    try:
        atomic_write_bytes(path, content, create_only=True)
    except AgentError as exc:
        if exc.code != "BTAG-WRITE-EXISTS":
            raise
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise AgentError(
                "BTAG-RUN-CONFLICT",
                "persisted run artifact conflicts with the approved effect",
            ) from exc


def _persist_exact_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".json-stage-", dir=str(path.parent)
    ) as name:
        staged = Path(name) / "value.json"
        atomic_write_json(staged, value, create_only=True)
        _persist_exact_bytes(path, staged.read_bytes())


def _render_reports_resumable(
    runner,
    run_root: Path,
    result: Dict[str, Any],
) -> Dict[str, str]:
    run_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{result['run_id']}.report-stage-",
        dir=str(run_root.parent),
    ) as name:
        staged_root = Path(name)
        report_files = ReportRenderer().render(staged_root, result)
        for filename in (
            report_files["result"],
            report_files["markdown"],
            report_files["html"],
        ):
            runner._persist_exact_bytes(
                run_root / filename,
                (staged_root / filename).read_bytes(),
            )
    return report_files


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
    payload = {key: value for key, value in result.items() if key != "result_hash"}
    extension = result.get("extensions", {}).get("backtrader_agent", {})
    if (
        result.get("run_id") != run_id
        or result.get("status") != "passed"
        or hash_object(payload) != result.get("result_hash")
        or extension.get("mode") != mode
        or extension.get("dataset_manifest_hash") != dataset["manifest_hash"]
        or extension.get("applied_artifact_hash") != applied["applied_artifact_hash"]
        or extension.get("validation_token_id") != validation_token["token_id"]
        or extension.get("run_token_id") != run_token["token_id"]
    ):
        raise AgentError(
            "BTAG-RUN-CONFLICT",
            "persisted run result conflicts with the approved effect",
        )


def _verify_persisted_manifest(
    run_root: Path,
    result: Dict[str, Any],
    expected: Dict[str, Any],
) -> None:
    manifest_path = run_root / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise AgentError(
            "BTAG-RUN-CONFLICT", "persisted run manifest is absent or unsafe"
        )
    manifest = read_json(manifest_path)
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if (
        manifest != expected
        or hash_object(payload) != manifest.get("manifest_hash")
        or manifest.get("manifest_hash")
        != result.get("extensions", {}).get("backtrader_agent", {}).get("manifest_hash")
    ):
        raise AgentError("BTAG-RUN-CONFLICT", "persisted run manifest is invalid")


def _redact_stderr(
    stderr: str,
    *,
    state_root: Path,
    entrypoint: Path,
    descriptors: List[Dict[str, Any]],
) -> str:
    redacted = stderr[-2000:]
    replacements = {
        str(state_root.resolve()): "<state>",
        str(entrypoint.parent.resolve()): "<artifact>",
        str(Path(sys.executable).resolve().parent): "<runtime>",
    }
    replacements.update(
        {str(Path(item["path"]).resolve()): "<dataset>" for item in descriptors}
    )
    for value, label in sorted(replacements.items(), key=lambda item: -len(item[0])):
        redacted = redacted.replace(value, label)
    redacted = re.sub(
        r"(?<![A-Za-z0-9_])/(?:[A-Za-z0-9._~+@%=-]+/)*[A-Za-z0-9._~+@%=-]+",
        "<path>",
        redacted,
    )
    redacted = re.sub(
        r"\b[A-Za-z]:\\(?:[^\\\s:'\"]+\\)*[^\\\s:'\"]+",
        "<path>",
        redacted,
    )
    return redacted
