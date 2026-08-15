"""Two-phase, hash-bound, confined change preparation and application."""

from pathlib import Path
from typing import Any, Dict, List

from ..roots import RootRegistry
from ..tokens import TokenAuthority
from . import apply as apply_module
from . import prepare as prepare_module
from . import rollback as rollback_module


class ChangeManager:
    def __init__(
        self, roots: RootRegistry, state_root: Path, authority: TokenAuthority
    ) -> None:
        self.roots = roots
        self.state_root = Path(state_root)
        self.authority = authority
        self.action_root = self.state_root / "actions"
        self.transaction_root = self.state_root / "transactions"

    def prepare(
        self,
        *,
        session_id: str,
        draft_root: Path,
        files: List[Dict[str, str]],
        target_root_id: str,
        validation_token: Dict[str, Any],
    ) -> Dict[str, Any]:
        return prepare_module.prepare(
            self,
            session_id=session_id,
            draft_root=draft_root,
            files=files,
            target_root_id=target_root_id,
            validation_token=validation_token,
        )

    def apply(
        self,
        change_manifest: Dict[str, Any],
        change_token: Dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return apply_module.apply(
            self,
            change_manifest,
            change_token,
            idempotency_key=idempotency_key,
        )

    def _action_path(self, key: str) -> Path:
        return rollback_module._action_path(self, key)

    def _action_lock_path(self, key: str) -> Path:
        return rollback_module._action_lock_path(self, key)

    def _locked_action_key(self, key: str):
        return rollback_module._locked_action_key(self, key)

    def _transaction_directory(self, change_id: str) -> Path:
        return rollback_module._transaction_directory(self, change_id)

    def _target_root_lock_path(self, target_root_id: str) -> Path:
        return rollback_module._target_root_lock_path(self, target_root_id)

    def _locked_target_root(self, target_root_id: str):
        return rollback_module._locked_target_root(self, target_root_id)

    @staticmethod
    def _replace_target(
        index: int,
        target: Path,
        staged: Path,
        create_only: bool,
    ) -> None:
        return rollback_module._replace_target(index, target, staged, create_only)

    @staticmethod
    def _write_transaction(path: Path, transaction: Dict[str, Any]) -> None:
        return rollback_module._write_transaction(path, transaction)

    def _rollback_transaction(
        self,
        transaction_path: Path,
        transaction: Dict[str, Any],
        resolved_by_path: Dict[str, Path],
    ) -> None:
        return rollback_module._rollback_transaction(
            self, transaction_path, transaction, resolved_by_path
        )

    def _prepare_transaction(
        self,
        change_manifest: Dict[str, Any],
        effect_id: str,
        resolved: List[tuple],
    ) -> tuple:
        return rollback_module._prepare_transaction(
            self, change_manifest, effect_id, resolved
        )

    def _apply_transaction(
        self,
        transaction_path: Path,
        transaction: Dict[str, Any],
        resolved: List[tuple],
    ) -> None:
        return rollback_module._apply_transaction(
            self, transaction_path, transaction, resolved
        )

    @staticmethod
    def _expected_applied_artifact(
        change_manifest: Dict[str, Any],
        change_token: Dict[str, Any],
    ) -> Dict[str, Any]:
        return apply_module._expected_applied_artifact(change_manifest, change_token)

    def _signed_action_record(
        self,
        request_hash: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        return apply_module._signed_action_record(self, request_hash, result)

    def _load_action_record(self, path: Path) -> Dict[str, Any]:
        return apply_module._load_action_record(self, path)

    def _validate_cached_result(
        self,
        recorded: Dict[str, Any],
        change_manifest: Dict[str, Any],
        change_token: Dict[str, Any],
    ) -> Dict[str, Any]:
        return apply_module._validate_cached_result(
            self, recorded, change_manifest, change_token
        )

    def _ensure_applied_session(
        self,
        result: Dict[str, Any],
        change_token: Dict[str, Any],
        *,
        idempotency_key: str,
    ) -> None:
        return apply_module._ensure_applied_session(
            self, result, change_token, idempotency_key=idempotency_key
        )

    def _apply_locked(
        self,
        change_manifest: Dict[str, Any],
        prepared_record: Dict[str, Any],
        change_token: Dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return apply_module._apply_locked(
            self,
            change_manifest,
            prepared_record,
            change_token,
            idempotency_key=idempotency_key,
        )
