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


# ---------------------------------------------------------------------------
# LLM-in-the-loop gate (Task 11): the keyed path is exercised offline through
# a stub ``anthropic`` SDK whose ``messages.create`` replays canned responses
# read from BACKTRADER_AGENT_EVAL_STUB_SCRIPT and records the tool_results it
# receives in BACKTRADER_AGENT_EVAL_STUB_TRANSCRIPT.
# ---------------------------------------------------------------------------


def _write_llm_stub(tmp_path, module_source):
    stub_dir = tmp_path / "stubs"
    stub_pkg = stub_dir / "anthropic"
    stub_pkg.mkdir(parents=True)
    (stub_pkg / "__init__.py").write_text(module_source, encoding="utf-8")
    return stub_dir


def _llm_loop_env(stub_dir, script, transcript):
    env = dict(os.environ)
    env["BACKTRADER_AGENT_EVAL_API_KEY"] = "test-key"
    env["BACKTRADER_AGENT_EVAL_STUB_SCRIPT"] = json.dumps(script)
    env["BACKTRADER_AGENT_EVAL_STUB_TRANSCRIPT"] = str(transcript)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(stub_dir) if not existing else str(stub_dir) + os.pathsep + existing
    )
    return env


def _run_llm_loop(env, tmp_path, log_dir_name, *arguments):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval_llm_loop.py"),
            "--log-dir",
            str(tmp_path / log_dir_name),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def _read_llm_log(tmp_path, log_dir_name):
    log_files = list((tmp_path / log_dir_name).glob("*-llm-loop.log"))
    assert len(log_files) == 1
    return log_files[0].read_text(encoding="utf-8")


# A stub SDK that serves canned response scripts. Each entry is
# {"stop_reason": ..., "blocks": [{"type": "tool_use", "name": ..., "id": ...,
# "input": {...}}]}. Every create() call appends the tool_results it received
# to the transcript file and consumes the next script entry.
CANNED_STUB_MODULE = """
import json
import os


class _Block:
    def __init__(self, type_, name=None, input=None, id_=None):
        self.type = type_
        self.name = name
        self.input = input
        self.id = id_


class _Response:
    def __init__(self, blocks, stop_reason):
        self.content = blocks
        self.stop_reason = stop_reason


def _tool_results(messages):
    found = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append(block)
    return found


class _Messages:
    def __init__(self):
        self._script = json.loads(os.environ["BACKTRADER_AGENT_EVAL_STUB_SCRIPT"])
        self._index = 0

    def create(self, **kwargs):
        transcript = os.environ.get("BACKTRADER_AGENT_EVAL_STUB_TRANSCRIPT")
        if transcript:
            with open(transcript, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"call": self._index, "tool_results": _tool_results(kwargs.get("messages") or [])})
                    + "\\n"
                )
        if self._index >= len(self._script):
            raise RuntimeError(
                "stub script exhausted after {} create() calls".format(self._index)
            )
        entry = self._script[self._index]
        self._index += 1
        return _Response(
            [
                _Block(
                    block["type"],
                    block.get("name"),
                    block.get("input"),
                    block.get("id"),
                )
                for block in entry["blocks"]
            ],
            entry["stop_reason"],
        )


class Anthropic:
    def __init__(self, *args, **kwargs):
        self.messages = _Messages()
"""


def _finish_script(success):
    return [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "finish",
                    "id": "toolu-1",
                    "input": {"success": success, "summary": "stubbed attempt"},
                }
            ],
        }
    ]


def test_llm_loop_keyed_path_with_failing_sdk_runs_offline(tmp_path):
    # A stub SDK that raises on client construction still exercises task
    # loading, fixture preparation, per-attempt error handling, log writing,
    # and the fail-closed exit code, all without network.
    stub_dir = _write_llm_stub(
        tmp_path,
        "class Anthropic:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        raise RuntimeError('stubbed anthropic SDK: no network in tests')\n",
    )
    env = dict(os.environ)
    env["BACKTRADER_AGENT_EVAL_API_KEY"] = "test-key"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(stub_dir) if not existing else str(stub_dir) + os.pathsep + existing
    )
    result = _run_llm_loop(env, tmp_path, "logs", "--tasks", "smoke-doctor")
    assert result.returncode == 1
    assert "stubbed anthropic SDK" in result.stdout + result.stderr
    content = _read_llm_log(tmp_path, "logs")
    assert "smoke-doctor" in content
    assert "FAIL" in content
    assert "pass@3" in content


def test_llm_loop_rejected_argv_is_returned_as_tool_error(tmp_path):
    # Scripted tool_use blocks with argv the validator must reject: the loop
    # must feed is_error tool_results back to the model and never execute the
    # rejected calls.
    stub_dir = _write_llm_stub(tmp_path, CANNED_STUB_MODULE)
    transcript = tmp_path / "transcript.jsonl"
    script = [
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "run_backtrader_agent_cli",
                    "id": "toolu-1",
                    "input": {"argv": ["doctor", "--state-root", "/tmp/x"]},
                }
            ],
        },
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "run_backtrader_agent_cli",
                    "id": "toolu-2",
                    "input": {
                        "argv": [
                            "roots",
                            "register",
                            "--id",
                            "bad",
                            "--path",
                            "/etc",
                            "--kind",
                            "workspace",
                            "--writable",
                        ]
                    },
                }
            ],
        },
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "run_backtrader_agent_cli",
                    "id": "toolu-3",
                    "input": {
                        "argv": [
                            "roots",
                            "register",
                            "--id",
                            "eng",
                            "--path",
                            "/does/not/matter",
                            "--kind",
                            "engine",
                            "--writable",
                        ]
                    },
                }
            ],
        },
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "run_backtrader_agent_cli",
                    "id": "toolu-4",
                    "input": {"argv": ["backtrader", "ensure"]},
                }
            ],
        },
        {
            "stop_reason": "tool_use",
            "blocks": [
                {
                    "type": "tool_use",
                    "name": "finish",
                    "id": "toolu-5",
                    "input": {"success": False, "summary": "blocked"},
                }
            ],
        },
    ]
    result = _run_llm_loop(
        _llm_loop_env(stub_dir, script, transcript),
        tmp_path,
        "logs",
        "--tasks",
        "smoke-doctor",
        "--attempts",
        "1",
    )
    assert result.returncode == 1
    entries = [
        json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()
    ]
    # The final create() call (serving the finish block) must have received
    # exactly the four rejected tool calls as is_error tool_results, so the
    # model saw them and nothing was executed.
    final_entry = entries[-1]
    assert final_entry["call"] == 4
    tool_errors = [
        block for block in final_entry["tool_results"] if block.get("is_error")
    ]
    contents = " | ".join(block["content"] for block in tool_errors)
    assert len(tool_errors) == 4
    assert "--state-root is managed" in contents
    assert "must resolve inside the attempt state root" in contents
    assert "--writable is only accepted" in contents
    assert "not in the allowed action set" in contents


def test_llm_loop_finish_triggers_deterministic_verification(tmp_path):
    # A finish(success=true) declaration alone is not enough: the verifier
    # replays the task's read-only end-state checks in the attempt root.
    # smoke-doctor's final step passes on an untouched state root, so the
    # attempt is a genuine end-to-end PASS; a pipeline task fails it, proving
    # declared success without real work cannot pass the gate.
    stub_dir = _write_llm_stub(tmp_path, CANNED_STUB_MODULE)
    pass_result = _run_llm_loop(
        _llm_loop_env(stub_dir, _finish_script(True), tmp_path / "pass.jsonl"),
        tmp_path,
        "logs-pass",
        "--tasks",
        "smoke-doctor",
        "--attempts",
        "1",
    )
    assert pass_result.returncode == 0, pass_result.stdout + pass_result.stderr
    pass_log = _read_llm_log(tmp_path, "logs-pass")
    assert "smoke-doctor: attempts=[PASS" in pass_log
    assert "pass@3=1" in pass_log
    fail_result = _run_llm_loop(
        _llm_loop_env(stub_dir, _finish_script(True), tmp_path / "fail.jsonl"),
        tmp_path,
        "logs-fail",
        "--tasks",
        "pipeline-single-data-indicator-single-test",
        "--attempts",
        "1",
    )
    assert fail_result.returncode == 1
    fail_log = _read_llm_log(tmp_path, "logs-fail")
    assert "verification failed" in fail_log
    assert "pipeline-single-data-indicator-single-test: attempts=[FAIL" in fail_log


def test_llm_loop_validate_argv_allowlist(tmp_path):
    from scripts import eval_llm_loop

    state_root = tmp_path / "state"
    state_root.mkdir()
    engine_root = str(tmp_path / "engine")
    validate = eval_llm_loop._validate_argv
    # Worked-trace shapes are accepted, including confined paths and the
    # single allowed host path (read-only engine registration).
    assert validate(
        [
            "roots",
            "register",
            "--id",
            "workspace",
            "--path",
            str(state_root),
            "--kind",
            "workspace",
            "--writable",
        ],
        state_root,
        engine_root,
    )
    assert validate(
        [
            "roots",
            "register",
            "--id",
            "engine",
            "--path",
            engine_root,
            "--kind",
            "engine",
        ],
        state_root,
        engine_root,
    )
    assert validate(["data", "inspect", "--spec", "{}"], state_root, engine_root)
    assert validate(
        [
            "spec",
            "--file",
            '{"spec_version": "strategy-spec-v1"}',
            "--session-id",
            "s",
            "--approve",
        ],
        state_root,
        engine_root,
    )
    assert validate(
        [
            "validate",
            "--artifact-manifest",
            "@" + str(state_root / "artifact-manifest.json"),
            "--draft-root",
            str(state_root),
            "--session-id",
            "s",
            "--dataset-hash",
            "h",
            "--engine-root-id",
            "engine",
        ],
        state_root,
        engine_root,
    )
    # Rejections: escaping paths, writable non-workspace roots, a wrong
    # engine path, disallowed commands/flags, bad JSON, and bad integers.
    rejected = [
        (
            [
                "roots",
                "register",
                "--id",
                "w",
                "--path",
                "/etc",
                "--kind",
                "workspace",
            ],
            "must resolve inside the attempt state root",
        ),
        (
            [
                "roots",
                "register",
                "--id",
                "e",
                "--path",
                engine_root,
                "--kind",
                "engine",
                "--writable",
            ],
            "--writable is only accepted",
        ),
        (
            [
                "roots",
                "register",
                "--id",
                "e",
                "--path",
                str(tmp_path / "other"),
                "--kind",
                "engine",
            ],
            "must be exactly the engine root",
        ),
        (
            [
                "roots",
                "register",
                "--id",
                "d",
                "--path",
                str(state_root),
                "--kind",
                "dataset",
                "--writable",
            ],
            "--writable is only accepted",
        ),
        (["backtrader", "ensure"], "not in the allowed action set"),
        (["doctor", "--bogus"], "is not allowed for doctor"),
        (["data", "inspect", "--spec", "@/etc/passwd"], "must resolve inside"),
        (["data", "inspect", "--spec", "not-json"], "must be inline JSON"),
        (["session", "create", "positional"], "unexpected positional argument"),
        (
            [
                "run",
                "--timeout",
                "abc",
                "--applied-artifact",
                "{}",
                "--dataset-manifest",
                "{}",
                "--validation-token",
                "{}",
                "--run-token",
                "{}",
                "--mode",
                "runonce",
                "--idempotency-key",
                "k",
            ],
            "must be an integer",
        ),
        (["--state-root", "x"], "must not be passed"),
    ]
    for argv, message in rejected:
        with pytest.raises(ValueError, match=message):
            validate(argv, state_root, engine_root)


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
