"""VectorBT engine: target-position series → vectorbt portfolio stats.

Receives the engine-agnostic position from ``engines.core`` and only does the
vectorbt-specific part: position → boolean entries/exits → ``Portfolio.from_signals``.
Returns vectorbt's ``stats()`` essentially untransformed (just made JSON-safe).
"""
from __future__ import annotations

import json

# Canonical interval → pandas offset alias, used as ``freq`` so vbt's annualized ratios
# (Sharpe etc.) are meaningful. Only FIXED-length periods are here: vectorbt annualizes
# by converting freq to a Timedelta, which months can't be (variable length) — so 1mo/1M
# are deliberately excluded and rejected upfront rather than crashing deep in vbt.
_FREQ = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "1d": "1D", "1wk": "1W",
}


def _jsonable(series) -> dict:
    """vbt stats() Series → JSON-safe dict (Timestamp/Timedelta/NaN → str), untransformed."""
    return json.loads(json.dumps(series.to_dict(), default=str))


def _position_to_signals(position):
    """Engine glue: target-position series (1=long / 0=flat) → vectorbt boolean entries/exits.

    Entries where the position turns on (flat→long), exits where it turns off (long→flat).
    This is the only vectorbt-specific step; the position itself is engine-agnostic.
    """
    pos = position.fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    entries = (prev <= 0) & (pos > 0)
    exits = (prev > 0) & (pos <= 0)
    return entries, exits


def run(ohlcv, position, interval: str) -> dict:
    """Simulate the position with vectorbt and return its native portfolio stats."""
    import vectorbt as vbt  # lazy: keep numba/vbt JIT import cost off module load

    if interval not in _FREQ:
        raise ValueError(
            f"Interval {interval!r} isn't supported for annualized backtest stats — "
            f"vectorbt needs a fixed-length period and months are variable. Supported: "
            f"{sorted(_FREQ)}. Use daily 1d (the default)."
        )
    entries, exits = _position_to_signals(position)
    pf = vbt.Portfolio.from_signals(
        ohlcv["close"], entries=entries, exits=exits, freq=_FREQ.get(interval)
    )
    return _jsonable(pf.stats())
