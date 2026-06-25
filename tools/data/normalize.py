"""Normalizers — source-shaped raw frames → canonical column names.

Each function handles one source's quirks (column casing, MultiIndex, index name)
and returns a frame with the canonical column *names*; ``schema.enforce_canonical``
then applies the universal guarantees.
"""
from __future__ import annotations

import pandas as pd

from schema import CANONICAL_COLUMNS

# yfinance column label (level 0) → canonical name. "Adj Close" is intentionally
# dropped: the canonical store holds raw OHLC; adjustment is a consumer concern.
_YF_RENAME: dict[str, str] = {
    "Date": "timestamp",
    "Datetime": "timestamp",
    "index": "timestamp",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}


def from_yfinance(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a yfinance download into canonical-named columns."""
    df = raw.copy()
    # Single-ticker downloads come back with a MultiIndex on columns
    # ((field, ticker)); collapse to the field level.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # The datetime index ("Date" daily, "Datetime" intraday) becomes a column.
    df = df.reset_index()
    df = df.rename(columns=_YF_RENAME)
    keep = [c for c in CANONICAL_COLUMNS if c in df.columns]
    return df.loc[:, keep]
