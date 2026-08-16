"""Typed core contracts with deterministic hashes."""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .adapters import ADAPTER_FORMATS
from .archetypes import ARCHETYPE_IDS
from .canonical import hash_object
from .errors import AgentError

ARCHETYPES = set(ARCHETYPE_IDS)
PROFILES = {"single_test", "python_bundle"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATASET_ID_RE = re.compile(r"^ds_[0-9a-f]{64}$")
ARCHETYPE_ALIASES = {
    "single_indicator": "single_data_indicator",
    "multi_indicator": "multi_indicator_system",
    "multi_asset": "multi_asset_allocation",
}
TIMER_WHEN_VALUES = frozenset({"session", "cheat", "both"})
TIMER_CALLBACKS = frozenset({"notify_timer", "check_rebalance"})


def _require_text(value: Dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise AgentError(
            "BTAG-SPEC-REQUIRED", f"StrategySpec field '{field}' is required"
        )
    return item.strip()


def _finite_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _normalize_sizing(value: Any) -> Optional[Dict[str, Any]]:
    """Validate the R24 sizing block: {method: fixed|percent, fixed_size|percent}.

    Absent or null sizing keeps Backtrader's default sizer (no addsizer
    assembly), so specs without a sizing block behave exactly as before.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise AgentError("BTAG-SPEC-SIZING", "sizing must be an object or null")
    method = value.get("method")
    if method == "fixed":
        if set(value) != {"method", "fixed_size"} or not _finite_positive_number(
            value.get("fixed_size")
        ):
            raise AgentError(
                "BTAG-SPEC-SIZING",
                "sizing method 'fixed' requires a positive numeric fixed_size",
            )
        return {"method": "fixed", "fixed_size": value["fixed_size"]}
    if method == "percent":
        percent = value.get("percent")
        if (
            set(value) != {"method", "percent"}
            or not _finite_positive_number(percent)
            or percent > 100
        ):
            raise AgentError(
                "BTAG-SPEC-SIZING",
                "sizing method 'percent' requires a numeric percent in (0, 100]",
            )
        return {"method": "percent", "percent": percent}
    raise AgentError("BTAG-SPEC-SIZING", "sizing method must be 'fixed' or 'percent'")


def _normalize_timers(value: Any) -> Optional[List[Dict[str, str]]]:
    """Validate the R26 timers block: [{when: session|cheat|both, callback}].

    Absent or null keeps timers off, so specs without the block behave
    exactly as before. ``callback`` must be one of the fixed,
    renderer-supported strategy hooks (notify_timer, check_rebalance).
    """

    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 8:
        raise AgentError(
            "BTAG-SPEC-TIMERS",
            "timers must be a non-empty array of at most 8 entries, or null",
        )
    normalized = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"when", "callback"}:
            raise AgentError(
                "BTAG-SPEC-TIMERS",
                "timer descriptors require exactly 'when' and 'callback'",
            )
        when = entry.get("when")
        callback = entry.get("callback")
        if when not in TIMER_WHEN_VALUES:
            raise AgentError(
                "BTAG-SPEC-TIMERS", "timer 'when' must be session, cheat, or both"
            )
        if not isinstance(callback, str) or callback not in TIMER_CALLBACKS:
            raise AgentError(
                "BTAG-SPEC-TIMERS",
                "timer callback must be an allowlisted strategy method name",
            )
        normalized.append({"when": when, "callback": callback})
    return normalized


def _normalize_cheat(value: Any) -> Optional[Dict[str, bool]]:
    """Validate the R26 cheat block: {on_open|on_close: bool}, default off.

    Absent or null keeps cheat execution off. The normalized value always
    carries both flags so the renderer and spec hash stay deterministic.
    """

    if value is None:
        return None
    if not isinstance(value, dict) or not value or set(value) - {"on_open", "on_close"}:
        raise AgentError(
            "BTAG-SPEC-CHEAT",
            "cheat must be an object with only on_open and on_close flags",
        )
    normalized = {"on_open": False, "on_close": False}
    for key in ("on_open", "on_close"):
        item = value.get(key)
        if item is not None and not isinstance(item, bool):
            raise AgentError(
                "BTAG-SPEC-CHEAT", "cheat on_open and on_close must be booleans"
            )
        if item:
            normalized[key] = True
    return normalized


@dataclass(frozen=True)
class StrategySpec:
    value: Dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "StrategySpec":
        version = raw.get("spec_version", raw.get("schema_version"))
        if version != "strategy-spec-v1":
            raise AgentError(
                "BTAG-SPEC-VERSION", "StrategySpec must use strategy-spec-v1"
            )
        source = dict(raw)
        archetype = ARCHETYPE_ALIASES.get(
            source.get("archetype"), source.get("archetype")
        )
        output_profile = source.get("output_profile", source.get("profile"))
        name = _require_text(source, "name")
        slug = _require_text(source, "slug").replace("_", "-")
        category = _require_text(source, "category")
        dataset_id = _require_text(source, "dataset_id")
        if not SLUG_RE.fullmatch(slug) or len(slug) > 120:
            raise AgentError("BTAG-SPEC-SLUG", "slug must be lowercase kebab-case")
        if not DATASET_ID_RE.fullmatch(dataset_id):
            raise AgentError(
                "BTAG-SPEC-DATASET",
                "dataset_id must be ds_ followed by the full 64-hex semantic hash",
            )
        if archetype not in ARCHETYPES:
            raise AgentError(
                "BTAG-SPEC-ARCHETYPE", "archetype is not one of the seven P0 values"
            )
        if output_profile not in PROFILES:
            raise AgentError(
                "BTAG-SPEC-PROFILE",
                "output_profile must be single_test or python_bundle",
            )
        raw_feeds = source.get("feeds")
        if not isinstance(raw_feeds, list) or not raw_feeds:
            raise AgentError("BTAG-SPEC-FEEDS", "at least one feed is required")
        feeds = []
        for index, feed in enumerate(raw_feeds):
            if not isinstance(feed, dict):
                raise AgentError("BTAG-SPEC-FEEDS", "feed descriptors must be objects")
            feed_name = feed.get("name", f"data{index}")
            role = feed.get("role", "execution" if index == 0 else "signal")
            lines = feed.get(
                "lines", ["open", "high", "low", "close", "volume", "openinterest"]
            )
            if (
                not isinstance(feed_name, str)
                or not feed_name.isidentifier()
                or role
                not in {"execution", "signal", "benchmark", "hedge", "cash_proxy"}
                or not isinstance(lines, list)
                or not lines
            ):
                raise AgentError("BTAG-SPEC-FEEDS", "feed descriptor is invalid")
            feeds.append(
                {
                    "name": feed_name,
                    "role": role,
                    "symbol": str(feed.get("symbol", feed_name)),
                    "timeframe": str(feed.get("timeframe", "manifest")),
                    "lines": sorted({str(line) for line in lines}),
                }
            )
        sizing = _normalize_sizing(source.get("sizing"))
        timers = _normalize_timers(source.get("timers"))
        cheat = _normalize_cheat(source.get("cheat"))
        risk = source.get("risk")
        if not isinstance(risk, dict) or not risk:
            raise AgentError("BTAG-SPEC-RISK", "risk rules must be explicit")
        questions = source.get("open_questions", [])
        if questions:
            raise AgentError(
                "BTAG-SPEC-OPEN", "open questions must be resolved before rendering"
            )
        raw_parameters = source.get("parameters", [])
        if isinstance(raw_parameters, dict):
            raw_parameters = [
                (
                    {
                        "name": parameter_name,
                        "type": descriptor.get("type", "str"),
                        "default": descriptor.get("default"),
                        **(
                            {"minimum": descriptor["minimum"]}
                            if "minimum" in descriptor
                            else {}
                        ),
                        **(
                            {"maximum": descriptor["maximum"]}
                            if "maximum" in descriptor
                            else {}
                        ),
                    }
                    if isinstance(descriptor, dict)
                    else {
                        "name": parameter_name,
                        "type": (
                            "bool"
                            if isinstance(descriptor, bool)
                            else (
                                "int"
                                if isinstance(descriptor, int)
                                else "float" if isinstance(descriptor, float) else "str"
                            )
                        ),
                        "default": descriptor,
                    }
                )
                for parameter_name, descriptor in raw_parameters.items()
            ]
        if not isinstance(raw_parameters, list) or len(raw_parameters) > 64:
            raise AgentError(
                "BTAG-SPEC-PARAMETERS", "parameters must be a bounded array"
            )
        parameters = []
        parameter_names = set()
        for parameter in raw_parameters:
            if not isinstance(parameter, dict):
                raise AgentError(
                    "BTAG-SPEC-PARAMETERS", "parameter descriptor is invalid"
                )
            descriptor = dict(parameter)
            descriptor["type"] = {
                "integer": "int",
                "number": "float",
                "boolean": "bool",
            }.get(descriptor.get("type"), descriptor.get("type"))
            parameter_name = descriptor.get("name")
            if (
                not isinstance(parameter_name, str)
                or not parameter_name.isidentifier()
                or parameter_name in parameter_names
                or descriptor.get("type") not in {"int", "float", "bool", "str"}
                or "default" not in descriptor
            ):
                raise AgentError(
                    "BTAG-SPEC-PARAMETERS", "parameter descriptor is invalid"
                )
            parameters.append(
                {
                    key: descriptor[key]
                    for key in ("name", "type", "default", "minimum", "maximum")
                    if key in descriptor
                }
            )
            parameter_names.add(parameter_name)

        def rules(value: Any, default: str) -> Dict[str, Any]:
            if isinstance(value, dict) and isinstance(value.get("rule_names"), list):
                names = value["rule_names"]
            elif isinstance(value, str):
                names = [default]
            else:
                names = [default]
            if not names or any(
                not isinstance(item, str) or not item for item in names
            ):
                raise AgentError("BTAG-SPEC-RULES", "rule references are invalid")
            return {"rule_names": sorted(set(names))}

        run_modes = source.get(
            "run_modes", source.get("execution_modes", ["runonce", "runnext"])
        )
        if run_modes != ["runonce", "runnext"]:
            raise AgentError(
                "BTAG-SPEC-MODES", "run_modes must be exactly runonce then runnext"
            )
        if source.get("allowed_imports", ["backtrader"]) not in (
            ["backtrader"],
            ["backtrader", "json", "os", "math"],
        ):
            raise AgentError(
                "BTAG-SPEC-IMPORTS", "allowed_imports exceeds the P0 allowlist"
            )
        extensions = source.get("extensions", {})
        extension_config = (
            extensions.get("backtrader_agent", {})
            if isinstance(extensions, dict)
            else {}
        )
        analyzers = source.get(
            "analyzers",
            extension_config.get("analyzers", ["TradeAnalyzer", "DrawDown"]),
        )
        if not isinstance(analyzers, list) or any(
            not isinstance(analyzer, str) or not analyzer for analyzer in analyzers
        ):
            raise AgentError("BTAG-SPEC-ANALYZERS", "analyzers must be a list of names")
        normalized = {
            "spec_version": "strategy-spec-v1",
            "name": name,
            "slug": slug,
            "category": category,
            "archetype": archetype,
            "output_profile": output_profile,
            "dataset_id": dataset_id,
            "feeds": feeds,
            "parameters": parameters,
            "entry": rules(source.get("entry"), "enter"),
            "exit": rules(source.get("exit"), "exit"),
            "sizing": sizing,
            "timers": timers,
            "cheat": cheat,
            "risk": risk,
            "cash": float(source.get("cash", 100000.0)),
            "commission": float(source.get("commission", 0.0)),
            "run_modes": run_modes,
            "allowed_imports": ["backtrader"],
            "non_goals": [str(item) for item in source.get("non_goals", [])],
            "extensions": {"backtrader_agent": {"analyzers": analyzers}},
        }
        normalized["spec_hash"] = hash_object(
            {key: item for key, item in normalized.items() if key != "spec_hash"}
        )
        return cls(normalized)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.value)

    @property
    def slug(self) -> str:
        return self.value["slug"]

    @property
    def archetype(self) -> str:
        return self.value["archetype"]

    @property
    def profile(self) -> str:
        return self.value["output_profile"]

    @property
    def spec_hash(self) -> str:
        return self.value["spec_hash"]

    @property
    def module_slug(self) -> str:
        return self.slug.replace("-", "_")

    @property
    def parameter_defaults(self) -> Dict[str, Any]:
        return {item["name"]: item["default"] for item in self.value["parameters"]}


@dataclass(frozen=True)
class DatasetManifest:
    value: Dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DatasetManifest":
        if raw.get("schema_version") != "dataset-manifest-v1":
            raise AgentError(
                "BTAG-DATASET-VERSION", "DatasetManifest version is unsupported"
            )
        if not isinstance(raw.get("feeds"), list) or not raw["feeds"]:
            raise AgentError("BTAG-DATASET-FEEDS", "DatasetManifest requires feeds")
        for feed in raw["feeds"]:
            if feed.get("format") not in ADAPTER_FORMATS:
                raise AgentError(
                    "BTAG-DATASET-FORMAT", "dataset adapter is not allowlisted"
                )
        return cls(dict(raw))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.value)
