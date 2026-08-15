"""Deterministic scripted-host eval harness (R9).

Drives the installed ``backtrader-agent`` CLI as a subprocess, one step at a
time, and asserts each step's observables with deterministic graders (exit
code, envelope, dot-path JSON equality, sha256, file existence). No LLM
judgment is involved anywhere.

The subprocess is invoked as ``[sys.executable, "-m", "backtrader_agent",
"--state-root", <state_root>, *argv]`` so the harness always exercises the
CLI the way a host adapter would and the root-level ``--state-root`` flag
sits before the subcommand, which is the only position argparse accepts.

Because the harness is a *scripted host* executing the agent-payload worked
trace, task steps may reference artifacts earlier steps printed:

- ``{state_root}`` / ``{engine_root}`` substitute the task's state root and
  the resolved Backtrader engine root (raw path strings).
- ``{steps.<n>.result}`` and ``{steps.<n>.result.<dot.path>}`` substitute the
  parsed ``result`` of step ``<n>`` (zero-based, must have already run). A
  placeholder that spans a whole argv item is spliced raw (scalars unquoted,
  objects/arrays as compact JSON); a placeholder embedded inside a larger
  argv item is spliced as compact JSON so it stays valid inside inline JSON
  arguments. When an embedded placeholder is the entire content of a JSON
  string (``"..."``), the surrounding quotes are consumed by the splice.
  ``@``-prefixed argv items are file paths and always splice raw.
- Expectation values may use the same step placeholders: a string that is
  exactly a placeholder becomes the raw Python value (type preserved for
  ``json_path_eq`` comparisons); embedded placeholders inside expectation
  strings splice raw string forms so paths stay readable.

Failure-injection tasks need host-side filesystem mutations between CLI
steps, so a step may also carry ``"mutate"`` instead of ``"argv"`` with one
of ``write`` / ``append`` / ``delete`` (plain bytes at a state-root-relative
path) or ``expire_token`` (rewrite a persisted approval record so its issued
token's ``expires_at`` is in the past, re-signed with the state's local
token secret). Mutation steps are graded with the same expect dict; their
stdout context is empty.

Task fixtures may be ``null``, a path copied from ``tests/evals/fixtures/``,
or a generator spec ``{"generator": "<tests/helpers.py function>", "path":
<filename>, "kwargs": {...}}`` (or a list of generator specs) that writes a
deterministic CSV into the state root at task-run time.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

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

MUTATE_OPS = {"write", "append", "delete", "expire_token"}

_STEP_PLACEHOLDER_RE = re.compile(
    r"\{steps\.(\d+)\.result((?:\.[A-Za-z_][A-Za-z0-9_]*|\.[0-9]+)*)\}"
)
_QUOTED_STEP_RE = re.compile(
    r'"(\{steps\.\d+\.result(?:\.[A-Za-z_][A-Za-z0-9_]*|\.[0-9]+)*\})"'
)
_engine_root_cache: Dict[bool, Optional[str]] = {}


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


def _resolve_engine_root() -> str:
    """Resolve the Backtrader engine root via tests/helpers.py.

    Honors ``BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT`` (set by the CI job)
    and falls back to the installed ``backtrader`` package's parent. Cached
    per process.
    """
    if False in _engine_root_cache and _engine_root_cache[False] is not None:
        return _engine_root_cache[False]
    tests_dir = str(PROJECT_ROOT / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from helpers import resolve_acceptance_engine_root

    _engine_root_cache[False] = str(resolve_acceptance_engine_root(PROJECT_ROOT))
    return _engine_root_cache[False]


def _lookup_steps(
    placeholder: "re.Match[str]", parsed_steps: List[Optional[Dict[str, Any]]]
) -> Any:
    index = int(placeholder.group(1))
    if not 0 <= index < len(parsed_steps):
        raise ValueError(
            "placeholder {!r} references step {} which has not run yet".format(
                placeholder.group(0), index
            )
        )
    envelope = parsed_steps[index]
    if envelope is None:
        raise ValueError(
            "placeholder {!r} references step {} whose stdout was not a JSON "
            "object".format(placeholder.group(0), index)
        )
    value: Any = envelope.get("result")
    segments = placeholder.group(2)
    if segments:
        for segment in segments.split(".")[1:]:
            try:
                value = (
                    value[int(segment)] if isinstance(value, list) else value[segment]
                )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError(
                    "placeholder {!r} path is not present in step {}'s result".format(
                        placeholder.group(0), index
                    )
                ) from exc
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _splice_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return _compact_json(value)


def _substitute_argv_item(
    item: str,
    parsed_steps: List[Optional[Dict[str, Any]]],
    state_root: Path,
    engine_root: Optional[str],
) -> str:
    """Substitute placeholders in one argv item (see module docstring)."""
    if "{state_root}" in item:
        item = item.replace("{state_root}", str(state_root))
    if "{engine_root}" in item:
        if engine_root is None:
            raise ValueError("the Backtrader engine root could not be resolved")
        item = item.replace("{engine_root}", engine_root)
    if "{steps." not in item:
        return item
    match = _STEP_PLACEHOLDER_RE.search(item)
    if match is None:
        raise ValueError(
            "argv item {!r} contains a malformed steps placeholder".format(item)
        )
    if item.strip() == match.group(0):
        # Whole-item placeholders splice raw scalars (paths, ids, hashes)
        # and compact JSON for objects/arrays (full artifacts and tokens).
        return _splice_raw(_lookup_steps(match, parsed_steps))
    # Embedded placeholders live inside inline JSON documents and must be
    # valid JSON fragments; @-prefixed items are file paths and splice raw.
    raw_mode = item.startswith("@")
    if not raw_mode:
        # A placeholder that is the whole content of a JSON string (built by
        # json.dumps over a dict whose value is the placeholder) carries its
        # surrounding quotes in the match so the splice does not double-quote.
        def replace_quoted(placeholder: "re.Match[str]") -> str:
            value = _lookup_steps(
                _STEP_PLACEHOLDER_RE.fullmatch(placeholder.group(1)), parsed_steps
            )
            return _compact_json(value)

        item = _QUOTED_STEP_RE.sub(replace_quoted, item)

    def replace(placeholder: "re.Match[str]") -> str:
        value = _lookup_steps(placeholder, parsed_steps)
        return _splice_raw(value) if raw_mode else _compact_json(value)

    return _STEP_PLACEHOLDER_RE.sub(replace, item)


def _substitute_expect(
    value: Any,
    parsed_steps: List[Optional[Dict[str, Any]]],
    state_root: Path,
    engine_root: Optional[str],
) -> Any:
    """Substitute placeholders inside an expectation value (see docstring)."""
    if isinstance(value, list):
        return [
            _substitute_expect(item, parsed_steps, state_root, engine_root)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _substitute_expect(item, parsed_steps, state_root, engine_root)
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return value
    if "{state_root}" in value:
        value = value.replace("{state_root}", str(state_root))
    if "{engine_root}" in value:
        if engine_root is None:
            raise ValueError("the Backtrader engine root could not be resolved")
        value = value.replace("{engine_root}", engine_root)
    if "{steps." not in value:
        return value
    match = _STEP_PLACEHOLDER_RE.fullmatch(value)
    if match is not None:
        return _lookup_steps(match, parsed_steps)
    if _STEP_PLACEHOLDER_RE.search(value) is None:
        raise ValueError(
            "expectation string {!r} contains a malformed steps placeholder".format(
                value
            )
        )
    return _STEP_PLACEHOLDER_RE.sub(
        lambda placeholder: _splice_raw(_lookup_steps(placeholder, parsed_steps)),
        value,
    )


def _substitution_failure(exc: Exception, subject: Any) -> CheckResult:
    return CheckResult(
        "substitution",
        False,
        "could not substitute placeholders in {!r}: {}".format(subject, exc),
    )


def _prepare_fixture_generators(state_root: Path, fixture: Any) -> None:
    tests_dir = str(PROJECT_ROOT / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    import helpers

    for spec in fixture:
        if not isinstance(spec, dict) or set(spec) != {"generator", "path", "kwargs"}:
            raise ValueError(
                "fixture generator spec must carry generator, path, and kwargs "
                "keys, got {!r}".format(spec)
            )
        name = spec["generator"]
        if not isinstance(name, str) or not hasattr(helpers, name):
            raise ValueError("unknown fixture generator {!r}".format(name))
        path_value = spec["path"]
        if (
            not isinstance(path_value, str)
            or Path(path_value).is_absolute()
            or ".." in Path(path_value).parts
        ):
            raise ValueError("fixture generator path must be a relative name")
        kwargs = spec["kwargs"]
        if not isinstance(kwargs, dict):
            raise ValueError("fixture generator kwargs must be an object")
        getattr(helpers, name)(state_root / path_value, **kwargs)


def _prepare_state_root(state_root: Path, fixture: Any) -> None:
    """Create the state root and place the task's fixture data into it.

    ``fixture`` is ``None``, a path string, a generator spec
    ``{"generator", "path", "kwargs"}``, or a list of generator specs.
    Generator functions come from ``tests/helpers.py`` and receive the
    state-root-relative destination path plus their kwargs.
    """
    state_root.mkdir(parents=True, exist_ok=True)
    if fixture is None:
        return
    if isinstance(fixture, list):
        _prepare_fixture_generators(state_root, fixture)
        return
    if isinstance(fixture, dict):
        _prepare_fixture_generators(state_root, [fixture])
        return
    if not isinstance(fixture, str):
        raise ValueError(
            "task fixture must be None, a path string, or a generator spec, "
            "got {!r}".format(fixture)
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


def _safe_relative(path_value: str, state_root: Path) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(
            "mutation path {!r} must stay inside the state root".format(path_value)
        )
    return state_root / candidate


def _expire_approval_token(
    state_root: Path, relative_path: str, emit_token: Optional[str]
) -> None:
    """Rewrite a persisted approval record so its token is deterministically
    expired (``expires_at`` in the past), re-signed with the state's local
    token secret so every later verification check still authenticates.

    ``emit_token`` optionally names a state-root-relative file that receives
    the expired token: the scripted host hands that file to the next CLI
    step, because the persisted record must byte-match the presented token.
    """
    src = str(PROJECT_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from backtrader_agent.canonical import atomic_write_json, hash_object, read_json
    from backtrader_agent.tokens import TokenAuthority

    path = _safe_relative(relative_path, state_root)
    record = read_json(path)
    token = record.get("token")
    if not isinstance(token, dict):
        raise ValueError("expire_token target {} has no issued token".format(path))
    expired = {key: value for key, value in token.items() if key != "signature"}
    expired["expires_at"] = 1
    authority = TokenAuthority(state_root)
    # ``_signature`` is the private token HMAC; the harness is a same-repo
    # dev tool and re-signing with the on-disk secret is the only
    # deterministic way to produce an expired-but-authentic token.
    expired["signature"] = authority._signature(expired)
    record["token"] = expired
    record["token_hash"] = hash_object(expired)
    record["request_hash"] = hash_object(
        {key: value for key, value in record.items() if key != "request_hash"}
    )
    atomic_write_json(path, record)
    if emit_token is not None:
        atomic_write_json(_safe_relative(emit_token, state_root), expired)


def _grade(context: GradeContext, expect: Dict[str, Any]) -> List[CheckResult]:
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
    return checks


def _run_mutation(
    mutate: Dict[str, Any],
    parsed_steps: List[Optional[Dict[str, Any]]],
    state_root: Path,
    engine_root: Optional[str],
    expect: Dict[str, Any],
) -> StepResult:
    operations = sorted(set(mutate) & MUTATE_OPS)
    checks: List[CheckResult] = []
    if len(operations) != 1:
        checks.append(
            CheckResult(
                "substitution",
                False,
                "mutate step must carry exactly one of {}".format(sorted(MUTATE_OPS)),
            )
        )
        return StepResult([], None, checks, False, "", "")
    operation = operations[0]
    spec = mutate[operation]
    if not isinstance(spec, dict):
        checks.append(
            CheckResult(
                "substitution",
                False,
                "mutate {!r} spec must be an object".format(operation),
            )
        )
        return StepResult([], None, checks, False, "", "")
    path_value = spec.get("path")
    if not isinstance(path_value, str):
        checks.append(
            CheckResult(
                "substitution",
                False,
                "mutate {!r} requires a path string".format(operation),
            )
        )
        return StepResult([], None, checks, False, "", "")
    try:
        substituted_path = _substitute_argv_item(
            "@" + path_value, parsed_steps, state_root, engine_root
        )[1:]
        path = _safe_relative(substituted_path, state_root)
        content = spec.get("content")
        if operation in {"write", "append"} and not isinstance(content, str):
            raise ValueError("mutate {!r} requires a string content".format(operation))
        if operation == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        elif operation == "append":
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(content.encode("utf-8"))
        elif operation == "delete":
            if not path.is_file():
                raise ValueError("mutate delete target does not exist: {}".format(path))
            path.unlink()
        else:
            emit_token = spec.get("emit_token")
            if emit_token is not None and not isinstance(emit_token, str):
                raise ValueError("mutate expire_token emit_token must be a path string")
            _expire_approval_token(state_root, substituted_path, emit_token)
    except Exception as exc:  # a mutation must never take the harness down
        checks.append(
            CheckResult(
                "mutate",
                False,
                "mutate {!r} on {!r} failed: {}".format(operation, path_value, exc),
            )
        )
        return StepResult([], None, checks, False, "", "")
    checks.append(
        CheckResult("mutate", True, "{} applied to {}".format(operation, path_value))
    )
    try:
        substituted_expect = _substitute_expect(
            expect, parsed_steps, state_root, engine_root
        )
    except Exception as exc:
        checks.append(_substitution_failure(exc, expect))
        return StepResult([], None, checks, False, "", "")
    context = GradeContext(
        returncode=None,
        stdout="",
        stderr="",
        parsed={},
        state_root=state_root,
    )
    checks.extend(_grade(context, substituted_expect))
    return StepResult(
        argv=["mutate:{}:{}".format(operation, path_value)],
        returncode=None,
        checks=checks,
        passed=bool(checks) and all(check.passed for check in checks),
        stdout_tail="",
        stderr_tail="",
    )


def _run_step(
    argv: List[str],
    parsed_steps: List[Optional[Dict[str, Any]]],
    state_root: Path,
    env: Dict[str, str],
    engine_root: Optional[str],
    expect: Dict[str, Any],
) -> Tuple[StepResult, Optional[Dict[str, Any]]]:
    checks: List[CheckResult] = []
    try:
        argv = [
            _substitute_argv_item(item, parsed_steps, state_root, engine_root)
            for item in argv
        ]
    except Exception as exc:  # keep the step result informative, not fatal
        checks.append(_substitution_failure(exc, argv))
        return StepResult(argv, None, checks, False, "", ""), None
    try:
        expect = _substitute_expect(expect, parsed_steps, state_root, engine_root)
    except Exception as exc:
        checks.append(_substitution_failure(exc, argv))
        return StepResult(argv, None, checks, False, "", ""), None
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
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        stderr = "step timed out after {}s".format(STEP_TIMEOUT_SECONDS)
        timed_out = True

    parsed = _parse_stdout(stdout)
    context = GradeContext(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        parsed=parsed,
        state_root=state_root,
    )
    checks.extend(_grade(context, expect))
    if timed_out:
        # A hung CLI must fail the step no matter what the expect dict says:
        # graders like file_exists would otherwise pass vacuously.
        checks.append(
            CheckResult(
                "timeout",
                False,
                "step timed out after {}s".format(STEP_TIMEOUT_SECONDS),
            )
        )
    step_passed = bool(checks) and all(check.passed for check in checks)
    return (
        StepResult(
            argv=command,
            returncode=returncode,
            checks=checks,
            passed=step_passed,
            stdout_tail=stdout[-STDOUT_STDERR_TAIL:],
            stderr_tail=stderr[-STDOUT_STDERR_TAIL:],
        ),
        parsed,
    )


def _validate_step(task_id: str, index: int, step: Dict[str, Any]) -> None:
    argv = step.get("argv")
    mutate = step.get("mutate")
    expect = step.get("expect") or {}
    if not isinstance(expect, dict) or not expect:
        # A step with no assertions would pass vacuously (and a typo like
        # "expects" would silently become an empty expect), so refuse it.
        raise ValueError(
            "task {!r} step {} is missing a non-empty expect object".format(
                task_id, index
            )
        )
    if argv is not None and (
        not isinstance(argv, list) or not all(isinstance(item, str) for item in argv)
    ):
        raise ValueError(
            "task {!r} step {} argv must be a list of strings".format(task_id, index)
        )
    if mutate is not None and not isinstance(mutate, dict):
        raise ValueError(
            "task {!r} step {} mutate must be an object".format(task_id, index)
        )
    if argv is None and mutate is None:
        raise ValueError(
            "task {!r} step {} must carry argv or mutate".format(task_id, index)
        )


def run_task(task: Dict[str, Any], state_root: Path, env: Dict[str, str]) -> TaskResult:
    """Execute one eval task and return its graded result.

    ``task`` follows the shape ``{"task_id", "intent", "fixture", "steps":
    [{"argv": [...], "expect": {...}} | {"mutate": {...}, "expect":
    {...}}]}``. ``state_root`` is created as needed and passed to every step
    via ``--state-root``; ``env`` entries override the inherited environment
    for the subprocess.
    """
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task is missing a string task_id")
    steps_spec = task.get("steps")
    if not isinstance(steps_spec, list):
        raise ValueError("task {!r} is missing a steps list".format(task_id))
    _prepare_state_root(state_root, task.get("fixture"))
    try:
        engine_root = _resolve_engine_root()
    except Exception as exc:
        engine_root = None
        # Steps that actually use ``{engine_root}`` fail with a clear
        # substitution check; tasks that never mention it still run.
        if any("{engine_root}" in str(step) for step in steps_spec):
            raise ValueError(
                "task {!r} needs the Backtrader engine root but it could not "
                "be resolved: {}".format(task_id, exc)
            )
    step_results: List[StepResult] = []
    parsed_steps: List[Optional[Dict[str, Any]]] = []
    for index, step in enumerate(steps_spec):
        if not isinstance(step, dict):
            raise ValueError(
                "task {!r} step {} is not an object".format(task_id, index)
            )
        _validate_step(task_id, index, step)
        expect = step.get("expect") or {}
        if step.get("mutate") is not None:
            result = _run_mutation(
                step["mutate"], parsed_steps, state_root, engine_root, expect
            )
            step_results.append(result)
            parsed_steps.append({})
            continue
        result, parsed = _run_step(
            step.get("argv", []),
            parsed_steps,
            state_root,
            env,
            engine_root,
            expect,
        )
        step_results.append(result)
        parsed_steps.append(parsed)
    return TaskResult(
        task_id=task_id,
        steps=step_results,
        passed=bool(step_results) and all(step.passed for step in step_results),
    )
