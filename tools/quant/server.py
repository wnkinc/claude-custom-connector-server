import os
import sys
from pathlib import Path

from fastmcp import FastMCP

# Make the repo root importable regardless of CWD, then load shared OAuth.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

import catalog  # noqa: E402
from engines import core as engine_core  # noqa: E402

mcp = FastMCP(name="quant")


def load_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=True)


@mcp.tool
def library_list(layer: str | None = None, family: str | None = None) -> list[dict]:
    """List catalogued pieces (indicators / features / alpha-signals).

    Optionally filter by tag: layer ("indicator", "feature", "alpha") and/or
    family ("momentum", "volatility", ...). Each item has name, return type, tags.
    """
    return catalog.list_pieces(layer=layer, family=family)


@mcp.tool
def library_lineage(name: str) -> dict:
    """Lineage for a piece: what it depends on (upstream) and what uses it (downstream)."""
    return catalog.lineage(name)


@mcp.tool
def backtest_strategies() -> list[dict]:
    """List strategies: tunable params (name → default), the library pieces each `uses`, and a one-line doc.

    `uses` is derived from the DAG (e.g. mean_reversion → mr_signal), so it always reflects
    the signal a strategy rests on. Trace it further with library_lineage. Pick a strategy +
    fill its params, then call backtest_vectorbt.
    """
    return catalog.strategies()


@mcp.tool
def backtest(
    symbol: str,
    strategy: str = "mean_reversion",
    engine: str = "vectorbt",
    source: str = "yfinance",
    namespace: str = "bars",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    params: dict | None = None,
) -> dict:
    """Backtest a strategy over data ALREADY in the lake, on the chosen engine.

    Reads OHLCV from the lake (it does not fetch — use the data tool's data-catalog to see
    what exists and copy its exact symbol/namespace/source/interval), runs the strategy's
    library signal through the Hamilton DAG to a target position, then simulates it.

    engine: "vectorbt" (vectorized, fast — good for sweeps) or "nautilus" (event-driven,
    realistic execution). The strategy decision is identical across engines; only the
    fill/accounting differs, so `stats` is engine-native (vectorbt's portfolio stats vs
    Nautilus's analyzer stats).

    Defaults (vectorbt/bars/yfinance/1d daily) match the seeded data. Prefer daily 1d:
    indicator windows (e.g. RSI-14) need ample bars, so sparse data will fail. See
    backtest_strategies for strategies + params.
    """
    return engine_core.run_backtest(
        symbol, strategy=strategy, engine=engine, namespace=namespace, source=source,
        interval=interval, start=start, end=end, params=params,
    )


@mcp.tool
def backtest_sweep(
    symbol: str,
    strategy: str = "mean_reversion",
    param_grid: dict | None = None,
    engine: str = "vectorbt",
    metric: str = "return_pct",
    top: int = 20,
    source: str = "yfinance",
    namespace: str = "bars",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Sweep a strategy's params over a grid and return the combos ranked by `metric`.

    param_grid is {param: [values, ...]} (see backtest_strategies for a strategy's tunable
    params); the cartesian product is the combo set, capped at 200. Each combo is backtested
    and reduced to one normalized metric row — return_pct, sharpe, max_drawdown_pct, trades,
    win_rate — so combos are directly comparable. Results are sorted best-first by `metric`
    (default return_pct, the metric computed identically on both engines) and trimmed to `top`.

    Same data/engine semantics as backtest. Note: a nautilus sweep runs a full event-loop
    backtest per combo, so it's slow on big grids or large intraday data — prefer vectorbt
    (or a small grid) for wide sweeps.
    """
    return engine_core.sweep(
        symbol, strategy=strategy, param_grid=param_grid, engine=engine, metric=metric,
        top=top, namespace=namespace, source=source, interval=interval, start=start, end=end,
    )


def main() -> None:
    load_env()
    port = int(os.getenv("MCP_PORT", "8064"))
    # quant returns trusted, internally-generated content -> no guardrail / approval.
    serve(mcp, port=port)


if __name__ == "__main__":
    main()
