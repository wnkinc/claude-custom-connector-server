"""The canonical data contract — the single shape every consumer trusts.

This is the seam between the lifecycle stages: a source/normalizer produces a frame
with these column *names*; ``enforce_canonical`` applies the universal *guarantees*
(UTC timestamps, float OHLCV, sorted, de-duplicated, no NaN OHLC). The store writes
only canonical frames, so nothing downstream ever has to re-clean.

Kinds vocabulary
----------------
``kind`` is the top-level axis of the store — *what* the data is (bars, quotes,
ticks, order book, …). Only ``bars`` is implemented today; the rest are named
seams, not code.
"""
from __future__ import annotations

import pandas as pd

# ── Kinds (what the data is) ────────────────────────────────────────────────
KIND_BARS = "bars"
# Roadmap kinds — named so the structure advertises them; not yet implemented.
KIND_QUOTES = "quotes"
KIND_TICKS = "ticks"
KIND_BOOK = "book"

KINDS_IMPLEMENTED: tuple[str, ...] = (KIND_BARS,)

# ── The canonical bars schema ───────────────────────────────────────────────
TIMESTAMP = "timestamp"
OHLCV: tuple[str, ...] = ("open", "high", "low", "close", "volume")
CANONICAL_COLUMNS: tuple[str, ...] = (TIMESTAMP, *OHLCV)


def enforce_canonical(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a bars frame to the canonical contract, or raise if it can't.

    Guarantees on the returned frame:
      - exactly ``CANONICAL_COLUMNS``, in order;
      - ``timestamp`` is tz-aware UTC (naive input is assumed UTC);
      - OHLCV are float64;
      - rows with NaN in any OHLC column are dropped (volume NaN → 0.0);
      - one row per timestamp (last wins), sorted ascending, index reset.
    """
    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"frame is missing canonical columns {missing}; has {list(df.columns)}"
        )

    out = df.loc[:, list(CANONICAL_COLUMNS)].copy()
    out[TIMESTAMP] = pd.to_datetime(out[TIMESTAMP], utc=True)
    for col in OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")

    out = out.dropna(subset=["open", "high", "low", "close"])
    out["volume"] = out["volume"].fillna(0.0)
    out = (
        out.drop_duplicates(subset=TIMESTAMP, keep="last")
        .sort_values(TIMESTAMP)
        .reset_index(drop=True)
    )
    return out
