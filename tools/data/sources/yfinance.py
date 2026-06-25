"""yfinance source — fetch raw OHLCV bars from Yahoo Finance.

Returns yfinance's frame *as-is* (MultiIndex columns, a Date/Datetime index);
``normalize.from_yfinance`` reshapes it to the canonical contract.
"""
from __future__ import annotations

import pandas as pd

SOURCE = "yfinance"

# Canonical interval → yfinance interval. yfinance mostly accepts these verbatim;
# the allowlist keeps unsupported strings from silently reaching the API.
_INTERVALS: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1d": "1d",
    "1wk": "1wk",
    "1mo": "1mo",
}


def yf_interval(interval: str) -> str:
    if interval not in _INTERVALS:
        raise ValueError(
            f"Unsupported interval {interval!r}. Supported: {sorted(_INTERVALS)}"
        )
    return _INTERVALS[interval]


def fetch(symbol: str, interval: str, start: str | None, end: str | None) -> pd.DataFrame:
    """Download raw bars. With no start/end, pulls the full available history."""
    import yfinance as yf

    iv = yf_interval(interval)
    kwargs: dict = {"interval": iv, "progress": False, "auto_adjust": False}
    if start is None and end is None:
        kwargs["period"] = "max"
    else:
        kwargs["start"] = start
        kwargs["end"] = end
    return yf.download(symbol, **kwargs)
