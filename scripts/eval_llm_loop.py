"""Opt-in LLM-in-the-loop eval gate (R11).

Runs the full agentic workflow for a subset of the R9 scripted-host eval
tasks with a real host LLM driving the typed ``backtrader-agent`` CLI, then
computes pass@1 / pass@3 statistics per task and appends them to
``docs/evals/<payload-version>-llm-loop.log``.

This is an OPT-IN, developer-only tool:

- It never ships inside the runtime and is never imported by ``src/``. The
  deterministic CI gate remains ``scripts/run_evals.py``.
- Without ``BACKTRADER_AGENT_EVAL_API_KEY`` the script prints a skip notice
  and exits 0. The optional ``BACKTRADER_AGENT_EVAL_MODEL`` selects the model
  (default ``claude-fable-5``).
- The ``anthropic`` SDK is a dev-only dependency (the ``eval`` extra in
  pyproject.toml), imported lazily so the skip path needs nothing.

Safety model
------------

- The LLM is never given arbitrary shell access. Its only tool executes one
  typed CLI action as ``[sys.executable, "-m", "backtrader_agent",
  "--state-root", <attempt dir>, *argv]`` — the root-level ``--state-root``
  flag is injected by the loop and the model cannot override it, and argv is
  validated (strings only, no NUL bytes, item/length caps, no ``--state-root``,
  ``@file`` references confined to the attempt state root).
- Every attempt runs in its own fresh temporary state root, deleted when the
  attempt finishes. Fixtures are generated into it exactly like the
  deterministic harness does.
- Budgets: a turn cap per attempt, a timeout per LLM call and per CLI call.
- The runtime's own two-phase approval flow still gates writes and runs: the
  LLM is the host and, exactly like the scripted host in the harness tasks,
  must itself drive ``approval request`` then ``approval grant --confirm``.
  This only runs inside the attempt's throwaway state root.

Grading
-------

An attempt passes only when the LLM declares success (``finish`` tool with
``success=true``) AND the deterministic end-state verification passes:

1. Final-step replay: the task's last scripted step (all tasks end with a
   read-only step: ``doctor``, ``actions``, ``data list``, ``session status``,
   or ``runs list``) is re-executed in the attempt's state root and graded
   against the placeholder-free subset of its ``expect`` entries with the
   deterministic graders from ``tests/evals/graders.py``.
2. Constant ``file_exists`` (exists) expectations from the task's steps are
   checked in the attempt's state root. ``exists: false`` checks are skipped:
   they describe mid-task negative states that do not hold at the end.
3. Tasks that contain a ``run-subject`` step additionally require at least
   one run with status ``passed`` in ``runs list``.

The LLM sees the agent payload (fetched via ``backtrader-agent payload``,
falling back to the packaged ``agent-payload.md``) plus the task intent and
the attempt environment — it never sees the task's scripted steps, so the
gate measures the payload + intent contract, not step memorization.

Task subset
-----------

The default subset is every task in ``tests/evals/tasks/*.json`` whose
``task_id`` does not start with ``inject-``. The inject-* tasks require
host-side file mutations mid-pipeline (corrupt journal, expire token, tamper
preimage) which this loop deliberately does not expose; those recovery paths
stay covered by the deterministic scripted host (R10). ``--tasks`` selects
ids explicitly and overrides the default subset.

pass@1 / pass@3
---------------

Per task, pass@1 is 1 when the first attempt passes and pass@3 is 1 when any
of the three attempts passes; the overall score is the mean across tasks.
The R11 goal is overall pass@3 > 90%. The first real run establishes the
baseline recorded in ``docs/evals/payload-changelog.md``.

Exit codes: 0 skip or full pass@3; 1 at least one selected task failed all
attempts; 2 usage/configuration error (bad flags, unresolvable payload, SDK
missing, authentication/model-not-found errors).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = PROJECT_ROOT / "tests" / "evals" / "tasks"
DEFAULT_LOG_DIR = PROJECT_ROOT / "docs" / "evals"
PAYLOAD_PATH = (
    PROJECT_ROOT / "src" / "backtrader_agent" / "resources" / "agent-payload.md"
)

API_KEY_ENV = "BACKTRADER_AGENT_EVAL_API_KEY"
MODEL_ENV = "BACKTRADER_AGENT_EVAL_MODEL"
DEFAULT_MODEL = "claude-fable-5"
DEFAULT_ATTEMPTS = 3
DEFAULT_MAX_TURNS = 60
PASS_AT_K_TARGET = 0.90

CLI_CALL_TIMEOUT_SECONDS = 300  # mirrors the deterministic harness
LLM_CALL_TIMEOUT_SECONDS = 300.0  # anthropic client timeout, seconds
LLM_MAX_TOKENS = 4096
TOOL_RESULT_CHAR_CAP = 16000
MAX_ARGV_ITEMS = 40
MAX_ARGV_ITEM_CHARS = 10000
DETAIL_CHAR_CAP = 600

VERSION_RE = re.compile(r'^version:\s*"([^"]+)"\s*$', re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"\{steps\.\d+\.result|\{state_root\}|\{engine_root\}")

# Import the deterministic harness as ``evals.harness`` (tests/ on sys.path)
# rather than ``tests.evals.harness``: a site-packages package named ``tests``
# (present in some Python distributions) shadows the project's tests/ for the
# dotted form. Fixture preparation and the child environment mirror the
# scripted host exactly so both gates exercise identical starting states.
if str(PROJECT_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from evals.graders import GRADERS, GradeContext  # noqa: E402
from evals.harness import (  # noqa: E402
    _child_env,
    _prepare_state_root,
    _resolve_engine_root,
    _within,
)

TOOLS = [
    {
        "name": "run_backtrader_agent_cli",
        "description": (
            "Execute one typed backtrader-agent CLI action as a subprocess. "
            "Call this for EVERY CLI action: argv is the subcommand plus its "
            "arguments, WITHOUT the leading program name and WITHOUT "
            "--state-root (the eval loop injects both). One action per call. "
            "Returns the exit code plus the JSON envelope (or raw stdout) and "
            "stderr. Read the payload's recovery table when a call fails."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "CLI subcommand and arguments, e.g. "
                        '["doctor", "--json"] or ["data", "list"].'
                    ),
                }
            },
            "required": ["argv"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Declare the attempt finished. Call this exactly once as your "
            "very last action: success=true ONLY when the whole task intent "
            "is complete end to end, otherwise success=false. The summary "
            "should state what was done and the final session/run state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "summary": {"type": "string"},
            },
            "required": ["success", "summary"],
        },
    },
]

INITIAL_USER_MESSAGE = (
    "Begin the task now. You have the full payload above; drive the typed "
    "CLI through run_backtrader_agent_cli and call finish when the task is "
    "complete (or when you conclude it cannot succeed)."
)

CONTINUE_NUDGE = "Continue. Call a tool, or call finish when you are done."


class FatalEvalError(Exception):
    """Configuration errors that abort the whole run (not one attempt)."""


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _payload_version(payload: str) -> str:
    match = VERSION_RE.search(payload)
    return match.group(1) if match else "unknown"


def _safe_log_name(version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", version)
    return safe or "unknown"


def _fetch_payload() -> Tuple[str, str, str]:
    """Return ``(payload_text, version, sha256)``.

    Prefers the live ``backtrader-agent payload`` output (the exact bytes a
    host receives) and falls back to the packaged ``agent-payload.md``.
    """
    command = [sys.executable, "-m", "backtrader_agent", "payload"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=_child_env({}),
            timeout=60,
            check=False,
        )
        if proc.returncode == 0:
            parsed = json.loads(proc.stdout)
            result = parsed.get("result") if isinstance(parsed, dict) else None
            payload = result.get("payload") if isinstance(result, dict) else None
            if isinstance(payload, str) and payload.strip():
                reported = result.get("sha256") if isinstance(result, dict) else None
                sha = reported if isinstance(reported, str) else _sha256_hex(payload)
                return payload, _payload_version(payload), sha
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    try:
        payload = PAYLOAD_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise FatalEvalError(
            "cannot fetch the agent payload from the CLI or {}: {}".format(
                PAYLOAD_PATH, exc
            )
        )
    return payload, _payload_version(payload), _sha256_hex(payload)


def _load_tasks(tasks_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    tasks: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("error: cannot parse {}: {}".format(path, exc), file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            print(
                "error: {} must contain a JSON object with a task_id".format(path),
                file=sys.stderr,
            )
            raise SystemExit(2)
        tasks.append((path, task))
    return tasks


def _select_tasks(
    tasks: List[Tuple[Path, Dict[str, Any]]], task_ids: Optional[List[str]]
) -> List[Dict[str, Any]]:
    """Deterministic selection: sorted by task_id; ``--tasks`` overrides the
    default subset (which excludes the host-side-mutation inject-* tasks)."""
    selected = [
        task
        for _, task in sorted(tasks, key=lambda pair: pair[1]["task_id"])
        if not task["task_id"].startswith("inject-")
    ]
    if task_ids:
        selected = [task for task in selected if task["task_id"] in task_ids]
    return selected


def _fixture_names(fixture: Any) -> List[str]:
    if fixture is None:
        return []
    specs = fixture if isinstance(fixture, list) else [fixture]
    names: List[str] = []
    for spec in specs:
        if isinstance(spec, str):
            names.append(Path(spec).name)
        elif isinstance(spec, dict) and isinstance(spec.get("path"), str):
            names.append(spec["path"])
    return names


def _build_system_prompt(
    payload: str,
    task_id: str,
    intent: str,
    state_root: Path,
    engine_root: Optional[str],
    fixture_names: List[str],
) -> str:
    lines: List[str] = [
        payload,
        "",
        "--- eval harness framing (backtrader-agent LLM-in-the-loop gate) ---",
        "",
        "You are being evaluated on exactly one task. Complete the whole "
        "task end to end using only the run_backtrader_agent_cli tool, then "
        "call finish.",
        "",
        "Task id: {}".format(task_id),
        "Task intent: {}".format(intent),
        "",
        "Environment, already prepared for you:",
        "- state root (use it as both the workspace and the dataset "
        "directory): {}".format(state_root),
    ]
    if fixture_names:
        lines.append(
            "- fixture files already present in the state root: {}".format(
                ", ".join(fixture_names)
            )
        )
    if engine_root:
        lines.append(
            "- Backtrader engine root (register it read-only as root id "
            "'engine'): {}".format(engine_root)
        )
    else:
        lines.append("- Backtrader engine root: unavailable")
    lines.extend(
        [
            "- Every run_backtrader_agent_cli call runs with --state-root "
            "fixed to the state root; do not pass --state-root yourself, and "
            "do not attempt to run any other program or edit files outside "
            "the typed CLI.",
            "",
            "Protocol:",
            "- One typed CLI action per tool call; read stdout envelopes and "
            "BTAG diagnostics from the payload's recovery table when a call "
            "fails.",
            "- After you call finish, the eval loop grades the attempt: it "
            "replays the task's read-only end-state CLI calls (doctor, data "
            "list, session status, runs list) against your state root and "
            "checks the payload's end states (registered datasets valid, "
            "session COMPLETED after run + report, at least one passed run "
            "for run-bearing tasks, applied strategy files present).",
            "- Call finish exactly once at the end: success=true only if the "
            "whole intent is complete; otherwise success=false.",
        ]
    )
    return "\n".join(lines)


def _validate_argv(argv: Any, state_root: Path) -> List[str]:
    """Confine a model-proposed argv to the typed CLI in this attempt's root."""
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) for item in argv)
    ):
        raise ValueError("argv must be a non-empty list of strings")
    if len(argv) > MAX_ARGV_ITEMS:
        raise ValueError(
            "argv has {} items; the cap is {}".format(len(argv), MAX_ARGV_ITEMS)
        )
    for item in argv:
        if len(item) > MAX_ARGV_ITEM_CHARS:
            raise ValueError(
                "an argv item is {} chars; the cap is {}".format(
                    len(item), MAX_ARGV_ITEM_CHARS
                )
            )
        if "\x00" in item:
            raise ValueError("argv items must not contain NUL bytes")
        if item == "--state-root" or item.startswith("--state-root="):
            raise ValueError(
                "--state-root is managed by the eval loop and must not be passed"
            )
        if item.startswith("@") and len(item) > 1:
            reference = Path(item[1:])
            if not reference.is_absolute():
                reference = Path.cwd() / reference
            if not _within(reference.resolve(), state_root):
                raise ValueError(
                    "@file references must stay inside the attempt state root: "
                    "{}".format(item[1:])
                )
    return argv


def _run_cli(argv: List[str], state_root: Path) -> str:
    command = [
        sys.executable,
        "-m",
        "backtrader_agent",
        # argparse only accepts root-level flags before the subcommand.
        "--state-root",
        str(state_root),
        *argv,
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=_child_env({}),
            timeout=CLI_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "exit_code: none (timed out after {}s)".format(CLI_CALL_TIMEOUT_SECONDS)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if len(stdout) > TOOL_RESULT_CHAR_CAP:
        stdout = (
            stdout[:TOOL_RESULT_CHAR_CAP] + "\n... [stdout truncated by the eval loop]"
        )
    if len(stderr) > 4000:
        stderr = stderr[:4000] + "\n... [stderr truncated by the eval loop]"
    return "exit_code: {}\nstdout:\n{}\nstderr:\n{}".format(
        proc.returncode, stdout, stderr
    )


def _execute_tool(name: str, tool_input: Any, state_root: Path) -> Tuple[str, bool]:
    """Execute one model tool call; returns ``(result_text, is_error)``."""
    if name == "run_backtrader_agent_cli":
        try:
            argv = _validate_argv(
                tool_input.get("argv") if isinstance(tool_input, dict) else None,
                state_root,
            )
        except ValueError as exc:
            return "tool error (the call was NOT executed): {}".format(exc), True
        return _run_cli(argv, state_root), False
    if name == "finish":
        return "finish recorded", False
    return "unknown tool {!r}".format(name), True


def _fatal_error_types(anthropic: Any) -> Tuple[type, ...]:
    """Exception classes that indicate a bad configuration, not a bad attempt.

    ``getattr`` guards keep the check robust even against a stubbed SDK
    module (used by the offline structural test).
    """
    names = ("AuthenticationError", "NotFoundError", "BadRequestError")
    return tuple(
        klass
        for klass in (getattr(anthropic, name, None) for name in names)
        if klass is not None
    )


def _run_attempt(
    task: Dict[str, Any],
    state_root: Path,
    payload: str,
    model: str,
    api_key: str,
    anthropic: Any,
    max_turns: int,
) -> Tuple[str, str]:
    """Drive one LLM attempt and return ``(outcome, detail)``.

    ``outcome`` is "PASS"/"FAIL"; "PASS" means the model declared success —
    deterministic verification happens afterwards in ``_verify_attempt``.
    """
    task_id = task["task_id"]
    intent = task.get("intent")
    if not isinstance(intent, str):
        intent = "(no intent recorded)"
    try:
        engine_root = _resolve_engine_root()
    except Exception:
        engine_root = None
    system_prompt = _build_system_prompt(
        payload,
        task_id,
        intent,
        state_root,
        engine_root,
        _fixture_names(task.get("fixture")),
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=LLM_CALL_TIMEOUT_SECONDS)
    fatal_types = _fatal_error_types(anthropic)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": INITIAL_USER_MESSAGE}]
    try:
        finish_result: Optional[Tuple[bool, str]] = None
        for _ in range(max_turns):
            response = client.messages.create(
                model=model,
                max_tokens=LLM_MAX_TOKENS,
                system=system_prompt,
                messages=messages,
                tools=TOOLS,
            )
            if getattr(response, "stop_reason", None) == "refusal":
                return ("FAIL", "the model refused the request")
            content = list(getattr(response, "content", []))
            messages.append({"role": "assistant", "content": content})
            tool_results: List[Dict[str, Any]] = []
            tool_uses = [
                block for block in content if getattr(block, "type", None) == "tool_use"
            ]
            for block in tool_uses:
                tool_input = getattr(block, "input", None)
                if block.name == "finish":
                    if isinstance(tool_input, dict):
                        success = bool(tool_input.get("success"))
                        summary = str(tool_input.get("summary") or "")[:1000]
                    else:
                        success = False
                        summary = "finish called with a non-object input"
                    finish_result = (success, summary)
                    result_text, is_error = "finish recorded", False
                else:
                    result_text, is_error = _execute_tool(
                        block.name, tool_input, state_root
                    )
                tool_result: Dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                }
                if is_error:
                    tool_result["is_error"] = True
                tool_results.append(tool_result)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            if finish_result is not None:
                success, summary = finish_result
                return ("PASS" if success else "FAIL", summary or "(no summary)")
            if not tool_uses:
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
        return ("FAIL", "turn budget exhausted after {} turns".format(max_turns))
    except fatal_types as exc:
        raise FatalEvalError("{}: {}".format(exc.__class__.__name__, exc))
    except Exception as exc:  # transient API/network errors fail the attempt
        return ("FAIL", "{}: {}".format(exc.__class__.__name__, exc))


def _constant_expect(value: Any) -> Optional[Any]:
    """Keep only expectation entries with no step placeholders.

    Placeholder-bearing leaves are dropped so the verifier can grade the
    attempt state against the task's own constant end-state assertions.
    """
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            subset = _constant_expect(item)
            if subset is not None:
                kept[key] = subset
        return kept or None
    if isinstance(value, list):
        kept = [item for item in value if _constant_expect(item) is not None]
        return kept or None
    if isinstance(value, str):
        return None if PLACEHOLDER_RE.search(value) else value
    return value


def _parse_stdout(stdout: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _run_verification_cli(argv: List[str], state_root: Path) -> Tuple[int, str, str]:
    command = [
        sys.executable,
        "-m",
        "backtrader_agent",
        "--state-root",
        str(state_root),
        *argv,
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=_child_env({}),
            timeout=CLI_CALL_TIMEOUT_SECONDS,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        return (
            -1,
            stdout,
            "verification step timed out after {}s".format(CLI_CALL_TIMEOUT_SECONDS),
        )


def _grade(
    context: GradeContext, expect: Dict[str, Any], state_root: Path
) -> Tuple[List[str], int]:
    """Grade a constant expect dict; returns ``(failures, checks_graded)``."""
    failures: List[str] = []
    checked = 0
    for name, expected in expect.items():
        grader = GRADERS.get(name)
        if grader is None:
            continue
        try:
            passed, detail = grader(context, expected)
        except Exception as exc:  # a grader must never take the gate down
            passed, detail = False, "grader raised {}: {}".format(
                exc.__class__.__name__, exc
            )
        checked += 1
        if not passed:
            failures.append("{}: {}".format(name, detail))
    return failures, checked


def _replay_step(
    argv: List[str], expect: Dict[str, Any], state_root: Path
) -> Tuple[List[str], int]:
    """Replay one read-only CLI step in the attempt root and grade it."""
    if any(PLACEHOLDER_RE.search(item) for item in argv):
        return [], 0
    constant = _constant_expect(expect)
    if not constant:
        return [], 0
    returncode, stdout, stderr = _run_verification_cli(argv, state_root)
    context = GradeContext(
        returncode=returncode if returncode != -1 else None,
        stdout=stdout,
        stderr=stderr,
        parsed=_parse_stdout(stdout),
        state_root=state_root,
    )
    return _grade(context, constant, state_root)


def _replay_file_exists(
    task: Dict[str, Any], state_root: Path
) -> Tuple[List[str], int]:
    """Check constant exists-assertions against the attempt's end state.

    ``exists: false`` expectations describe mid-task negative states and are
    skipped; files applied mid-pipeline persist to the end state.
    """
    failures: List[str] = []
    checked = 0
    for index, step in enumerate(task.get("steps", [])):
        if not isinstance(step, dict):
            continue
        expect = step.get("expect") or {}
        expected = expect.get("file_exists")
        if expected is None:
            continue
        constant = _constant_expect(expected)
        if constant is None:
            continue
        wanted_true = isinstance(constant, str) or (
            isinstance(constant, dict) and constant.get("exists") is True
        )
        if not wanted_true:
            continue
        context = GradeContext(
            returncode=None, stdout="", stderr="", parsed={}, state_root=state_root
        )
        step_failures, step_checked = _grade(
            context, {"file_exists": constant}, state_root
        )
        checked += step_checked
        failures.extend(
            "step {} {}".format(index, failure) for failure in step_failures
        )
    return failures, checked


def _check_passed_run(task: Dict[str, Any], state_root: Path) -> Tuple[List[str], int]:
    """Run-bearing tasks must end with at least one passed run on record."""
    has_run_subject = any(
        isinstance(step, dict)
        and isinstance(step.get("argv"), list)
        and step["argv"][:1] == ["run-subject"]
        for step in task.get("steps", [])
    )
    if not has_run_subject:
        return [], 0
    returncode, stdout, _ = _run_verification_cli(["runs", "list"], state_root)
    failures: List[str] = []
    parsed = _parse_stdout(stdout)
    runs = parsed.get("result", {}).get("runs") if parsed else None
    if (
        returncode != 0
        or not isinstance(runs, list)
        or not any(
            isinstance(run, dict) and run.get("status") == "passed" for run in runs
        )
    ):
        failures.append(
            "runs list: no passed run recorded (returncode {})".format(returncode)
        )
    return failures, 1


def _verify_attempt(task: Dict[str, Any], state_root: Path) -> Tuple[bool, str]:
    """Deterministic end-state verification (see the module docstring)."""
    steps = task.get("steps")
    last = steps[-1] if isinstance(steps, list) and steps else None
    failures: List[str] = []
    checked = 0
    if isinstance(last, dict) and isinstance(last.get("argv"), list):
        step_failures, step_checked = _replay_step(
            last["argv"], last.get("expect") or {}, state_root
        )
        failures.extend(step_failures)
        checked += step_checked
    file_failures, file_checked = _replay_file_exists(task, state_root)
    failures.extend(file_failures)
    checked += file_checked
    run_failures, run_checked = _check_passed_run(task, state_root)
    failures.extend(run_failures)
    checked += run_checked
    if not checked:
        return False, "no verifiable end-state checks for this task"
    if failures:
        return False, "; ".join(failures)
    return True, "{} end-state check(s) passed".format(checked)


def _run_one_task(
    task: Dict[str, Any],
    payload: str,
    model: str,
    api_key: str,
    anthropic: Any,
    attempts: int,
    max_turns: int,
) -> Tuple[List[str], List[str]]:
    """Run all attempts for one task; returns ``(outcomes, details)``."""
    outcomes: List[str] = []
    details: List[str] = []
    for _ in range(attempts):
        with tempfile.TemporaryDirectory(prefix="backtrader-agent-llm-eval-") as name:
            state_root = Path(name) / "state"
            try:
                _prepare_state_root(state_root, task.get("fixture"))
                outcome, detail = _run_attempt(
                    task, state_root, payload, model, api_key, anthropic, max_turns
                )
                if outcome == "PASS":
                    verified, verify_detail = _verify_attempt(task, state_root)
                    if not verified:
                        outcome = "FAIL"
                        detail = "declared success, but verification failed: {}".format(
                            verify_detail
                        )
            except FatalEvalError:
                raise
            except Exception as exc:  # fixture prep must fail the attempt only
                outcome, detail = "FAIL", "{}: {}".format(exc.__class__.__name__, exc)
        outcomes.append(outcome)
        details.append(detail)
    return outcomes, details


def _write_log(
    log_dir: Path,
    log_name: str,
    run: Dict[str, Any],
    task_ids: List[str],
    outcomes: Dict[str, List[str]],
    details: Dict[str, List[str]],
    attempts: int,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append(
        "=== llm-loop run {} model={} payload_version={} payload_sha256={} "
        "tasks={} attempts_per_task={} ===".format(
            run["timestamp"],
            run["model"],
            run["version"],
            run["sha256"],
            len(task_ids),
            attempts,
        )
    )
    for task_id in task_ids:
        task_outcomes = outcomes[task_id]
        lines.append(
            "{}: attempts=[{}] pass@1={} pass@3={}".format(
                task_id,
                ", ".join(task_outcomes),
                int(task_outcomes[0] == "PASS"),
                int("PASS" in task_outcomes),
            )
        )
        for index, detail in enumerate(details[task_id]):
            if detail:
                lines.append("  attempt {}: {}".format(index + 1, detail))
    pass1 = sum(int(outcomes[t][0] == "PASS") for t in task_ids) / float(len(task_ids))
    pass3 = sum(int("PASS" in outcomes[t]) for t in task_ids) / float(len(task_ids))
    lines.append(
        "summary: pass@1={:.2f} ({}/{}) pass@3={:.2f} ({}/{}) target=>{:.2f} "
        "met={}".format(
            pass1,
            sum(int(outcomes[t][0] == "PASS") for t in task_ids),
            len(task_ids),
            pass3,
            sum(int("PASS" in outcomes[t]) for t in task_ids),
            len(task_ids),
            PASS_AT_K_TARGET,
            "yes" if pass3 > PASS_AT_K_TARGET else "no",
        )
    )
    lines.append("")
    block = "\n".join(lines) + "\n"
    with (log_dir / log_name).open("a", encoding="utf-8") as handle:
        handle.write(block)
    print(block, end="")


def _parse_task_filter(values: Optional[List[str]]) -> List[str]:
    ids: List[str] = []
    for value in values or []:
        ids.extend(part.strip() for part in value.split(",") if part.strip())
    return ids


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tasks",
        action="append",
        metavar="ID[,ID...]",
        help=(
            "only run matching task ids (comma-separated or repeatable); "
            "overrides the default subset"
        ),
    )
    parser.add_argument(
        "--tasks-dir",
        default=str(TASKS_DIR),
        help="directory scanned for task JSONs (default: tests/evals/tasks)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=DEFAULT_ATTEMPTS,
        help="attempts per task for pass@k (default: 3)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help="LLM turns per attempt before the attempt fails (default: 60)",
    )
    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="directory receiving the log (default: docs/evals)",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(
            "skip: {} is not set; the opt-in LLM-in-the-loop eval gate was "
            "not run (scripts/run_evals.py remains the deterministic CI "
            "gate)".format(API_KEY_ENV)
        )
        return 0
    if args.attempts < 1:
        print("error: --attempts must be at least 1", file=sys.stderr)
        return 2
    if args.max_turns < 1:
        print("error: --max-turns must be at least 1", file=sys.stderr)
        return 2

    try:
        import anthropic
    except ImportError as exc:
        print(
            "error: the 'anthropic' SDK is required for the LLM-in-the-loop "
            "gate; install it with 'pip install \".[eval]\"' ({})".format(exc),
            file=sys.stderr,
        )
        return 2

    tasks = _load_tasks(Path(args.tasks_dir))
    task_ids = _parse_task_filter(args.tasks)
    selected = _select_tasks(tasks, task_ids)
    if task_ids and not selected:
        print(
            "error: --tasks matched no task ids (available: {})".format(
                ", ".join(sorted(task["task_id"] for _, task in tasks))
            ),
            file=sys.stderr,
        )
        return 2
    payload, version, sha = _fetch_payload()
    model = os.environ.get(MODEL_ENV, DEFAULT_MODEL) or DEFAULT_MODEL
    log_name = "{}-llm-loop.log".format(_safe_log_name(version))
    run = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "version": version,
        "sha256": sha,
    }

    outcomes: Dict[str, List[str]] = {}
    details: Dict[str, List[str]] = {}
    ids = [task["task_id"] for task in selected]
    try:
        for task in selected:
            task_id = task["task_id"]
            task_outcomes, task_details = _run_one_task(
                task,
                payload,
                model,
                api_key,
                anthropic,
                attempts=args.attempts,
                max_turns=args.max_turns,
            )
            outcomes[task_id] = task_outcomes
            details[task_id] = [detail[:DETAIL_CHAR_CAP] for detail in task_details]
    except FatalEvalError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    _write_log(Path(args.log_dir), log_name, run, ids, outcomes, details, args.attempts)
    return 1 if any("PASS" not in outcomes[task_id] for task_id in ids) else 0


if __name__ == "__main__":
    raise SystemExit(main())
