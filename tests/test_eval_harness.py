import json
import subprocess

import pytest

# Imported as ``evals`` (rather than ``tests.evals``) because pytest puts the
# tests/ directory itself on sys.path, and a site-packages package named
# ``tests`` (present in some Python distributions) shadows the project's
# tests/ directory for the dotted form.
from evals import harness
from evals.graders import GRADERS, GradeContext

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
