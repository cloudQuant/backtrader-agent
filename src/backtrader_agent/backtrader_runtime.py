"""CloudQuant Backtrader discovery, provenance checks, and explicit bootstrap."""

import importlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from .errors import AgentError


CLOUDQUANT_BACKTRADER_REPOSITORY = "https://github.com/cloudQuant/backtrader"
CLOUDQUANT_BACKTRADER_REQUIREMENT = (
    "backtrader @ git+https://github.com/cloudQuant/backtrader.git"
)
INSTALL_TIMEOUT_SECONDS = 300


def _canonical_repository(value: Optional[str]) -> Optional[str]:
    """Return a stable HTTPS repository identifier for a GitHub VCS URL."""

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.startswith("git+"):
        raw = raw[4:]
    if raw.startswith("git@"):
        raw = "ssh://" + raw.replace(":", "/", 1)
    parsed = urlparse(raw)
    if parsed.scheme == "file" or not parsed.hostname:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[0], parts[1]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        return None
    return "https://{}/{}/{}".format(parsed.hostname.lower(), owner.lower(), repository.lower())


def _git_remote(start: Path) -> Optional[str]:
    """Read an ancestor Git origin without following arbitrary user arguments."""

    try:
        resolved = Path(start).resolve()
    except OSError:
        return None
    for candidate in (resolved, *resolved.parents):
        marker = candidate / ".git"
        if not marker.is_dir() and not marker.is_file():
            continue
        try:
            completed = subprocess.run(
                ["git", "-C", str(candidate), "config", "--get", "remote.origin.url"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        output = completed.stdout
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        value = str(output).strip()
        return value or None
    return None


def _direct_url_evidence(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Derive a normalized repository and evidence type from pip direct-url data."""

    if not isinstance(value, str) or not value:
        return None, None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        return None, None
    source = payload["url"]
    repository = _canonical_repository(source)
    if repository:
        return repository, "direct_url"
    parsed = urlparse(source)
    if parsed.scheme != "file":
        return None, None
    local_path = Path(unquote(parsed.path))
    remote = _git_remote(local_path)
    repository = _canonical_repository(remote)
    return repository, "git_remote" if repository else None


def _distribution_details() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return distribution version, direct-url metadata, and home page if available."""

    try:
        distribution = importlib.metadata.distribution("backtrader")
    except importlib.metadata.PackageNotFoundError:
        return None, None, None
    direct_url = distribution.read_text("direct_url.json")
    metadata = distribution.metadata
    home_page = metadata.get("Home-page") if metadata else None
    return distribution.version, direct_url, home_page


def _warning_for_repository(repository: Optional[str], *, subject: str = "Installed Backtrader") -> str:
    if repository:
        evidence = "detected repository '{}'".format(repository)
    else:
        evidence = "no direct VCS or Git-origin evidence was available"
    return (
        "{} is not verified as cloudQuant/backtrader ({}). "
        "It was not replaced automatically; run 'backtrader-agent backtrader ensure' "
        "after removing or correcting the existing installation."
    ).format(subject, evidence)


def inspect_backtrader_runtime() -> Dict[str, Any]:
    """Inspect the current interpreter's Backtrader package without changing it."""

    status: Dict[str, Any] = {
        "schema_version": "backtrader-runtime-v1",
        "package": "backtrader",
        "required_repository": CLOUDQUANT_BACKTRADER_REPOSITORY,
        "required_requirement": CLOUDQUANT_BACKTRADER_REQUIREMENT,
        "installed": False,
        "installed_during_check": False,
        "status": "missing",
        "version": None,
        "import_path": None,
        "direct_url": None,
        "repository": None,
        "source_evidence": None,
        "metadata_home_page": None,
        "is_cloudquant_backtrader": False,
        "warning": None,
    }
    spec = importlib.util.find_spec("backtrader")
    if spec is None or not spec.origin:
        return status

    status["installed"] = True
    try:
        status["import_path"] = str(Path(spec.origin).resolve())
    except OSError:
        status["import_path"] = str(spec.origin)
    version, direct_url, home_page = _distribution_details()
    status["version"] = version
    status["direct_url"] = direct_url
    status["metadata_home_page"] = home_page
    repository, evidence = _direct_url_evidence(direct_url)
    if repository is None:
        repository = _canonical_repository(_git_remote(Path(status["import_path"])))
        evidence = "git_remote" if repository else None
    status["repository"] = repository
    status["source_evidence"] = evidence
    expected = _canonical_repository(CLOUDQUANT_BACKTRADER_REPOSITORY)
    if repository == expected:
        status["status"] = "verified"
        status["is_cloudquant_backtrader"] = True
        status["repository"] = CLOUDQUANT_BACKTRADER_REPOSITORY
        return status
    status["status"] = "warning"
    status["warning"] = _warning_for_repository(repository)
    return status


def inspect_backtrader_engine_root(root: Path) -> Dict[str, Any]:
    """Inspect source provenance for a registered Backtrader engine root."""

    remote = _git_remote(Path(root))
    repository = _canonical_repository(remote)
    evidence = "git_remote" if repository else None
    if repository is None:
        runtime = inspect_backtrader_runtime()
        import_path = runtime.get("import_path")
        try:
            installed_root = Path(str(import_path)).resolve().parent.parent
            requested_root = Path(root).resolve()
        except (OSError, TypeError, ValueError):
            installed_root = None
            requested_root = None
        if installed_root is not None and installed_root == requested_root:
            repository = _canonical_repository(runtime.get("repository"))
            evidence = runtime.get("source_evidence")
    expected = _canonical_repository(CLOUDQUANT_BACKTRADER_REPOSITORY)
    verified = repository == expected
    status: Dict[str, Any] = {
        "schema_version": "backtrader-engine-source-v1",
        "required_repository": CLOUDQUANT_BACKTRADER_REPOSITORY,
        "repository": CLOUDQUANT_BACKTRADER_REPOSITORY if verified else repository,
        "source_evidence": evidence,
        "is_cloudquant_backtrader": verified,
        "status": "verified" if verified else "warning",
        "warning": None,
    }
    if not verified:
        status["warning"] = _warning_for_repository(
            repository,
            subject="Registered Backtrader engine",
        )
    return status


def ensure_cloudquant_backtrader() -> Dict[str, Any]:
    """Install CloudQuant Backtrader only when the current interpreter lacks it."""

    before = inspect_backtrader_runtime()
    if before["installed"]:
        return before
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        CLOUDQUANT_BACKTRADER_REQUIREMENT,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentError(
            "BTAG-BACKTRADER-INSTALL",
            "CloudQuant Backtrader could not be installed in the current Python environment",
            hint="Check network access and run the documented backtrader ensure command again.",
        ) from exc
    if completed.returncode != 0:
        raise AgentError(
            "BTAG-BACKTRADER-INSTALL",
            "CloudQuant Backtrader installation failed",
            hint="Check network access and pip configuration before retrying.",
            details={"returncode": completed.returncode},
        )
    importlib.invalidate_caches()
    after = inspect_backtrader_runtime()
    if not after["installed"]:
        raise AgentError(
            "BTAG-BACKTRADER-INSTALL",
            "CloudQuant Backtrader installation did not make the package importable",
            hint="Restart the Python environment and run the documented ensure command again.",
        )
    after["installed_during_check"] = True
    return after
