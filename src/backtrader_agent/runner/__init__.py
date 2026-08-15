"""Fixed-profile child-process runner. Candidate modules are never host-imported."""

import subprocess

from ..backtrader_runtime import ensure_cloudquant_backtrader
from ..canonical import sha256_bytes
from ..engines import inspect_engine
from .execute import ControlledRunner, _dataset_feed_sha256
from .profiles import PROFILE_DEPENDENCIES, _probe_engine, missing_profile_dependencies
from .reports import list_runs

__all__ = [
    # Public API: ``from backtrader_agent.runner import ControlledRunner, list_runs``
    # and ``from .runner import ...`` call sites keep working unchanged.
    "ControlledRunner",
    "list_runs",
    # Compat surface referenced by sibling modules, tests, and monkeypatch
    # targets through the ``backtrader_agent.runner.*`` namespace.
    "PROFILE_DEPENDENCIES",
    "missing_profile_dependencies",
    "ensure_cloudquant_backtrader",
    "inspect_engine",
    "sha256_bytes",
    "_probe_engine",
    "_dataset_feed_sha256",
    "subprocess",
]
