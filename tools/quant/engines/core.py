"""Engine-agnostic backtest core: lake → target-position series, plus engine dispatch.

Everything an engine needs *before* its own simulation — reading OHLCV from the lake,
checking the strategy is real, running the Hamilton DAG down to the target-position
series — is identical across engines and lives here. An engine runner consumes only
``(ohlcv, position, interval)`` and owns just its native simulation + stats, so adding
an engine is one ``run`` function + one ``_ENGINES`` entry, never a new MCP tool.
"""
from __future__ import annotations

import pandas as pd

import catalog
from engines import lake
from engines.nautilus import runner as nautilus_runner
from engines.vectorbt import runner as vbt_runner

# engine name → run(ohlcv, position, interval) -> stats dict. Each runner keeps its heavy
# library import lazy (inside run), so listing engines here costs nothing until one is used.
_ENGINES = {
    "vectorbt": vbt_runner.run,
    "nautilus": nautilus_runner.run,
}


def prepare(
    symbol: str,
    strategy: str,
    namespace: str,
    source: str,
    interval: str,
    start: str | None,
    end: str | None,
    params: dict | None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Read the lake and run the DAG to a target-position series → ``(ohlcv, position)``.

    Engine-agnostic: validates the strategy is a real strategy node, then materializes its
    target position (1 = long, 0 = flat). The too-few-bars hint is raised here because an
    indicator window outrunning the data is an input problem, not an engine problem.
    """
    ohlcv = lake.read_ohlcv(
        symbol, interval=interval, namespace=namespace, source=source, start=start, end=end
    )
    if ohlcv.empty:
        raise ValueError(f"No rows for {symbol} {interval} in the requested window.")

    valid = {s["name"] for s in catalog.strategies()}
    if strategy not in valid:
        raise ValueError(f"Unknown strategy {strategy!r}. Available: {sorted(valid)}")

    try:
        position = catalog.materialize(strategy, ohlcv, params=params)[strategy]
    except Exception:  # noqa: BLE001
        if len(ohlcv) < 50:
            raise ValueError(
                f"{symbol} {interval} has only {len(ohlcv)} bars — too few for "
                f"{strategy!r}'s indicator window. Use a longer date range or a finer "
                f"interval (daily 1d has far more bars than monthly)."
            ) from None
        raise
    return ohlcv, position


def run_backtest(
    symbol: str,
    strategy: str = "mean_reversion",
    engine: str = "vectorbt",
    namespace: str = "bars",
    source: str = "yfinance",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    params: dict | None = None,
) -> dict:
    """Run one backtest end to end on the named engine. Blocking. Returns a stats envelope.

    Shared pre-flight (lake → position) runs once here; the chosen engine only simulates
    the position and returns its native stats. The envelope around those stats is uniform
    across engines so runs are comparable at the metadata level.
    """
    if engine not in _ENGINES:
        raise ValueError(f"Unknown engine {engine!r}. Available: {sorted(_ENGINES)}")

    ohlcv, position = prepare(
        symbol, strategy, namespace, source, interval, start, end, params
    )
    stats = _ENGINES[engine](ohlcv, position, interval)
    return {
        "engine": engine,
        "symbol": symbol.strip().upper(),
        "strategy": strategy,
        "namespace": namespace,
        "source": source,
        "interval": interval,
        "rows": int(len(ohlcv)),
        "stats": stats,
    }
