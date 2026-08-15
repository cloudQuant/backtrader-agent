"""Idempotent, hash-bound change application."""

from pathlib import Path
from typing import Any, Dict, List

from ..canonical import atomic_write_json, hash_object, read_json, sha256_bytes
from ..errors import AgentError
from ..sessions import SessionStore
from ..tokens import expected_bindings
from .prepare import _safe_relative


def _expected_applied_artifact(
    change_manifest: Dict[str, Any],
    change_token: Dict[str, Any],
) -> Dict[str, Any]:
    applied_files = [
        {
            "relative_path": change["target_relative_path"],
            "role": change["role"],
            "sha256": change["source_hash"],
            "size_bytes": change["size_bytes"],
        }
        for change in change_manifest["changes"]
    ]
    portable: Dict[str, Any] = {
        "schema_version": "applied-artifact-v1",
        "applied_artifact_id": f"applied-{change_manifest['manifest_hash'][:20]}",
        "generated_by": "backtrader-agent",
        "session_id": change_manifest["session_id"],
        "target_root_id": change_manifest["target_root_id"],
        "profile": change_manifest["profile"],
        "entrypoint": change_manifest["entrypoint"],
        "artifact_hash": change_manifest["artifact_hash"],
        "artifact_record_hash": change_manifest["artifact_record_hash"],
        "spec_hash": change_manifest["spec_hash"],
        "dataset_id": change_manifest["dataset_id"],
        "dataset_manifest_hash": change_manifest["dataset_manifest_hash"],
        "change_manifest_hash": change_manifest["manifest_hash"],
        "validation_token_id": change_manifest["validation_token_id"],
        "validation_token_hash": change_manifest["validation_token_hash"],
        "approval_id": change_token["approval_id"],
        "files": applied_files,
    }
    portable["applied_artifact_hash"] = hash_object(portable)
    return {
        **portable,
        "status": "applied",
    }


def _signed_action_record(
    manager,
    request_hash: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    portable: Dict[str, Any] = {
        "schema_version": "idempotent-action-v1",
        "request_hash": request_hash,
        "result": result,
    }
    portable["record_hash"] = hash_object(portable)
    return {
        **portable,
        "signature": manager.authority.sign_product_record(portable),
    }


def _load_action_record(manager, path: Path) -> Dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AgentError(
            "BTAG-IDEMPOTENCY-RECORD",
            "idempotent action record is not a regular product-owned file",
        )
    payload = manager.authority.verify_product_record(read_json(path))
    expected_hash = payload.get("record_hash")
    actual_hash = hash_object(
        {key: value for key, value in payload.items() if key != "record_hash"}
    )
    if (
        payload.get("schema_version") != "idempotent-action-v1"
        or expected_hash != actual_hash
        or not isinstance(payload.get("request_hash"), str)
        or not isinstance(payload.get("result"), dict)
    ):
        raise AgentError(
            "BTAG-IDEMPOTENCY-RECORD",
            "idempotent action record is malformed or has an invalid hash",
        )
    return payload


def _validate_cached_result(
    manager,
    recorded: Dict[str, Any],
    change_manifest: Dict[str, Any],
    change_token: Dict[str, Any],
) -> Dict[str, Any]:
    expected_artifact = manager._expected_applied_artifact(
        change_manifest, change_token
    )
    applied_record = manager.authority.load_bound_record(
        "applied-artifact",
        change_manifest["session_id"],
        expected_artifact["applied_artifact_hash"],
    )
    expected_result = {
        **expected_artifact,
        "applied_record_hash": applied_record["record_hash"],
    }
    if (
        applied_record.get("applied_artifact") != expected_artifact
        or recorded.get("result") != expected_result
    ):
        raise AgentError(
            "BTAG-IDEMPOTENCY-RESULT",
            "cached apply result does not match the signed artifact and original bindings",
        )
    return expected_result


def _ensure_applied_session(
    manager,
    result: Dict[str, Any],
    change_token: Dict[str, Any],
    *,
    idempotency_key: str,
) -> None:
    sessions = SessionStore(manager.state_root)
    session_id = result["session_id"]
    session = sessions.load(session_id)
    if session["state"] == "APPLY_PREPARED":
        sessions.transition(
            session_id,
            "APPLIED",
            "changes-apply",
            {"applied_artifact": result["applied_artifact_hash"]},
            idempotency_key=idempotency_key,
            approval_token_id=change_token["token_id"],
            effect_references={
                "applied_artifact_hash": result["applied_artifact_hash"],
                "applied_record_hash": result["applied_record_hash"],
            },
        )
    elif session["state"] == "APPLIED":
        if (
            session.get("artifacts", {}).get("applied_artifact_hash")
            != result["applied_artifact_hash"]
            or session.get("artifacts", {}).get("applied_record_hash")
            != result["applied_record_hash"]
        ):
            raise AgentError(
                "BTAG-CHANGE-SESSION",
                "session is bound to another applied artifact",
            )
    else:
        raise AgentError(
            "BTAG-CHANGE-SESSION",
            "session is no longer ready to commit this applied artifact",
        )


def _apply_locked(
    manager,
    change_manifest: Dict[str, Any],
    prepared_record: Dict[str, Any],
    change_token: Dict[str, Any],
    *,
    idempotency_key: str,
) -> Dict[str, Any]:
    action_path = manager._action_path(idempotency_key)
    request_hash = hash_object(
        {
            "action": "changes-apply",
            "manifest_hash": change_manifest["manifest_hash"],
            "token_id": change_token["token_id"],
        }
    )
    if action_path.exists():
        recorded = manager._load_action_record(action_path)
        if recorded.get("request_hash") != request_hash:
            raise AgentError(
                "BTAG-IDEMPOTENCY-CONFLICT",
                "idempotency key was already used for another request",
            )
        cached_result = manager._validate_cached_result(
            recorded,
            change_manifest,
            change_token,
        )
        manager._ensure_applied_session(
            cached_result,
            change_token,
            idempotency_key=idempotency_key,
        )
        return cached_result

    effect_id = hash_object(
        {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }
    )
    manager.authority.consume(change_token, effect_id=effect_id)

    draft = (manager.state_root / prepared_record["draft_relative_path"]).resolve(
        strict=True
    )
    try:
        draft.relative_to(manager.state_root.resolve())
    except ValueError as exc:
        raise AgentError(
            "BTAG-CHANGE-SOURCE",
            "signed prepared draft escapes the private state root",
        ) from exc
    resolved: List[tuple] = []
    for change in change_manifest["changes"]:
        source_rel = _safe_relative(change["source_relative_path"])
        source = draft.joinpath(*source_rel.parts).resolve(strict=True)
        try:
            source.relative_to(draft)
        except ValueError as exc:
            raise AgentError("BTAG-CHANGE-SOURCE", "source escapes draft root") from exc
        content = source.read_bytes()
        if sha256_bytes(content) != change["source_hash"]:
            raise AgentError(
                "BTAG-CHANGE-SOURCE-HASH", "draft bytes changed after prepare"
            )
        target = manager.roots.resolve(
            change_manifest["target_root_id"],
            change["target_relative_path"],
            for_write=True,
        )
        resolved.append((change, target, content))

    transaction_path = (
        manager._transaction_directory(change_manifest["change_id"])
        / "transaction.json"
    )
    if transaction_path.exists():
        transaction_path, transaction = manager._prepare_transaction(
            change_manifest,
            effect_id,
            resolved,
        )
    else:
        for change, target, _ in resolved:
            actual_preimage = (
                sha256_bytes(target.read_bytes()) if target.exists() else None
            )
            if actual_preimage != change["expected_target_hash"]:
                raise AgentError(
                    "BTAG-CHANGE-PREIMAGE",
                    "target changed after preview; prepare a new change manifest",
                )
        transaction_path, transaction = manager._prepare_transaction(
            change_manifest,
            effect_id,
            resolved,
        )
    if transaction["state"] != "COMMITTED":
        for change, target, _ in resolved:
            actual_preimage = (
                sha256_bytes(target.read_bytes()) if target.exists() else None
            )
            if actual_preimage != change["expected_target_hash"]:
                raise AgentError(
                    "BTAG-CHANGE-PREIMAGE",
                    "target changed after preview; prepare a new change manifest",
                )
    manager._apply_transaction(transaction_path, transaction, resolved)

    applied_artifact = manager._expected_applied_artifact(
        change_manifest,
        change_token,
    )
    applied_record = manager.authority.store_bound_record(
        "applied-artifact",
        change_manifest["session_id"],
        applied_artifact["applied_artifact_hash"],
        {"applied_artifact": applied_artifact},
    )
    result = {
        **applied_artifact,
        "applied_record_hash": applied_record["record_hash"],
    }
    atomic_write_json(
        action_path,
        manager._signed_action_record(request_hash, result),
        create_only=True,
    )
    manager._ensure_applied_session(
        result,
        change_token,
        idempotency_key=idempotency_key,
    )
    return result


def apply(
    manager,
    change_manifest: Dict[str, Any],
    change_token: Dict[str, Any],
    *,
    idempotency_key: str,
) -> Dict[str, Any]:
    portable = {
        key: value
        for key, value in change_manifest.items()
        if not key.startswith("_") and key != "manifest_hash"
    }
    if hash_object(portable) != change_manifest.get("manifest_hash"):
        raise AgentError("BTAG-CHANGE-HASH", "change manifest hash is invalid")
    canonical_manifest = {
        **portable,
        "manifest_hash": change_manifest["manifest_hash"],
    }
    prepared_record = manager.authority.load_bound_record(
        "prepared-change",
        str(canonical_manifest.get("session_id", "")),
        str(canonical_manifest.get("manifest_hash", "")),
    )
    if prepared_record.get("change_manifest") != canonical_manifest:
        raise AgentError(
            "BTAG-CHANGE-RECORD",
            "change manifest does not match the signed prepared change",
        )
    session = SessionStore(manager.state_root).load(canonical_manifest["session_id"])
    expected_session = {
        "artifact_hash": canonical_manifest["artifact_hash"],
        "artifact_record_hash": canonical_manifest["artifact_record_hash"],
        "approved_spec_hash": canonical_manifest["spec_hash"],
        "change_manifest_hash": canonical_manifest["manifest_hash"],
        "dataset_id": canonical_manifest["dataset_id"],
        "dataset_manifest_hash": canonical_manifest["dataset_manifest_hash"],
        "validation_token_hash": canonical_manifest["validation_token_hash"],
        "validation_token_id": canonical_manifest["validation_token_id"],
    }
    if session.get("state") not in {"APPLY_PREPARED", "APPLIED"} or any(
        session.get("artifacts", {}).get(key) != value
        for key, value in expected_session.items()
    ):
        raise AgentError(
            "BTAG-CHANGE-SESSION",
            "change manifest no longer matches its prepared session",
        )
    change_bindings = expected_bindings(
        "change",
        artifact_hash=canonical_manifest["artifact_hash"],
        artifact_record_hash=canonical_manifest["artifact_record_hash"],
        change_manifest_hash=canonical_manifest["manifest_hash"],
        dataset_hash=canonical_manifest["dataset_manifest_hash"],
        dataset_id=canonical_manifest["dataset_id"],
        session_id=canonical_manifest["session_id"],
        spec_hash=canonical_manifest["spec_hash"],
        validation_token_hash=canonical_manifest["validation_token_hash"],
        validation_token_id=canonical_manifest["validation_token_id"],
    )
    manager.authority.verify(
        change_token,
        kind="change",
        subject_hash=canonical_manifest["manifest_hash"],
        required_bindings=change_bindings,
    )
    with manager._locked_action_key(idempotency_key):
        with manager._locked_target_root(canonical_manifest["target_root_id"]):
            return manager._apply_locked(
                canonical_manifest,
                prepared_record,
                change_token,
                idempotency_key=idempotency_key,
            )
