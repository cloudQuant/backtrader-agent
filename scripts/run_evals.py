"""Run the deterministic scripted-host eval tasks (R9, the default CI gate).

Scans ``tests/evals/tasks/*.json``, executes each task against the CLI
through the harness, prints a ``{"passed", "failed", "total"}`` summary, and
exits 1 when any task fails.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = PROJECT_ROOT / "tests" / "evals" / "tasks"
# Import the harness as ``evals.harness`` (tests/ on sys.path) rather than
# ``tests.evals.harness``: a site-packages package named ``tests`` (present in
# some Python distributions) shadows the project's tests/ directory for the
# dotted form.
if str(PROJECT_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from evals.harness import run_task  # noqa: E402


def _load_tasks(tasks_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    tasks: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("error: cannot parse {}: {}".format(path, exc), file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(task, dict):
            print("error: {} must contain a JSON object".format(path), file=sys.stderr)
            raise SystemExit(2)
        tasks.append((path, task))
    return tasks


def _run_task(task: Dict[str, Any], task_id: str) -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix="backtrader-agent-eval-") as name:
            result = run_task(task, Path(name) / "state", {})
    except Exception as exc:
        # A malformed task or an unresolvable environment (e.g. missing
        # Backtrader engine root) must fail the task, not take down the run.
        print("FAIL {}".format(task_id))
        print("  error: {}".format(exc))
        return False
    if result.passed:
        print("PASS {}".format(task_id))
        return True
    print("FAIL {}".format(task_id))
    for step_index, step in enumerate(result.steps):
        for check in step.checks:
            if not check.passed:
                print("  step {} {}: {}".format(step_index, check.name, check.detail))
        if step.stdout_tail:
            print("  step {} stdout: {}".format(step_index, step.stdout_tail))
        if step.stderr_tail:
            print("  step {} stderr: {}".format(step_index, step.stderr_tail))
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tasks-dir",
        default=str(TASKS_DIR),
        help="directory scanned for task JSONs (default: tests/evals/tasks)",
    )
    parser.add_argument("task_ids", nargs="*", help="only run matching task ids")
    args = parser.parse_args(argv)

    tasks = _load_tasks(Path(args.tasks_dir))
    if args.task_ids:
        tasks = [
            (path, task) for path, task in tasks if task.get("task_id") in args.task_ids
        ]

    passed: List[str] = []
    failed: List[str] = []
    for path, task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            print("error: {} has no string task_id".format(path), file=sys.stderr)
            return 2
        if _run_task(task, task_id):
            passed.append(task_id)
        else:
            failed.append(task_id)
    print(
        json.dumps(
            {"passed": len(passed), "failed": len(failed), "total": len(tasks)},
            sort_keys=True,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
