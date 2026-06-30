"""Generic parquet persistence — the one piece of owned code, source/kind-agnostic.

This layer knows nothing about OpenBB. It persists *any* DataFrame under a **key** —
the path segments that name a dataset; the path IS the metadata:

    <DATA_ROOT>/<*key>.parquet   e.g.  equity/yfinance/AAPL/1d.parquet

The first key segment is the dataset namespace (``equity``, ``crypto``, …); a capability
owns its namespace, so two capabilities never collide. ``ingest`` merges a freshly
fetched frame into the stored file (de-duplicated on the timestamp index, fetched wins)
so a file accumulates history across calls. ``read`` reads it back.

Adding an OpenBB capability does NOT touch this file — only ``feeds.py`` (the fetch) and
``server.py`` (the tool) grow. This is the surface you maintain; it stays constant.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

# Tool dir = this file's parent; the repo-local default lake lives under it.
_TOOL_ROOT = Path(__file__).resolve().parent


def data_root() -> Path:
    """Parquet lake root; ``DATA_ROOT`` overrides the tool-local default."""
    return Path(os.environ.get("DATA_ROOT") or (_TOOL_ROOT / "var" / "data"))


def _safe(part: str) -> str:
    """Filesystem-safe path segment (only '/' is genuinely problematic on Linux)."""
    return str(part).replace("/", "_")


def path_for(*key: str) -> Path:
    """``("equity","yfinance","AAPL","1d")`` → ``<root>/equity/yfinance/AAPL/1d.parquet``."""
    *parents, leaf = [_safe(k) for k in key]
    return data_root().joinpath(*parents, f"{_safe(leaf)}.parquet")


def read(*key: str) -> pd.DataFrame | None:
    """Return the stored frame for ``key``, or None if nothing is stored yet."""
    path = path_for(*key)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def catalog(*prefix: str) -> list[dict]:
    """List every dataset stored in the lake (optionally under a key ``prefix``).

    Discovery from a pure file perspective: walk the lake for ``*.parquet`` leaves and turn
    each path back into its key segments — the path IS the metadata, so this needs no index
    or registry. Light metadata only: the row count is read from the parquet footer (no data
    is loaded) and the time span from just the index column. One dict per file, sorted by key.
    A bad/unreadable file is listed with null stats rather than failing the whole listing.
    """
    root = data_root()
    base = root.joinpath(*[_safe(p) for p in prefix])
    if not base.exists():
        return []
    out: list[dict] = []
    for path in sorted(base.rglob("*.parquet")):
        entry = {"key": "/".join(path.relative_to(root).with_suffix("").parts),
                 "rows": None, "start": None, "end": None, "path": str(path)}
        try:
            import pyarrow.parquet as pq
            entry["rows"] = pq.ParquetFile(path).metadata.num_rows
            idx = pd.read_parquet(path, columns=[]).index  # index only, no data columns
            if len(idx):
                entry["start"], entry["end"] = idx.min().isoformat(), idx.max().isoformat()
        except Exception:  # noqa: BLE001 — a listing must never fail on one bad file
            pass
        out.append(entry)
    return out


def _merge(existing: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """Append ``fetched`` onto ``existing``, dropping duplicate index entries (fetched wins).

    ``fetched`` is concatenated last so a re-downloaded row overwrites the stored one
    (corrections, late values) rather than the other way around.
    """
    combined = pd.concat([existing, fetched])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def _summary(key: tuple[str, ...], df: pd.DataFrame, path: Path, *, fetched: int, added: int) -> dict:
    idx = df.index
    return {
        "key": "/".join(key),
        "rows": int(len(df)),       # total rows now stored
        "fetched": int(fetched),    # rows the feed returned this call
        "added": int(added),        # net-new rows after dedupe/merge
        "start": idx.min().isoformat() if len(df) else None,
        "end": idx.max().isoformat() if len(df) else None,
        "path": str(path),
    }


def ingest(key: tuple[str, ...], fetched: pd.DataFrame, *, refresh: bool = False) -> dict:
    """Merge ``fetched`` into the parquet file at ``key`` and return a summary.

    The fetched frame is appended to whatever is already stored and de-duplicated on the
    index, so the file accumulates history across calls. ``refresh=True`` ignores the
    stored file and replaces it with just this fetch.
    """
    if fetched.empty:
        raise ValueError(f"no data to ingest for {'/'.join(key)}")

    path = path_for(*key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not refresh and path.exists():
        existing = pd.read_parquet(path)
        prev = len(existing)
        df = _merge(existing, fetched)
    else:
        prev = 0
        df = fetched.sort_index()
    df.to_parquet(path)

    return _summary(key, df, path, fetched=len(fetched), added=len(df) - prev)
