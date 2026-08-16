"""Extract the packaged indicator registry (R25) from a Backtrader source tree.

Offline, read-only, AST-static scan of ``backtrader/indicators/*.py`` (core and
``contrib``) that records pure metadata per indicator class: module name, class
name, and the field names of the class-local ``params`` tuple. The fork source
is **never imported or executed**, and the scan never writes inside the source
root. Every entry carries ``source_available: false``, the same metadata-only
discipline as the packaged corpus snapshot.

Source root resolution order:

1. ``--root`` (explicit path, must contain ``backtrader/indicators/``);
2. ``BACKTRADER_AGENT_INDICATOR_ROOT`` (same layout as ``--root``);
3. the first registered engine root (``roots.json`` records with
   ``kind == "engine"``, ordered by root ID) that contains the layout;
4. otherwise the script **skips with an explanation** and exits 0, leaving any
   existing registry asset untouched.

Usage::

    python scripts/extract_indicator_registry.py \
        --root /path/to/cloudquant-backtrader \
        --output src/backtrader_agent/resources/catalog/indicator-registry-v1.json

Exit codes: 0 success or skip-with-explanation, 2 invalid root or source error.
"""

import argparse
import ast
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "backtrader_agent"
    / "resources"
    / "catalog"
    / "indicator-registry-v1.json"
)
SCHEMA_VERSION = "indicator-registry-v1"
ENTRY_SCHEMA_VERSION = "indicator-entry-v1"
ENV_ROOT = "BACKTRADER_AGENT_INDICATOR_ROOT"


def _indicators_dir(root: Path) -> Optional[Path]:
    """Return the ``backtrader/indicators`` directory of a source root, if any."""

    candidate = root / "backtrader" / "indicators"
    if candidate.is_dir() and any(candidate.glob("*.py")):
        return candidate
    return None


def _registered_engine_roots(state_root: Path) -> List[Path]:
    """Read registered engine roots from the opaque root registry, if present."""

    registry_path = state_root / "roots.json"
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        records = payload.get("roots", {})
    except (OSError, ValueError):
        return []
    roots: List[Tuple[str, Path]] = []
    for root_id, record in records.items():
        if not isinstance(record, dict) or record.get("kind") != "engine":
            continue
        path = record.get("path")
        if isinstance(path, str):
            roots.append((root_id, Path(path)))
    return [path for _, path in sorted(roots)]


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _resolve_root(args: argparse.Namespace) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve the source root.

    Returns ``(root, None)`` when a root is found, ``(None, explanation)`` when
    no root is available and the run should skip, or ``(None, None)`` when an
    explicit ``--root``/environment root is invalid and the run must fail.
    """

    explicit = Path(args.root) if args.root else None
    if explicit is not None and _indicators_dir(explicit) is None:
        return None, None  # explicit invalid roots fail loudly, not skip
    if explicit is not None:
        return explicit, None

    environment = os.environ.get(ENV_ROOT)
    if environment:
        candidate = Path(environment)
        if _indicators_dir(candidate) is None:
            return None, None  # explicit environment roots fail loudly, not skip
        return candidate, None

    state_root = Path(args.state_root)
    for root in _registered_engine_roots(state_root):
        if _indicators_dir(root) is not None:
            return root, None

    explanation = (
        "skip: no Backtrader indicator source root is available; set "
        f"{ENV_ROOT} to a cloudQuant backtrader checkout or register an engine "
        "root (backtrader-agent roots register --kind engine) and retry. The "
        "existing registry asset was left unchanged."
    )
    return None, explanation


def _param_names(node: ast.ClassDef) -> List[str]:
    """Extract field names from a class-local ``params`` tuple or list.

    Only the static ``params = (("name", default), ...)`` form is recognized;
    dynamic expressions and dict forms yield no names (AST-only, never
    evaluated).
    """

    for statement in node.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "params"
            for target in statement.targets
        ):
            continue
        if not isinstance(statement.value, (ast.Tuple, ast.List)):
            return []
        names: List[str] = []
        for element in statement.value.elts:
            if (
                isinstance(element, (ast.Tuple, ast.List))
                and element.elts
                and isinstance(element.elts[0], ast.Constant)
                and isinstance(element.elts[0].value, str)
            ):
                names.append(element.elts[0].value)
        return names
    return []


def _scan_module(path: Path, module: str) -> List[Dict[str, Any]]:
    """AST-scan one indicator module without importing or executing it."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        _fail(f"error: {path} could not be parsed statically: {exc}")
    entries: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.bases:
            continue
        entries.append(
            {
                "schema_version": ENTRY_SCHEMA_VERSION,
                "entry_id": f"{module}:{node.name}",
                "module": module,
                "class_name": node.name,
                "param_names": _param_names(node),
                "source_available": False,
            }
        )
    return entries


def _extract(root: Path) -> Dict[str, Any]:
    indicators = _indicators_dir(root)
    if indicators is None:
        _fail(f"error: {root} does not contain backtrader/indicators/*.py")
    contrib = indicators / "contrib"
    areas: List[Tuple[str, Path, str]] = [
        ("core", indicators, "backtrader.indicators"),
    ]
    if contrib.is_dir():
        areas.append(("contrib", contrib, "backtrader.indicators.contrib"))

    entries: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {"core_modules": 0, "contrib_modules": 0}
    for area, directory, module_prefix in areas:
        for path in sorted(directory.glob("*.py")):
            if path.name == "__init__.py":
                continue
            counts[f"{area}_modules"] += 1
            entries.extend(_scan_module(path, f"{module_prefix}.{path.stem}"))

    return {
        "schema_version": SCHEMA_VERSION,
        "registry_id": "backtrader-indicator-registry-v1",
        "mode": "snapshot",
        "provenance": {
            "extraction": "ast-static-v1",
            "scan": "read-only",
        },
        "counts": {
            **counts,
            "indicators": len(entries),
        },
        "indicators": entries,
    }


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        delete=False,
        encoding="utf-8",
    )
    try:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
        handle.close()
        os.replace(handle.name, str(path))
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract the packaged indicator registry from Backtrader source."
    )
    parser.add_argument(
        "--root",
        help="Backtrader source root containing backtrader/indicators/",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"registry JSON output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--state-root",
        default=".backtrader-agent",
        help="state root consulted for registered engine roots",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root, skip_explanation = _resolve_root(args)
    if skip_explanation is not None:
        print(skip_explanation)
        return 0
    if root is None:
        requested = args.root or os.environ.get(ENV_ROOT) or ""
        _fail(
            f"error: indicator root {requested!r} does not contain "
            "backtrader/indicators/*.py"
        )
    registry = _extract(root)
    _atomic_write_json(Path(args.output), registry)
    print(
        f"registry: {registry['counts']} -> {Path(args.output).resolve()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
