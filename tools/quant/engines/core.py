"""Engine-agnostic backtest core: lake → target-position series, plus engine dispatch.

Everything an engine needs *before* its own simulation — reading OHLCV from the lake,
checking the strategy is real, running the Hamilton DAG down to the target-position
series — is identical across engines and lives here. An engine runner consumes only
``(ohlcv, position, interval)`` and owns just its native simulation + stats, so adding
an engine is one runner (``run`` + ``summary``) + one ``_ENGINES`` entry, never a new
MCP tool.
"""
from __future__ import annotations

import itertools

import pandas as pd

import catalog
from engines import lake
from engines.nautilus import runner as nautilus_runner
from engines.vectorbt import runner as vbt_runner

# engine name → runner module, each exposing run(ohlcv, position, interval) -> native stats
# and summary(native) -> normalized metrics. Runners keep their heavy library import lazy
# (inside run), so listing engines here costs nothing until one is actually used.
_ENGINES = {
    "vectorbt": vbt_runner,
    "nautilus": nautilus_runner,
}

# A single sweep runs this many full sims at most; reject larger grids up front (every combo
# is a real backtest, and a Nautilus combo is a whole event loop).
_MAX_COMBOS = 200

# Metrics where a smaller value is better, so ranking sorts ascending instead of descending.
_LOWER_IS_BETTER = {"max_drawdown_pct"}


def load(
    symbol: str,
    strategy: str,
    namespace: str,
    source: str,
    interval: str,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    """Read the lake and validate the strategy — the param/engine-independent pre-flight.

    Split out from ``prepare`` so a sweep reads the data and validates once, then varies
    only the params across the grid.
    """
    ohlcv = lake.read_ohlcv(
        symbol, interval=interval, namespace=namespace, source=source, start=start, end=end
    )
    if ohlcv.empty:
        raise ValueError(f"No rows for {symbol} {interval} in the requested window.")

    valid = {s["name"] for s in catalog.strategies()}
    if strategy not in valid:
        raise ValueError(f"Unknown strategy {strategy!r}. Available: {sorted(valid)}")
    return ohlcv


def _materialize(strategy: str, ohlcv: pd.DataFrame, params: dict | None) -> pd.Series:
    """Run the DAG to the strategy's target position, with the too-few-bars hint."""
    try:
        return catalog.materialize(strategy, ohlcv, params=params)[strategy]
    except Exception:  # noqa: BLE001
        if len(ohlcv) < 50:
            raise ValueError(
                f"Only {len(ohlcv)} bars — too few for {strategy!r}'s indicator window. "
                f"Use a longer date range or a finer interval (daily 1d has far more bars "
                f"than monthly)."
            ) from None
        raise


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
    """Read the lake and run the DAG to a target-position series → ``(ohlcv, position)``."""
    ohlcv = load(symbol, strategy, namespace, source, interval, start, end)
    position = _materialize(strategy, ohlcv, params)
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
    stats = _ENGINES[engine].run(ohlcv, position, interval)
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


def sweep(
    symbol: str,
    strategy: str = "mean_reversion",
    param_grid: dict | None = None,
    engine: str = "vectorbt",
    metric: str = "return_pct",
    top: int = 20,
    namespace: str = "bars",
    source: str = "yfinance",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Backtest a strategy across a grid of params; return combos ranked by ``metric``.

    ``param_grid`` is ``{param: [values]}``; its cartesian product is the combo set (capped
    at _MAX_COMBOS). The lake is read and validated once, then each combo only re-materializes
    the position and re-simulates. Each combo's native stats are reduced to one normalized
    metric row (return_pct / sharpe / max_drawdown_pct / trades / win_rate) so combos — and
    engines — are comparable. Results are sorted best-first by ``metric`` and trimmed to ``top``.
    """
    if engine not in _ENGINES:
        raise ValueError(f"Unknown engine {engine!r}. Available: {sorted(_ENGINES)}")
    if not param_grid:
        raise ValueError("param_grid is empty — pass {param: [values, ...]} to sweep over.")

    strat = next((s for s in catalog.strategies() if s["name"] == strategy), None)
    if strat is None:
        names = sorted(s["name"] for s in catalog.strategies())
        raise ValueError(f"Unknown strategy {strategy!r}. Available: {names}")
    unknown = [k for k in param_grid if k not in strat["params"]]
    if unknown:
        raise ValueError(
            f"Unknown param(s) {unknown} for {strategy!r}. Tunable: {sorted(strat['params'])}."
        )

    names = list(param_grid)
    value_lists = [
        v if isinstance(v, (list, tuple)) else [v] for v in (param_grid[n] for n in names)
    ]
    combos = [dict(zip(names, vals)) for vals in itertools.product(*value_lists)]
    if len(combos) > _MAX_COMBOS:
        raise ValueError(
            f"{len(combos)} combos exceeds the {_MAX_COMBOS} cap — narrow the grid "
            f"(every combo is a full backtest)."
        )

    ohlcv = load(symbol, strategy, namespace, source, interval, start, end)
    eng = _ENGINES[engine]
    rows = []
    for combo in combos:
        position = _materialize(strategy, ohlcv, combo)
        native = eng.run(ohlcv, position, interval)
        rows.append({"params": combo, **eng.summary(native)})

    if metric not in rows[0]:
        available = sorted(k for k in rows[0] if k != "params")
        raise ValueError(f"Unknown metric {metric!r}. Available: {available}.")
    sign = 1 if metric in _LOWER_IS_BETTER else -1
    rows.sort(key=lambda r: (r[metric] is None, sign * (r[metric] or 0.0)))

    return {
        "engine": engine,
        "symbol": symbol.strip().upper(),
        "strategy": strategy,
        "namespace": namespace,
        "source": source,
        "interval": interval,
        "metric": metric,
        "combos_run": len(combos),
        "results": rows[:top],
    }
