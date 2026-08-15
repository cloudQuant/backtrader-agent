# Iteration 001 Trusted Execution and Release Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace caller-supplied execution identity hashes with product-derived, revalidated engine and environment evidence while making runnable dependency and minimum-Python promises installable and continuously tested.

**Architecture:** Validation derives a versioned engine descriptor from a registered readonly root and a versioned interpreter descriptor from the actual agent process. Signed tokens bind those descriptors; the runner rechecks both before token consumption and child launch. Optional extras describe runtime capabilities without imposing third-party imports on the base package.

**Tech Stack:** Python 3.8+, standard library hashing/path APIs, setuptools PEP 621 metadata, pytest, GitHub Actions, Backtrader and pandas supplied only by optional extras.

## Global Constraints

- Preserve offline-first behavior, no live trading/network data, no candidate import in the host process, no dynamic execution, and no `shell=True`.
- Maintain Python `>=3.8`; do not add a new mandatory runtime dependency.
- Keep change/run approvals distinct and do not consume a run token before engine, environment and dependency preflight pass.
- MIT is the maintainer-confirmed license: preserve the existing `LICENSE` text, align `pyproject.toml` metadata to MIT, and do not change the release version.
- Regenerate both distribution manifests after every tracked file is final.

---

### Task 1: Add versioned engine-tree and execution-environment evidence

**Files:**
- Modify: `src/backtrader_agent/engines.py`
- Modify: `tests/test_runner_installer_audit.py`
- Test: `tests/test_runner_installer_audit.py`

**Interfaces:**
- Produces `inspect_engine(roots, root_id) -> Dict[str, Any]` with `package_tree_sha256`, `package_file_count`, and `engine_hash`.
- Produces `inspect_execution_environment() -> Dict[str, Any]` with `environment_hash`.
- Adds test helper `_registered_copied_engine(tmp_path: Path) -> Tuple[RootRegistry, Path]`, which copies `BACKTRADER_ROOT/backtrader` to `tmp_path/engine/backtrader`, registers root ID `engine` as read-only, and returns the registry plus copied engine root.

- [x] **Step 1: Write failing descriptor tests**

```python
def test_inspect_engine_hashes_all_regular_package_members(tmp_path: Path) -> None:
    roots, engine_root = _registered_copied_engine(tmp_path)
    before = inspect_engine(roots, "engine")
    (engine_root / "backtrader" / "cerebro.py").write_text("changed", encoding="utf-8")
    after = inspect_engine(roots, "engine")
    assert before["package_tree_sha256"] != after["package_tree_sha256"]
    assert before["engine_hash"] != after["engine_hash"]
```

- [x] **Step 2: Verify the tests fail against the v1 descriptor**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_runner_installer_audit.py -k engine_tree -q -p no:cacheprovider`

Expected: FAIL because `package_tree_sha256` is absent.

- [x] **Step 3: Implement deterministic tree hashing and environment inspection**

```python
def inspect_execution_environment() -> Dict[str, Any]:
    descriptor = {
        "schema_version": "execution-environment-v1",
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    descriptor["environment_hash"] = hash_object(descriptor)
    return descriptor
```

Use a sorted `{relative_path: sha256}` map for regular non-cache files and reject symbolic links or members outside `root/backtrader`.

- [x] **Step 4: Run descriptor tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_runner_installer_audit.py -k 'engine or environment' -q -p no:cacheprovider`

Expected: PASS.

### Task 2: Bind CLI validation and tokens to derived descriptors

**Files:**
- Modify: `src/backtrader_agent/cli.py`
- Modify: `src/backtrader_agent/tokens.py`
- Modify: `tests/test_cli_workflow.py`
- Modify: `tests/test_tokens_changes_sessions.py`
- Test: `tests/test_cli_workflow.py`

**Interfaces:**
- Consumes `inspect_engine()` and `inspect_execution_environment()` from Task 1.
- Requires `engine_root_id` in every executable validation token.

- [x] **Step 1: Write failing CLI regression tests**

```python
def test_validate_rejects_raw_engine_and_environment_hash_flags() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["validate", "--engine-hash", "x", "--environment-hash", "y"])
```

- [x] **Step 2: Replace raw CLI inputs with root-derived bindings**

```python
validate.add_argument("--engine-root-id", required=True)
engine = inspect_engine(roots, args.engine_root_id)
environment = inspect_execution_environment()
engine_bindings = {
    "engine_hash": engine["engine_hash"],
    "engine_root_id": engine["root_id"],
    "environment_hash": environment["environment_hash"],
}
```

Add `engine_root_id` to `REQUIRED_BINDINGS["validation"]`, and change the CLI workflow fixture to register an immutable engine root before validation.

- [x] **Step 3: Run the updated workflow and token tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_cli_workflow.py tests/test_tokens_changes_sessions.py -q -p no:cacheprovider`

Expected: PASS with a root-derived validation token.

### Task 3: Revalidate preconditions before run-token consumption

**Files:**
- Modify: `src/backtrader_agent/runner.py`
- Modify: `src/backtrader_agent/doctor.py`
- Modify: `tests/test_runner_installer_audit.py`
- Test: `tests/test_runner_installer_audit.py`

**Interfaces:**
- Consumes Task 1 descriptors and Task 2 token bindings.
- Produces `BTAG-ENGINE-HASH`, `BTAG-ENVIRONMENT-HASH`, and `execution_ready` diagnostics.

- [x] **Step 1: Write failing pre-consumption tests**

```python
with pytest.raises(AgentError, match="BTAG-ENGINE-HASH"):
    runner.run(applied, dataset, validation_token, run_token, mode="runonce", idempotency_key="mutated")
authority.require_issued(run_token)
assert not list((state / "runs").rglob("run-result.json"))
```

- [x] **Step 2: Implement engine/environment/dependency preflight before `consume`**

```python
engine_root, engine_descriptor = self._resolve_engine(validation_token)
self._verify_execution_environment(validation_token)
self._require_profile_dependencies(applied["profile"])
self.authority.consume(run_token, effect_id=effect_id)
```

`_verify_execution_environment` must recompute the descriptor and use `hmac.compare_digest` or equivalent exact comparison for hashes. Dependency failures list only profile and missing module names.

- [x] **Step 3: Add doctor execution readiness**

```python
report["execution_ready"] = bool(valid_readonly_engine and not profile_missing["python_bundle"])
report["execution_profiles"] = profile_status
```

- [x] **Step 4: Run runner/doctor tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_runner_installer_audit.py tests/test_run_resume.py -q -p no:cacheprovider`

Expected: PASS; mutation and environment mismatch leave tokens unused, valid root-bound runs succeed.

### Task 4: Publish accurate optional dependencies and consumer tests

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_distribution_contracts.py`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Test: `tests/test_distribution_contracts.py`

**Interfaces:**
- Provides extras `backtest`, `single-test`, and `test`.
- Keeps `[project].dependencies = []`.

- [x] **Step 1: Write failing wheel metadata assertions**

```python
metadata = wheel.read("backtrader_agent-0.1.0.dist-info/METADATA").decode("utf-8")
assert "Provides-Extra: backtest" in metadata
assert "Requires-Dist: backtrader" in metadata
assert "Requires-Dist: pandas" in metadata
```

- [x] **Step 2: Add PEP 621 extras and update installation text**

```toml
[project.optional-dependencies]
backtest = ["backtrader>=1.9.78.123", "pandas>=1.0"]
single-test = ["pytest>=7"]
test = ["backtrader>=1.9.78.123", "pandas>=1.0", "pytest>=7", "jsonschema>=4", "build>=1", "setuptools>=68", "wheel>=0.41"]
```

README and CONTRIBUTING must show `python -m pip install '.[test]'` for contributors and `backtrader-agent[backtest]` for users who run a strategy.

- [x] **Step 3: Run wheel metadata tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_distribution_contracts.py -q -p no:cacheprovider`

Expected: PASS with base package still having no mandatory runtime third-party dependencies.

### Task 5: Align CI and executable documentation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `examples/README.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: `tests/test_distribution_contracts.py`

**Interfaces:**
- CI validates 3.8 and 3.12 as supported endpoints.
- Example creates a session before registering data.

- [x] **Step 1: Write assertion or smoke coverage for the example session order**

```python
example = (PRODUCT_ROOT / "examples/README.md").read_text(encoding="utf-8")
assert example.index("session create --session-id session-001") < example.index("data register")
```

- [x] **Step 2: Replace CI hand-installed package list**

```yaml
matrix:
  python-version: ["3.8", "3.9", "3.11", "3.12"]
run: python -m pip install '.[test]'
```

Run `scripts/run_acceptance.py` in a dedicated Python 3.12 job after the matrix test job succeeds.

- [x] **Step 3: Update walkthrough and changelog**

Add `session create` before `data register`, document root-derived execution proof, and record the release-contract changes under `[Unreleased]`.

- [x] **Step 4: Run focused docs/CI tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/test_distribution_contracts.py tests/test_cli_workflow.py -q -p no:cacheprovider`

Expected: PASS.

### Task 6: Regenerate manifests and execute the complete acceptance matrix

**Files:**
- Modify: `manifest.json`
- Modify: `src/backtrader_agent/resources/distribution-manifest.json`
- Test: all repository gates

**Interfaces:**
- Consumes the completed source, docs, tests and CI configuration from Tasks 1–5.
- Produces exact root/package manifests and machine-readable acceptance evidence.

- [x] **Step 1: Regenerate manifests**

Run: `/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python scripts/build_manifest.py`

Expected: root and package manifests report final file counts with no manual edits.

- [x] **Step 2: Run full local test and static gates**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests -q -p no:cacheprovider && /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python scripts/audit_independence.py && /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python scripts/doctor.py`

Expected: all tests pass, independence is passed, doctor reports valid JSON and explicit readiness.

- [x] **Step 3: Run version endpoints and complete acceptance**

Run: `PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n py38 python -m pytest tests -q -p no:cacheprovider && PYTHONDONTWRITEBYTECODE=1 /Users/yunjinqi/opt/anaconda3/bin/conda run -n py312 python -m pytest tests -q -p no:cacheprovider && /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python scripts/run_acceptance.py`

Expected: 3.8 and 3.12 suites pass; acceptance report is `status=passed`, has 14 executed cells, two modes per cell, and passed repair/crash-resume gates.

- [x] **Step 4: Verify the final diff and document outcomes**

Run: `git diff --check && git status --short && git diff -- manifest.json src/backtrader_agent/resources/distribution-manifest.json`

Expected: no whitespace errors, only iteration-scoped files changed, manifests have no drift. Update checkboxes and `acceptance.md` with exact command outcomes before beginning Iteration 002.
