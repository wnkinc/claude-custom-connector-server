"""MCP server: market-data tools over OpenBB (yfinance provider).

Every tool is a thin read-through over OpenBB. Bars are additionally *persisted* to a
plain parquet lake (``bars.py``) so a download is kept and accumulates across calls;
the ``equity-*`` tools are pure live passthroughs. All return structured market data,
hence trusted output (no guardrail).

Tools exposed:
  data-ingest        — fetch bars and merge them into the parquet lake
  data-read          — read stored bars back out of the lake
  equity-quote       — latest quote (live)
  equity-fundamentals— income/balance/cash/metrics/dividends (live)
  equity-profile     — company profile (live)
  equity-estimates   — analyst price-target consensus (live)
  equity-ownership   — share statistics / float / short interest (live)
  equity-discovery   — market screens (gainers/losers/active/…) (live)
"""
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

# Make the repo root importable regardless of CWD (systemd runs us from the tool
# dir), then load the shared Google-OAuth provider used by every public server.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

import bars  # noqa: E402
import equity  # noqa: E402

mcp = FastMCP(name="data")


def load_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=True)


def _summary_line(s: dict) -> str:
    return (
        f"Ingested {s.get('symbol')} {s.get('interval')} bars ({s.get('source')}): "
        f"fetched {s.get('fetched')}, +{s.get('added')} new "
        f"→ {s.get('rows')} stored ({s.get('start')} → {s.get('end')}).\n"
        f"Stored at {s.get('path')}"
    )


@mcp.tool(name="data-ingest")
def data_ingest(
    symbol: str,
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    source: str = "yfinance",
    refresh: bool = False,
) -> str:
    """
    Fetch market data from Yahoo Finance and persist it to the local parquet lake.

    Fetches OHLCV bars for one ``symbol`` (e.g. "AAPL", "BTC-USD") and merges them into
    the symbol's stored parquet file, de-duplicating on timestamp — so the file
    accumulates history across calls (fetch 2024 today, 2023 tomorrow, keep both).
    ``interval`` is OpenBB's bar size (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1W,
    1M, 1Q; default 1d). ``start``/``end`` are ISO dates (YYYY-MM-DD); omit both for the
    provider's default window (yfinance: ~1y). Pass ``refresh=true`` to replace the
    stored file with just this fetch instead of merging.
    """
    return _summary_line(bars.ingest(symbol, interval, start, end, source, refresh))


@mcp.tool(name="data-read")
def data_read(
    symbol: str,
    interval: str = "1d",
    source: str = "yfinance",
    tail: int = 10,
) -> str:
    """
    Read stored bars back out of the parquet lake (ingest them first with data-ingest).

    Returns the last ``tail`` rows (default 10) of stored OHLCV bars for
    ``symbol``/``interval``/``source`` as text, plus the total row count and the
    stored file path. Reads only — never fetches.
    """
    symbol = (symbol or "").strip().upper()
    df = bars.read(symbol, interval, source)
    if df is None or df.empty:
        return (
            f"No stored bars for {symbol} {interval} ({source}). "
            f"Run data-ingest first."
        )
    path = bars.path_for(source, symbol, interval)
    n = max(0, int(tail))
    view = df.tail(n) if n else df
    return (
        f"{len(df)} {interval} bars for {symbol} ({source}); showing last {len(view)}.\n"
        f"Stored at {path}\n\n"
        f"{view.to_string()}"
    )


# ── Live equity tools (read-through OpenBB; not lake-cached) ─────────────────


@mcp.tool(name="equity-quote")
def equity_quote(symbol: str, source: str = "yfinance") -> str:
    """
    Latest quote for one equity ``symbol`` (e.g. "AAPL"): price, bid/ask, day range,
    volume, market cap, and related fields. Live — fetched fresh each call, not cached.
    """
    return equity.quote(symbol.strip().upper(), provider=source)


@mcp.tool(name="equity-fundamentals")
def equity_fundamentals(
    symbol: str, statement: str = "income", limit: int = 4, source: str = "yfinance"
) -> str:
    """
    A fundamental financial statement for ``symbol``. ``statement`` is one of:
    income, balance, cash (each shows the last ``limit`` periods), or metrics, dividends.
    Live — fetched fresh each call, not cached.
    """
    return equity.fundamentals(symbol.strip().upper(), statement, limit, provider=source)


@mcp.tool(name="equity-profile")
def equity_profile(symbol: str, source: str = "yfinance") -> str:
    """
    Company profile for ``symbol``: name, exchange, sector/industry, description, and
    identifiers. Live — fetched fresh each call, not cached.
    """
    return equity.profile(symbol.strip().upper(), provider=source)


@mcp.tool(name="equity-estimates")
def equity_estimates(symbol: str, source: str = "yfinance") -> str:
    """
    Analyst price-target consensus and recommendation for ``symbol``.
    Live — fetched fresh each call, not cached.
    """
    return equity.consensus(symbol.strip().upper(), provider=source)


@mcp.tool(name="equity-ownership")
def equity_ownership(symbol: str, source: str = "yfinance") -> str:
    """
    Share statistics for ``symbol``: shares outstanding, float, and short interest.
    Live — fetched fresh each call, not cached.
    """
    return equity.share_statistics(symbol.strip().upper(), provider=source)


@mcp.tool(name="equity-discovery")
def equity_discovery(category: str = "gainers", limit: int = 20, source: str = "yfinance") -> str:
    """
    A market screen (not symbol-specific). ``category`` is one of: gainers, losers,
    active, growth_tech, aggressive_small_caps, undervalued_growth, undervalued_large_caps.
    Returns up to ``limit`` rows. Live — fetched fresh each call, not cached.
    """
    return equity.discovery(category, limit, provider=source)


def main() -> None:
    load_env()
    port = int(os.getenv("MCP_PORT", "8062"))
    # data returns trusted, structured market data -> no guardrail / approval.
    serve(mcp, port=port)


if __name__ == "__main__":
    main()
