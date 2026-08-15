"""Deterministic scripted-host eval harness (R9).

``tests/evals/harness.py`` drives the installed ``backtrader-agent`` CLI as a
subprocess; ``tests/evals/graders.py`` provides the deterministic graders
(exit code, envelope, JSON dot-path equality, sha256, file existence). Task
definitions live in ``tests/evals/tasks/*.json`` and are executed by
``scripts/run_evals.py``.
"""
