"""Process-local memoization for security-sensitive hashing paths.

Engine tree hashes, child-process probe results, and dataset feed hashes are
security bindings: they must be recomputed from disk in every new process.
This module therefore provides a strictly in-memory memo that lives and dies
with the process and is never persisted to any cache directory.
"""

import functools
import threading
from typing import Any, Callable, Dict, Tuple, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_CACHE: Dict[Tuple[str, Any, Any], Any] = {}
_LOCK = threading.Lock()
_UNSET = object()


def _freeze(value: Any) -> Any:
    """Recursively convert a value into a hashable, equality-stable key fragment."""
    try:
        hash(value)
    except TypeError:
        if isinstance(value, dict):
            return tuple(
                sorted((_freeze(key), _freeze(item)) for key, item in value.items())
            )
        if isinstance(value, (list, tuple, set)):
            return tuple(_freeze(item) for item in value)
        return repr(value)
    return value


def memoized(fn: F) -> F:
    """Wrap ``fn`` with a thread-safe, process-local, never-persisted memo.

    The cache key is ``(fn.__qualname__, frozen args, frozen kwargs)``. Only
    successful calls populate the cache; exceptions always propagate so
    failures are retried on the next call instead of replayed. Cached values
    are returned as-is, so callers must treat them as read-only.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = (fn.__qualname__, _freeze(args), _freeze(kwargs))
        with _LOCK:
            cached = _CACHE.get(key, _UNSET)
        if cached is not _UNSET:
            return cached
        result = fn(*args, **kwargs)
        with _LOCK:
            _CACHE[key] = result
        return result

    return wrapper  # type: ignore[return-value]
