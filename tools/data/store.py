"""The store — the only reader/writer of the canonical parquet data lake.

Layout (self-describing; the path *is* the metadata):

    <DATA_ROOT>/<kind>/<source>/<symbol>/<interval>.parquet
    e.g.  var/data/bars/yfinance/AAPL/1d.parquet

Engine-agnostic on purpose: plain parquet readable by any pandas/pyarrow consumer.

Cache coverage is decided by comparing *requested* windows, not data bounds: markets
have holidays/weekends and yfinance's ``end`` is exclusive, so the stored data rarely
lands exactly on the requested edges. We therefore record the requested ``[start, end]``
in the parquet's own key-value metadata (no sidecar file) and treat a new request as
covered iff its window is a subset of the stored one. Whole-range only — no incremental
gap-filling in this phase.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Tool dir = this file's parent; the repo-local default lake lives under it.
_TOOL_ROOT = Path(__file__).resolve().parent

_META_START = b"dpr_req_start"
_META_END = b"dpr_req_end"
_META_FETCHED = b"dpr_fetched_at"


def data_root() -> Path:
    """Shared data lake root; ``DATA_ROOT`` overrides the tool-local default."""
    return Path(os.environ.get("DATA_ROOT") or (_TOOL_ROOT / "var" / "data"))


def _safe(symbol: str) -> str:
    """Filesystem-safe symbol (only '/' is genuinely problematic on Linux)."""
    return symbol.replace("/", "_")


def path_for(kind: str, source: str, symbol: str, interval: str) -> Path:
    return data_root() / kind / source / _safe(symbol) / f"{interval}.parquet"


def write(
    kind: str,
    source: str,
    symbol: str,
    interval: str,
    df: pd.DataFrame,
    *,
    req_start: str | None,
    req_end: str | None,
) -> Path:
    """Write a canonical frame (overwriting), recording the requested window. Returns the path."""
    p = path_for(kind, source, symbol, interval)
    p.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[_META_START] = (req_start or "").encode()
    meta[_META_END] = (req_end or "").encode()
    meta[_META_FETCHED] = datetime.datetime.now(datetime.UTC).isoformat().encode()
    pq.write_table(table.replace_schema_metadata(meta), p)
    return p


def read(kind: str, source: str, symbol: str, interval: str) -> pd.DataFrame | None:
    """Return the stored canonical frame, or None if nothing is stored yet."""
    p = path_for(kind, source, symbol, interval)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def request_window(kind: str, source: str, symbol: str, interval: str) -> tuple[str | None, str | None] | None:
    """Return the stored ``(req_start, req_end)`` (empty → None), or None if no file."""
    p = path_for(kind, source, symbol, interval)
    if not p.exists():
        return None
    meta = pq.read_schema(p).metadata or {}
    start = (meta.get(_META_START) or b"").decode() or None
    end = (meta.get(_META_END) or b"").decode() or None
    return start, end


def covers(kind: str, source: str, symbol: str, interval: str, start: str | None, end: str | None) -> bool:
    """True if a prior fetch's requested window already contains ``[start, end]``.

    A ``None`` stored bound means "unbounded" on that side (a full-history fetch);
    a ``None`` requested bound means the caller wants it unbounded, which only a
    stored unbounded bound can satisfy.
    """
    win = request_window(kind, source, symbol, interval)
    if win is None:
        return False
    stored_start, stored_end = win
    if stored_start is not None:  # we only have data from stored_start onward
        if start is None or pd.Timestamp(start) < pd.Timestamp(stored_start):
            return False
    if stored_end is not None:  # we only fetched up to stored_end
        if end is None or pd.Timestamp(end) > pd.Timestamp(stored_end):
            return False
    return True
