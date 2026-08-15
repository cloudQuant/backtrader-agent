"""Journaled multi-file transactions with verified rollback."""

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator

from ..canonical import (
    atomic_write_bytes,
    atomic_write_json,
    read_json,
    sha256_bytes,
)
from ..errors import AgentError
from ..locking import exclusive_file_lock
from .prepare import IDEMPOTENCY_RE


def _action_path(manager, key: str) -> Path:
    if not IDEMPOTENCY_RE.fullmatch(key):
        raise AgentError("BTAG-IDEMPOTENCY-KEY", "idempotency key is malformed")
    return manager.action_root / f"{sha256_bytes(key.encode('utf-8'))}.json"


def _action_lock_path(manager, key: str) -> Path:
    """Return the stable state-wide lock for one idempotency key."""

    _action_path(manager, key)
    digest = sha256_bytes(key.encode("utf-8"))
    return manager.state_root / "change-action-locks" / f"{digest}.lock"


@contextmanager
def _locked_action_key(manager, key: str) -> Iterator[None]:
    with exclusive_file_lock(
        _action_lock_path(manager, key),
        error_code="BTAG-CHANGE-ACTION-LOCK",
        subject="change idempotency action",
    ):
        yield


def _transaction_directory(manager, change_id: str) -> Path:
    if not re.fullmatch(r"change-[0-9a-f]{20}", change_id):
        raise AgentError("BTAG-CHANGE-ID", "change ID is malformed")
    return manager.transaction_root / change_id


def _target_root_lock_path(manager, target_root_id: str) -> Path:
    """Return the stable process lock for one registered mutable root."""

    manager.roots.get_record(target_root_id)
    digest = sha256_bytes(target_root_id.encode("utf-8"))
    return manager.state_root / "change-locks" / f"{digest}.lock"


@contextmanager
def _locked_target_root(manager, target_root_id: str) -> Iterator[None]:
    with exclusive_file_lock(
        _target_root_lock_path(manager, target_root_id),
        error_code="BTAG-CHANGE-LOCK",
        subject="change target root",
    ):
        yield


def _replace_target(
    index: int,
    target: Path,
    staged: Path,
    create_only: bool,
) -> None:
    del index
    atomic_write_bytes(target, staged.read_bytes(), create_only=create_only)


def _write_transaction(path: Path, transaction: Dict[str, Any]) -> None:
    atomic_write_json(path, transaction)


def _rollback_transaction(
    manager,
    transaction_path: Path,
    transaction: Dict[str, Any],
    resolved_by_path: Dict[str, Path],
) -> None:
    transaction_directory = transaction_path.parent
    for entry in reversed(transaction["entries"]):
        target = resolved_by_path[entry["target_relative_path"]]
        actual_hash = sha256_bytes(target.read_bytes()) if target.exists() else None
        if actual_hash == entry["expected_target_hash"]:
            entry["applied"] = False
            continue
        if actual_hash != entry["source_hash"]:
            raise AgentError(
                "BTAG-CHANGE-ROLLBACK",
                "transaction target has an unexpected recovery image",
            )
        backup_relative = entry.get("backup_relative_path")
        if backup_relative is None:
            if target.exists():
                target.unlink()
        else:
            backup = transaction_directory / backup_relative
            backup_bytes = backup.read_bytes()
            if sha256_bytes(backup_bytes) != entry["expected_target_hash"]:
                raise AgentError(
                    "BTAG-CHANGE-ROLLBACK",
                    "transaction backup hash is invalid",
                )
            atomic_write_bytes(target, backup_bytes)
        restored_hash = sha256_bytes(target.read_bytes()) if target.exists() else None
        if restored_hash != entry["expected_target_hash"]:
            raise AgentError(
                "BTAG-CHANGE-ROLLBACK",
                "transaction rollback did not restore the preimage",
            )
        entry["applied"] = False
    transaction["state"] = "ROLLED_BACK"
    manager._write_transaction(transaction_path, transaction)


def _prepare_transaction(
    manager,
    change_manifest: Dict[str, Any],
    effect_id: str,
    resolved: list,
) -> tuple:
    directory = manager._transaction_directory(change_manifest["change_id"])
    directory.mkdir(parents=True, exist_ok=True)
    transaction_path = directory / "transaction.json"
    resolved_by_path = {
        change["target_relative_path"]: target for change, target, _ in resolved
    }
    if transaction_path.exists():
        existing = read_json(transaction_path)
        if (
            existing.get("change_manifest_hash") != change_manifest["manifest_hash"]
            or existing.get("effect_id") != effect_id
        ):
            raise AgentError(
                "BTAG-CHANGE-TRANSACTION",
                "change transaction identity conflicts with an existing journal",
            )
        if existing.get("state") == "APPLYING":
            manager._rollback_transaction(transaction_path, existing, resolved_by_path)
        elif existing.get("state") == "COMMITTED":
            for entry in existing["entries"]:
                target = resolved_by_path[entry["target_relative_path"]]
                if (
                    not target.exists()
                    or sha256_bytes(target.read_bytes()) != entry["source_hash"]
                ):
                    raise AgentError(
                        "BTAG-CHANGE-TRANSACTION",
                        "committed transaction target no longer matches",
                    )
            return transaction_path, existing

    stage_directory = directory / "stage"
    backup_directory = directory / "backup"
    stage_directory.mkdir(parents=True, exist_ok=True)
    backup_directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (change, target, content) in enumerate(resolved):
        staged = stage_directory / f"{index}.bin"
        atomic_write_bytes(staged, content)
        backup_relative = None
        if target.exists():
            backup = backup_directory / f"{index}.bin"
            atomic_write_bytes(backup, target.read_bytes())
            backup_relative = backup.relative_to(directory).as_posix()
        entries.append(
            {
                "index": index,
                "target_relative_path": change["target_relative_path"],
                "expected_target_hash": change["expected_target_hash"],
                "source_hash": change["source_hash"],
                "staged_relative_path": staged.relative_to(directory).as_posix(),
                "backup_relative_path": backup_relative,
                "applied": False,
            }
        )
    transaction = {
        "schema_version": "change-transaction-v1",
        "change_id": change_manifest["change_id"],
        "change_manifest_hash": change_manifest["manifest_hash"],
        "effect_id": effect_id,
        "state": "PREPARED",
        "entries": entries,
    }
    manager._write_transaction(transaction_path, transaction)
    return transaction_path, transaction


def _apply_transaction(
    manager,
    transaction_path: Path,
    transaction: Dict[str, Any],
    resolved: list,
) -> None:
    if transaction["state"] == "COMMITTED":
        return
    directory = transaction_path.parent
    resolved_by_path = {
        change["target_relative_path"]: target for change, target, _ in resolved
    }
    transaction["state"] = "APPLYING"
    manager._write_transaction(transaction_path, transaction)
    try:
        for entry in transaction["entries"]:
            target = resolved_by_path[entry["target_relative_path"]]
            staged = directory / entry["staged_relative_path"]
            manager._replace_target(
                int(entry["index"]),
                target,
                staged,
                entry["expected_target_hash"] is None,
            )
            if sha256_bytes(target.read_bytes()) != entry["source_hash"]:
                raise AgentError(
                    "BTAG-CHANGE-POSTIMAGE",
                    "target hash verification failed",
                )
            entry["applied"] = True
            manager._write_transaction(transaction_path, transaction)
    except (AgentError, OSError) as exc:
        try:
            manager._rollback_transaction(
                transaction_path, transaction, resolved_by_path
            )
        except AgentError as rollback_error:
            raise AgentError(
                "BTAG-CHANGE-ROLLBACK",
                "multi-file change failed and rollback could not be verified",
            ) from rollback_error
        raise AgentError(
            "BTAG-CHANGE-TRANSACTION",
            "multi-file change failed and was rolled back",
        ) from exc
    transaction["state"] = "COMMITTED"
    manager._write_transaction(transaction_path, transaction)
