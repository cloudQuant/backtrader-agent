"""Host invocation tracing: append-only JSONL for every CLI dispatch."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .canonical import canonical_json_bytes
from .errors import AgentError
from .locking import exclusive_file_lock

# Same shape as sessions.SESSION_RE: any session ID accepted by SessionStore is
# also a safe trace filename component, so routing never escapes trace/.
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_call(
    state: Path,
    session_id: Optional[str],
    command: str,
    arg_hashes: Dict[str, str],
    duration_ms: int,
    exit_code: int,
    error_code: Optional[str],
) -> None:
    """Append one host-invocation record to the scoped JSONL trace.

    Session-scoped invocations append to ``<state>/trace/<session-id>.jsonl``;
    all other invocations append to ``<state>/trace/global.jsonl``. Only
    argument hashes are persisted — raw values (secrets, absolute target
    paths) never reach the trace. Appends hold the locking module's stable
    exclusive lock so concurrent invocations never interleave records.
    """

    state = Path(state)
    trace_dir = state / "trace"
    filename = "global.jsonl"
    if session_id:
        if not SESSION_RE.fullmatch(session_id):
            raise AgentError("BTAG-TRACE-ID", "session ID is malformed")
        filename = f"{session_id}.jsonl"
    record = {
        "schema_version": "agent-trace-v1",
        "ts": _now(),
        "session_id": session_id or None,
        "command": command,
        "arg_hashes": dict(
            sorted(
                {str(name): str(value) for name, value in arg_hashes.items()}.items()
            )
        ),
        "duration_ms": int(duration_ms),
        "exit_code": int(exit_code),
        "error_code": error_code,
    }
    with exclusive_file_lock(
        trace_dir / f"{filename}.lock",
        error_code="BTAG-TRACE-LOCK",
        subject="trace",
    ):
        data = canonical_json_bytes(record) + b"\n"
        descriptor = os.open(
            str(trace_dir / filename), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
