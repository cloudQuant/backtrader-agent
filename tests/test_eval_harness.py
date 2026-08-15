import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Imported as ``evals`` (rather than ``tests.evals``) because pytest puts the
# tests/ directory itself on sys.path, and a site-packages package named
# ``tests`` (present in some Python distributions) shadows the project's
# tests/ directory for the dotted form.
from evals import harness
from evals.graders import GRADERS, GradeContext

ROOT = Path(__file__).resolve().parents[1]

TASK = {
    "task_id": "smoke-doctor",
    "intent": "diagnose the environment",
    "fixture": None,
    "steps": [
        {
            "argv": ["doctor", "--json"],
            "expect": {
                "exit_code": 0,
                "status": "ok",
                "json_path_eq": {"result.status": "ready"},
            },
        }
    ],
}


def test_run_task_returns_passed_result(tmp_path):
    result = harness.run_task(TASK, tmp_path / "state", {})
    assert result.passed is True
    assert len(result.steps) == 1


def _context(tmp_path, parsed=None, returncode=0):
    return GradeContext(
        returncode=returncode,
        stdout=json.dumps(parsed) if parsed is not None else "not json",
        stderr="",
        parsed=parsed,
        state_root=tmp_path,
    )


def test_graders_registry_has_documented_keys():
    assert set(GRADERS) == {
        "exit_code",
        "status",
        "envelope",
        "schema",
        "json_path_eq",
        "hash_eq",
        "file_exists",
    }


def test_exit_code_grader_fails_on_mismatch(tmp_path):
    passed, detail = GRADERS["exit_code"](_context(tmp_path, returncode=3), 0)
    assert passed is False
    assert "returncode 3 != 0" in detail


def test_status_grader_requires_json(tmp_path):
    passed, detail = GRADERS["status"](_context(tmp_path, parsed=None), "ok")
    assert passed is False
    assert "not a JSON object" in detail


def test_json_path_eq_grader_reports_missing_and_mismatch(tmp_path):
    ctx = _context(tmp_path, parsed={"status": "ok", "result": {"status": "ready"}})
    passed, detail = GRADERS["json_path_eq"](
        ctx, {"result.status": "ready", "result.other": 1}
    )
    assert passed is False
    assert "path not found" in detail
    passed, detail = GRADERS["json_path_eq"](ctx, {"result.status": "other"})
    assert passed is False
    assert "'ready' != 'other'" in detail
    passed, _ = GRADERS["json_path_eq"](ctx, {"result.status": "ready"})
    assert passed is True


def test_envelope_grader_checks_contract(tmp_path):
    passed, detail = GRADERS["envelope"](
        _context(tmp_path, parsed={"status": "ok"}), "ok"
    )
    assert passed is False
    assert "missing 'result'" in detail
    passed, _ = GRADERS["envelope"](
        _context(
            tmp_path,
            parsed={
                "status": "failed",
                "diagnostic": {
                    "code": "BTAG-X",
                    "severity": "error",
                    "message": "bad",
                },
            },
        ),
        "failed",
    )
    assert passed is True


def test_hash_eq_grader_mismatch_and_missing_file(tmp_path):
    (tmp_path / "artifact.json").write_text("hello eval", encoding="utf-8")
    ctx = _context(tmp_path, parsed={"status": "ok"})
    passed, detail = GRADERS["hash_eq"](
        ctx, {"path": "artifact.json", "sha256": "0" * 64}
    )
    assert passed is False
    assert "sha256" in detail
    passed, detail = GRADERS["hash_eq"](
        ctx, {"path": "missing.json", "sha256": "0" * 64}
    )
    assert passed is False
    assert "does not exist" in detail


def test_file_exists_grader_negative(tmp_path):
    ctx = _context(tmp_path, parsed={"status": "ok"})
    passed, _ = GRADERS["file_exists"](ctx, {"path": "absent.txt", "exists": False})
    assert passed is True
    passed, detail = GRADERS["file_exists"](ctx, "absent.txt")
    assert passed is False
    assert "exists=False" in detail


def test_file_exists_rejects_non_bool_exists(tmp_path):
    ctx = _context(tmp_path, parsed={"status": "ok"})
    passed, detail = GRADERS["file_exists"](
        ctx, {"path": "absent.txt", "exists": "false"}
    )
    assert passed is False
    assert "'exists' must be a boolean" in detail


def test_schema_grader_validates_and_rejects(tmp_path):
    pytest.importorskip("jsonschema")
    ctx = _context(tmp_path, parsed={"status": "ok", "result": {"status": "ready"}})
    valid_schema = {
        "type": "object",
        "required": ["status", "result"],
        "properties": {"status": {"const": "ok"}},
    }
    passed, detail = GRADERS["schema"](ctx, valid_schema)
    assert passed is True
    assert "validates" in detail
    rejecting_schema = {
        "type": "object",
        "required": ["status", "result"],
        "properties": {"status": {"const": "failed"}},
    }
    passed, detail = GRADERS["schema"](ctx, rejecting_schema)
    assert passed is False
    assert "schema validation failed" in detail
    # A schema that is itself invalid must be reported, not crash.
    passed, detail = GRADERS["schema"](ctx, {"properties": 5})
    assert passed is False
    assert "schema is invalid" in detail


def test_run_task_rejects_missing_or_empty_expect(tmp_path):
    base = {"task_id": "no-expect", "intent": "x", "fixture": None}
    with pytest.raises(ValueError, match="non-empty expect"):
        harness.run_task(
            {**base, "steps": [{"argv": ["doctor", "--json"]}]}, tmp_path / "s", {}
        )
    with pytest.raises(ValueError, match="non-empty expect"):
        harness.run_task(
            {**base, "steps": [{"argv": ["doctor", "--json"], "expect": {}}]},
            tmp_path / "s",
            {},
        )


def test_timeout_forces_step_failure(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else kwargs["args"], 300)

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "artifact.txt").write_text("x", encoding="utf-8")
    task = {
        "task_id": "hung-cli",
        "intent": "x",
        "fixture": None,
        # file_exists alone would pass vacuously on timeout if the timeout
        # were not forced to fail the step.
        "steps": [
            {
                "argv": ["doctor", "--json"],
                "expect": {"file_exists": "artifact.txt"},
            }
        ],
    }
    result = harness.run_task(task, state_root, {})
    assert result.passed is False
    timeout_check = next(
        check for check in result.steps[0].checks if check.name == "timeout"
    )
    assert timeout_check.passed is False
    assert "timed out" in timeout_check.detail


def test_unknown_grader_fails_the_step(tmp_path):
    task = {
        "task_id": "unknown-grader",
        "intent": "x",
        "fixture": None,
        "steps": [{"argv": ["doctor", "--json"], "expect": {"bogus": 1}}],
    }
    result = harness.run_task(task, tmp_path / "state", {})
    assert result.passed is False
    assert result.steps[0].checks[0].name == "bogus"
    assert result.steps[0].checks[0].passed is False


def test_run_evals_summary_and_exit_codes(tmp_path):
    from scripts import run_evals

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "ok.json").write_text(
        json.dumps(
            {
                "task_id": "evals-ok",
                "intent": "x",
                "fixture": None,
                "steps": [
                    {
                        "argv": ["doctor", "--json"],
                        "expect": {"exit_code": 0, "status": "ok"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tasks_dir / "bad.json").write_text(
        json.dumps(
            {
                "task_id": "evals-bad",
                "intent": "x",
                "fixture": None,
                "steps": [
                    {
                        "argv": ["doctor", "--json"],
                        "expect": {"exit_code": 7},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert run_evals.main(["--tasks-dir", str(tasks_dir), "evals-ok"]) == 0
    assert run_evals.main(["--tasks-dir", str(tasks_dir), "evals-bad"]) == 1
    assert run_evals.main(["--tasks-dir", str(tasks_dir)]) == 1


# ---------------------------------------------------------------------------
# Scripted-host capabilities (Task 10): argv/expect templating, host-side
# mutation steps for failure injection, and fixture generation from helpers.
# ---------------------------------------------------------------------------


def _result_envelope(**result):
    return {"status": "ok", "result": result}


def test_substitute_argv_item_splices_whole_embedded_and_raw(tmp_path):
    parsed = [
        _result_envelope(dataset_id="ds-x", count=3, obj={"a": 1}),
    ]
    substitute = harness._substitute_argv_item
    assert substitute("{steps.0.result.dataset_id}", parsed, tmp_path, None) == "ds-x"
    assert substitute("{steps.0.result.count}", parsed, tmp_path, None) == "3"
    assert json.loads(substitute("{steps.0.result.obj}", parsed, tmp_path, None)) == {
        "a": 1
    }
    # A placeholder that fills a whole JSON string consumes the quotes.
    assert (
        substitute('{"id": "{steps.0.result.dataset_id}"}', parsed, tmp_path, None)
        == '{"id": "ds-x"}'
    )
    # @-prefixed argv items are file paths and splice raw strings.
    assert (
        substitute("@{steps.0.result.dataset_id}/x.json", parsed, tmp_path, None)
        == "@ds-x/x.json"
    )
    assert substitute("{state_root}/file", parsed, tmp_path, None) == str(
        tmp_path / "file"
    )


def test_substitute_argv_item_rejects_future_step_and_unknown_path(tmp_path):
    parsed = [_result_envelope(dataset_id="ds-x")]
    with pytest.raises(ValueError, match="has not run yet"):
        harness._substitute_argv_item("{steps.3.result.x}", parsed, tmp_path, None)
    with pytest.raises(ValueError, match="not present"):
        harness._substitute_argv_item(
            "{steps.0.result.missing}", parsed, tmp_path, None
        )


def test_substitute_expect_preserves_value_types(tmp_path):
    parsed = [_result_envelope(name="n1", count=2, flag=True)]
    substituted = harness._substitute_expect(
        {
            "result.name": "{steps.0.result.name}",
            "result.count": "{steps.0.result.count}",
            "result.flag": "{steps.0.result.flag}",
            "path": "dir/{steps.0.result.name}/file",
        },
        parsed,
        tmp_path,
        None,
    )
    assert substituted == {
        "result.name": "n1",
        "result.count": 2,
        "result.flag": True,
        "path": "dir/n1/file",
    }


def test_run_task_chains_step_results_through_placeholders(tmp_path):
    task = {
        "task_id": "chained-session",
        "intent": "x",
        "fixture": None,
        "steps": [
            {
                "argv": ["session", "create", "--session-id", "sess-1"],
                "expect": {
                    "exit_code": 0,
                    "status": "ok",
                    "json_path_eq": {"result.state": "NEW"},
                },
            },
            {
                "argv": [
                    "session",
                    "status",
                    "--session-id",
                    "{steps.0.result.session_id}",
                ],
                "expect": {
                    "exit_code": 0,
                    "status": "ok",
                    "json_path_eq": {
                        "result.session_id": "{steps.0.result.session_id}",
                        "result.state": "NEW",
                    },
                },
            },
        ],
    }
    result = harness.run_task(task, tmp_path / "state", {})
    assert result.passed is True
    assert len(result.steps) == 2


def test_mutation_steps_write_append_delete(tmp_path):
    import hashlib

    digest = hashlib.sha256(b"one\ntwo\n").hexdigest()
    task = {
        "task_id": "mutate-ops",
        "intent": "x",
        "fixture": None,
        "steps": [
            {
                "mutate": {"write": {"path": "a.txt", "content": "one\n"}},
                "expect": {"file_exists": "a.txt"},
            },
            {
                "mutate": {"append": {"path": "a.txt", "content": "two\n"}},
                "expect": {"hash_eq": {"path": "a.txt", "sha256": digest}},
            },
            {
                "mutate": {"delete": {"path": "a.txt"}},
                "expect": {"file_exists": {"path": "a.txt", "exists": False}},
            },
        ],
    }
    result = harness.run_task(task, tmp_path / "state", {})
    assert result.passed is True
    assert [step.checks[0].name for step in result.steps] == ["mutate"] * 3


def test_mutation_rejects_escaping_paths_and_unknown_ops(tmp_path):
    task = {
        "task_id": "mutate-escape",
        "intent": "x",
        "fixture": None,
        "steps": [
            {
                "mutate": {"write": {"path": "../outside.txt", "content": "x"}},
                "expect": {"file_exists": "a.txt"},
            },
        ],
    }
    result = harness.run_task(task, tmp_path / "state", {})
    assert result.passed is False
    assert "must stay inside the state root" in result.steps[0].checks[0].detail


def test_expire_token_mutation_produces_expired_authentic_token(tmp_path):
    pytest.importorskip("backtrader_agent")
    import time as clock

    from backtrader_agent.canonical import atomic_write_json, hash_object
    from backtrader_agent.errors import AgentError
    from backtrader_agent.tokens import TokenAuthority

    state = tmp_path / "state"
    state.mkdir()
    authority = TokenAuthority(state)
    now = int(clock.time())
    token = {
        "schema_version": "action-token-v1",
        "token_id": "tok-test",
        "kind": "change",
        "subject_hash": "s" * 64,
        "bindings": {},
        "approval_request_id": "aprq-" + "0" * 24,
        "approval_id": "approval-test",
        "approver_hash": "a" * 64,
        "issued_at": now,
        "expires_at": now + 900,
    }
    token["signature"] = authority._signature(token)
    record = {
        "schema_version": "approval-request-v1",
        "request_id": "aprq-" + "0" * 24,
        "kind": "change",
        "subject_hash": "s" * 64,
        "bindings": {},
        "state": "ISSUED",
        "created_at": now,
        "expires_at": now + 900,
        "token": token,
        "token_hash": hash_object(token),
    }
    record["request_hash"] = hash_object(
        {key: value for key, value in record.items() if key != "request_hash"}
    )
    approvals = state / "approvals"
    approvals.mkdir()
    atomic_write_json(approvals / "aprq-000000000000000000000000.json", record)

    harness._expire_approval_token(
        state, "approvals/aprq-000000000000000000000000.json", "expired-token.json"
    )
    expired = json.loads((state / "expired-token.json").read_text(encoding="utf-8"))
    assert expired["expires_at"] == 1
    with pytest.raises(AgentError) as failure:
        authority.verify(expired, kind="change", subject_hash="s" * 64)
    assert failure.value.code == "BTAG-TOKEN-EXPIRED"


def test_fixture_generator_spec_writes_csv_into_state_root(tmp_path):
    task = {
        "task_id": "fixture-generator",
        "intent": "x",
        "fixture": [
            {
                "generator": "write_price_csv",
                "path": "prices.csv",
                "kwargs": {"rows": 5},
            }
        ],
        "steps": [
            {
                "argv": ["doctor", "--json"],
                "expect": {"exit_code": 0, "status": "ok", "file_exists": "prices.csv"},
            }
        ],
    }
    result = harness.run_task(task, tmp_path / "state", {})
    assert result.passed is True
    assert (tmp_path / "state" / "prices.csv").is_file()


def test_llm_loop_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKTRADER_AGENT_EVAL_API_KEY", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval_llm_loop.py"),
            "--tasks",
            "smoke-doctor",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "skip" in result.stdout.lower()


def test_llm_loop_keyed_path_with_stubbed_sdk_runs_offline(tmp_path):
    # The keyed path must be exercised structurally without reaching the
    # Anthropic API: a stub SDK that raises on client construction makes the
    # script load tasks, prepare fixtures, attempt each run, and write its
    # log, all offline.
    stub_dir = tmp_path / "stubs"
    stub_pkg = stub_dir / "anthropic"
    stub_pkg.mkdir(parents=True)
    (stub_pkg / "__init__.py").write_text(
        "class Anthropic:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        raise RuntimeError('stubbed anthropic SDK: no network in tests')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["BACKTRADER_AGENT_EVAL_API_KEY"] = "test-key"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(stub_dir) if not existing else str(stub_dir) + os.pathsep + existing
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval_llm_loop.py"),
            "--tasks",
            "smoke-doctor",
            "--log-dir",
            str(tmp_path / "logs"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 1
    assert "stubbed anthropic SDK" in result.stdout + result.stderr
    log_files = list((tmp_path / "logs").glob("*-llm-loop.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "smoke-doctor" in content
    assert "FAIL" in content
    assert "pass@3" in content


def test_schema_grader_unwrap_validates_the_result_object(tmp_path):
    actions_schema = {
        "path": "src/backtrader_agent/resources/contracts/actions-v1.schema.json",
        "unwrap": "result",
    }
    good = _context(
        tmp_path,
        parsed={
            "status": "ok",
            "result": {"schema_version": "actions-v1", "actions": {}},
        },
    )
    passed, detail = GRADERS["schema"](good, actions_schema)
    assert passed, detail
    bad = _context(
        tmp_path,
        parsed={
            "status": "ok",
            "result": {"schema_version": "actions-v2", "actions": {}},
        },
    )
    passed, _ = GRADERS["schema"](bad, actions_schema)
    assert passed is False
    missing = _context(tmp_path, parsed={"status": "ok", "result": {}})
    passed, detail = GRADERS["schema"](missing, {**actions_schema, "unwrap": "absent"})
    assert passed is False
    assert "not found" in detail
