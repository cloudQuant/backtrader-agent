"""Deterministic graders for scripted-host eval steps.

Each grader compares one observable produced by a CLI subprocess run — the
exit code, the JSON envelope on stdout, or files on disk — against a fixed
expectation from the task JSON. There is no LLM judgment anywhere in this
module: every grader is a pure function of its inputs and returns a
``(passed, detail)`` tuple.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

# Package-relative schema file paths in task JSON resolve against the project
# root first and against the packaged resource root as a fallback.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "backtrader_agent"

GradeResult = Tuple[bool, str]


class GradeContext(NamedTuple):
    """Observables of one CLI subprocess run, plus the task's state root."""

    returncode: Optional[int]
    stdout: str
    stderr: str
    parsed: Optional[Dict[str, Any]]  # stdout parsed as a JSON object, if possible
    state_root: Path


def _ok(detail: str) -> GradeResult:
    return (True, detail)


def _fail(detail: str) -> GradeResult:
    return (False, detail)


def _require_json(ctx: GradeContext) -> Optional[GradeResult]:
    if ctx.parsed is None:
        return _fail("stdout is not a JSON object: {!r}".format(ctx.stdout[-200:]))
    return None


def _resolve_path(state_root: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if not path.is_absolute():
        path = state_root / path
    return path


def _lookup(value: Any, segments: List[str]) -> Tuple[bool, Any]:
    """Resolve a dot path; integer segments index into lists."""
    current = value
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                return (False, None)
            current = current[segment]
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return (False, None)
        else:
            return (False, None)
    return (True, current)


def exit_code(ctx: GradeContext, expected: Any) -> GradeResult:
    """The process returncode equals the expected integer."""
    if not isinstance(expected, int):
        return _fail("exit_code expectation must be an int, got {!r}".format(expected))
    if ctx.returncode == expected:
        return _ok("returncode == {}".format(expected))
    return _fail("returncode {} != {}".format(ctx.returncode, expected))


def status(ctx: GradeContext, expected: Any) -> GradeResult:
    """The top-level envelope ``status`` field equals the expected string."""
    problem = _require_json(ctx)
    if problem:
        return problem
    actual = ctx.parsed.get("status")
    if actual == expected:
        return _ok("envelope status == {!r}".format(expected))
    return _fail("envelope status {!r} != {!r}".format(actual, expected))


def envelope(ctx: GradeContext, expected: Any) -> GradeResult:
    """The full envelope contract holds for the expected status.

    ``ok`` requires a ``result`` key and forbids ``diagnostic``; ``failed``
    requires a ``diagnostic`` object carrying a ``BTAG-*`` code, severity,
    and message.
    """
    problem = _require_json(ctx)
    if problem:
        return problem
    if not isinstance(ctx.parsed, dict):
        return _fail("envelope is not a JSON object")
    status_check = status(ctx, expected)
    if not status_check[0]:
        return status_check
    if expected == "ok":
        if "result" not in ctx.parsed:
            return _fail("ok envelope is missing 'result'")
        if "diagnostic" in ctx.parsed:
            return _fail("ok envelope must not carry 'diagnostic'")
        return _ok("envelope contract holds for status ok")
    if expected == "failed":
        diagnostic = ctx.parsed.get("diagnostic")
        if not isinstance(diagnostic, dict):
            return _fail("failed envelope is missing a 'diagnostic' object")
        for required in ("code", "severity", "message"):
            if required not in diagnostic:
                return _fail(
                    "failed envelope diagnostic is missing {!r}".format(required)
                )
        code = diagnostic.get("code")
        if not isinstance(code, str) or not code.startswith("BTAG-"):
            return _fail("diagnostic code {!r} is not a BTAG-* code".format(code))
        return _ok("envelope contract holds for status failed")
    return _fail(
        "envelope expectation must be 'ok' or 'failed', got {!r}".format(expected)
    )


def schema(ctx: GradeContext, expected: Any) -> GradeResult:
    """stdout JSON validates against a JSON Schema (Draft 2020-12).

    ``expected`` is either an inline schema object, a path to a ``.json``
    schema file, or ``{"path": str, "unwrap": dot-path}`` which loads the
    schema file and validates the envelope's ``result`` object (or the value
    at ``unwrap``) instead of the full stdout envelope. Relative file paths
    resolve against the project root, then against the packaged resource
    root.
    """
    problem = _require_json(ctx)
    if problem:
        return problem
    unwrap: Optional[str] = None
    if (
        isinstance(expected, dict)
        and "path" in expected
        and set(expected)
        <= {
            "path",
            "unwrap",
        }
    ):
        unwrap = expected.get("unwrap")
        if unwrap is not None and not isinstance(unwrap, str):
            return _fail("schema unwrap must be a dot path string")
        expected = expected["path"]
    if isinstance(expected, str):
        schema_path = Path(expected)
        if not schema_path.is_absolute():
            for root in (PROJECT_ROOT, PACKAGE_ROOT):
                candidate = root / schema_path
                if candidate.is_file():
                    schema_path = candidate
                    break
        try:
            schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _fail("schema file {} unreadable: {}".format(schema_path, exc))
        if not isinstance(schema_value, dict):
            return _fail(
                "schema file {} does not contain an object".format(schema_path)
            )
        expected = schema_value
    if not isinstance(expected, dict):
        return _fail("schema expectation must be an inline object or a file path")
    target = ctx.parsed
    if unwrap is not None:
        found, target = _lookup(ctx.parsed, unwrap.split("."))
        if not found:
            return _fail("schema unwrap path {!r} not found".format(unwrap))
        if not isinstance(target, dict):
            return _fail("schema unwrap path {!r} is not a JSON object".format(unwrap))
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError

        validator = Draft202012Validator(expected)
        validator.check_schema(expected)
        validator.validate(target)
    except ImportError:
        return _fail("jsonschema is not installed (>=4.18 required)")
    except SchemaError as exc:
        return _fail("schema is invalid: {}".format(exc))
    except ValidationError as exc:
        return _fail("schema validation failed: {}".format(exc))
    return _ok("stdout validates against the schema")


def json_path_eq(ctx: GradeContext, expected: Any) -> GradeResult:
    """Dot-path map ``{path: value}``: each path resolves and equals the value."""
    problem = _require_json(ctx)
    if problem:
        return problem
    if not isinstance(expected, dict):
        return _fail("json_path_eq expectation must be a {path: value} object")
    failures: List[str] = []
    for path, wanted in expected.items():
        if not isinstance(path, str):
            failures.append("path {!r} is not a string".format(path))
            continue
        found, actual = _lookup(ctx.parsed, path.split("."))
        if not found:
            failures.append("{}: path not found".format(path))
        elif actual != wanted:
            failures.append("{}: {!r} != {!r}".format(path, actual, wanted))
    if failures:
        return _fail("json_path_eq failed: " + "; ".join(failures))
    return _ok("json_path_eq matched {} path(s)".format(len(expected)))


def hash_eq(ctx: GradeContext, expected: Any) -> GradeResult:
    """The sha256 of a file equals the expected hex digest.

    ``expected`` is ``{"path": str, "sha256": hex}``; non-absolute paths
    resolve against the task's state root.
    """
    if not isinstance(expected, dict) or set(expected) != {"path", "sha256"}:
        return _fail("hash_eq expectation must be {'path': str, 'sha256': hex}")
    path = _resolve_path(ctx.state_root, expected.get("path"))
    if path is None:
        return _fail("hash_eq path must be a string")
    if not path.is_file():
        return _fail("hash_eq file does not exist: {}".format(path))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    wanted = expected["sha256"]
    if digest != wanted:
        return _fail("{} sha256 {} != {}".format(path, digest, wanted))
    return _ok("{} sha256 matches".format(path))


def file_exists(ctx: GradeContext, expected: Any) -> GradeResult:
    """A path exists (or does not) relative to the task's state root.

    ``expected`` is either a path string (asserted to exist) or
    ``{"path": str, "exists": bool}``.
    """
    if isinstance(expected, str):
        path_value, wanted = expected, True
    elif isinstance(expected, dict) and set(expected) == {"path", "exists"}:
        exists_value = expected["exists"]
        if not isinstance(exists_value, bool):
            return _fail(
                "file_exists 'exists' must be a boolean, got {!r}".format(exists_value)
            )
        path_value, wanted = expected["path"], exists_value
    else:
        return _fail(
            "file_exists expectation must be a path string or "
            "{'path': str, 'exists': bool}"
        )
    path = _resolve_path(ctx.state_root, path_value)
    if path is None:
        return _fail("file_exists path must be a string")
    exists = path.exists()
    if exists == wanted:
        return _ok("{} {}exists".format(path, "" if wanted else "does not "))
    return _fail("{} exists={} but expected exists={}".format(path, exists, wanted))


GRADERS: Dict[str, Callable[[GradeContext, Any], GradeResult]] = {
    "exit_code": exit_code,
    "status": status,
    "envelope": envelope,
    "schema": schema,
    "json_path_eq": json_path_eq,
    "hash_eq": hash_eq,
    "file_exists": file_exists,
}
