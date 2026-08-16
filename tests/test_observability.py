"""Host invocation tracing (R19): append-only JSONL trace per CLI invocation."""

import json

import pytest

from backtrader_agent import cli
from backtrader_agent.errors import AgentError
from backtrader_agent.observability import record_call
from backtrader_agent.sessions import SessionStore


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
