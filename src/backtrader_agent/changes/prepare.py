"""Confined change-set preparation bound to a validated artifact."""

import difflib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

from ..canonical import (
    hash_object,
    read_json,
    sha256_bytes,
)
from ..errors import AgentError
from ..scaffold import load_product_artifact_record
from ..sessions import SessionStore
from ..tokens import expected_bindings

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")


def _safe_relative(value: str) -> PurePosixPath:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if not value or candidate.is_absolute() or ".." in candidate.parts:
        raise AgentError(
            "BTAG-CHANGE-PATH", "change paths must be confined relative paths"
        )
    return candidate


def _role(source: str) -> str:
    name = PurePosixPath(source).name
    if name == "run.py":
        return "runner"
    if name == "config.yaml":
        return "config"
    if name.startswith("test_"):
        return "strategy"
    if name.startswith("strategy_"):
        return "strategy"
    return "support"


def prepare(
    manager,
    *,
    session_id: str,
    draft_root: Path,
    files: List[Dict[str, str]],
    target_root_id: str,
    validation_token: Dict[str, Any],
) -> Dict[str, Any]:
    manager.authority.verify(
        validation_token,
        kind="validation",
        subject_hash=validation_token.get("subject_hash", ""),
        required_bindings=expected_bindings("validation", session_id=session_id),
    )
    draft = Path(draft_root).resolve(strict=True)
    artifact_path = draft / "artifact-manifest.json"
    if not artifact_path.is_file():
        raise AgentError(
            "BTAG-CHANGE-ARTIFACT",
            "draft is missing its validated artifact manifest",
        )
    artifact = read_json(artifact_path)
    artifact_payload = {
        key: value for key, value in artifact.items() if key != "artifact_hash"
    }
    product_record = load_product_artifact_record(
        manager.state_root,
        session_id,
        str(artifact.get("artifact_hash", "")),
        manager.authority,
    )
    expected_draft = (
        manager.state_root / product_record["draft_relative_path"]
    ).resolve(strict=True)
    extension = artifact.get("extensions", {}).get("backtrader_agent", {})
    if (
        hash_object(artifact_payload) != artifact.get("artifact_hash")
        or artifact.get("artifact_hash") != validation_token["subject_hash"]
        or draft != expected_draft
        or sha256_bytes(artifact_path.read_bytes()) != product_record["manifest_sha256"]
        or extension.get("generated_by") != "backtrader-agent"
        or extension.get("session_id") != session_id
        or extension.get("dataset_manifest_hash")
        != product_record["dataset_manifest_hash"]
        or validation_token.get("bindings", {}).get("artifact_record_hash")
        != product_record["record_hash"]
        or validation_token.get("bindings", {}).get("spec_hash")
        != product_record["spec_hash"]
        or validation_token.get("bindings", {}).get("dataset_id")
        != product_record["dataset_id"]
        or validation_token.get("bindings", {}).get("dataset_hash")
        != product_record["dataset_manifest_hash"]
    ):
        raise AgentError(
            "BTAG-CHANGE-ARTIFACT",
            "draft artifact is not bound to the validation token",
        )
    artifact_files = {item["path"]: item for item in artifact.get("files", [])}
    if not files or len(files) > 8:
        raise AgentError("BTAG-CHANGE-COUNT", "change set must contain 1 to 8 files")
    changes: List[Dict[str, Any]] = []
    targets = set()
    for item in files:
        source_relative = _safe_relative(item["source"])
        target_relative = _safe_relative(item["target"])
        target_text = target_relative.as_posix()
        if target_text in targets:
            raise AgentError("BTAG-CHANGE-DUPLICATE", "target path is duplicated")
        targets.add(target_text)
        source = draft.joinpath(*source_relative.parts).resolve(strict=True)
        try:
            source.relative_to(draft)
        except ValueError as exc:
            raise AgentError(
                "BTAG-CHANGE-SOURCE", "draft source escapes draft root"
            ) from exc
        if not source.is_file() or source.is_symlink():
            raise AgentError(
                "BTAG-CHANGE-SOURCE", "draft source must be a regular file"
            )
        content = source.read_bytes()
        artifact_file = artifact_files.get(source_relative.as_posix())
        if artifact_file is None or artifact_file.get("sha256") != sha256_bytes(
            content
        ):
            raise AgentError(
                "BTAG-CHANGE-ARTIFACT",
                "change source is not in the validated artifact",
            )
        if len(content) > 256 * 1024:
            raise AgentError("BTAG-CHANGE-SIZE", "change file exceeds byte quota")
        target = manager.roots.resolve(
            target_root_id, target_text, for_write=True, require_file=False
        )
        if target.exists() and not target.is_file():
            raise AgentError("BTAG-CHANGE-TARGET", "target exists but is not a file")
        old_bytes = target.read_bytes() if target.exists() else b""
        old_hash = sha256_bytes(old_bytes) if target.exists() else None
        try:
            old_text = old_bytes.decode("utf-8").splitlines(keepends=True)
            new_text = content.decode("utf-8").splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(
                    old_text,
                    new_text,
                    fromfile=f"a/{target_text}",
                    tofile=f"b/{target_text}",
                )
            )
        except UnicodeDecodeError:
            diff = f"binary {len(old_bytes)} -> {len(content)} bytes"
        changes.append(
            {
                "source_relative_path": source_relative.as_posix(),
                "target_relative_path": target_text,
                "role": _role(source_relative.as_posix()),
                "source_hash": sha256_bytes(content),
                "expected_target_hash": old_hash,
                "size_bytes": len(content),
                "diff": diff[:128_000],
            }
        )
    profile = (
        "python_bundle"
        if any(change["source_relative_path"] == "run.py" for change in changes)
        else "single_test"
    )
    entrypoint = next(
        (
            change["target_relative_path"]
            for change in changes
            if (
                change["source_relative_path"] == "run.py"
                if profile == "python_bundle"
                else change["source_relative_path"].startswith("test_")
            )
        ),
        None,
    )
    portable: Dict[str, Any] = {
        "schema_version": "change-manifest-v1",
        "change_id": "",
        "session_id": session_id,
        "target_root_id": target_root_id,
        "profile": profile,
        "entrypoint": entrypoint,
        "artifact_hash": validation_token["subject_hash"],
        "artifact_record_hash": product_record["record_hash"],
        "spec_hash": product_record["spec_hash"],
        "dataset_id": artifact["dataset_id"],
        "dataset_manifest_hash": product_record["dataset_manifest_hash"],
        "validation_token_id": validation_token["token_id"],
        "validation_token_hash": hash_object(validation_token),
        "policy": "create-or-expected-hash",
        "changes": changes,
    }
    identity = hash_object(
        {key: value for key, value in portable.items() if key != "change_id"}
    )
    portable["change_id"] = f"change-{identity[:20]}"
    portable["manifest_hash"] = hash_object(portable)
    result = {**portable, "_draft_path": str(draft)}
    sessions = SessionStore(manager.state_root)
    session = sessions.load(session_id)
    if (
        session.get("artifacts", {}).get("artifact_hash") != portable["artifact_hash"]
        or session.get("artifacts", {}).get("approved_spec_hash")
        != portable["spec_hash"]
        or session.get("artifacts", {}).get("dataset_id") != portable["dataset_id"]
        or session.get("artifacts", {}).get("dataset_manifest_hash")
        != portable["dataset_manifest_hash"]
        or session.get("artifacts", {}).get("validation_hash")
        != validation_token.get("bindings", {}).get("validation_hash")
        or session.get("artifacts", {}).get("validation_token_id")
        != validation_token.get("token_id")
        or session.get("artifacts", {}).get("validation_token_hash")
        != portable["validation_token_hash"]
        or session.get("artifacts", {}).get("artifact_record_hash")
        != product_record["record_hash"]
    ):
        raise AgentError(
            "BTAG-CHANGE-SESSION",
            "session evidence does not match the validated product artifact",
        )
    manager.authority.store_bound_record(
        "prepared-change",
        session_id,
        portable["manifest_hash"],
        {
            "change_manifest": portable,
            "draft_relative_path": product_record["draft_relative_path"],
            "validation_token_hash": portable["validation_token_hash"],
        },
    )
    if session["state"] == "VALIDATED":
        sessions.transition(
            session_id,
            "APPLY_PREPARED",
            "changes-prepare",
            {
                "artifact": portable["artifact_hash"],
                "change_manifest": portable["manifest_hash"],
            },
            effect_references={"change_manifest_hash": portable["manifest_hash"]},
        )
    elif (
        session["state"] != "APPLY_PREPARED"
        or session.get("artifacts", {}).get("change_manifest_hash")
        != portable["manifest_hash"]
    ):
        raise AgentError(
            "BTAG-CHANGE-SESSION",
            "session is not ready for this prepared change",
        )
    return result
