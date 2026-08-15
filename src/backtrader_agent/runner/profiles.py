"""Allowlisted run profiles, engine probes, and child-process environment."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..caching import memoized
from ..errors import AgentError

PROFILE_DEPENDENCIES = {
    "python_bundle": ("backtrader", "pandas"),
    "single_test": ("backtrader", "pandas", "pytest"),
}


def missing_profile_dependencies(profile: str) -> List[str]:
    modules = PROFILE_DEPENDENCIES.get(profile)
    if modules is None:
        raise AgentError(
            "BTAG-RUN-PROFILE", "controlled run profile is not allowlisted"
        )
    return [module for module in modules if importlib.util.find_spec(module) is None]


ENGINE_PROBE = (
    "import json,pathlib,backtrader;"
    "print(json.dumps({'path':str(pathlib.Path(backtrader.__file__).resolve()),"
    "'version':getattr(backtrader,'__version__','unknown')},sort_keys=True))"
)


@memoized
def _probe_engine(
    root: Path, cwd: Path, expected_version: Optional[str]
) -> Tuple[str, str]:
    """Run the child-process engine import probe once per (root, cwd, version).

    The attestation is a security binding, so the memo is strictly
    process-local. Failures are raised and never cached, so a later retry
    probes again instead of replaying a stale failure.

    The key deliberately carries no stat freshness signal: any stat-visible
    change to the engine root is already rejected by the fresh
    ``inspect_engine`` binding check in ``_resolve_engine`` before this probe
    is consulted, and a version change alters ``expected_version`` and
    therefore the key itself.
    """

    probe = subprocess.run(
        [sys.executable, "-c", ENGINE_PROBE],
        cwd=cwd,
        env=_child_environment([], "runonce", root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        shell=False,
    )
    try:
        attestation = json.loads(probe.stdout.decode("utf-8"))
        imported = Path(attestation["path"]).resolve(strict=True)
        relative_import = imported.relative_to(root).as_posix()
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise AgentError(
            "BTAG-ENGINE-IMPORT",
            "registered Backtrader engine could not be imported from its bound root",
        ) from exc
    if (
        probe.returncode != 0
        or not relative_import.startswith("backtrader/")
        or (
            expected_version != "unknown"
            and attestation.get("version") != expected_version
        )
    ):
        raise AgentError(
            "BTAG-ENGINE-IMPORT",
            "child-process Backtrader import does not match the registered engine",
        )
    return relative_import, attestation["version"]


def _resource_limits(timeout_seconds: int):
    def set_limits() -> None:
        try:
            import resource

            # CPU time and file-size limits are reliable POSIX guards. The
            # address-space limit (RLIMIT_AS) is intentionally NOT applied:
            # scientific-Python BLAS libraries (numpy/OpenBLAS) reserve large
            # virtual regions on Linux that exceed any fixed AS budget while
            # resident memory stays small, producing false BTAG-RUN-FAILED
            # kills. The wall-clock timeout and RLIMIT_CPU remain the real
            # runaway guards; this is defense in depth, not an OS sandbox.
            resource.setrlimit(
                resource.RLIMIT_CPU, (timeout_seconds + 2, timeout_seconds + 2)
            )
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024)
            )
        except (ImportError, OSError, ValueError):
            # The command remains allowlisted and timeout-bound where a specific
            # POSIX resource limit is unavailable.
            return

    return set_limits


def _child_environment(
    descriptors: List[Dict[str, Any]],
    mode: str,
    engine_root: Optional[Path] = None,
) -> Dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "BACKTRADER_AGENT_DATASETS_JSON": json.dumps(
            descriptors, sort_keys=True, separators=(",", ":")
        ),
        "BACKTRADER_AGENT_MODE": mode,
    }
    if engine_root is not None:
        environment["PYTHONPATH"] = str(engine_root)
    return environment
