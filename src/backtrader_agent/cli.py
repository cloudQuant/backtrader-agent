"""Typed command-line interface for deterministic agent actions."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .audit import IndependenceAuditor
from .backtrader_runtime import ensure_cloudquant_backtrader, inspect_backtrader_runtime
from .canonical import hash_object, sha256_bytes
from .catalog import SnapshotCatalog
from .changes import ChangeManager
from .contracts import StrategySpec
from .data import DatasetService
from .doctor import diagnose
from .engines import inspect_engine, inspect_execution_environment
from .errors import AgentError
from .installer import AdapterInstaller
from .repair import RepairWorkflow
from .report import compare_metrics, normalize_metrics
from .roots import RootRegistry
from .runner import ControlledRunner, list_runs
from .scaffold import ArtifactRenderer
from .sessions import SessionStore
from .sweep import prepare_sweep
from .sweep_run import run_sweep, sweep_report
from .tokens import TokenAuthority
from .validator import StrategyValidator


def _json_file(path: str) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AgentError("BTAG-CLI-JSON", "input JSON must be an object")
    return value


def _json_load(value: str) -> Any:
    if value.startswith("@"):
        return _json_file(value[1:])
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _json_file(value)
    if not isinstance(parsed, dict):
        raise AgentError("BTAG-CLI-JSON", "input JSON must be an object")
    return parsed


def _emit(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _state(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "state_root", ".backtrader-agent")).resolve()


def _runtime(args: argparse.Namespace):
    state = _state(args)
    roots = RootRegistry(state)
    authority = TokenAuthority(state)
    return state, roots, authority


def _list_engines(roots: RootRegistry) -> List[Dict[str, Any]]:
    """Summarize each registered engine root and its validity.

    An invalid engine is reported with its diagnostic code instead of raising
    so a listing command never hides the registry behind one bad root.
    """

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


RUN_ID_RE = re.compile(r"^run-[0-9a-f]{20}$")

# Product-owned activation/persona payload. Mirrored byte-identically at the
# repository root as SKILL.md (enforced by tests/test_runner_installer_audit.py)
# and pinned by a golden SHA-256 in tests/test_payload_contract.py.
PAYLOAD_PATH = Path(__file__).resolve().parent / "resources" / "agent-payload.md"


def _state_run_result(state: Path, run_id: str) -> Dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise AgentError("BTAG-RUN-ID", "run ID is malformed")
    path = state / "runs" / run_id / "run-result.json"
    if not path.is_file():
        raise AgentError("BTAG-RUN-UNKNOWN", "run result does not exist")
    result = _json_file(str(path))
    if result.get("schema_version") != "run-result-v1":
        raise AgentError("BTAG-RUN-RESULT", "run result schema is invalid")
    expected = result.get("result_hash")
    portable = {key: value for key, value in result.items() if key != "result_hash"}
    if expected != hash_object(portable):
        raise AgentError("BTAG-RUN-RESULT", "run result hash is invalid")
    result["metrics"] = normalize_metrics(result.get("metrics", {}))
    return result


def _subparsers_action(parser: argparse.ArgumentParser) -> Optional[argparse.Action]:
    """Return the parser's subparsers action across supported Python versions.

    Python 3.13+ exposes ``parser._subparsers`` as an argument group whose
    actions hold the ``_SubParsersAction``; earlier versions may surface the
    action through the shared actions list instead. Scan every candidate
    container so the reflection never depends on one private layout.
    """

    containers = [parser]
    group = getattr(parser, "_subparsers", None)
    if group is not None and group is not parser:
        containers.append(group)
    for container in containers:
        actions = getattr(container, "_group_actions", None)
        if actions is None:
            actions = getattr(container, "_actions", ())
        for action in actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
    return None


def _action_param(action: argparse.Action) -> Dict[str, Any]:
    """Describe one leaf argparse action as a machine-readable parameter."""

    action_type = action.type
    return {
        "name": action.dest,
        "option_strings": list(action.option_strings),
        "required": bool(action.required),
        "type": (
            getattr(action_type, "__name__", None) if action_type is not None else None
        ),
        "choices": list(action.choices) if action.choices else None,
        "default": action.default,
        "help": action.help,
    }


def build_action_schema(parser: argparse.ArgumentParser) -> Dict[str, Any]:
    """Reflect every CLI subcommand into the machine-readable ``actions-v1`` shape.

    Subcommand groups are flattened into path keys (``"data register"``). Each
    entry lists the leaf parameters of its own parser; a group parser's own
    entry carries an empty parameter list because subcommand routing is not a
    leaf parameter.
    """

    actions: Dict[str, Any] = {}

    def walk(subparsers: argparse.Action, prefix: str) -> None:
        help_by_name = {
            choice.dest: choice.help
            for choice in getattr(subparsers, "_choices_actions", ()) or ()
        }
        for name, child in subparsers.choices.items():
            key = f"{prefix} {name}".strip()
            params = [
                _action_param(action)
                for action in child._actions
                if not isinstance(
                    action, (argparse._HelpAction, argparse._SubParsersAction)
                )
            ]
            actions[key] = {"params": params, "help": help_by_name.get(name)}
            nested = _subparsers_action(child)
            if nested is not None:
                walk(nested, key)

    top = _subparsers_action(parser)
    if top is not None:
        walk(top, "")
    return {"schema_version": "actions-v1", "actions": actions}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtrader-agent",
        description="Independent deterministic Backtrader agent runtime",
    )
    parser.add_argument("--state-root", default=".backtrader-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor", help="diagnose environment and packaged capabilities"
    )
    # ``--json`` is retained for CLI compatibility (the README documents it):
    # doctor output is always machine-readable JSON, with or without the flag.
    doctor.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    backtrader = sub.add_parser(
        "backtrader",
        help="inspect or install the required CloudQuant Backtrader runtime",
    )
    backtrader_sub = backtrader.add_subparsers(dest="backtrader_command", required=True)
    backtrader_sub.add_parser(
        "check", help="check the current interpreter without changing it"
    )
    backtrader_sub.add_parser(
        "ensure", help="install CloudQuant Backtrader when it is missing"
    )
    sub.add_parser("payload", help="return packaged product-owned agent instructions")

    roots = sub.add_parser("roots", help="manage opaque controlled roots")
    roots_sub = roots.add_subparsers(dest="roots_command", required=True)
    root_register = roots_sub.add_parser("register")
    root_register.add_argument("--id", required=True)
    root_register.add_argument("--path", required=True)
    root_register.add_argument(
        "--kind", choices=["workspace", "dataset", "engine", "runtime"], required=True
    )
    root_register.add_argument("--writable", action="store_true")
    roots_sub.add_parser("list")

    engine = sub.add_parser(
        "engine", help="inspect or list registered Backtrader runtimes"
    )
    engine_mode = engine.add_mutually_exclusive_group(required=True)
    engine_mode.add_argument("--root-id")
    engine_mode.add_argument("--list", action="store_true")

    data = sub.add_parser("data", help="inspect, register, or preview offline data")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    inspect_data = data_sub.add_parser("inspect")
    inspect_data.add_argument("--spec", required=True, help="DataSpec JSON")
    register_data = data_sub.add_parser("register")
    register_data.add_argument("--spec", required=True, help="DataSpec JSON")
    register_data.add_argument("--session-id", required=True)
    preview = data_sub.add_parser("preview")
    preview.add_argument("--dataset-id", required=True)
    preview.add_argument("--rows", type=int, default=5)
    data_sub.add_parser("list", help="list registered dataset manifests")

    spec = sub.add_parser("spec", help="validate StrategySpec")
    spec.add_argument("--file", required=True)
    spec.add_argument("--session-id", required=True)
    spec.add_argument("--approve", action="store_true", required=True)

    catalog = sub.add_parser("catalog", help="search or inspect packaged snapshot")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    search = catalog_sub.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--archetype")
    search.add_argument("--profile", choices=["single_test", "python_bundle"])
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--snapshot-path", help="optional corpus snapshot override")
    inspect = catalog_sub.add_parser("inspect")
    inspect.add_argument("--entry-id", required=True)
    inspect.add_argument("--snapshot-path", help="optional corpus snapshot override")
    refresh = catalog_sub.add_parser("refresh")
    refresh.add_argument("--functional-root-id", required=True)
    refresh.add_argument("--package-root-id", required=True)
    refresh.add_argument("--allow-count-drift", action="store_true")

    draft = sub.add_parser("draft", help="render a product-owned scaffold")
    draft.add_argument("--session-id", required=True)
    draft.add_argument("--spec", required=True)
    draft.add_argument("--dataset-manifest", required=True)

    validate = sub.add_parser("validate", help="AST/security validate a draft artifact")
    validate.add_argument("--artifact-manifest", required=True)
    validate.add_argument("--draft-root", required=True)
    validate.add_argument("--session-id", required=True)
    validate.add_argument("--dataset-hash", required=True)
    validate.add_argument("--engine-root-id", required=True)

    approval = sub.add_parser(
        "approval", help="request and locally grant one-time actions"
    )
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
    approval_request = approval_sub.add_parser("request")
    approval_request.add_argument(
        "--kind", choices=["change", "run", "sweep"], required=True
    )
    approval_request.add_argument("--subject-hash", required=True)
    approval_request.add_argument("--bindings", required=True, help="JSON object")
    approval_grant = approval_sub.add_parser("grant")
    approval_grant.add_argument("--request-id", required=True)
    approval_grant.add_argument("--approver", required=True)
    approval_grant.add_argument("--confirm", action="store_true", required=True)

    changes = sub.add_parser("changes", help="prepare or apply a confined change set")
    change_sub = changes.add_subparsers(dest="change_command", required=True)
    prepare = change_sub.add_parser("prepare")
    prepare.add_argument("--session-id", required=True)
    prepare.add_argument("--draft-root", required=True)
    prepare.add_argument(
        "--files", required=True, help="JSON array of source/target entries"
    )
    prepare.add_argument("--target-root-id", required=True)
    prepare.add_argument("--validation-token", required=True)
    apply_action = change_sub.add_parser("apply")
    apply_action.add_argument("--manifest", required=True)
    apply_action.add_argument("--change-token", required=True)
    apply_action.add_argument("--idempotency-key", required=True)

    run = sub.add_parser("run", help="execute a fixed controlled child-process profile")
    run.add_argument("--applied-artifact", required=True)
    run.add_argument("--dataset-manifest", required=True)
    run.add_argument("--validation-token", required=True)
    run.add_argument("--run-token", required=True)
    run.add_argument("--mode", choices=["runonce", "runnext"], required=True)
    run.add_argument("--idempotency-key", required=True)
    run.add_argument("--timeout", type=int, default=120)

    run_subject = sub.add_parser(
        "run-subject", help="compute the exact hash that a run approval must bind"
    )
    run_subject.add_argument("--applied-artifact", required=True)
    run_subject.add_argument("--dataset-manifest", required=True)
    run_subject.add_argument("--validation-token", required=True)
    run_subject.add_argument("--mode", choices=["runonce", "runnext"], required=True)

    compare = sub.add_parser(
        "compare", help="compare two immutable local RunResult objects"
    )
    compare.add_argument("--left-run-id", required=True)
    compare.add_argument("--right-run-id", required=True)

    report = sub.add_parser("report", help="read an immutable local run report")
    report.add_argument("--run-id", required=True)
    report.add_argument(
        "--format", choices=["json", "markdown", "html"], default="markdown"
    )

    repair = sub.add_parser(
        "repair",
        help="revise StrategySpec and deterministically re-render a failed owned draft",
    )
    repair.add_argument("--session-id", required=True)
    repair.add_argument("--spec", required=True)
    repair.add_argument("--dataset-manifest", required=True)
    repair.add_argument("--failure-report", required=True)

    sweep = sub.add_parser(
        "sweep", help="prepare immutable bounded parameter sweep plans"
    )
    sweep_sub = sweep.add_subparsers(dest="sweep_command", required=True)
    sweep_prepare = sweep_sub.add_parser("prepare")
    sweep_prepare.add_argument("--session-id", required=True)
    sweep_prepare.add_argument(
        "--spec", required=True, help="approved StrategySpec JSON"
    )
    sweep_prepare.add_argument(
        "--dataset-manifest", required=True, help="registered DatasetManifest JSON"
    )
    sweep_prepare.add_argument(
        "--param-grid",
        required=True,
        help="JSON object mapping parameter names to non-empty numeric lists",
    )
    sweep_prepare.add_argument(
        "--engine-root-id", required=True, help="registered engine root id"
    )
    sweep_run = sweep_sub.add_parser(
        "run", help="execute an approved sweep plan cell by cell"
    )
    sweep_run.add_argument("--sweep-id", required=True)
    sweep_run.add_argument("--token", required=True, help="sweep approval token JSON")
    sweep_run.add_argument("--max-cells", type=int, default=100)
    sweep_run.add_argument("--timeout-per-cell", type=int, default=120)
    sweep_report_action = sweep_sub.add_parser(
        "report", help="rank the per-cell sweep results by final_value"
    )
    sweep_report_action.add_argument("--sweep-id", required=True)

    session = sub.add_parser(
        "session", help="create, inspect, recover, cancel, or archive"
    )
    session_sub = session.add_subparsers(dest="session_command", required=True)
    for name in ("create", "status", "recover", "cancel", "archive"):
        action = session_sub.add_parser(name)
        action.add_argument("--session-id", required=True)
    session_sub.add_parser("list", help="list session manifests")

    runs = sub.add_parser("runs", help="list persisted run results")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_sub.add_parser("list")

    install = sub.add_parser("install", help="preview/apply a native host adapter")
    install.add_argument("--target", required=True)
    install.add_argument(
        "--host", choices=["claude", "codex", "opencode", "openclaw"], required=True
    )
    mode = install.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--uninstall", action="store_true")

    audit = sub.add_parser(
        "audit-independence", help="verify no sibling product dependency"
    )
    audit.add_argument(
        "--product-root",
        default=None,
        help="product root to audit (defaults to the installed source tree)",
    )

    actions = sub.add_parser("actions", help="emit the machine-readable action schema")
    actions.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    return parser


def dispatch(args: argparse.Namespace) -> Dict[str, Any]:
    if args.command == "doctor":
        return diagnose(state_root=_state(args))
    if args.command == "actions":
        return build_action_schema(build_parser())
    if args.command == "backtrader":
        if args.backtrader_command == "ensure":
            return ensure_cloudquant_backtrader()
        return inspect_backtrader_runtime()
    if args.command == "payload":
        content = PAYLOAD_PATH.read_text(encoding="utf-8")
        return {
            "schema_version": "agent-payload-v1",
            "payload": content,
            "sha256": sha256_bytes(content.encode("utf-8")),
        }
    state, roots, authority = _runtime(args)
    if args.command == "roots":
        if args.roots_command == "register":
            return roots.register(
                args.id, Path(args.path), writable=args.writable, kind=args.kind
            )
        return {"roots": roots.list()}
    if args.command == "engine":
        if args.list:
            return {"engines": _list_engines(roots)}
        return inspect_engine(roots, args.root_id)
    if args.command == "data":
        service = DatasetService(roots, state)
        if args.data_command == "inspect":
            value = service.inspect(_json_load(args.spec))
            value.pop("_canonical_feeds", None)
            return value
        if args.data_command == "register":
            value = service.register(_json_load(args.spec))
            SessionStore(state).transition(
                args.session_id,
                "DATA_READY",
                "dataset-register",
                {"dataset": value["manifest_hash"]},
                effect_references={
                    "dataset_id": value["dataset_id"],
                    "dataset_manifest_hash": value["manifest_hash"],
                },
            )
            return value
        if args.data_command == "list":
            return {"datasets": service.list()}
        return service.preview(args.dataset_id, rows=args.rows)
    if args.command == "spec":
        specification = StrategySpec.from_dict(_json_load(args.file))
        sessions = SessionStore(state)
        sessions.transition(
            args.session_id,
            "SPEC_DRAFT",
            "spec-draft",
            {"spec": specification.spec_hash},
            effect_references={"spec_hash": specification.spec_hash},
        )
        sessions.transition(
            args.session_id,
            "SPEC_APPROVED",
            "spec-approve",
            {"spec": specification.spec_hash},
            effect_references={"approved_spec_hash": specification.spec_hash},
        )
        return specification.to_dict()
    if args.command == "catalog":
        snapshot_path = getattr(args, "snapshot_path", None)
        catalog = SnapshotCatalog(
            snapshot_path=Path(snapshot_path) if snapshot_path else None
        )
        if args.catalog_command == "search":
            return {
                "results": catalog.search(
                    args.query,
                    archetype=args.archetype,
                    profile=args.profile,
                    top_k=args.top_k,
                )
            }
        if args.catalog_command == "inspect":
            return catalog.inspect(args.entry_id)
        functional = roots.get_record(args.functional_root_id)
        packages = roots.get_record(args.package_root_id)
        if functional["writable"] or packages["writable"]:
            raise AgentError(
                "BTAG-CATALOG-ROOT",
                "source-attached catalog roots must be registered read-only",
            )
        output = state / "catalog" / "source-attached.jsonl"
        return SnapshotCatalog.refresh_source_attached(
            Path(functional["path"]),
            Path(packages["path"]),
            output,
            require_verified_counts=not args.allow_count_drift,
        )
    if args.command == "draft":
        specification = StrategySpec.from_dict(_json_load(args.spec))
        value = ArtifactRenderer(state).render(
            args.session_id, specification, _json_load(args.dataset_manifest)
        )
        sessions = SessionStore(state)
        sessions.transition(
            args.session_id,
            "SOURCES_SELECTED",
            "sources-select",
            {"catalog": "package-snapshot-v1"},
        )
        sessions.transition(
            args.session_id,
            "DRAFT_READY",
            "draft-render",
            {"artifact": value["artifact_hash"]},
            effect_references={"artifact_hash": value["artifact_hash"]},
        )
        return value
    if args.command == "validate":
        artifact = _json_load(args.artifact_manifest)
        artifact["_draft_path"] = str(Path(args.draft_root).resolve())
        engine = inspect_engine(roots, args.engine_root_id)
        environment = inspect_execution_environment()
        value = StrategyValidator(authority).validate_artifact(
            artifact,
            bindings={
                "dataset_hash": args.dataset_hash,
                "engine_hash": engine["engine_hash"],
                "engine_root_id": engine["root_id"],
                "environment_hash": environment["environment_hash"],
            },
            approval="validator",
            session_id=args.session_id,
        )
        SessionStore(state).transition(
            args.session_id,
            "VALIDATED" if value["status"] == "passed" else "NEEDS_REVALIDATION",
            "strategy-validate",
            {"validation": value["validation_hash"]},
            effect_references=(
                {
                    "artifact_record_hash": value["validation_token"]["bindings"][
                        "artifact_record_hash"
                    ],
                    "validation_hash": value["validation_hash"],
                    "validation_token_hash": hash_object(value["validation_token"]),
                    "validation_token_id": value["validation_token"]["token_id"],
                }
                if value["status"] == "passed"
                else {"validation_hash": value["validation_hash"]}
            ),
        )
        return value
    if args.command == "approval":
        if args.approval_command == "grant":
            return authority.grant_approval(
                args.request_id,
                approver=args.approver,
                confirmed=args.confirm,
            )
        bindings = json.loads(args.bindings)
        if not isinstance(bindings, dict):
            raise AgentError(
                "BTAG-CLI-BINDINGS", "token bindings must be a JSON object"
            )
        return authority.prepare_approval(
            args.kind,
            args.subject_hash,
            {str(key): str(value) for key, value in bindings.items()},
        )
    if args.command == "changes":
        manager = ChangeManager(roots, state, authority)
        if args.change_command == "prepare":
            files = json.loads(args.files)
            if not isinstance(files, list):
                raise AgentError("BTAG-CLI-CHANGES", "files must be a JSON array")
            return manager.prepare(
                session_id=args.session_id,
                draft_root=Path(args.draft_root),
                files=files,
                target_root_id=args.target_root_id,
                validation_token=_json_load(args.validation_token),
            )
        manifest = _json_load(args.manifest)
        return manager.apply(
            manifest,
            _json_load(args.change_token),
            idempotency_key=args.idempotency_key,
        )
    if args.command == "run":
        return ControlledRunner(roots, state, authority).run(
            _json_load(args.applied_artifact),
            _json_load(args.dataset_manifest),
            _json_load(args.validation_token),
            _json_load(args.run_token),
            mode=args.mode,
            idempotency_key=args.idempotency_key,
            timeout_seconds=args.timeout,
        )
    if args.command == "run-subject":
        subject = ControlledRunner.compute_run_subject(
            _json_load(args.applied_artifact),
            _json_load(args.dataset_manifest),
            _json_load(args.validation_token),
            mode=args.mode,
        )
        return {"schema_version": "run-subject-v1", "subject_hash": subject}
    if args.command == "compare":
        left = _state_run_result(state, args.left_run_id)
        right = _state_run_result(state, args.right_run_id)
        comparison = compare_metrics(left["metrics"], right["metrics"])
        return {
            **comparison,
            "left_run_id": args.left_run_id,
            "right_run_id": args.right_run_id,
        }
    if args.command == "report":
        result = _state_run_result(state, args.run_id)
        if args.format == "json":
            return result
        suffix = "md" if args.format == "markdown" else "html"
        report_path = state / "runs" / args.run_id / f"report.{suffix}"
        if not report_path.is_file():
            raise AgentError(
                "BTAG-REPORT-MISSING", "requested immutable report does not exist"
            )
        content = report_path.read_text(encoding="utf-8")
        return {
            "schema_version": "report-view-v1",
            "run_id": args.run_id,
            "format": args.format,
            "sha256": sha256_bytes(content.encode("utf-8")),
            "content": content,
        }
    if args.command == "repair":
        return RepairWorkflow(state).rerender(
            args.session_id,
            _json_load(args.spec),
            _json_load(args.dataset_manifest),
            _json_load(args.failure_report),
        )
    if args.command == "sweep":
        if args.sweep_command == "prepare":
            specification = StrategySpec.from_dict(_json_load(args.spec))
            return prepare_sweep(
                state,
                args.session_id,
                specification,
                _json_load(args.dataset_manifest),
                _json_load(args.param_grid),
                engine_root_id=args.engine_root_id,
            )
        if args.sweep_command == "run":
            return run_sweep(
                state,
                roots,
                authority,
                args.sweep_id,
                _json_load(args.token),
                max_cells=args.max_cells,
                timeout_per_cell=args.timeout_per_cell,
            )
        return sweep_report(state, args.sweep_id)
    if args.command == "session":
        sessions = SessionStore(state)
        if args.session_command == "create":
            return sessions.create(args.session_id)
        if args.session_command == "status":
            return sessions.load(args.session_id)
        if args.session_command == "recover":
            return sessions.recover(args.session_id)
        if args.session_command == "cancel":
            return sessions.cancel(args.session_id)
        if args.session_command == "list":
            return {"sessions": sessions.list()}
        return sessions.archive(args.session_id)
    if args.command == "runs":
        if args.runs_command == "list":
            return {"runs": list_runs(state)}
    if args.command == "install":
        installer = AdapterInstaller()
        if args.uninstall:
            return installer.uninstall(Path(args.target), args.host, apply=True)
        return installer.install(Path(args.target), args.host, apply=args.apply)
    if args.command == "audit-independence":
        product_root = args.product_root
        if product_root is None:
            product_root = str(Path(__file__).resolve().parents[2])
        return IndependenceAuditor(Path(product_root)).audit()
    raise AgentError("BTAG-CLI-COMMAND", "unknown command")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
    except AgentError as exc:
        _emit({"status": "failed", "diagnostic": exc.as_dict()})
        return 3
    except OSError as exc:
        _emit(
            {
                "status": "failed",
                "diagnostic": {
                    "code": "BTAG-CLI-IO",
                    "severity": "error",
                    "message": "runtime I/O failure: {}".format(exc.__class__.__name__),
                },
            }
        )
        return 4
    except (ValueError, json.JSONDecodeError):
        _emit(
            {
                "status": "failed",
                "diagnostic": {
                    "code": "BTAG-CLI-INPUT",
                    "severity": "error",
                    "message": "input could not be read or parsed",
                },
            }
        )
        return 3
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    runtime_warning = result.get("warning")
    if isinstance(runtime_warning, str) and runtime_warning:
        warnings.append(runtime_warning)
    for warning in dict.fromkeys(
        item for item in warnings if isinstance(item, str) and item
    ):
        print("WARNING: {}".format(warning), file=sys.stderr)
    _emit({"status": "ok", "result": result})
    return 0
