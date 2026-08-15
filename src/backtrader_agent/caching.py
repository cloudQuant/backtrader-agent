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

_CACHE: Dict[Tuple[str, str, Any, Any], Any] = {}
_LOCK = threading.Lock()
_UNSET = object()


def _freeze(value: Any) -> Any:
    """Recursively convert a value into a hashable, equality-stable key fragment.

    Every form is type-tagged so distinct container shapes can never collide
    (a dict and a list of the same pairs freeze differently). Dict pairs are
    ordered by a type-agnostic sort key so heterogeneous key types cannot
    raise ``TypeError`` during sorting.
    """
    try:
        hash(value)
    except TypeError:
        pass
    else:
        return ("value", value)
    if isinstance(value, dict):
        return (
            "dict",
            tuple(
                sorted(
                    ((_freeze(key), _freeze(item)) for key, item in value.items()),
                    key=lambda pair: repr(pair[0]),
                )
            ),
        )
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze(item) for item in value))
    if isinstance(value, set):
        return (
            "set",
            tuple(sorted((_freeze(item) for item in value), key=repr)),
        )
    return ("raw", repr(value))


def memoized(fn: F) -> F:
    """Wrap ``fn`` with a thread-safe, process-local, never-persisted memo.

    The cache key is ``(fn.__module__, fn.__qualname__, frozen args, frozen
    kwargs)`` so same-named functions in different modules cannot collide.
    Only successful calls populate the cache; exceptions always propagate so
    failures are retried on the next call instead of replayed. Cached values
    are returned as-is, so callers must treat them as read-only.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = (fn.__module__, fn.__qualname__, _freeze(args), _freeze(kwargs))
        with _LOCK:
            cached = _CACHE.get(key, _UNSET)
        if cached is not _UNSET:
            return cached
        result = fn(*args, **kwargs)
        with _LOCK:
            _CACHE[key] = result
        return result

    return wrapper  # type: ignore[return-value]
