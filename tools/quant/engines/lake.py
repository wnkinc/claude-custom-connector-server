"""Read OHLCV from the canonical data lake — a read-only consumer of the data tool.

The data tool writes parquet under ``<DATA_ROOT>/<namespace>/<source>/<symbol>/
<interval>.parquet`` (e.g. ``bars/yfinance/AAPL/1d`` or ``equity/tiingo/AAPL/1m``).
This module only *reads*; it never fetches. If the data isn't there it raises with
guidance to run ``data-ingest`` (the data tool) first.

Cross-tool the lake is shared via ``DATA_ROOT``: the data tool writes its parquet lake
to ``DATA_ROOT`` (``/app/state/data`` in the container); point quant's ``DATA_ROOT`` at
the same location (a shared volume) to read it. The dev default below lets a hand-run
server find a locally-ingested lake.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

# Dev default = the data tool's container lake path; DATA_ROOT overrides.
_DEFAULT_ROOT = Path("/app/state/data")
_OHLCV = ["open", "high", "low", "close", "volume"]


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT") or _DEFAULT_ROOT)


def _safe(part: str) -> str:
    return str(part).replace("/", "_")


def catalog(symbol: str | None = None) -> list[dict]:
    """Every OHLCV dataset in the lake (optionally filtered to one symbol).

    Walks ``DATA_ROOT`` for parquet leaves and turns each path back into its key
    (``namespace/source/symbol/interval``) plus the row count from the parquet footer
    (no data loaded). The path IS the metadata — no index needed. This is what an
    agent reads to pick an existing dataset to backtest instead of guessing keys or
    re-ingesting. Sorted with the longest history first so the best candidate leads.
    """
    root = data_root()
    if not root.exists():
        return []
    want = symbol.strip().upper() if symbol else None
    out: list[dict] = []
    for p in sorted(root.rglob("*.parquet")):
        parts = p.relative_to(root).with_suffix("").parts
        if len(parts) != 4:  # namespace/source/symbol/interval
            continue
        namespace, source, sym, interval = parts
        if want and sym.upper() != want:
            continue
        rows = None
        try:
            import pyarrow.parquet as pq
            rows = pq.ParquetFile(p).metadata.num_rows
        except Exception:  # noqa: BLE001 — a listing must never fail on one bad file
            pass
        out.append({"symbol": sym, "namespace": namespace, "source": source,
                    "interval": interval, "rows": rows})
    return sorted(out, key=lambda d: (d["symbol"], -(d["rows"] or 0)))


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical frame: tz-aware UTC DatetimeIndex named ``timestamp`` + lowercase OHLCV.

    Handles both lake layouts: a ``timestamp`` *column* with a RangeIndex (``bars/``
    namespace) or an already-datetime index, possibly in a non-UTC tz (``equity/``).
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index = idx
    df.index.name = "timestamp"
    keep = [c for c in _OHLCV if c in df.columns]
    return df[keep].sort_index()


def read_ohlcv(
    symbol: str,
    interval: str = "1d",
    namespace: str = "bars",
    source: str = "yfinance",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load canonical OHLCV for one dataset key, sliced to ``[start, end]`` if given."""
    symbol = symbol.strip().upper()
    path = data_root().joinpath(
        _safe(namespace), _safe(source), _safe(symbol), f"{_safe(interval)}.parquet"
    )
    if not path.exists():
        avail = catalog(symbol)
        if avail:
            opts = "; ".join(
                f"{d['namespace']}/{d['source']}/{d['interval']} ({d['rows']} bars)"
                for d in avail
            )
            hint = (
                f"But {symbol} IS in the lake — use one of: {opts}. Pass the matching "
                f"namespace/source/interval (don't re-ingest)."
            )
        else:
            hint = f"{symbol} is not in the lake yet; ingest it via the data tool first."
        raise FileNotFoundError(f"No {namespace}/{source}/{symbol}/{interval} bars. {hint}")
    df = _normalize(pd.read_parquet(path))
    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    return df
