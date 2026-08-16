import json
import re

from backtrader_agent import cli
from backtrader_agent.canonical import sha256_bytes

EXPECTED_PAYLOAD_SHA256 = (
    "bde367eeb291bf1cdeaeb26d2b9ad84ac3f0dc9d2f61cae2fbc002018c481aaf"
)


def test_payload_content_hash_pinned():
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    assert sha256_bytes(content.encode("utf-8")) == EXPECTED_PAYLOAD_SHA256


def test_payload_has_version():
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    assert re.search(r'^version:\s*"13\.0\.4"', content, re.M)


def test_payload_menu_rows_point_to_real_commands(capsys, tmp_path):
    cli.main(["--state-root", str(tmp_path / "s"), "actions", "--json"])
    schema = json.loads(capsys.readouterr().out)["result"]["actions"]
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    # 提取菜单表路由列中的命令词,断言每个都在 schema 中
    for cmd in re.findall(r"`([a-z][a-z-]+)`", content):
        top = cmd.split()[0]
        assert top in schema
