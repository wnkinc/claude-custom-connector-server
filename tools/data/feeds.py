"""Thin wrappers over OpenBB endpoints — the only layer that knows about OpenBB.

One function per capability, each calling a single OpenBB endpoint and returning its
standardized DataFrame **as-is** (OpenBB's own schema + ``date`` index). No persistence,
no MCP — just the fetch. Adding a capability = add a function here (+ a tool in
``server.py``); the persistence layer (``lake.py``) is untouched.

Each *data type* is a command extension (``openbb-equity``, ``openbb-crypto``); each
*data source* is a provider extension (``openbb-yfinance``, ``openbb-tiingo``). Because
OpenBB standardizes across providers, a new provider for an existing data type is nearly
free — it's just another ``provider=`` value on the same feed fn, no new code. Re-run the
accessor prebuild after adding any extension.

Keyed providers (e.g. tiingo) need a token. OpenBB does NOT read credential env vars, so
we inject them from the env (``.env``) onto ``obb.user.credentials`` — see
``_CREDENTIALS`` / ``_apply_credentials``. yfinance needs no key.

Tiingo's intraday endpoint caps each response at ~10k bars, anchored to ``end`` (it returns
the most recent ≤10k bars ending at ``end``). To pull a longer intraday window we PAGE it:
``_bars`` walks backward, repeatedly fetching the next ≤10k-bar window ending just before
the earliest bar already held, until it reaches ``start``. Daily+ bars never hit the cap, so
they pass straight through. This is the only reason ``feeds`` does more than one fetch.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

# Tiingo is the fixed provider for equity + crypto (deeper intraday history than yfinance,
# same daily) — the tools don't expose a provider choice. yfinance stays installed and
# reachable in code via provider="yfinance", but nothing uses it by default.
DEFAULT_PROVIDER = "tiingo"

# env var -> the obb.user.credentials attribute it populates. Add a keyed provider here.
_CREDENTIALS = {"TIINGO_API_KEY": "tiingo_token"}

# Tiingo intraday per-response bar cap, and the intervals it applies to (daily+ never cap).
_TIINGO_CAP = 10000
_TIINGO_INTRADAY = frozenset({"1m", "2m", "5m", "15m", "30m", "60m", "1h", "90m"})

# Databento (opt-in alt equity source; direct SDK, not OpenBB). It offers only these four
# OHLCV bar sizes — map our interval vocabulary onto its schema names. Dataset is fixed to
# EQUS.MINI: Databento's consolidated US-equity feed (Nasdaq + NYSE + other venues), one
# clean bar per interval (history from 2023-03-28).
_DATABENTO_SCHEMA = {"1s": "ohlcv-1s", "1m": "ohlcv-1m", "1h": "ohlcv-1h", "1d": "ohlcv-1d"}
_DATABENTO_EQUITY_DATASET = "EQUS.MINI"


def _apply_credentials(obb) -> None:
    """Inject provider tokens from the env onto OpenBB's credential store (idempotent)."""
    creds = obb.user.credentials
    for env_var, attr in _CREDENTIALS.items():
        token = os.getenv(env_var)
        if token and getattr(creds, attr, None) != token:
            setattr(creds, attr, token)


def _obb():
    from openbb import obb

    _apply_credentials(obb)
    return obb


def _fetch(namespace, symbol, interval, start, end, provider) -> pd.DataFrame:
    """One OpenBB call: ``obb.<namespace>.price.historical(...).to_df()`` (the source's frame)."""
    cmd = getattr(_obb(), namespace).price.historical
    return cmd(
        symbol=symbol, interval=interval, start_date=start, end_date=end, provider=provider
    ).to_df()


def _partial_reason(exc) -> str:
    """Classify a mid-walk fetch failure for the partial-result note."""
    msg = str(exc).lower()
    if "allocation" in msg or "429" in msg or "rate" in msg:
        return "Tiingo hourly rate limit reached"
    return f"fetch interrupted ({type(exc).__name__})"


def _bars(namespace, symbol, interval, start, end, provider) -> pd.DataFrame:
    """Fetch bars, paging Tiingo's end-anchored intraday cap so the full window is assembled.

    A single fetch is enough unless it's a capped (== ``_TIINGO_CAP`` rows) tiingo intraday
    pull with a known ``start``. When it is, walk backward — each step fetches the next
    window ending the day before the earliest bar held — until we reach ``start`` (or a short
    page signals the start of available data). Frames are concatenated, de-duplicated on the
    timestamp index, and sorted.

    **Graceful partial:** if a page fails mid-walk (Tiingo's free tier caps requests per
    hour, so a deep pull can run out), the pages already fetched are kept and returned with
    ``df.attrs["partial"]`` set — the caller persists what it got and the lake's merge lets a
    later re-run extend further back. Only the *first* fetch failing propagates (nothing yet).
    """
    df = _fetch(namespace, symbol, interval, start, end, provider)
    paged = (
        provider == "tiingo" and interval in _TIINGO_INTRADAY and start and len(df) >= _TIINGO_CAP
    )
    if not paged:
        return df

    start_date = pd.Timestamp(start).date()
    frames = [df]
    earliest = df.index.min()
    partial = None
    while earliest.date() > start_date:
        chunk_end = (earliest.date() - dt.timedelta(days=1)).isoformat()
        try:
            prev = _fetch(namespace, symbol, interval, start, chunk_end, provider)
        except Exception as exc:  # noqa: BLE001 — keep the pages already fetched
            partial = _partial_reason(exc)
            break
        if prev.empty or prev.index.min().date() >= earliest.date():
            break  # no backward progress -> stop (avoids an infinite loop)
        frames.append(prev)
        earliest = prev.index.min()
        if len(prev) < _TIINGO_CAP:
            break  # short page -> reached the start of available history
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if partial is not None:
        out.attrs["partial"] = partial
    return out


def equity_bars(
    symbol: str, interval: str = "1d", start: str | None = None,
    end: str | None = None, provider: str = DEFAULT_PROVIDER,
) -> pd.DataFrame:
    """Historical OHLCV bars for an equity symbol (e.g. AAPL)."""
    return _bars("equity", symbol, interval, start, end, provider)


def crypto_bars(
    symbol: str, interval: str = "1d", start: str | None = None,
    end: str | None = None, provider: str = DEFAULT_PROVIDER,
) -> pd.DataFrame:
    """Historical OHLCV bars for a crypto pair (e.g. BTC-USD)."""
    return _bars("crypto", symbol, interval, start, end, provider)


def fx_bars(
    symbol: str, interval: str = "1d", start: str | None = None,
    end: str | None = None, provider: str = DEFAULT_PROVIDER,
) -> pd.DataFrame:
    """Historical OHLC bars for an FX pair (e.g. EURUSD). FX frames carry no volume."""
    return _bars("currency", symbol, interval, start, end, provider)


# ── Databento (opt-in alt equity source; direct SDK, NOT OpenBB) ─────────────


def _databento():
    """A Databento Historical client; reads ``DATABENTO_API_KEY`` from the env (paid)."""
    if not os.getenv("DATABENTO_API_KEY"):
        raise ValueError(
            "DATABENTO_API_KEY is not set — add it to .env to ingest equities from databento."
        )
    import databento as db

    return db.Historical()  # picks up DATABENTO_API_KEY from the environment


def databento_bars(
    symbol: str, interval: str = "1d", start: str | None = None, end: str | None = None,
) -> pd.DataFrame:
    """Equity OHLCV bars from Databento (direct SDK) — same signature as ``equity_bars``.

    Databento isn't an OpenBB provider, so this calls its SDK directly. It offers only
    1s/1m/1h/1d OHLCV bars (``interval`` is mapped to its ``ohlcv-*`` schema) on the
    consolidated US-equity dataset (EQUS.MINI — Nasdaq + NYSE + other venues, one bar per
    interval), and requires a ``start``. Returns the SDK frame as-is (``ts_event``-indexed,
    carrying the ticker ``symbol``). Uses default symbology (``raw_symbol`` in).
    """
    schema = _DATABENTO_SCHEMA.get(interval)
    if schema is None:
        raise ValueError(
            f"databento offers only {sorted(_DATABENTO_SCHEMA)} bar intervals; got {interval!r}."
        )
    if not start:
        raise ValueError("databento requires an explicit start date (YYYY-MM-DD).")
    store = _databento().timeseries.get_range(
        dataset=_DATABENTO_EQUITY_DATASET, symbols=[symbol], schema=schema, start=start, end=end,
    )
    return store.to_df()
