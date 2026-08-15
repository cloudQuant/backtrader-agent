"""Read-only Backtrader engine-root inspection and hash binding."""

import os
import platform
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict

from .backtrader_runtime import inspect_backtrader_engine_root
from .canonical import hash_object, sha256_bytes
from .errors import AgentError
from .roots import RootRegistry

VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _regular_member(path: Path, *, required: bool = False) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        if required:
            raise AgentError(
                "BTAG-ENGINE-LAYOUT",
                "engine root must contain backtrader/__init__.py and backtrader/version.py",
            ) from exc
        raise AgentError("BTAG-ENGINE-MEMBER", "engine package member could not be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise AgentError("BTAG-ENGINE-SYMLINK", "engine package cannot contain symbolic links")
    if not stat.S_ISREG(metadata.st_mode):
        raise AgentError("BTAG-ENGINE-TYPE", "engine package members must be regular files")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AgentError("BTAG-ENGINE-MEMBER", "engine package member could not be read") from exc


def _package_tree(package: Path) -> Dict[str, str]:
    try:
        package_metadata = package.lstat()
    except OSError as exc:
        raise AgentError(
            "BTAG-ENGINE-LAYOUT",
            "engine root must contain backtrader/__init__.py and backtrader/version.py",
        ) from exc
    if stat.S_ISLNK(package_metadata.st_mode):
        raise AgentError("BTAG-ENGINE-SYMLINK", "engine package cannot be a symbolic link")
    if not stat.S_ISDIR(package_metadata.st_mode):
        raise AgentError("BTAG-ENGINE-LAYOUT", "engine root must contain a backtrader package")

    package_root = package.resolve(strict=True)
    files: Dict[str, str] = {}
    for current, directories, names in os.walk(str(package), topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directories):
            child = current_path / name
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise AgentError(
                    "BTAG-ENGINE-MEMBER", "engine package member could not be inspected"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise AgentError(
                    "BTAG-ENGINE-SYMLINK", "engine package cannot contain symbolic links"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise AgentError("BTAG-ENGINE-TYPE", "engine package member must be a directory")
            if name != "__pycache__":
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(names):
            child = current_path / name
            contents = _regular_member(child)
            if name.endswith(".pyc"):
                continue
            try:
                child.resolve(strict=True).relative_to(package_root)
            except (OSError, ValueError) as exc:
                raise AgentError(
                    "BTAG-ENGINE-PATH", "engine package member escapes the registered package"
                ) from exc
            files[child.relative_to(package).as_posix()] = sha256_bytes(contents)
    return files


def inspect_engine(roots: RootRegistry, root_id: str) -> Dict[str, Any]:
    record = roots.get_record(root_id)
    if record.get("kind") != "engine" or record.get("writable"):
        raise AgentError(
            "BTAG-ENGINE-ROOT",
            "engine root must be registered read-only with kind 'engine'",
        )
    try:
        root = Path(record["path"]).resolve(strict=True)
    except OSError as exc:
        raise AgentError("BTAG-ENGINE-ROOT", "registered engine root is unavailable") from exc
    package = root / "backtrader"
    initializer = package / "__init__.py"
    version_file = package / "version.py"
    package_files = _package_tree(package)
    initializer_bytes = _regular_member(initializer, required=True)
    version_bytes = _regular_member(version_file, required=True)
    try:
        version_source = version_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentError("BTAG-ENGINE-VERSION", "engine version file must be UTF-8 text") from exc
    match = VERSION_RE.search(version_source)
    descriptor: Dict[str, Any] = {
        "schema_version": "engine-runtime-v2",
        "root_id": root_id,
        "package": "backtrader",
        "version": match.group(1) if match else "unknown",
        "initializer_sha256": sha256_bytes(initializer_bytes),
        "version_file_sha256": sha256_bytes(version_bytes),
        "package_tree_sha256": hash_object(package_files),
        "package_file_count": len(package_files),
        "source": inspect_backtrader_engine_root(root),
    }
    descriptor["engine_hash"] = hash_object(descriptor)
    return descriptor


def inspect_execution_environment() -> Dict[str, Any]:
    descriptor: Dict[str, Any] = {
        "schema_version": "execution-environment-v1",
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    descriptor["environment_hash"] = hash_object(descriptor)
    return descriptor
