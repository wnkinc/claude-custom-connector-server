"""Thin wrappers over OpenBB endpoints — the only layer that knows about OpenBB.

One function per capability, each calling a single OpenBB endpoint and returning its
standardized DataFrame **as-is** (OpenBB's own schema + ``date`` index). No persistence,
no MCP — just the fetch. Adding a capability = add a function here (+ a tool in
``server.py``); the persistence layer (``lake.py``) is untouched.

Each historical endpoint is a separate OpenBB *command extension* (``openbb-equity``,
``openbb-crypto``, …) on top of the shared yfinance *provider* extension; see
``requirements.txt``. Re-run the accessor prebuild after adding one.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_PROVIDER = "yfinance"


def _obb():
    from openbb import obb

    return obb


def equity_bars(
    symbol: str, interval: str = "1d", start: str | None = None,
    end: str | None = None, provider: str = DEFAULT_PROVIDER,
) -> pd.DataFrame:
    """Historical OHLCV bars for an equity symbol (e.g. AAPL)."""
    return _obb().equity.price.historical(
        symbol=symbol, interval=interval, start_date=start, end_date=end, provider=provider
    ).to_df()


def crypto_bars(
    symbol: str, interval: str = "1d", start: str | None = None,
    end: str | None = None, provider: str = DEFAULT_PROVIDER,
) -> pd.DataFrame:
    """Historical OHLCV bars for a crypto pair (e.g. BTC-USD)."""
    return _obb().crypto.price.historical(
        symbol=symbol, interval=interval, start_date=start, end_date=end, provider=provider
    ).to_df()
