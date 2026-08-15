"""Environment and packaged-capability diagnosis without candidate imports."""

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .audit import IndependenceAuditor
from .backtrader_runtime import inspect_backtrader_runtime
from .engines import inspect_engine, inspect_execution_environment
from .errors import AgentError
from .roots import RootRegistry
from .runner import PROFILE_DEPENDENCIES, missing_profile_dependencies


def _git_revision(start: Path) -> Tuple[Optional[str], Optional[str]]:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        marker = candidate / ".git"
        if marker.is_dir():
            git_dir = marker
        elif marker.is_file():
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir: "):
                continue
            git_dir = (candidate / text[8:]).resolve()
        else:
            continue
        head_path = git_dir / "HEAD"
        if not head_path.is_file():
            return None, None
        head = head_path.read_text(encoding="ascii", errors="replace").strip()
        if head.startswith("ref: "):
            reference = head[5:]
            ref_path = git_dir / reference
            commit = ref_path.read_text(encoding="ascii").strip() if ref_path.is_file() else None
            if commit is None:
                packed = git_dir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="ascii", errors="replace").splitlines():
                        if line.endswith(f" {reference}"):
                            commit = line.split(" ", 1)[0]
                            break
            return reference.rsplit("/", 1)[-1], commit
        return None, head
    return None, None


def _registered_engines(state_root: Optional[Path]) -> List[Dict[str, Any]]:
    """Summarize each registered engine root and whether it currently validates.

    Returns an empty list when no state root is given or no roots are
    registered. An invalid engine is reported with its diagnostic code rather
    than raised so ``doctor`` always reports the full registry.
    """

    if state_root is None:
        return []
    registry_path = Path(state_root) / "roots.json"
    if not registry_path.is_file():
        return []
    roots = RootRegistry(Path(state_root))
    engines: List[Dict[str, Any]] = []
    for record in roots.list():
        if record["kind"] != "engine":
            continue
        entry: Dict[str, Any] = {"root_id": record["root_id"], "writable": record["writable"]}
        try:
            descriptor = inspect_engine(roots, record["root_id"])
            entry.update(
                {
                    "status": "valid",
                    "version": descriptor["version"],
                    "engine_hash": descriptor["engine_hash"],
                    "source": descriptor["source"],
                }
            )
        except AgentError as exc:
            entry.update({"status": "invalid", "diagnostic": exc.code})
        engines.append(entry)
    return engines


def diagnose(product_root: Optional[Path] = None, state_root: Optional[Path] = None) -> Dict[str, Any]:
    default_product_root = Path(__file__).resolve().parents[2]
    product = Path(product_root).resolve() if product_root else default_product_root
    spec = importlib.util.find_spec("backtrader")
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    backtrader_runtime = inspect_backtrader_runtime()
    branch, commit = _git_revision(origin.parent if origin else product)
    audit = IndependenceAuditor(product).audit() if (product / "src").is_dir() else None
    engines = _registered_engines(state_root)
    profile_status: Dict[str, Dict[str, Any]] = {}
    for profile in PROFILE_DEPENDENCIES:
        missing = missing_profile_dependencies(profile)
        profile_status[profile] = {"ready": not missing, "missing": missing}
    execution_ready = bool(
        any(entry.get("status") == "valid" and not entry.get("writable") for entry in engines)
        and profile_status["python_bundle"]["ready"]
    )
    hints: List[str] = []
    warnings: List[str] = []
    runtime_warning = backtrader_runtime.get("warning")
    if isinstance(runtime_warning, str) and runtime_warning:
        warnings.append(runtime_warning)
    for engine in engines:
        source_warning = engine.get("source", {}).get("warning")
        if isinstance(source_warning, str) and source_warning:
            warnings.append(source_warning)
    if backtrader_runtime["status"] == "missing":
        hints.append(
            "CloudQuant Backtrader is not installed. Install it in this interpreter with: "
            "backtrader-agent backtrader ensure"
        )
    if state_root is not None and not engines:
        hints.append(
            "No engine root registered. Register a cloudQuant/backtrader source checkout with: "
            "backtrader-agent roots register --id <id> --kind engine "
            "--path <cloudquant-backtrader-source>"
        )
    for profile, status in profile_status.items():
        if not status["ready"]:
            hints.append(
                "Execution profile '{}' is missing: {}".format(
                    profile, ", ".join(status["missing"])
                )
            )
    capabilities = {
        "offline_dataset_cas": True,
        "snapshot_catalog": True,
        "fourteen_scaffolds": True,
        "ast_security_validation": True,
        "hash_bound_approvals": True,
        "controlled_child_process": True,
        "session_hash_chain": True,
        "native_host_adapters": ["claude", "codex", "opencode", "openclaw"],
        "live_trading": False,
        "network_data": False,
        "os_sandbox": False,
        "verified_network_isolation": False,
    }
    execution_environment = inspect_execution_environment()
    environment = {
        **{
            key: value
            for key, value in execution_environment.items()
            if key not in {"schema_version", "environment_hash"}
        },
        "backtrader_import_path": str(origin) if origin else None,
        "backtrader_branch": branch,
        "backtrader_commit": commit,
        "backtrader": backtrader_runtime,
    }
    return {
        "schema_version": "doctor-report-v1",
        "product": "backtrader-agent",
        "version": __version__,
        "status": (
            "ready" if origin and (audit is None or audit["status"] == "passed") else "blocked"
        ),
        "environment": environment,
        "environment_hash": execution_environment["environment_hash"],
        "capabilities": capabilities,
        "engines": engines,
        "execution_ready": execution_ready,
        "execution_profiles": profile_status,
        "hints": hints,
        "warnings": warnings,
        "independence_audit": audit,
        "limitations": [
            "P0 is offline and does not download market data.",
            "The controlled child process is not an OS sandbox.",
            "Network isolation is policy-based, not OS-verified.",
            "Fresh master/dev baseline orchestration requires separately registered engine roots.",
        ],
    }
