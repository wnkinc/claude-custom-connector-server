import os
import sys
from pathlib import Path

from fastmcp import FastMCP

# Make the repo root importable regardless of CWD, then load shared OAuth.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

import catalog  # noqa: E402
from engines.vectorbt import runner as vbt_runner  # noqa: E402

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
def backtest_vectorbt(
    symbol: str,
    strategy: str = "mean_reversion",
    source: str = "yfinance",
    namespace: str = "bars",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    params: dict | None = None,
) -> dict:
    """Backtest a strategy over data ALREADY in the lake, with the vectorbt engine.

    Reads OHLCV from the lake (it does not fetch — use the data tool's data-catalog to
    see what exists and copy its exact symbol/namespace/source/interval), computes the strategy's
    library signal via the Hamilton DAG, converts it to entries/exits, and returns
    vectorbt's portfolio stats. The defaults (bars/yfinance/1d daily) match the seeded
    daily data. Prefer daily 1d: indicator windows (e.g. RSI-14) need ample bars, so
    sparse monthly data will fail. See backtest_strategies for strategies + params.
    """
    return vbt_runner.run_backtest(
        symbol, strategy=strategy, namespace=namespace, source=source,
        interval=interval, start=start, end=end, params=params,
    )


def main() -> None:
    load_env()
    port = int(os.getenv("MCP_PORT", "8064"))
    # quant returns trusted, internally-generated content -> no guardrail / approval.
    serve(mcp, port=port)


if __name__ == "__main__":
    main()
