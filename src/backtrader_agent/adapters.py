"""Single-source registry of the six P0 dataset adapters (R6).

Every adapter enumeration, default column mapping, and runner assembly path in
contracts.py / scaffold.py / data.py is derived from this module, so the six
P0 formats are defined exactly once and ``canonical_csv_v1`` can no longer
drift into any allowlist.

``assembly`` holds the runner dispatch fragment for the adapter.  For the CSV
family it is the full ``if adapter == ...`` branch of ``_csv_feed`` with its
indentation intact; for the Pandas family it is the bare feed-class expression
interpolated into ``_pandas_feed`` (the enclosing template supplies the
indentation).  ``CSV_FORMATS`` / ``PANDAS_FORMATS`` keep the dispatch families
in the exact order the rendered runner emits them.
"""

from typing import Dict, FrozenSet, NamedTuple, Optional, Tuple


class AdapterSpec(NamedTuple):
    format: str
    default_columns: Tuple[Tuple[str, Optional[str]], ...]
    assembly: str


ADAPTER_SPECS: Dict[str, AdapterSpec] = {
    "generic_csv": AdapterSpec(
        format="generic_csv",
        default_columns=(
            ("datetime", "datetime"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", "openinterest"),
        ),
        assembly="""    if adapter == "generic_csv":
        return bt.feeds.GenericCSVData(
            dtformat="%Y-%m-%dT%H:%M:%SZ",
            datetime=0,
            open=1,
            high=2,
            low=3,
            close=4,
            volume=5,
            openinterest=6,
            **common,
        )""",
    ),
    "backtrader_csv": AdapterSpec(
        format="backtrader_csv",
        default_columns=(
            ("datetime", "date"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", "openinterest"),
        ),
        assembly="""    if adapter == "backtrader_csv":
        return CanonicalBacktraderCSVData(**common)""",
    ),
    "yahoo_csv": AdapterSpec(
        format="yahoo_csv",
        default_columns=(
            ("datetime", "Date"),
            ("open", "Open"),
            ("high", "High"),
            ("low", "Low"),
            ("close", "Close"),
            ("volume", "Volume"),
            ("openinterest", None),
        ),
        assembly="""    if adapter == "yahoo_csv":
        return CanonicalYahooCSVData(
            reverse=False,
            adjclose=False,
            adjvolume=False,
            round=False,
            **common,
        )""",
    ),
    "mt5_csv": AdapterSpec(
        format="mt5_csv",
        default_columns=(
            ("datetime", "<DATE>"),
            ("open", "<OPEN>"),
            ("high", "<HIGH>"),
            ("low", "<LOW>"),
            ("close", "<CLOSE>"),
            ("volume", "<TICKVOL>"),
            ("openinterest", None),
        ),
        assembly="""    if adapter == "mt5_csv":
        return CanonicalMT5CSVData(
            dtformat="%Y-%m-%dT%H:%M:%SZ",
            datetime=0,
            open=1,
            high=2,
            low=3,
            close=4,
            volume=5,
            openinterest=6,
            **common,
        )""",
    ),
    "pandas": AdapterSpec(
        format="pandas",
        default_columns=(
            ("datetime", "datetime"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", "openinterest"),
        ),
        assembly="bt.feeds.PandasData",
    ),
    "pandas_custom_lines": AdapterSpec(
        format="pandas_custom_lines",
        default_columns=(
            ("datetime", "datetime"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", "openinterest"),
        ),
        assembly='_pandas_custom_feed_class(descriptor["name"])',
    ),
}

ADAPTER_FORMATS: FrozenSet[str] = frozenset(ADAPTER_SPECS)
CSV_FORMATS: Tuple[str, ...] = ("generic_csv", "backtrader_csv", "yahoo_csv", "mt5_csv")
PANDAS_FORMATS: Tuple[str, ...] = ("pandas", "pandas_custom_lines")
