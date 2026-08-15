"""Deterministic scripted-host eval harness (R9).

Drives the installed ``backtrader-agent`` CLI as a subprocess, one step at a
time, and asserts each step's observables with deterministic graders (exit
code, envelope, dot-path JSON equality, sha256, file existence). No LLM
judgment is involved anywhere.

The subprocess is invoked as ``[sys.executable, "-m", "backtrader_agent",
...]`` so the harness always exercises the CLI the way a host adapter would.
The root-level ``--state-root`` flag is placed *before* the subcommand
because argparse only accepts root-level flags in that position.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from .graders import GRADERS, GradeContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
STEP_TIMEOUT_SECONDS = 300
STDOUT_STDERR_TAIL = 2000

CheckResult = NamedTuple(
    "CheckResult", [("name", str), ("passed", bool), ("detail", str)]
)
StepResult = NamedTuple(
    "StepResult",
    [
        ("argv", List[str]),
        ("returncode", Optional[int]),
        ("checks", List[CheckResult]),
        ("passed", bool),
        ("stdout_tail", str),
        ("stderr_tail", str),
    ],
)
TaskResult = NamedTuple(
    "TaskResult", [("task_id", str), ("steps", List[StepResult]), ("passed", bool)]
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _child_env(env: Dict[str, str]) -> Dict[str, str]:
    """Merge task env over the inherited environment for the CLI subprocess.

    The harness drives the *installed* CLI: when ``backtrader_agent`` resolves
    to an out-of-tree installation (CI), the child inherits the environment
    untouched. In a bare source checkout (local pytest without installation)
    the package is only importable through the checkout, so the checkout's
    ``src/`` is prepended to ``PYTHONPATH`` to keep the harness runnable
    everywhere without masking a real installed copy.
    """
    merged = dict(os.environ)
    merged.update(env)
    spec = importlib.util.find_spec("backtrader_agent")
    needs_source = (
        spec is None
        or spec.origin is None
        or _within(Path(spec.origin).resolve(), PROJECT_ROOT)
    )
    if needs_source:
        src = str(PROJECT_ROOT / "src")
        existing = merged.get("PYTHONPATH")
        merged["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    return merged


def _prepare_state_root(state_root: Path, fixture: Any) -> None:
    """Create the state root and copy the task's fixture file into it.

    ``fixture`` is ``None`` or a path string; non-absolute paths resolve
    against ``tests/evals/fixtures/``.
    """
    state_root.mkdir(parents=True, exist_ok=True)
    if fixture is None:
        return
    if not isinstance(fixture, str):
        raise ValueError(
            "task fixture must be None or a string path, got {!r}".format(fixture)
        )
    source = Path(fixture)
    if not source.is_absolute():
        source = FIXTURE_DIR / source
    if not source.is_file():
        raise FileNotFoundError("eval fixture does not exist: {}".format(source))
    shutil.copy2(source, state_root / source.name)


def _parse_stdout(stdout: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _run_step(
    argv: List[str], state_root: Path, env: Dict[str, str], expect: Dict[str, Any]
) -> StepResult:
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
            env=_child_env(env),
            timeout=STEP_TIMEOUT_SECONDS,
            check=False,
        )
        returncode: Optional[int] = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        stderr = "step timed out after {}s".format(STEP_TIMEOUT_SECONDS)

    parsed = _parse_stdout(stdout)
    context = GradeContext(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        parsed=parsed,
        state_root=state_root,
    )
    checks: List[CheckResult] = []
    for name, expected in expect.items():
        grader = GRADERS.get(name)
        if grader is None:
            checks.append(
                CheckResult(
                    name,
                    False,
                    "unknown grader {!r} (expected one of: {})".format(
                        name, ", ".join(sorted(GRADERS))
                    ),
                )
            )
            continue
        try:
            passed, detail = grader(context, expected)
        except Exception as exc:  # a grader must never take the harness down
            passed, detail = False, "grader raised {}: {}".format(
                exc.__class__.__name__, exc
            )
        checks.append(CheckResult(name, passed, detail))
    step_passed = all(check.passed for check in checks) if checks else True
    return StepResult(
        argv=command,
        returncode=returncode,
        checks=checks,
        passed=step_passed,
        stdout_tail=stdout[-STDOUT_STDERR_TAIL:],
        stderr_tail=stderr[-STDOUT_STDERR_TAIL:],
    )


def run_task(task: Dict[str, Any], state_root: Path, env: Dict[str, str]) -> TaskResult:
    """Execute one eval task and return its graded result.

    ``task`` follows the shape ``{"task_id", "intent", "fixture", "steps":
    [{"argv": [...], "expect": {...}}]}``. ``state_root`` is created as
    needed and passed to every step via ``--state-root``; ``env`` entries
    override the inherited environment for the subprocess.
    """
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task is missing a string task_id")
    steps_spec = task.get("steps")
    if not isinstance(steps_spec, list):
        raise ValueError("task {!r} is missing a steps list".format(task_id))
    _prepare_state_root(state_root, task.get("fixture"))
    step_results: List[StepResult] = []
    for index, step in enumerate(steps_spec):
        if not isinstance(step, dict):
            raise ValueError(
                "task {!r} step {} is not an object".format(task_id, index)
            )
        argv = step.get("argv")
        expect = step.get("expect", {})
        if not isinstance(argv, list) or not all(
            isinstance(item, str) for item in argv
        ):
            raise ValueError(
                "task {!r} step {} argv must be a list of strings".format(
                    task_id, index
                )
            )
        if not isinstance(expect, dict):
            raise ValueError(
                "task {!r} step {} expect must be an object".format(task_id, index)
            )
        step_results.append(_run_step(argv, state_root, env, expect))
    return TaskResult(
        task_id=task_id,
        steps=step_results,
        passed=bool(step_results) and all(step.passed for step in step_results),
    )
