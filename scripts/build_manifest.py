"""Regenerate the root and package distribution manifests.

Both manifests are content-addressed file maps: every non-excluded file is
listed with its sha256, and each manifest excludes itself from its own listing.
Run this after any source, resource, or repository file change so the two
distribution manifests stay exact. The independence audit
(``scripts/audit_independence.py``) and the distribution contract test both
fail closed when a manifest drifts, so this script is the single source of
truth for keeping them in sync.

The script also refreshes the packaged action-schema snapshot
(``src/backtrader_agent/resources/actions-v1.json``) from the live CLI parser
before building either manifest, so the snapshot and its recorded hashes can
never drift apart. The snapshot is machine-independent (parameter defaults
contain no absolute paths), so regeneration is byte-stable on any checkout.

Usage::

    python scripts/build_manifest.py

Exit code 0 on success. Prints the file counts written to each manifest.
"""

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Set

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "backtrader_agent"
ROOT_MANIFEST = PROJECT_ROOT / "manifest.json"
PKG_MANIFEST = PACKAGE_ROOT / "resources" / "distribution-manifest.json"

# Exclusion rules must match the consumers exactly:
# - root manifest: tests/test_distribution_contracts.py::test_source_distribution_manifest_covers_every_file
# - package manifest: src/backtrader_agent/audit.py (distribution_manifest check)
ROOT_EXCLUDED_PARTS: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".git",
    ".superpowers",
    ".backtrader-agent",
}
PKG_EXCLUDED_PARTS: Set[str] = {"__pycache__"}
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_files(root: Path, excluded_parts: Set[str], skip: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if excluded_parts.intersection(path.parts):
            continue
        if any(part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix == ".pyc":
            continue
        if path == skip:
            continue
        yield path


def _version() -> str:
    match = VERSION_RE.search(
        (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    )
    return match.group(1) if match else "0.0.0"


def build_root_manifest() -> Dict[str, Any]:
    files = {
        path.relative_to(PROJECT_ROOT).as_posix(): _sha256(path)
        for path in _iter_files(PROJECT_ROOT, ROOT_EXCLUDED_PARTS, ROOT_MANIFEST)
    }
    return {
        "schema_version": "distribution-manifest-v1",
        "product": "backtrader-agent",
        "version": _version(),
        "compatibility": {
            "python": ">=3.8",
            "backtrader": "source fork or compatible installed distribution",
            "hosts": ["claude-code", "codex", "opencode", "openclaw"],
        },
        "hash_algorithm": "sha256",
        "manifest_excludes": ["manifest.json"],
        "file_count": len(files),
        "files": dict(sorted(files.items())),
    }


def build_package_manifest() -> Dict[str, Any]:
    files = {
        path.relative_to(PACKAGE_ROOT).as_posix(): _sha256(path)
        for path in _iter_files(PACKAGE_ROOT, PKG_EXCLUDED_PARTS, PKG_MANIFEST)
    }
    return {
        "schema_version": "distribution-manifest-v1",
        "product": "backtrader-agent",
        "version": _version(),
        "compatibility": {
            "python": ">=3.8",
            "backtrader": "source fork or compatible installed distribution",
        },
        "hash_algorithm": "sha256",
        "manifest_excludes": ["resources/distribution-manifest.json"],
        "files": dict(sorted(files.items())),
    }


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_actions_resource() -> Dict[str, Any]:
    """Refresh ``resources/actions-v1.json`` from the live CLI parser.

    The snapshot is the canonical serialization of ``build_action_schema``;
    regenerating it here keeps the packaged resource byte-identical to what
    ``backtrader-agent actions --json`` emits.
    """

    src_root = str(PACKAGE_ROOT.parent)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    from backtrader_agent.cli import (
        build_action_schema,
        build_parser,
    )  # generator-only import

    schema = build_action_schema(build_parser())
    path = PACKAGE_ROOT / "resources" / "actions-v1.json"
    path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return schema


def main() -> int:
    # Refresh the action schema snapshot first: the manifests below record its
    # sha256, so the snapshot must be final before either manifest is built.
    actions_schema = build_actions_resource()
    # Write the package manifest first: the root manifest records the package
    # manifest's sha256, so it must hash the final package manifest bytes.
    package = build_package_manifest()
    _write(PKG_MANIFEST, package)
    root = build_root_manifest()
    _write(ROOT_MANIFEST, root)
    print(
        f"action schema:     {len(actions_schema['actions'])} actions -> "
        f"{(PACKAGE_ROOT / 'resources' / 'actions-v1.json').relative_to(PROJECT_ROOT)}"
    )
    print(
        f"root manifest:     {root['file_count']} files -> {ROOT_MANIFEST.relative_to(PROJECT_ROOT)}"
    )
    print(
        f"package manifest:  {len(package['files'])} files -> "
        f"{PKG_MANIFEST.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
