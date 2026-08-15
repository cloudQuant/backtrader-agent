"""Stable cross-process file locking for private state artifacts."""

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import AgentError

LOCK_RETRY_SECONDS = 0.05
LOCK_TIMEOUT_SECONDS = 30.0


def _prepare_windows_lock(descriptor: int) -> None:
    """Ensure the byte range used by ``msvcrt.locking`` exists."""

    if os.name != "nt" or os.fstat(descriptor).st_size != 0:
        return
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, b"\0")
    os.fsync(descriptor)


def _acquire_file_lock(
    descriptor: int,
    *,
    retry_seconds: float,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError("exclusive file lock acquisition timed out")
            time.sleep(retry_seconds)


def _release_file_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    error_code: str,
    subject: str,
    retry_seconds: float = LOCK_RETRY_SECONDS,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Acquire a stable, exclusive OS lock without deleting its lock file.

    ``path`` is intentionally retained after release so a concurrent opener never
    starts locking a replacement inode. Callers own input validation and choose
    the stable diagnostic code exposed at their API boundary.
    """

    lock_path = Path(path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise AgentError(error_code, f"{subject} lock file could not be opened") from exc
    acquired = False
    try:
        try:
            _prepare_windows_lock(descriptor)
        except OSError as exc:
            raise AgentError(error_code, f"{subject} lock could not be prepared") from exc
        try:
            _acquire_file_lock(
                descriptor,
                retry_seconds=retry_seconds,
                timeout_seconds=timeout_seconds,
            )
        except ImportError as exc:
            raise AgentError(error_code, f"{subject} locking is unavailable") from exc
        except TimeoutError as exc:
            raise AgentError(error_code, f"{subject} lock acquisition timed out") from exc
        except OSError as exc:
            raise AgentError(error_code, f"{subject} lock could not be acquired") from exc
        acquired = True
        yield
    finally:
        try:
            if acquired:
                try:
                    _release_file_lock(descriptor)
                except (ImportError, OSError) as exc:
                    raise AgentError(error_code, f"{subject} lock could not be released") from exc
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise AgentError(error_code, f"{subject} lock could not be closed") from exc
