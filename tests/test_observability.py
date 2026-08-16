"""Host invocation tracing (R19) and child output retention (R20)."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

import backtrader_agent
from backtrader_agent import cli
from backtrader_agent.canonical import hash_object
from backtrader_agent.catalog import SnapshotCatalog
from backtrader_agent.changes import ChangeManager
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.data import DatasetService
from backtrader_agent.engines import inspect_engine, inspect_execution_environment
from backtrader_agent.errors import AgentError
from backtrader_agent.observability import record_call
from backtrader_agent.roots import RootRegistry
from backtrader_agent.runner import ControlledRunner
from backtrader_agent.scaffold import ArtifactRenderer
from backtrader_agent.sessions import SessionStore
from backtrader_agent.tokens import TokenAuthority
from backtrader_agent.validator import StrategyValidator

from helpers import (
    data_spec,
    resolve_acceptance_engine_root,
    strategy_spec,
    write_price_csv,
)

PRODUCT_ROOT = Path(backtrader_agent.__file__).resolve().parent.parents[1]
BACKTRADER_ROOT = resolve_acceptance_engine_root(PRODUCT_ROOT)


def test_success_and_failure_calls_are_traced(tmp_path):
    state = tmp_path / "state"
    cli.main(["--state-root", str(state), "doctor", "--json"])  # 成功
    cli.main(["--state-root", str(state), "report", "--run-id", "bad"])  # 失败
    lines = [
        json.loads(line)
        for line in (state / "trace" / "global.jsonl").read_text().splitlines()
    ]
    assert {line["command"] for line in lines} == {"doctor", "report"}
    assert any(line["exit_code"] == 0 for line in lines)
    assert any(line["exit_code"] == 3 and line["error_code"] for line in lines)


def test_trace_has_session_context(tmp_path):
    state = tmp_path / "state"
    SessionStore(state).create("session-001")
    cli.main(
        ["--state-root", str(state), "session", "status", "--session-id", "session-001"]
    )
    lines = [
        json.loads(line)
        for line in (state / "trace" / "session-001.jsonl").read_text().splitlines()
    ]
    assert lines[-1]["session_id"] == "session-001"
    assert "duration_ms" in lines[-1]


def test_trace_contains_no_secrets(tmp_path):
    state = tmp_path / "state"
    cli.main(
        [
            "--state-root",
            str(state),
            "approval",
            "grant",
            "--request-id",
            "req-x",
            "--approver",
            "secret-approver",
            "--confirm",
        ]
    )
    blob = (state / "trace" / "global.jsonl").read_text()
    assert "secret-approver" not in blob  # 只记参数 hash


def test_record_call_routes_non_session_to_global(tmp_path):
    state = tmp_path / "state"
    record_call(
        state,
        None,
        "doctor",
        {"json": "a" * 64},
        duration_ms=7,
        exit_code=0,
        error_code=None,
    )
    lines = [
        json.loads(line)
        for line in (state / "trace" / "global.jsonl").read_text().splitlines()
    ]
    assert len(lines) == 1
    assert lines[0]["session_id"] is None
    assert lines[0]["arg_hashes"]["json"] == "a" * 64
    assert lines[0]["duration_ms"] == 7


def test_record_call_rejects_malformed_session_id(tmp_path):
    with pytest.raises(AgentError) as excinfo:
        record_call(
            tmp_path / "state",
            "../../escape",
            "session",
            {},
            duration_ms=1,
            exit_code=3,
            error_code="BTAG-TEST",
        )
    assert excinfo.value.code == "BTAG-TRACE-ID"
    assert not (tmp_path / "state" / "trace").exists()


def test_trace_write_failure_warns_without_changing_exit_code(
    monkeypatch, capsys, tmp_path
):
    def boom(*args, **kwargs):
        raise AgentError("BTAG-TRACE", "trace unavailable")

    monkeypatch.setattr(cli, "record_call", boom)
    state = tmp_path / "state"
    code = cli.main(["--state-root", str(state), "doctor", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["status"] == "ok"
    assert "WARNING" in captured.err
    assert "BTAG-TRACE" in captured.err
    assert not (state / "trace").exists()


def test_unresolvable_state_root_keeps_io_error_envelope(monkeypatch, capsys, tmp_path):
    # A deleted working directory makes Path.resolve() raise FileNotFoundError
    # (an OSError) inside main(); the Task 2 exit-code matrix must still hold.
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()
    code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["status"] == "failed"
    assert payload["diagnostic"]["code"] == "BTAG-CLI-IO"


# --- R20: child stdout/stderr retention --------------------------------------


def _prepare_approved_run_context(tmp_path: Path) -> Dict[str, Any]:
    """Drive one session through the real pipeline up to ``RUN_APPROVED``.

    Roots, a CAS dataset, an approved spec, a rendered+validated artifact, an
    applied change, and a granted run token — everything the controlled runner
    needs except the run itself. The state root lands at ``tmp_path/state`` so
    test assertions can address ``tmp_path/state/runs/<run_id>`` directly.
    """

    workspace = tmp_path / "workspace"
    input_root = tmp_path / "input"
    state = tmp_path / "state"
    workspace.mkdir(parents=True)
    input_root.mkdir(parents=True)
    write_price_csv(input_root / "prices.csv", rows=40)
    roots = RootRegistry(state)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    roots.register("input", input_root, writable=False, kind="dataset")
    roots.register("engine", BACKTRADER_ROOT, writable=False, kind="engine")
    engine = inspect_engine(roots, "engine")
    sessions = SessionStore(state)
    sessions.create("session-obs")
    dataset = DatasetService(roots, state).register(data_spec())
    sessions.transition(
        "session-obs",
        "DATA_READY",
        "dataset-register",
        {"dataset": dataset["manifest_hash"]},
        effect_references={
            "dataset_id": dataset["dataset_id"],
            "dataset_manifest_hash": dataset["manifest_hash"],
        },
    )
    spec = StrategySpec.from_dict(
        strategy_spec(dataset["dataset_id"], archetype="single_data_indicator")
    )
    sessions.transition(
        "session-obs",
        "SPEC_DRAFT",
        "spec-draft",
        {"spec": spec.spec_hash},
        effect_references={"spec_hash": spec.spec_hash},
    )
    sessions.transition(
        "session-obs",
        "SPEC_APPROVED",
        "spec-approve",
        {"spec": spec.spec_hash},
        effect_references={"approved_spec_hash": spec.spec_hash},
    )
    sources = SnapshotCatalog().search("single data indicator", top_k=1)
    assert sources
    sessions.transition(
        "session-obs",
        "SOURCES_SELECTED",
        "sources-select",
        {"catalog": sources[0]["source_hash"]},
        effect_references={"selected_source_hash": sources[0]["source_hash"]},
    )
    artifact = ArtifactRenderer(state).render("session-obs", spec, dataset)
    sessions.transition(
        "session-obs",
        "DRAFT_READY",
        "draft-render",
        {"artifact": artifact["artifact_hash"]},
        effect_references={"artifact_hash": artifact["artifact_hash"]},
    )
    authority = TokenAuthority(state)
    validation_report = StrategyValidator(authority).validate_artifact(
        artifact,
        bindings={
            "dataset_hash": dataset["manifest_hash"],
            "engine_hash": engine["engine_hash"],
            "engine_root_id": "engine",
            "environment_hash": inspect_execution_environment()["environment_hash"],
        },
        approval="validator",
        session_id="session-obs",
    )
    assert validation_report["status"] == "passed"
    validation_token = validation_report["validation_token"]
    sessions.transition(
        "session-obs",
        "VALIDATED",
        "strategy-validate",
        {"validation": validation_report["validation_hash"]},
        effect_references={
            "artifact_record_hash": validation_token["bindings"][
                "artifact_record_hash"
            ],
            "validation_hash": validation_report["validation_hash"],
            "validation_token_hash": hash_object(validation_token),
            "validation_token_id": validation_token["token_id"],
        },
    )
    changes = ChangeManager(roots, state, authority)
    prepared = changes.prepare(
        session_id="session-obs",
        draft_root=Path(artifact["_draft_path"]),
        files=[
            {
                "source": item["path"],
                "target": f"strategies/generated/obs/{item['path']}",
            }
            for item in artifact["files"]
        ],
        target_root_id="workspace",
        validation_token=validation_token,
    )
    change_request = authority.prepare_approval(
        "change",
        prepared["manifest_hash"],
        {
            "artifact_hash": prepared["artifact_hash"],
            "artifact_record_hash": prepared["artifact_record_hash"],
            "change_manifest_hash": prepared["manifest_hash"],
            "dataset_hash": prepared["dataset_manifest_hash"],
            "dataset_id": prepared["dataset_id"],
            "spec_hash": prepared["spec_hash"],
            "validation_token_hash": prepared["validation_token_hash"],
            "validation_token_id": validation_token["token_id"],
            "session_id": "session-obs",
        },
    )
    change_token = authority.grant_approval(
        change_request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    applied = changes.apply(prepared, change_token, idempotency_key="obs-apply")
    assert sessions.load("session-obs")["state"] == "APPLIED"
    run_subject = ControlledRunner.compute_run_subject(
        applied, dataset, validation_token, mode="runonce"
    )
    run_request = authority.prepare_approval(
        "run",
        run_subject,
        {
            "applied_artifact_hash": applied["applied_artifact_hash"],
            "applied_record_hash": applied["applied_record_hash"],
            "artifact_hash": applied["artifact_hash"],
            "artifact_record_hash": applied["artifact_record_hash"],
            "change_manifest_hash": applied["change_manifest_hash"],
            "validation_token_id": validation_token["token_id"],
            "validation_token_hash": hash_object(validation_token),
            "dataset_hash": dataset["manifest_hash"],
            "dataset_id": dataset["dataset_id"],
            "mode": "runonce",
            "session_id": "session-obs",
            "spec_hash": applied["spec_hash"],
        },
    )
    run_token = authority.grant_approval(
        run_request["request_id"],
        approver="local-user",
        confirmed=True,
    )["token"]
    assert sessions.load("session-obs")["state"] == "RUN_APPROVED"
    return {
        "state": state,
        "roots": roots,
        "authority": authority,
        "applied": applied,
        "dataset": dataset,
        "validation_token": validation_token,
        "run_token": run_token,
    }


@pytest.fixture
def make_approved_run_env(tmp_path: Path):
    """Factory running the approved context through the classic runner."""

    def _run() -> str:
        context = _prepare_approved_run_context(tmp_path)
        result = ControlledRunner(
            context["roots"], context["state"], context["authority"]
        ).run(
            context["applied"],
            context["dataset"],
            context["validation_token"],
            context["run_token"],
            mode="runonce",
            idempotency_key="obs-run",
            timeout_seconds=120,
        )
        assert result["status"] == "passed"
        return result["run_id"]

    return _run


class _ProbeChildRunner(ControlledRunner):
    """Controlled runner whose child is a fixed probe script instead of the
    hash-verified applied artifact; everything else stays real (R20 tests)."""

    def __init__(self, context: Dict[str, Any], script: str) -> None:
        super().__init__(context["roots"], context["state"], context["authority"])
        self._script = script

    def _verify_files(self, applied: Dict[str, Any]) -> Path:
        del applied
        probe = self.state_root / "probe-artifact"
        probe.mkdir(parents=True, exist_ok=True)
        (probe / "run.py").write_text(self._script, encoding="utf-8")
        return probe / "run.py"


def test_successful_run_retains_child_outputs(tmp_path, make_approved_run_env):
    run_id = make_approved_run_env()
    run_dir = tmp_path / "state" / "runs" / run_id
    assert (run_dir / "stdout.log").is_file()
    assert (run_dir / "stderr.log").is_file()
    # The machine protocol line is consumed, not logged.
    assert "BACKTRADER_AGENT_RESULT=" not in (run_dir / "stdout.log").read_text(
        encoding="utf-8"
    )


def test_failed_run_lands_outputs_and_keeps_redacted_details(tmp_path):
    context = _prepare_approved_run_context(tmp_path)
    script = (
        "import os, sys\n"
        "sys.stdout.write('PROBE-STDOUT-LINE\\n')\n"
        "sys.stderr.write('PROBE-STDERR-LINE ' + os.path.abspath(__file__) + '\\n')\n"
        "sys.exit(7)\n"
    )
    with pytest.raises(AgentError) as raised:
        _ProbeChildRunner(context, script).run(
            context["applied"],
            context["dataset"],
            context["validation_token"],
            context["run_token"],
            mode="runonce",
            idempotency_key="obs-run",
            timeout_seconds=120,
        )
    assert raised.value.code == "BTAG-RUN-FAILED"
    # Existing failure semantics: the diagnostic keeps only the redacted tail.
    details = raised.value.details or {}
    assert "PROBE-STDERR-LINE" in details["stderr"]
    assert str(context["state"].resolve()) not in details["stderr"]
    # The run directory still receives both logs (raw retained content).
    run_id = SessionStore(context["state"]).load("session-obs")["artifacts"]["run_id"]
    run_dir = context["state"] / "runs" / run_id
    assert (run_dir / "stdout.log").is_file()
    assert (run_dir / "stderr.log").is_file()
    assert "PROBE-STDOUT-LINE" in (run_dir / "stdout.log").read_text(encoding="utf-8")
    assert "PROBE-STDERR-LINE" in (run_dir / "stderr.log").read_text(encoding="utf-8")


def test_over_quota_run_lands_truncated_logs_with_marker(tmp_path):
    context = _prepare_approved_run_context(tmp_path)
    script = (
        "import sys\n"
        "sys.stdout.write('A' * 1100000 + '\\n')\n"
        "sys.stdout.write('BACKTRADER_AGENT_RESULT={}\\n')\n"
    )
    with pytest.raises(AgentError) as raised:
        _ProbeChildRunner(context, script).run(
            context["applied"],
            context["dataset"],
            context["validation_token"],
            context["run_token"],
            mode="runonce",
            idempotency_key="obs-run",
            timeout_seconds=120,
        )
    assert raised.value.code == "BTAG-RUN-OUTPUT"
    run_id = SessionStore(context["state"]).load("session-obs")["artifacts"]["run_id"]
    run_dir = context["state"] / "runs" / run_id
    stdout_log = (run_dir / "stdout.log").read_bytes()
    assert 0 < len(stdout_log) <= ControlledRunner.MAX_OUTPUT_BYTES
    text = stdout_log.decode("utf-8", errors="replace")
    assert "truncated" in text.splitlines()[0]
    assert text.rstrip().endswith("AAAA")
    assert "BACKTRADER_AGENT_RESULT=" not in text
