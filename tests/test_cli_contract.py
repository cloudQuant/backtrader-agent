import json
from backtrader_agent import cli


def test_success_envelope_wraps_result(capsys, tmp_path):
    state = tmp_path / "state"
    code = cli.main(["--state-root", str(state), "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["result"]["status"] == "ready"  # doctor 原输出移入 result


def test_failure_envelope_unchanged(capsys, tmp_path):
    code = cli.main(["--state-root", str(tmp_path / "s"), "report", "--run-id", "run-0" * 2])
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
