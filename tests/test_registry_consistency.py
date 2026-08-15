"""Consistency tests for the single-source archetype and adapter registries (R6)."""

import re

import pytest

from backtrader_agent import adapters, archetypes, catalog, contracts, data, scaffold
from backtrader_agent.contracts import DatasetManifest
from backtrader_agent.errors import AgentError


def test_archetype_registry_is_single_source() -> None:
    assert len(archetypes.ARCHETYPE_IDS) == 7
    assert contracts.ARCHETYPES == archetypes.ARCHETYPE_IDS
    assert set(scaffold.ARCHETYPE_CODE) == archetypes.ARCHETYPE_IDS
    assert set(catalog.ARCHETYPES) == archetypes.ARCHETYPE_IDS


def test_archetype_templates_and_params_live_only_in_the_registry() -> None:
    for name, spec in archetypes.ARCHETYPE_SPECS.items():
        assert spec.contract_value == name
        assert scaffold.ARCHETYPE_CODE[name] == spec.template
        referenced = {
            match.group(1)
            for body in spec.template
            for match in re.finditer(r"self\.p\.([a-z_]+)", body)
        }
        assert referenced == set(spec.allowed_params)


def test_adapter_registry_is_single_source() -> None:
    assert len(adapters.ADAPTER_FORMATS) == 6
    assert data.ALLOWED_FORMATS == adapters.ADAPTER_FORMATS
    assert (
        set(adapters.CSV_FORMATS) | set(adapters.PANDAS_FORMATS)
        == adapters.ADAPTER_FORMATS
    )
    assert not (set(adapters.CSV_FORMATS) & set(adapters.PANDAS_FORMATS))
    for name, spec in adapters.ADAPTER_SPECS.items():
        assert spec.format == name
        assert data.DEFAULT_COLUMN_NAMES[name] == dict(spec.default_columns)


def test_dataset_manifest_allowlist_derives_from_adapter_registry() -> None:
    for name in adapters.ADAPTER_FORMATS:
        DatasetManifest.from_dict(
            {"schema_version": "dataset-manifest-v1", "feeds": [{"format": name}]}
        )
    with pytest.raises(AgentError) as exc_info:
        DatasetManifest.from_dict(
            {
                "schema_version": "dataset-manifest-v1",
                "feeds": [{"format": "canonical_csv_v1"}],
            }
        )
    assert "BTAG-DATASET-FORMAT" in str(exc_info.value)
