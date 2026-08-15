"""R7 caching discipline: process-local memoization and manifest-level verification."""

import json
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
    first = runner._dataset_feed_sha256(feed, metadata.st_size, metadata.st_mtime_ns)
    second = runner._dataset_feed_sha256(feed, metadata.st_size, metadata.st_mtime_ns)
    assert first == second
    assert len(calls) == 1


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


def test_catalog_verifies_snapshot_hash_not_each_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = []
    real = catalog.hash_object
    monkeypatch.setattr(
        catalog,
        "hash_object",
        lambda value: (hits.append(value) or real(value)),
    )
    loaded = catalog.SnapshotCatalog()
    assert loaded.manifest["entry_count"] > 1000
    assert len(hits) == 1  # single manifest-level snapshot_hash comparison
    assert "snapshot_hash" not in hits[0]
    assert hits[0]["schema_version"] == "corpus-manifest-v1"


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
