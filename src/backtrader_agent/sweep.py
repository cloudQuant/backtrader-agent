"""Immutable SweepPlan preparation from a bounded parameter grid (R15).

``prepare_sweep`` enumerates the cartesian product of an approved spec's
numeric parameter grid, checks every value against the spec-declared
``minimum``/``maximum`` bounds, and persists a content-addressed, hash-sealed
SweepPlan at ``<state>/sweeps/sweep_<64hex>/sweep-plan.json``. The plan binds
the spec, dataset, engine root, and execution environment hashes alongside
the per-cell parameter values. ``load_plan`` re-verifies the embedded
``plan_hash`` and every per-cell ``cell_hash`` and rejects any tampering with
``BTAG-SWEEP-PLAN``.

Cell rendering/execution (R17) is deliberately not part of this module: this
task only produces the immutable plan records.
"""

import itertools
import math
import re
from pathlib import Path
from typing import Any, Dict, List

from .canonical import create_or_verify_json, hash_object, read_json
from .contracts import DatasetManifest, StrategySpec
from .engines import inspect_engine, inspect_execution_environment
from .errors import AgentError
from .roots import RootRegistry
from .sessions import SessionStore

SCHEMA_VERSION = "sweep-plan-v1"
SWEEP_ID_RE = re.compile(r"^sweep_[0-9a-f]{64}$")
CELL_ID_RE = re.compile(r"^cell_[0-9a-f]{16}$")


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _parameter_descriptors(spec: StrategySpec) -> Dict[str, Dict[str, Any]]:
    return {item["name"]: item for item in spec.value["parameters"]}


def _validate_grid(spec: StrategySpec, param_grid: Dict[str, Any]) -> None:
    """Fail closed on any grid shape, key, type, or bounds violation."""

    if not isinstance(param_grid, dict) or not param_grid:
        raise AgentError("BTAG-SWEEP-GRID", "param grid must be a non-empty object")
    declared = _parameter_descriptors(spec)
    for name, values in param_grid.items():
        descriptor = declared.get(name)
        if descriptor is None:
            raise AgentError(
                "BTAG-SWEEP-PARAM",
                "param grid key is not a declared spec parameter",
                details={"parameter": name},
            )
        if descriptor.get("type") not in {"int", "float"}:
            raise AgentError(
                "BTAG-SWEEP-PARAM",
                "only numeric parameters may be swept",
                details={"parameter": name, "type": descriptor.get("type")},
            )
        if "minimum" not in descriptor or "maximum" not in descriptor:
            raise AgentError(
                "BTAG-SWEEP-BOUNDS",
                "swept parameter declares no minimum/maximum bounds",
                details={"parameter": name},
            )
        if not isinstance(values, list) or not values:
            raise AgentError(
                "BTAG-SWEEP-GRID",
                "param grid values must be a non-empty list",
                details={"parameter": name},
            )
        for value in values:
            if not _is_finite_number(value):
                raise AgentError(
                    "BTAG-SWEEP-GRID",
                    "param grid values must be finite numbers",
                    details={"parameter": name, "value": value},
                )
            if descriptor["type"] == "int" and not isinstance(value, int):
                raise AgentError(
                    "BTAG-SWEEP-GRID",
                    "integer parameter grid values must be integers",
                    details={"parameter": name, "value": value},
                )
            if not (descriptor["minimum"] <= value <= descriptor["maximum"]):
                raise AgentError(
                    "BTAG-SWEEP-BOUNDS",
                    "param grid value falls outside the spec bounds",
                    details={
                        "parameter": name,
                        "value": value,
                        "minimum": descriptor["minimum"],
                        "maximum": descriptor["maximum"],
                    },
                )
        if len(set(values)) != len(values):
            raise AgentError(
                "BTAG-SWEEP-GRID",
                "param grid values must be unique so every cell is distinct",
                details={"parameter": name},
            )


def _expand_cells(spec_hash: str, param_grid: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cartesian expansion in sorted parameter-name order with deterministic hashes."""

    names = sorted(param_grid)
    cells: List[Dict[str, Any]] = []
    for combo in itertools.product(*(param_grid[name] for name in names)):
        params = {name: value for name, value in zip(names, combo)}
        cell_hash = hash_object({"spec_hash": spec_hash, "params": params})
        cells.append(
            {
                "cell_id": "cell_" + cell_hash[:16],
                "params": params,
                "cell_hash": cell_hash,
            }
        )
    return cells


def _dataset_manifest_hash(spec: StrategySpec, dataset_manifest: Dict[str, Any]) -> str:
    """Validate the manifest contract and return its canonical content hash."""

    if not isinstance(dataset_manifest, dict):
        raise AgentError("BTAG-SWEEP-DATASET", "dataset manifest must be a JSON object")
    DatasetManifest.from_dict(dataset_manifest)
    if dataset_manifest.get("dataset_id") != spec.value["dataset_id"]:
        raise AgentError(
            "BTAG-SWEEP-DATASET",
            "dataset manifest does not match the spec dataset_id",
        )
    manifest_hash = hash_object(
        {
            key: value
            for key, value in dataset_manifest.items()
            if key != "manifest_hash"
        }
    )
    stored = dataset_manifest.get("manifest_hash")
    if stored is not None and stored != manifest_hash:
        raise AgentError("BTAG-SWEEP-DATASET", "dataset manifest hash is invalid")
    return manifest_hash


def _derive_sweep_id(payload: Dict[str, Any]) -> str:
    return "sweep_" + hash_object(payload)


def _build_plan(
    session_id: str,
    spec_hash: str,
    dataset_manifest_hash: str,
    cells: List[Dict[str, Any]],
    *,
    engine_hash: str,
    engine_root_id: str,
    environment_hash: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "spec_hash": spec_hash,
        "dataset_manifest_hash": dataset_manifest_hash,
        "engine_hash": engine_hash,
        "engine_root_id": engine_root_id,
        "environment_hash": environment_hash,
        "cells": cells,
    }
    sweep_id = _derive_sweep_id(payload)
    plan = dict(payload)
    plan["sweep_id"] = sweep_id
    plan["plan_hash"] = hash_object(plan)
    return plan


def _persist_plan(state_root: Path, plan: Dict[str, Any]) -> Path:
    path = Path(state_root) / "sweeps" / plan["sweep_id"] / "sweep-plan.json"
    create_or_verify_json(
        path,
        plan,
        conflict_code="BTAG-SWEEP-CONFLICT",
        conflict_message="sweep plan conflicts with existing immutable bytes",
    )
    return path


def _verify_session_bindings(
    session: Dict[str, Any], spec_hash: str, manifest_hash: str
) -> None:
    artifacts = session.get("artifacts", {})
    if artifacts.get("approved_spec_hash") != spec_hash:
        raise AgentError(
            "BTAG-SWEEP-SESSION",
            "session approved spec does not match the sweep spec",
        )
    if artifacts.get("dataset_manifest_hash") != manifest_hash:
        raise AgentError(
            "BTAG-SWEEP-DATASET",
            "session dataset does not match the sweep dataset manifest",
        )


def prepare_sweep(
    state: Path,
    session_id: str,
    spec: StrategySpec,
    dataset_manifest: Dict[str, Any],
    param_grid: Dict[str, List[float]],
    *,
    engine_root_id: str,
) -> Dict[str, Any]:
    """Enumerate a bounded parameter grid into a persisted immutable SweepPlan.

    The session must hold the same approved spec (state ``SPEC_APPROVED``)
    and the same registered dataset manifest. The engine root is resolved the
    same way the ``validate`` command does it, and its hash plus the execution
    environment hash are sealed into the plan. The plan is content-addressed:
    re-preparing identical inputs replays the stored plan without journaling
    another event.
    """

    if not isinstance(spec, StrategySpec):
        raise AgentError(
            "BTAG-SWEEP-SPEC", "spec must be parsed via StrategySpec.from_dict"
        )
    state_root = Path(state)
    _validate_grid(spec, param_grid)
    cells = _expand_cells(spec.spec_hash, param_grid)
    manifest_hash = _dataset_manifest_hash(spec, dataset_manifest)
    engine = inspect_engine(RootRegistry(state_root), engine_root_id)
    environment = inspect_execution_environment()
    plan = _build_plan(
        session_id,
        spec.spec_hash,
        manifest_hash,
        cells,
        engine_hash=engine["engine_hash"],
        engine_root_id=engine["root_id"],
        environment_hash=environment["environment_hash"],
    )
    sweep_id = plan["sweep_id"]

    sessions = SessionStore(state_root)
    session = sessions.load(session_id)
    state_name = session.get("state")
    if state_name == "SWEEP_PREPARED":
        artifacts = session.get("artifacts", {})
        if (
            artifacts.get("sweep_plan_hash") == plan["plan_hash"]
            and artifacts.get("sweep_id") == sweep_id
        ):
            return load_plan(state_root, sweep_id)
        raise AgentError(
            "BTAG-SWEEP-SESSION",
            "session already holds a different sweep plan",
        )
    if state_name != "SPEC_APPROVED":
        raise AgentError(
            "BTAG-SWEEP-SESSION",
            "session is not ready for sweep preparation",
            details={"state": state_name},
        )
    _verify_session_bindings(session, spec.spec_hash, manifest_hash)
    _persist_plan(state_root, plan)
    sessions.transition(
        session_id,
        "SWEEP_PREPARED",
        "sweep",
        {
            "spec": spec.spec_hash,
            "dataset": manifest_hash,
            "param_grid": hash_object(param_grid),
        },
        effect_references={
            "sweep_id": sweep_id,
            "sweep_plan_hash": plan["plan_hash"],
        },
    )
    return plan


def load_plan(state: Path, sweep_id: str) -> Dict[str, Any]:
    """Load a SweepPlan and reject any tampering with ``BTAG-SWEEP-PLAN``."""

    if not SWEEP_ID_RE.fullmatch(sweep_id):
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan ID is malformed")
    path = Path(state) / "sweeps" / sweep_id / "sweep-plan.json"
    if path.is_symlink() or not path.is_file():
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan does not exist")
    try:
        plan = read_json(path)
    except AgentError as exc:
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan file is corrupt") from exc
    expected = plan.get("plan_hash")
    actual = hash_object(
        {key: value for key, value in plan.items() if key != "plan_hash"}
    )
    if expected != actual:
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan hash is invalid")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan schema is invalid")
    if plan.get("sweep_id") != sweep_id:
        raise AgentError(
            "BTAG-SWEEP-PLAN", "sweep plan ID does not match its storage path"
        )
    payload = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_hash", "sweep_id"}
    }
    if _derive_sweep_id(payload) != sweep_id:
        raise AgentError(
            "BTAG-SWEEP-PLAN", "sweep plan ID is not derived from its content"
        )
    cells = plan.get("cells")
    if not isinstance(cells, list) or not cells:
        raise AgentError("BTAG-SWEEP-PLAN", "sweep plan cells are missing")
    spec_hash = plan.get("spec_hash")
    for cell in cells:
        if not isinstance(cell, dict):
            raise AgentError("BTAG-SWEEP-PLAN", "sweep plan cell is malformed")
        params = cell.get("params")
        cell_hash = cell.get("cell_hash")
        if not isinstance(params, dict):
            raise AgentError("BTAG-SWEEP-PLAN", "sweep plan cell is malformed")
        if (
            not isinstance(cell_hash, str)
            or not isinstance(cell.get("cell_id"), str)
            or not CELL_ID_RE.fullmatch(cell["cell_id"])
            or cell["cell_id"] != "cell_" + cell_hash[:16]
        ):
            raise AgentError("BTAG-SWEEP-PLAN", "sweep plan cell identity is invalid")
        if hash_object({"spec_hash": spec_hash, "params": params}) != cell_hash:
            raise AgentError("BTAG-SWEEP-PLAN", "sweep plan cell hash is invalid")
    return plan
