"""R7 caching discipline: process-local memoization and manifest-level verification."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtrader_agent import catalog, caching, engines, runner
from backtrader_agent.errors import AgentError
from backtrader_agent.roots import RootRegistry


def _fake_engine_root(tmp_path: Path) -> Path:
    engine = tmp_path / "engine"
    (engine / "backtrader").mkdir(parents=True)
    (engine / "backtrader" / "__init__.py").write_text(
        "__version__ = '1.3.0'\n", encoding="utf-8"
    )
    (engine / "backtrader" / "version.py").write_text(
        "__version__ = '1.3.0'\n", encoding="utf-8"
    )
    return engine


def test_engine_tree_hash_computed_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _fake_engine_root(tmp_path)
    calls = []
    real = engines.sha256_bytes
    monkeypatch.setattr(
        engines,
        "sha256_bytes",
        lambda data: (calls.append(data) or real(data)),
    )
    first = engines._package_tree(engine / "backtrader")
    second = engines._package_tree(engine / "backtrader")
    assert first == second
    assert len(calls) == 2  # one hash per member; the second call is a cache hit


def test_engine_tree_hash_cache_does_not_mask_member_mutation(tmp_path: Path) -> None:
    engine = _fake_engine_root(tmp_path)
    member = engine / "backtrader" / "__init__.py"
    before = engines._package_tree(engine / "backtrader")
    member.write_text("__version__ = '1.4.0'  # mutated\n", encoding="utf-8")
    after = engines._package_tree(engine / "backtrader")
    assert before != after


def test_engine_tree_hash_cache_detects_mtime_restored_tamper(tmp_path: Path) -> None:
    engine = _fake_engine_root(tmp_path)
    member = engine / "backtrader" / "__init__.py"
    original = member.stat()
    before = engines._package_tree(engine / "backtrader")
    member.write_text("__version__ = '2.0.0'\n", encoding="utf-8")  # same size
    os.utime(member, ns=(original.st_atime_ns, original.st_mtime_ns))
    after = engines._package_tree(engine / "backtrader")
    assert before != after  # ctime cannot be restored by an unprivileged writer


def test_dataset_feed_hash_computed_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    feed = tmp_path / "feeds.csv"
    feed.write_bytes(b"timestamp,close\n2024-01-01,1.0\n")
    metadata = feed.stat()
    calls = []
    real = runner.sha256_bytes
    monkeypatch.setattr(
        runner,
        "sha256_bytes",
        lambda data: (calls.append(data) or real(data)),
    )
    first = runner._dataset_feed_sha256(
        feed, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns
    )
    second = runner._dataset_feed_sha256(
        feed, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns
    )
    assert first == second
    assert len(calls) == 1


def test_dataset_feed_hash_detects_mtime_restored_tamper(tmp_path: Path) -> None:
    feed = tmp_path / "feeds.csv"
    feed.write_bytes(b"timestamp,close\n2024-01-01,1.0\n")
    original = feed.stat()
    first = runner._dataset_feed_sha256(
        feed, original.st_size, original.st_mtime_ns, original.st_ctime_ns
    )
    feed.write_bytes(b"timestamp,cloze\n2024-01-01,9.9\n")  # same size
    os.utime(feed, ns=(original.st_atime_ns, original.st_mtime_ns))
    current = feed.stat()
    second = runner._dataset_feed_sha256(
        feed, current.st_size, current.st_mtime_ns, current.st_ctime_ns
    )
    assert first != second


def test_engine_probe_computed_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _fake_engine_root(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    attestation = json.dumps(
        {
            "path": str((engine / "backtrader" / "__init__.py").resolve()),
            "version": "1.3.0",
        },
        sort_keys=True,
    ).encode("utf-8")
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=0, stdout=attestation, stderr=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    first = runner._probe_engine(engine, state, "1.3.0")
    second = runner._probe_engine(engine, state, "1.3.0")
    assert first == second
    assert len(calls) == 1


def test_no_persistent_cache_for_security_hashes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    engine = _fake_engine_root(tmp_path)
    roots = RootRegistry(state)
    roots.register("engine", engine, writable=False, kind="engine")
    first = engines.inspect_engine(roots, "engine")
    second = engines.inspect_engine(roots, "engine")
    assert first["engine_hash"] == second["engine_hash"]
    assert not (state / "cache").exists()
    assert not list(tmp_path.rglob("*cache*"))


def test_packaged_catalog_verifies_whole_file_once_not_each_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashes = []
    real = catalog.sha256_bytes
    monkeypatch.setattr(
        catalog,
        "sha256_bytes",
        lambda data: (hashes.append(data) or real(data)),
    )
    loaded = catalog.SnapshotCatalog()
    assert loaded.manifest["entry_count"] > 1000
    assert len(hashes) == 1  # one whole-file SHA-256 against the distribution pin


def test_source_attached_catalog_retains_per_entry_verification(tmp_path: Path) -> None:
    functional = tmp_path / "functional"
    packages = tmp_path / "packages"
    output = tmp_path / "catalog.jsonl"
    test_dir = functional / "trend"
    package_dir = packages / "trend" / "0001_example"
    test_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    (test_dir / "test_0001_example.py").write_text(
        "import backtrader as bt\nclass Example(bt.Strategy):\n    pass\n",
        encoding="utf-8",
    )
    (package_dir / "strategy_example.py").write_text(
        "import backtrader as bt\nclass Example(bt.Strategy):\n    pass\n",
        encoding="utf-8",
    )
    (package_dir / "config.yaml").write_text("period: 5\n", encoding="utf-8")
    (package_dir / "run.py").write_text(
        "from strategy_example import Example\n", encoding="utf-8"
    )
    catalog.SnapshotCatalog.refresh_source_attached(
        functional, packages, output, require_verified_counts=False
    )
    lines = output.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["slug"] = entry["slug"] + "_tampered"
    lines[1] = json.dumps(entry, sort_keys=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        catalog.SnapshotCatalog(snapshot_path=output)
    assert error.value.code == "BTAG-CATALOG-INTEGRITY"


def test_packaged_corpus_pin_rejects_tampered_bytes() -> None:
    with pytest.raises(AgentError) as error:
        catalog._verify_packaged_snapshot_bytes(b"tampered corpus bytes")
    assert error.value.code == "BTAG-CATALOG-INTEGRITY"


def test_verify_snapshot_once_accepts_packaged_snapshot(tmp_path: Path) -> None:
    packaged = (
        Path(catalog.__file__).resolve().parent
        / "resources"
        / "catalog"
        / "corpus-v1.jsonl"
    )
    catalog.verify_snapshot_once(packaged)

    manifest = json.loads(packaged.read_text(encoding="utf-8").splitlines()[0])
    tampered = tmp_path / "tampered.jsonl"
    manifest["snapshot_hash"] = "0" * 64
    tampered.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        catalog.verify_snapshot_once(tampered)
    assert error.value.code == "BTAG-CATALOG-INTEGRITY"

    missing = tmp_path / "missing-manifest.jsonl"
    missing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        catalog.verify_snapshot_once(missing)
    assert error.value.code == "BTAG-CATALOG-INTEGRITY"


def test_memoized_is_process_local_and_never_caches_failures() -> None:
    calls = []

    @caching.memoized
    def flaky(value: str) -> str:
        calls.append(value)
        if value == "boom":
            raise ValueError("boom")
        return value * 2

    assert flaky("x") == "xx"
    assert flaky("x") == "xx"
    assert calls == ["x"]
    with pytest.raises(ValueError):
        flaky("boom")
    with pytest.raises(ValueError):
        flaky("boom")
    assert calls == ["x", "boom", "boom"]  # failures are retried, never cached


def test_memoized_distinguishes_container_forms_and_mixed_key_types() -> None:
    calls = []

    @caching.memoized
    def identify(value):
        calls.append(value)
        return "done"

    identify({"a": 1})
    identify([("a", 1)])  # list-of-pairs form must not collide with the dict form
    identify({1: "x", "a": 2})  # heterogeneous dict keys must not raise
    assert len(calls) == 3
