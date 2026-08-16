"""Cross-session memory store for dataset notes and parameter priors (R22).

``MemoryStore`` persists two lightweight JSON stores under
``<state>/memory/``:

- ``datasets.json`` — dataset_id -> {registered_at, last_used_at, note}
  (the host's per-dataset reuse notes);
- ``params.json`` — archetype -> ranked list of the top-5 sweep-derived
  parameter priors.

Both stores are session-independent (deliberately NOT bound to a session
artifact record): they exist to carry knowledge across sessions. Every write
is atomic under a stable exclusive file lock, and every store file carries a
``schema_version`` plus a ``hash`` binding over the rest of its content;
a tampered or corrupt store is rejected on load with ``AgentError`` instead
of being trusted.
"""

import copy
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .canonical import atomic_write_json, hash_object, read_json
from .errors import AgentError
from .locking import exclusive_file_lock

DATASETS_SCHEMA_VERSION = "memory-datasets-v1"
PARAMS_SCHEMA_VERSION = "memory-params-v1"
MAX_PRIORS_PER_ARCHETYPE = 5
META_KEYS = {"schema_version", "hash"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


class MemoryStore:
    """Schema-bound, hash-sealed cross-session memory under the state root."""

    def __init__(self, state: Path) -> None:
        self.state_root = Path(state)
        self.memory_dir = self.state_root / "memory"
        self.datasets_path = self.memory_dir / "datasets.json"
        self.params_path = self.memory_dir / "params.json"

    def _lock_path(self) -> Path:
        return self.memory_dir / "memory-store.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with exclusive_file_lock(
            self._lock_path(),
            error_code="BTAG-MEMORY-LOCK",
            subject="memory store",
        ):
            yield

    def _load_store(self, path: Path, schema_version: str) -> Dict[str, Any]:
        """Read, schema-check, and hash-verify one memory store file.

        A missing file is an empty store; anything else that fails any check
        is rejected with a stable ``BTAG-MEMORY-*`` diagnostic instead of
        being trusted or silently reset.
        """

        if not path.exists():
            return {}
        if path.is_symlink() or not path.is_file():
            raise AgentError("BTAG-MEMORY-CORRUPT", "memory store path is unsafe")
        try:
            value = read_json(path)
        except AgentError as exc:
            raise AgentError(
                "BTAG-MEMORY-CORRUPT", "memory store is unreadable or not a JSON object"
            ) from exc
        if value.get("schema_version") != schema_version:
            raise AgentError("BTAG-MEMORY-SCHEMA", "memory store schema is invalid")
        expected = value.get("hash")
        if not isinstance(expected, str):
            raise AgentError("BTAG-MEMORY-SCHEMA", "memory store has no hash binding")
        portable = {key: item for key, item in value.items() if key != "hash"}
        if hash_object(portable) != expected:
            raise AgentError("BTAG-MEMORY-HASH", "memory store hash binding is invalid")
        return {key: item for key, item in portable.items() if key != "schema_version"}

    def _write_store(
        self, path: Path, schema_version: str, entries: Dict[str, Any]
    ) -> None:
        payload: Dict[str, Any] = {"schema_version": schema_version, **entries}
        payload["hash"] = hash_object(payload)
        atomic_write_json(path, payload)

    def _validate_dataset_record(self, dataset_id: str, record: Any) -> None:
        if (
            not isinstance(dataset_id, str)
            or not dataset_id
            or dataset_id in META_KEYS
            or not isinstance(record, dict)
            or not isinstance(record.get("note"), str)
        ):
            raise AgentError("BTAG-MEMORY-SCHEMA", "dataset memory record is invalid")
        for timestamp_key in ("registered_at", "last_used_at"):
            if timestamp_key in record and not isinstance(record[timestamp_key], str):
                raise AgentError(
                    "BTAG-MEMORY-SCHEMA", "dataset memory record is invalid"
                )

    def _validate_prior_record(self, archetype: str, priors: Any) -> None:
        if (
            not isinstance(archetype, str)
            or not archetype
            or archetype in META_KEYS
            or not isinstance(priors, list)
        ):
            raise AgentError("BTAG-MEMORY-SCHEMA", "params memory record is invalid")
        for prior in priors:
            if not isinstance(prior, dict):
                raise AgentError("BTAG-MEMORY-SCHEMA", "params memory prior is invalid")
            params = prior.get("params")
            if (
                not isinstance(params, dict)
                or not params
                or not _is_finite_number(prior.get("final_value"))
                or not isinstance(prior.get("recorded_at"), str)
                or (
                    prior.get("sweep_id") is not None
                    and not isinstance(prior.get("sweep_id"), str)
                )
                or (
                    prior.get("cell_id") is not None
                    and not isinstance(prior.get("cell_id"), str)
                )
            ):
                raise AgentError("BTAG-MEMORY-SCHEMA", "params memory prior is invalid")

    # -- dataset notes ---------------------------------------------------------

    def datasets(self) -> Dict[str, Any]:
        """Return every dataset memory record keyed by dataset_id."""

        entries = self._load_store(self.datasets_path, DATASETS_SCHEMA_VERSION)
        for dataset_id, record in entries.items():
            self._validate_dataset_record(dataset_id, record)
        return copy.deepcopy(entries)

    def note_dataset(self, dataset_id: str, note: str) -> None:
        """Record (or update) the host note for a dataset.

        The first note registers the record (``registered_at``); every note
        call bumps ``last_used_at``. The store file is rewritten atomically
        under the memory lock with a fresh hash binding.
        """

        if not isinstance(dataset_id, str) or not dataset_id or dataset_id in META_KEYS:
            raise AgentError(
                "BTAG-MEMORY-INPUT", "dataset ID must be a non-empty string"
            )
        if not isinstance(note, str) or not note:
            raise AgentError("BTAG-MEMORY-INPUT", "note must be a non-empty string")
        with self._locked():
            entries = self._load_store(self.datasets_path, DATASETS_SCHEMA_VERSION)
            for stored_id, record in entries.items():
                self._validate_dataset_record(stored_id, record)
            now = _utc_now()
            existing = entries.get(dataset_id)
            registered_at = (
                existing.get("registered_at") if isinstance(existing, dict) else None
            )
            entries[dataset_id] = {
                "registered_at": (
                    registered_at if isinstance(registered_at, str) else now
                ),
                "last_used_at": now,
                "note": note,
            }
            self._write_store(self.datasets_path, DATASETS_SCHEMA_VERSION, entries)

    # -- sweep parameter priors -------------------------------------------------

    def param_priors(self, archetype: str) -> List[Dict[str, Any]]:
        """Return the stored top-5 parameter priors for one archetype."""

        if not isinstance(archetype, str) or not archetype:
            raise AgentError(
                "BTAG-MEMORY-INPUT", "archetype must be a non-empty string"
            )
        entries = self._load_store(self.params_path, PARAMS_SCHEMA_VERSION)
        for stored_archetype, priors in entries.items():
            self._validate_prior_record(stored_archetype, priors)
        return copy.deepcopy(entries.get(archetype, []))

    def priors(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return every stored archetype's parameter priors."""

        entries = self._load_store(self.params_path, PARAMS_SCHEMA_VERSION)
        for archetype, priors in entries.items():
            self._validate_prior_record(archetype, priors)
        return copy.deepcopy(entries)

    def record_priors(self, archetype: str, cells: List[Dict[str, Any]]) -> None:
        """Merge ranked sweep cells into the archetype's top-5 priors.

        Each cell must carry a non-empty ``params`` object and a finite
        ``final_value`` (plus optional ``sweep_id``/``cell_id`` provenance).
        Cells merge with any stored priors, deduplicating by params content
        (the higher ``final_value`` wins) and keeping the best 5 ranked by
        ``final_value`` descending.
        """

        if not isinstance(archetype, str) or not archetype or archetype in META_KEYS:
            raise AgentError(
                "BTAG-MEMORY-INPUT", "archetype must be a non-empty string"
            )
        if not isinstance(cells, list) or not cells:
            raise AgentError("BTAG-MEMORY-INPUT", "cells must be a non-empty list")
        now = _utc_now()
        new_priors: List[Dict[str, Any]] = []
        for cell in cells:
            if not isinstance(cell, dict):
                raise AgentError("BTAG-MEMORY-PRIOR", "prior cell must be an object")
            params = cell.get("params")
            final_value = cell.get("final_value")
            if not isinstance(params, dict) or not params:
                raise AgentError(
                    "BTAG-MEMORY-PRIOR",
                    "prior cell must carry a non-empty params object",
                )
            if not _is_finite_number(final_value):
                raise AgentError(
                    "BTAG-MEMORY-PRIOR",
                    "prior cell final_value must be a finite number",
                )
            sweep_id = cell.get("sweep_id")
            cell_id = cell.get("cell_id")
            if sweep_id is not None and not isinstance(sweep_id, str):
                raise AgentError("BTAG-MEMORY-PRIOR", "prior cell sweep_id is invalid")
            if cell_id is not None and not isinstance(cell_id, str):
                raise AgentError("BTAG-MEMORY-PRIOR", "prior cell cell_id is invalid")
            new_priors.append(
                {
                    "sweep_id": sweep_id,
                    "cell_id": cell_id,
                    "params": params,
                    "final_value": final_value,
                    "recorded_at": now,
                }
            )
        with self._locked():
            entries = self._load_store(self.params_path, PARAMS_SCHEMA_VERSION)
            for stored_archetype, priors in entries.items():
                self._validate_prior_record(stored_archetype, priors)
            by_params: Dict[str, Dict[str, Any]] = {}
            for prior in entries.get(archetype, []):
                key = hash_object({"params": prior["params"]})
                by_params[key] = prior
            for prior in new_priors:
                key = hash_object({"params": prior["params"]})
                current = by_params.get(key)
                if current is None or prior["final_value"] > current["final_value"]:
                    by_params[key] = prior
            ranked = sorted(
                by_params.values(),
                key=lambda prior: (
                    -prior["final_value"],
                    hash_object({"params": prior["params"]}),
                ),
            )
            entries[archetype] = ranked[:MAX_PRIORS_PER_ARCHETYPE]
            self._write_store(self.params_path, PARAMS_SCHEMA_VERSION, entries)
