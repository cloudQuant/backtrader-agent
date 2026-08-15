import json
from pathlib import Path

import pytest

from backtrader_agent import cli

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "backtrader_agent"


def test_success_envelope_wraps_result(capsys, tmp_path):
    state = tmp_path / "state"
    code = cli.main(["--state-root", str(state), "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["result"]["status"] == "ready"  # doctor 原输出移入 result


def test_failure_envelope_unchanged(capsys, tmp_path):
    code = cli.main(
        ["--state-root", str(tmp_path / "s"), "report", "--run-id", "run-0" * 2]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code != 0
    assert payload["status"] == "failed"
    assert payload["diagnostic"]["code"].startswith("BTAG-")


def test_exit_code_domain_error(monkeypatch, capsys):
    def boom(args):
        raise cli.AgentError("BTAG-TEST", "boom")

    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["diagnostic"]["code"] == "BTAG-TEST"


def test_exit_code_io_error(monkeypatch, capsys):
    def boom(args):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic"]["code"] == "BTAG-CLI-IO"
    assert payload["diagnostic"]["severity"] == "error"


def test_exit_code_input_error(monkeypatch, capsys):
    def boom(args):
        raise ValueError("bad json")

    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["diagnostic"]["code"] == "BTAG-CLI-INPUT"


def test_json_load_inline_and_file_equivalent(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert cli._json_load('{"a": 1}') == {"a": 1}
    assert cli._json_load("@" + str(path)) == {"a": 1}
    assert cli._json_load(str(path)) == {"a": 1}


def test_json_load_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        cli._json_load(str(tmp_path / "nope.json"))


def test_action_schema_matches_packaged_resource():
    parser = cli.build_parser()
    schema = cli.build_action_schema(parser)
    packaged = json.loads(
        (PACKAGE_ROOT / "resources" / "actions-v1.json").read_text("utf-8")
    )
    assert schema == packaged


def test_action_schema_covers_required_params():
    schema = cli.build_action_schema(cli.build_parser())
    register = schema["actions"]["data register"]["params"]
    by_name = {p["name"] for p in register}
    assert "spec" in by_name and "session_id" in by_name
    assert all(p["required"] for p in register if p["name"] in {"spec", "session_id"})


def test_actions_command_emits_schema(capsys, tmp_path):
    code = cli.main(["--state-root", str(tmp_path / "s"), "actions", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["result"]["schema_version"] == "actions-v1"
