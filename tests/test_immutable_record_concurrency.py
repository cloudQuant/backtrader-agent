"""Real publish-boundary races for user-visible immutable records."""

import multiprocessing
from pathlib import Path
from threading import BrokenBarrierError
from typing import Any, Dict

import pytest

import backtrader_agent.canonical as canonical_module
from backtrader_agent.canonical import create_or_verify_bytes, create_or_verify_json, hash_object
from backtrader_agent.contracts import StrategySpec
from backtrader_agent.data import DatasetService
from backtrader_agent.errors import AgentError
from backtrader_agent.installer import AdapterInstaller
from backtrader_agent.roots import RootRegistry
from backtrader_agent.scaffold import ArtifactRenderer, load_product_artifact_record
from backtrader_agent.tokens import TokenAuthority

from helpers import data_spec, strategy_spec, write_price_csv


def _coordinate_target_link(destination: str, barrier):
    original_link = canonical_module.os.link
    crossed = False

    def coordinated_link(source, target):
        nonlocal crossed
        if not crossed and str(target) == destination:
            crossed = True
            try:
                barrier.wait(timeout=4)
            except BrokenBarrierError:
                pass
        return original_link(source, target)

    return original_link, coordinated_link


def _dataset_worker(
    state_text: str,
    spec: Dict[str, Any],
    destination: str,
    start_barrier,
    publish_barrier,
    outcomes,
) -> None:
    original_link, coordinated_link = _coordinate_target_link(destination, publish_barrier)
    canonical_module.os.link = coordinated_link
    try:
        start_barrier.wait(timeout=10)
        result = DatasetService(RootRegistry(Path(state_text)), Path(state_text)).register(spec)
        outcomes.put(("success", result["dataset_id"], result["manifest_hash"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - parent asserts exact worker outcomes
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        canonical_module.os.link = original_link


def _artifact_worker(
    state_text: str,
    raw_spec: Dict[str, Any],
    dataset: Dict[str, Any],
    destination: str,
    start_barrier,
    publish_barrier,
    outcomes,
) -> None:
    original_link, coordinated_link = _coordinate_target_link(destination, publish_barrier)
    canonical_module.os.link = coordinated_link
    try:
        start_barrier.wait(timeout=10)
        result = ArtifactRenderer(Path(state_text)).render(
            "session-immutable-race", StrategySpec.from_dict(raw_spec), dataset
        )
        outcomes.put(("success", result["artifact_hash"], result["_artifact_record_hash"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - parent asserts exact worker outcomes
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        canonical_module.os.link = original_link


def _bound_record_worker(
    state_text: str,
    destination: str,
    start_barrier,
    publish_barrier,
    outcomes,
) -> None:
    original_link, coordinated_link = _coordinate_target_link(destination, publish_barrier)
    canonical_module.os.link = coordinated_link
    try:
        start_barrier.wait(timeout=10)
        result = TokenAuthority(Path(state_text)).store_bound_record(
            "prepared-change",
            "session-immutable-race",
            "a" * 64,
            {"prepared_manifest_hash": "b" * 64},
        )
        outcomes.put(("success", result["record_hash"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - parent asserts exact worker outcomes
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        canonical_module.os.link = original_link


def _installer_worker(
    target_text: str,
    destination: str,
    start_barrier,
    publish_barrier,
    outcomes,
) -> None:
    original_link, coordinated_link = _coordinate_target_link(destination, publish_barrier)
    canonical_module.os.link = coordinated_link
    try:
        start_barrier.wait(timeout=10)
        result = AdapterInstaller().install(Path(target_text), "claude", apply=True)
        outcomes.put(("success", result["status"]))
    except AgentError as exc:
        outcomes.put(("agent-error", exc.code))
    except Exception as exc:  # pragma: no cover - parent asserts exact worker outcomes
        outcomes.put(("unexpected-error", type(exc).__name__))
    finally:
        canonical_module.os.link = original_link


def _run_race(target, args: tuple) -> list:
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(2)
    publish_barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(target=target, args=(*args, start_barrier, publish_barrier, outcomes))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    return [outcomes.get(timeout=5) for _ in processes]


def test_exact_create_or_verify_replays_only_identical_regular_files(tmp_path: Path) -> None:
    destination = tmp_path / "record.bin"
    assert create_or_verify_bytes(
        destination,
        b"expected",
        conflict_code="BTAG-TEST-CONFLICT",
        conflict_message="immutable bytes conflict",
    )
    assert not create_or_verify_bytes(
        destination,
        b"expected",
        conflict_code="BTAG-TEST-CONFLICT",
        conflict_message="immutable bytes conflict",
    )
    with pytest.raises(AgentError, match="BTAG-TEST-CONFLICT"):
        create_or_verify_bytes(
            destination,
            b"different",
            conflict_code="BTAG-TEST-CONFLICT",
            conflict_message="immutable bytes conflict",
        )

    link_target = tmp_path / "link-target.bin"
    link_target.write_bytes(b"expected")
    symlink = tmp_path / "record-link.bin"
    symlink.symlink_to(link_target)
    with pytest.raises(AgentError, match="BTAG-TEST-CONFLICT"):
        create_or_verify_bytes(
            symlink,
            b"expected",
            conflict_code="BTAG-TEST-CONFLICT",
            conflict_message="immutable bytes conflict",
        )

    json_path = tmp_path / "record.json"
    assert create_or_verify_json(
        json_path,
        {"value": "expected"},
        conflict_code="BTAG-TEST-CONFLICT",
        conflict_message="immutable JSON conflict",
    )
    assert not create_or_verify_json(
        json_path,
        {"value": "expected"},
        conflict_code="BTAG-TEST-CONFLICT",
        conflict_message="immutable JSON conflict",
    )


def test_same_dataset_register_race_replays_exact_cas_and_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    input_root = tmp_path / "input"
    workspace.mkdir()
    input_root.mkdir()
    write_price_csv(input_root / "prices.csv")
    state_root = workspace / ".backtrader-agent"
    roots = RootRegistry(state_root)
    roots.register("workspace", workspace, writable=True, kind="workspace")
    roots.register("input", input_root, writable=False, kind="dataset")
    spec = data_spec()
    inspected = DatasetService(roots, state_root).inspect(spec)
    digest = inspected["feeds"][0]["normalized_sha256"]
    cas_path = state_root / "data" / "sha256" / digest[:2] / f"{digest}.csv"

    outcomes = _run_race(_dataset_worker, (str(state_root), spec, str(cas_path)))

    assert [item[0] for item in outcomes] == ["success", "success"], outcomes
    registered = DatasetService(roots, state_root).register(spec)
    assert {item[1] for item in outcomes} == {registered["dataset_id"]}
    assert {item[2] for item in outcomes} == {registered["manifest_hash"]}
    assert cas_path.is_file()


def test_same_artifact_render_race_replays_exact_draft_and_provenance(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    dataset = {
        "dataset_id": "ds_" + "a" * 64,
        "manifest_hash": "a" * 64,
        "feeds": [{"name": "primary", "role": "execution", "columns": {"signal": "signal"}}],
    }
    raw_spec = strategy_spec(dataset["dataset_id"])
    spec = StrategySpec.from_dict(raw_spec)
    draft = state_root / "sessions" / "session-immutable-race" / "drafts"
    expected_revision = hash_object(
        {
            "spec_hash": spec.spec_hash,
            "dataset_hash": dataset["manifest_hash"],
            "renderer": "scaffold-v1",
        }
    )[:20]
    destination = draft / expected_revision / "config.yaml"
    TokenAuthority(state_root)._secret()

    outcomes = _run_race(
        _artifact_worker,
        (str(state_root), raw_spec, dataset, str(destination)),
    )

    assert [item[0] for item in outcomes] == ["success", "success"], outcomes
    assert len({item[1] for item in outcomes}) == 1
    assert len({item[2] for item in outcomes}) == 1
    artifact = ArtifactRenderer(state_root).render("session-immutable-race", spec, dataset)
    loaded = load_product_artifact_record(
        state_root,
        "session-immutable-race",
        artifact["artifact_hash"],
        TokenAuthority(state_root),
    )
    assert loaded["record_hash"] == artifact["_artifact_record_hash"]


def test_same_bound_record_race_replays_exact_signed_record(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    authority = TokenAuthority(state_root)
    authority._secret()
    destination = authority._bound_record_path(
        "prepared-change", "session-immutable-race", "a" * 64
    )

    outcomes = _run_race(_bound_record_worker, (str(state_root), str(destination)))

    assert [item[0] for item in outcomes] == ["success", "success"], outcomes
    assert len({item[1] for item in outcomes}) == 1
    loaded = authority.load_bound_record("prepared-change", "session-immutable-race", "a" * 64)
    assert loaded["record_hash"] == outcomes[0][1]


def test_same_installer_apply_race_replays_exact_files_and_manifest(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    destination = target / ".claude" / "agents" / "backtrader-agent.md"

    outcomes = _run_race(_installer_worker, (str(target), str(destination)))

    assert [item[0] for item in outcomes] == ["success", "success"], outcomes
    assert sorted(item[1] for item in outcomes) == ["installed", "unchanged"]
    assert destination.is_file()
    assert (target / ".backtrader-agent" / "installer" / "claude.json").is_file()
    assert AdapterInstaller().uninstall(target, "claude", apply=False)["status"] == "preview"
