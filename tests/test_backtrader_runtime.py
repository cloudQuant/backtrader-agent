"""Tests for the CloudQuant Backtrader runtime-source policy."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtrader_agent import backtrader_runtime as runtime
from backtrader_agent import cli, doctor
from backtrader_agent.runner import ControlledRunner


class _Distribution:
    def __init__(self, direct_url: str, version: str = "1.2.0") -> None:
        self.version = version
        self._direct_url = direct_url
        self.metadata = {"Home-page": "https://github.com/cloudQuant/backtrader"}

    def read_text(self, name: str):
        if name == "direct_url.json":
            return self._direct_url
        return None


def _patch_installed_runtime(
    monkeypatch,
    *,
    direct_url: str,
    origin: Path,
) -> None:
    monkeypatch.setattr(
        runtime.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(origin)) if name == "backtrader" else None,
    )
    monkeypatch.setattr(
        runtime.importlib.metadata,
        "distribution",
        lambda name: _Distribution(direct_url) if name == "backtrader" else None,
    )


def test_cloudquant_direct_url_is_verified(monkeypatch, tmp_path: Path) -> None:
    _patch_installed_runtime(
        monkeypatch,
        direct_url=json.dumps({"url": "https://github.com/cloudQuant/backtrader.git"}),
        origin=tmp_path / "site-packages" / "backtrader" / "__init__.py",
    )

    status = runtime.inspect_backtrader_runtime()

    assert status["status"] == "verified"
    assert status["is_cloudquant_backtrader"] is True
    assert status["repository"] == runtime.CLOUDQUANT_BACKTRADER_REPOSITORY
    assert status["warning"] is None


def test_foreign_backtrader_is_warned_without_replacement(monkeypatch, tmp_path: Path) -> None:
    _patch_installed_runtime(
        monkeypatch,
        direct_url=json.dumps({"url": "https://github.com/mementum/backtrader.git"}),
        origin=tmp_path / "site-packages" / "backtrader" / "__init__.py",
    )
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    status = runtime.ensure_cloudquant_backtrader()

    assert status["status"] == "warning"
    assert status["is_cloudquant_backtrader"] is False
    assert "not verified" in status["warning"]
    assert calls == []


def test_engine_root_source_requires_cloudquant_git_evidence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        runtime,
        "_git_remote",
        lambda path: "https://github.com/mementum/backtrader.git",
    )

    status = runtime.inspect_backtrader_engine_root(tmp_path / "engine")

    assert status["status"] == "warning"
    assert status["is_cloudquant_backtrader"] is False
    assert status["repository"] == "https://github.com/mementum/backtrader"
    assert "not verified" in status["warning"]


def test_missing_backtrader_is_installed_with_the_current_interpreter(
    monkeypatch, tmp_path: Path
) -> None:
    installed = {"value": False}
    origin = tmp_path / "site-packages" / "backtrader" / "__init__.py"

    def find_spec(name: str):
        if name == "backtrader" and installed["value"]:
            return SimpleNamespace(origin=str(origin))
        return None

    monkeypatch.setattr(runtime.importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(
        runtime.importlib.metadata,
        "distribution",
        lambda name: _Distribution(
            json.dumps({"url": "https://github.com/cloudQuant/backtrader.git"})
        ),
    )
    calls = []

    def install(command, **kwargs):
        calls.append((command, kwargs))
        installed["value"] = True
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(runtime.subprocess, "run", install)

    status = runtime.ensure_cloudquant_backtrader()

    assert status["status"] == "verified"
    assert status["installed_during_check"] is True
    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                runtime.CLOUDQUANT_BACKTRADER_REQUIREMENT,
            ],
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "timeout": runtime.INSTALL_TIMEOUT_SECONDS,
                "check": False,
                "shell": False,
            },
        )
    ]


def test_doctor_exposes_backtrader_source_warning(monkeypatch, tmp_path: Path) -> None:
    warning = "Installed Backtrader is not verified as cloudQuant/backtrader."
    status = {
        "schema_version": "backtrader-runtime-v1",
        "status": "warning",
        "warning": warning,
    }
    monkeypatch.setattr(doctor, "inspect_backtrader_runtime", lambda: status)

    report = doctor.diagnose(product_root=tmp_path, state_root=tmp_path / "state")

    assert report["environment"]["backtrader"] == status
    assert warning in report["warnings"]


def test_cli_backtrader_check_emits_a_mismatch_warning(monkeypatch, capsys) -> None:
    warning = "Installed Backtrader is not verified as cloudQuant/backtrader."
    status = {
        "schema_version": "backtrader-runtime-v1",
        "status": "warning",
        "warning": warning,
    }
    monkeypatch.setattr(cli, "inspect_backtrader_runtime", lambda: status, raising=False)

    assert cli.main(["backtrader", "check"]) == 0

    captured = capsys.readouterr()
    assert warning in captured.err
    assert '"status": "warning"' in captured.out


def test_controlled_runner_warns_for_an_unverified_existing_backtrader(monkeypatch) -> None:
    warning = "Installed Backtrader is not verified as cloudQuant/backtrader."
    monkeypatch.setattr(
        "backtrader_agent.runner.ensure_cloudquant_backtrader",
        lambda: {"installed": True, "warning": warning},
        raising=False,
    )
    monkeypatch.setattr(
        "backtrader_agent.runner.missing_profile_dependencies",
        lambda profile: [],
    )

    with pytest.warns(RuntimeWarning, match="not verified"):
        ControlledRunner._require_profile_dependencies("python_bundle")


def test_controlled_runner_warns_for_an_unverified_engine_root(
    monkeypatch, tmp_path: Path
) -> None:
    engine_root = tmp_path / "engine"
    initializer = engine_root / "backtrader" / "__init__.py"
    initializer.parent.mkdir(parents=True)
    initializer.write_text("__version__ = 'test'\n", encoding="utf-8")
    descriptor = {
        "engine_hash": "e" * 64,
        "root_id": "engine",
        "version": "unknown",
        "version_file_sha256": "v" * 64,
        "package_tree_sha256": "p" * 64,
        "source": {
            "warning": "Registered Backtrader engine is not verified as cloudQuant/backtrader."
        },
    }

    class _Roots:
        @staticmethod
        def get_record(root_id: str):
            assert root_id == "engine"
            return {"path": str(engine_root)}

    probe = subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps({"path": str(initializer), "version": "test"}).encode("utf-8"),
        stderr=b"",
    )
    monkeypatch.setattr("backtrader_agent.runner.inspect_engine", lambda roots, root_id: descriptor)
    monkeypatch.setattr("backtrader_agent.runner.subprocess.run", lambda *args, **kwargs: probe)
    controlled = ControlledRunner(_Roots(), tmp_path / "state", None)

    with pytest.warns(RuntimeWarning, match="Registered Backtrader engine"):
        controlled._resolve_engine({"bindings": {"engine_root_id": "engine", "engine_hash": "e" * 64}})
