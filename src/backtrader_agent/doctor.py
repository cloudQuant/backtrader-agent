"""Environment and packaged-capability diagnosis without candidate imports."""

import importlib.util
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .audit import IndependenceAuditor
from .backtrader_runtime import inspect_backtrader_runtime
from .canonical import HASH_RE, hash_object, read_json, sha256_bytes
from .engines import inspect_engine, inspect_execution_environment
from .errors import AgentError
from .roots import RootRegistry
from .runner import PROFILE_DEPENDENCIES, missing_profile_dependencies
from .sessions import SessionStore

# A session stuck in RUNNING for longer than this many seconds is reported
# as an orphan (R21, design §5.3).
ORPHAN_RUNNING_SECONDS = 3600

# Approval states that terminate a record's lifecycle: accumulated expiry only
# counts records that are still expected to move forward.
_TERMINAL_APPROVAL_STATES = {"CONSUMED", "REVOKED"}


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
            commit = (
                ref_path.read_text(encoding="ascii").strip()
                if ref_path.is_file()
                else None
            )
            if commit is None:
                packed = git_dir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(
                        encoding="ascii", errors="replace"
                    ).splitlines():
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
        entry: Dict[str, Any] = {
            "root_id": record["root_id"],
            "writable": record["writable"],
        }
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


def _audit_diag(
    code: str, severity: str, message: str, hint: Optional[str] = None
) -> Dict[str, Any]:
    """Build one structured audit diagnostic (R21 shape)."""

    value: Dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if hint:
        value["hint"] = hint
    return value


def _timestamp_seconds(text: Any) -> Optional[float]:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()


def _audit_sessions(state: Path) -> List[Dict[str, Any]]:
    """Read-only session checks: journal chain, manifest checkpoint, orphans.

    Journal verification reuses the recover valid-prefix parser without
    truncating, quarantining, or rewriting anything.
    """

    sessions_root = state / "sessions"
    if not sessions_root.is_dir():
        return []
    store = SessionStore(state)
    now = time.time()
    diags: List[Dict[str, Any]] = []
    for directory in sorted(path for path in sessions_root.iterdir() if path.is_dir()):
        session_id = directory.name
        journal = directory / "journal.jsonl"
        manifest_path = directory / "manifest.json"
        manifest: Optional[Dict[str, Any]] = None
        if manifest_path.is_file():
            try:
                manifest = read_json(manifest_path)
            except AgentError:
                manifest = None
                diags.append(
                    _audit_diag(
                        "BTAG-AUDIT-MANIFEST",
                        "error",
                        "session '{}' manifest is unreadable".format(session_id),
                        hint="run 'session recover --session-id {}' to rebuild it "
                        "from the journal".format(session_id),
                    )
                )
            else:
                expected = manifest.get("checkpoint_hash")
                portable = {
                    key: value
                    for key, value in manifest.items()
                    if key != "checkpoint_hash"
                }
                if expected != hash_object(portable):
                    manifest = None
                    diags.append(
                        _audit_diag(
                            "BTAG-AUDIT-MANIFEST",
                            "error",
                            "session '{}' manifest checkpoint hash is invalid".format(
                                session_id
                            ),
                            hint="run 'session recover --session-id {}' to rebuild "
                            "it from the journal".format(session_id),
                        )
                    )
        events: List[Dict[str, Any]] = []
        if journal.is_file():
            try:
                data = journal.read_bytes()
                events, valid_bytes = store._parse_valid_prefix(session_id, data)
            except (OSError, AgentError):
                diags.append(
                    _audit_diag(
                        "BTAG-AUDIT-JOURNAL",
                        "error",
                        "session '{}' journal could not be verified".format(session_id),
                        hint="run 'session recover --session-id {}' to quarantine "
                        "the invalid suffix".format(session_id),
                    )
                )
            else:
                if valid_bytes < len(data):
                    diags.append(
                        _audit_diag(
                            "BTAG-AUDIT-JOURNAL",
                            "error",
                            "session '{}' journal is corrupt or torn after byte {} "
                            "of {}".format(session_id, valid_bytes, len(data)),
                            hint="run 'session recover --session-id {}' to "
                            "quarantine the invalid suffix".format(session_id),
                        )
                    )
        elif manifest_path.is_file():
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-JOURNAL",
                    "error",
                    "session '{}' journal is missing".format(session_id),
                    hint="run 'session recover --session-id {}' to diagnose it "
                    "explicitly".format(session_id),
                )
            )
        if manifest is not None and manifest.get("state") == "RUNNING":
            last_timestamp = (
                _timestamp_seconds(events[-1].get("timestamp")) if events else None
            )
            if last_timestamp is None:
                try:
                    last_timestamp = manifest_path.stat().st_mtime
                except OSError:
                    last_timestamp = None
            if (
                last_timestamp is not None
                and now - last_timestamp > ORPHAN_RUNNING_SECONDS
            ):
                diags.append(
                    _audit_diag(
                        "BTAG-AUDIT-ORPHAN",
                        "warning",
                        "session '{}' has been RUNNING for over {} seconds".format(
                            session_id, ORPHAN_RUNNING_SECONDS
                        ),
                        hint="run 'session recover --session-id {}' to pause it, "
                        "or 'session cancel --session-id {}' to abandon "
                        "it".format(session_id, session_id),
                    )
                )
    return diags


def _audit_cas(state: Path, *, deep: bool) -> List[Dict[str, Any]]:
    """CAS checks: manifest-reference consistency plus, when deep, per-file
    content hashing (R21, design §5.3)."""

    cas_root = state / "data" / "sha256"
    datasets_root = state / "datasets"
    if not cas_root.is_dir() and not datasets_root.is_dir():
        return []
    diags: List[Dict[str, Any]] = []
    objects: Dict[str, Path] = {}
    misplaced: List[str] = []
    if cas_root.is_dir():
        for path in sorted(cas_root.glob("*/*")):
            digest = path.stem
            if (
                path.is_file()
                and not path.is_symlink()
                and HASH_RE.fullmatch(digest)
                and path.parent.name == digest[:2]
                and digest not in objects
            ):
                objects[digest] = path
            else:
                misplaced.append(str(path.relative_to(cas_root)))
    referenced: Dict[str, List[str]] = {}
    if datasets_root.is_dir():
        for manifest_path in sorted(datasets_root.glob("ds_*.json")):
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            try:
                manifest = read_json(manifest_path)
            except AgentError:
                diags.append(
                    _audit_diag(
                        "BTAG-AUDIT-CAS",
                        "error",
                        "dataset manifest '{}' is unreadable".format(
                            manifest_path.name
                        ),
                        hint="re-register the dataset or remove the corrupt "
                        "manifest",
                    )
                )
                continue
            expected = manifest.get("manifest_hash")
            portable = {
                key: value for key, value in manifest.items() if key != "manifest_hash"
            }
            if expected != hash_object(portable):
                diags.append(
                    _audit_diag(
                        "BTAG-AUDIT-CAS",
                        "error",
                        "dataset manifest '{}' hash is invalid".format(
                            manifest_path.name
                        ),
                        hint="re-register the dataset to restore the manifest",
                    )
                )
                continue
            feeds = manifest.get("feeds")
            if not isinstance(feeds, list):
                continue
            for feed in feeds:
                if not isinstance(feed, dict):
                    continue
                extension = (feed.get("extensions") or {}).get("backtrader_agent")
                relative = (extension or {}).get("cas_relative_path")
                if not isinstance(relative, str) or not relative:
                    continue
                digest = Path(relative).stem
                referenced.setdefault(digest, []).append(
                    str(manifest.get("dataset_id") or manifest_path.name)
                )
    missing = [digest for digest in referenced if digest not in objects]
    if missing:
        owners = sorted(
            {owner for digest in missing for owner in referenced[digest][:1]}
        )
        diags.append(
            _audit_diag(
                "BTAG-AUDIT-CAS",
                "error",
                "{} registered CAS object(s) referenced by dataset manifests "
                "({}) are missing from the CAS root".format(
                    len(missing), ", ".join(owners[:3])
                ),
                hint="re-register the affected datasets to restore the "
                "content-addressed objects",
            )
        )
    unreferenced = [digest for digest in objects if digest not in referenced]
    if unreferenced:
        diags.append(
            _audit_diag(
                "BTAG-AUDIT-CAS",
                "warning",
                "{} CAS object(s) are not referenced by any dataset "
                "manifest".format(len(unreferenced)),
                hint="unreferenced objects may be leftovers of failed "
                "registrations; they are inert but occupy space",
            )
        )
    if misplaced:
        shown = ", ".join(misplaced[:3])
        suffix = "" if len(misplaced) <= 3 else ", ..."
        diags.append(
            _audit_diag(
                "BTAG-AUDIT-CAS",
                "warning",
                "{} entry(ies) under the CAS root violate the sha256 layout: "
                "{}{}".format(len(misplaced), shown, suffix),
                hint="CAS objects must live at data/sha256/<digest[:2]>/"
                "<digest>.csv",
            )
        )
    if deep:
        violations: List[str] = []
        for digest, path in sorted(objects.items()):
            try:
                content = path.read_bytes()
            except OSError:
                violations.append("{} (unreadable)".format(digest))
                continue
            if sha256_bytes(content) != digest:
                violations.append(digest)
        if violations:
            shown = ", ".join(violations[:10])
            suffix = (
                ""
                if len(violations) <= 10
                else " and {} more".format(len(violations) - 10)
            )
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-CAS",
                    "error",
                    "{} CAS object(s) content no longer matches the digest: "
                    "{}{}".format(len(violations), shown, suffix),
                    hint="re-register the owning datasets or restore the "
                    "original bytes",
                )
            )
    return diags


def _audit_approvals(state: Path) -> List[Dict[str, Any]]:
    """Count accumulated expired approvals (R21, design §5.3)."""

    approval_root = state / "approvals"
    if not approval_root.is_dir():
        return []
    diags: List[Dict[str, Any]] = []
    now = time.time()
    expired = 0
    for path in sorted(approval_root.glob("*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            record = read_json(path)
        except AgentError:
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-APPROVALS",
                    "error",
                    "approval record '{}' is unreadable".format(path.name),
                    hint="a corrupt approval record is never usable again and "
                    "may be removed",
                )
            )
            continue
        expected = record.get("request_hash")
        if expected is not None and expected != hash_object(
            {key: value for key, value in record.items() if key != "request_hash"}
        ):
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-APPROVALS",
                    "error",
                    "approval record '{}' hash is invalid".format(path.name),
                    hint="a corrupt approval record is never usable again and "
                    "may be removed",
                )
            )
            continue
        expires_at = record.get("expires_at")
        if (
            isinstance(expires_at, int)
            and expires_at < now
            and record.get("state") not in _TERMINAL_APPROVAL_STATES
        ):
            expired += 1
    if expired:
        diags.append(
            _audit_diag(
                "BTAG-AUDIT-APPROVALS",
                "warning",
                "{} expired approval record(s) have accumulated in the state "
                "root".format(expired),
                hint="expired approvals are refused at grant and consume time "
                "and stay inert; no purge command exists yet",
            )
        )
    return diags


def _is_json_line(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


def _audit_trace(state: Path) -> List[Dict[str, Any]]:
    """Trace directory health: append-only JSONL shape (R21, design §5.3)."""

    trace_dir = state / "trace"
    if not trace_dir.is_dir():
        return []
    diags: List[Dict[str, Any]] = []
    for path in sorted(trace_dir.iterdir()):
        if path.name.endswith(".lock"):
            continue  # stable lock files persist by design
        if not path.is_file() or path.is_symlink():
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-TRACE",
                    "warning",
                    "unexpected non-regular entry '{}' in the trace "
                    "directory".format(path.name),
                    hint="the trace directory holds only append-only *.jsonl "
                    "logs and their stable lock files",
                )
            )
            continue
        if not path.name.endswith(".jsonl"):
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-TRACE",
                    "warning",
                    "unexpected file '{}' in the trace directory".format(path.name),
                    hint="the trace directory holds only append-only *.jsonl " "logs",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-TRACE",
                    "error",
                    "trace log '{}' is unreadable".format(path.name),
                    hint="quarantine the file if it cannot be read",
                )
            )
            continue
        bad_lines = [
            index
            for index, line in enumerate(text.splitlines(), 1)
            if line.strip() and not _is_json_line(line)
        ]
        if bad_lines:
            shown = ", ".join(str(index) for index in bad_lines[:5])
            suffix = "" if len(bad_lines) <= 5 else ", ..."
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-TRACE",
                    "error",
                    "trace log '{}' has {} unparseable JSON line(s): {}{}".format(
                        path.name, len(bad_lines), shown, suffix
                    ),
                    hint="trace logs are append-only; a torn tail can be "
                    "truncated back to the last complete line",
                )
            )
    return diags


def _audit_memory(state: Path) -> List[Dict[str, Any]]:
    """Memory directory health (lightweight until R22 lands its schema)."""

    memory_dir = state / "memory"
    if not memory_dir.exists():
        return []
    diags: List[Dict[str, Any]] = []
    if not memory_dir.is_dir():
        diags.append(
            _audit_diag(
                "BTAG-AUDIT-MEMORY",
                "error",
                "the memory path is not a directory",
                hint="remove the file and let the runtime recreate the memory " "store",
            )
        )
        return diags
    for path in sorted(memory_dir.iterdir()):
        if path.name.endswith(".lock"):
            continue
        if not path.is_file() or path.is_symlink() or not path.name.endswith(".json"):
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-MEMORY",
                    "warning",
                    "unexpected entry '{}' in the memory directory".format(path.name),
                    hint="the memory directory holds schema-bound *.json " "stores",
                )
            )
            continue
        try:
            read_json(path)
        except AgentError:
            diags.append(
                _audit_diag(
                    "BTAG-AUDIT-MEMORY",
                    "error",
                    "memory store '{}' is unreadable or not a JSON "
                    "object".format(path.name),
                    hint="restore the store from backup or delete it to start " "fresh",
                )
            )
    return diags


def audit_state(state: Path, *, deep: bool = False) -> List[Dict[str, Any]]:
    """Read-only health audit of a state root (R21, design §5.3).

    Returns structured diagnostics, one per finding, each with
    ``{code, severity, message, hint}``. A clean state root yields an empty
    list. Nothing under the state root is modified: journal verification
    reuses the session recover valid-prefix check without truncating or
    quarantining anything.
    """

    state_root = Path(state)
    diagnostics: List[Dict[str, Any]] = []
    diagnostics.extend(_audit_sessions(state_root))
    diagnostics.extend(_audit_cas(state_root, deep=deep))
    diagnostics.extend(_audit_approvals(state_root))
    diagnostics.extend(_audit_trace(state_root))
    diagnostics.extend(_audit_memory(state_root))
    return diagnostics


def diagnose(
    product_root: Optional[Path] = None,
    state_root: Optional[Path] = None,
    *,
    audit: bool = False,
    audit_deep: bool = False,
) -> Dict[str, Any]:
    default_product_root = Path(__file__).resolve().parents[2]
    product = Path(product_root).resolve() if product_root else default_product_root
    spec = importlib.util.find_spec("backtrader")
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    backtrader_runtime = inspect_backtrader_runtime()
    branch, commit = _git_revision(origin.parent if origin else product)
    independence = (
        IndependenceAuditor(product).audit() if (product / "src").is_dir() else None
    )
    engines = _registered_engines(state_root)
    profile_status: Dict[str, Dict[str, Any]] = {}
    for profile in PROFILE_DEPENDENCIES:
        missing = missing_profile_dependencies(profile)
        profile_status[profile] = {"ready": not missing, "missing": missing}
    execution_ready = bool(
        any(
            entry.get("status") == "valid" and not entry.get("writable")
            for entry in engines
        )
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
    result = {
        "schema_version": "doctor-report-v1",
        "product": "backtrader-agent",
        "version": __version__,
        "status": (
            "ready"
            if origin and (independence is None or independence["status"] == "passed")
            else "blocked"
        ),
        "environment": environment,
        "environment_hash": execution_environment["environment_hash"],
        "capabilities": capabilities,
        "engines": engines,
        "execution_ready": execution_ready,
        "execution_profiles": profile_status,
        "hints": hints,
        "warnings": warnings,
        "independence_audit": independence,
        "limitations": [
            "P0 is offline and does not download market data.",
            "The controlled child process is not an OS sandbox.",
            "Network isolation is policy-based, not OS-verified.",
            "Fresh master/dev baseline orchestration requires separately registered engine roots.",
        ],
    }
    if audit or audit_deep:
        audit_root = (
            Path(state_root).resolve()
            if state_root is not None
            else Path(".backtrader-agent").resolve()
        )
        result["diagnostics"] = audit_state(audit_root, deep=audit_deep)
    return result
