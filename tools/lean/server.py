"""lean -- QuantConnect Lean backtesting over MCP.

The engine has ONE operation: run an algorithm file against a config and emit a
results JSON. The agent writes the QCAlgorithm Python class itself (the whole Lean
API surface lives in that code, not in tools here), so this server only owns the
invocation path: write algorithm -> generate config -> run the launcher as a
subprocess -> parse the results. No lean-cli anywhere: the CLI drives Docker, which
a walled container must not do; we call the compiled launcher directly.

Runs INSIDE the pinned quantconnect/lean image (see Dockerfile), so the engine,
its miniconda Python, and the sample data under /Lean/Data are all present.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

# Make the repo root importable regardless of CWD, then load the shared serve()
# helper (applies OAuth + optional guardrail/approval, then runs).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

# Engine locations inside the quantconnect/lean image; env-overridable for other
# substrates (e.g. a host checkout during development).
LAUNCHER_DIR = Path(os.getenv("LEAN_LAUNCHER_DIR", "/Lean/Launcher/bin/Debug"))
DATA_FOLDER = os.getenv("LEAN_DATA_FOLDER", "/Lean/Data")
BACKTESTS = Path(os.getenv("LEAN_BACKTESTS_DIR", "/app/state/backtests"))
MAX_RUN_SECONDS = int(os.getenv("LEAN_MAX_RUN_SECONDS", "1800"))
LOG_TAIL_LINES = 60
MAX_EQUITY_POINTS = 500

mcp = FastMCP(name="lean")

_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\([^)]*QCAlgorithm[^)]*\)", re.MULTILINE)
_SLUG_RE = re.compile(r"[^a-zA-Z0-9-]+")


def _build_config(job: Path, backtest_id: str, class_name: str, parameters: dict) -> dict:
    """A complete backtesting config for the launcher (what lean-cli would generate).

    All paths are absolute because the subprocess runs with cwd=job (the only
    writable place: the engine drops log.txt in cwd); composer-dll-directory must
    then point back at the launcher bin so plugin assemblies still resolve.
    """
    return {
        "environment": "backtesting",
        "algorithm-type-name": class_name,
        "algorithm-language": "Python",
        "algorithm-location": str(job / "main.py"),
        "algorithm-id": backtest_id,  # names the results file: <job>/<id>.json
        "composer-dll-directory": str(LAUNCHER_DIR),
        "data-folder": DATA_FOLDER,
        "results-destination-folder": str(job),
        "object-store-root": str(job / "storage"),
        "debugging": False,
        "show-missing-data-logs": True,
        # get_parameter() values arrive as strings, same as the cloud.
        "parameters": {str(k): str(v) for k, v in parameters.items()},
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "data-channel-provider": "DataChannelProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
        "environments": {
            "backtesting": {
                "live-mode": False,
                "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
                "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
                "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
                "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
                "history-provider": [
                    "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
                ],
                "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
            }
        },
    }


def _tail(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def _read_meta(job: Path) -> dict | None:
    meta = job / "meta.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(job: Path, meta: dict) -> None:
    (job / "meta.json").write_text(json.dumps(meta, indent=2))


def _result_path(job: Path, backtest_id: str) -> Path:
    return job / f"{backtest_id}.json"


def _downsample(values: list, limit: int = MAX_EQUITY_POINTS) -> list:
    if len(values) <= limit:
        return values
    step = len(values) / limit
    sampled = [values[int(i * step)] for i in range(limit)]
    sampled[-1] = values[-1]  # always keep the final point
    return sampled


@mcp.tool
def backtest(
    code: str,
    name: str = "",
    parameters: dict | None = None,
    timeout_seconds: int = 600,
) -> dict:
    """Run a Lean backtest of a Python QCAlgorithm and return its statistics.

    ``code`` is a complete algorithm module defining exactly one
    ``class <Name>(QCAlgorithm)`` (start with ``from AlgorithmImports import *``;
    set start/end dates, cash, and universe inside ``initialize``). ``parameters``
    become ``self.get_parameter(...)`` values. Data available: the Lean sample set
    (e.g. SPY equity minute/daily). Runs synchronously -- typically tens of
    seconds. Fetch full results later with backtest_result(id).
    """
    match = _CLASS_RE.search(code)
    if not match:
        return {
            "status": "invalid",
            "error": "No `class <Name>(QCAlgorithm)` found in code; submit a complete "
            "algorithm module (subclassing QCAlgorithm directly).",
        }
    class_name = match.group(1)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _SLUG_RE.sub("-", name or class_name).strip("-").lower() or "backtest"
    backtest_id = f"{stamp}-{slug}"
    job = BACKTESTS / backtest_id
    job.mkdir(parents=True)

    (job / "main.py").write_text(code)
    config = _build_config(job, backtest_id, class_name, parameters or {})
    (job / "config.json").write_text(json.dumps(config, indent=2))

    meta = {
        "id": backtest_id,
        "name": name or class_name,
        "class": class_name,
        "created": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    _write_meta(job, meta)

    timeout = max(30, min(timeout_seconds, MAX_RUN_SECONDS))
    console = job / "console.log"
    try:
        with console.open("w") as out:
            proc = subprocess.run(
                ["dotnet", str(LAUNCHER_DIR / "QuantConnect.Lean.Launcher.dll"),
                 "--config", str(job / "config.json")],
                cwd=job, stdout=out, stderr=subprocess.STDOUT, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        meta["status"] = "timeout"
        _write_meta(job, meta)
        return {
            "status": "timeout",
            "id": backtest_id,
            "error": f"Engine exceeded {timeout}s; narrow the date range or universe.",
            "log_tail": _tail(console),
        }

    result_file = _result_path(job, backtest_id)
    if proc.returncode != 0 or not result_file.exists():
        meta["status"] = "failed"
        _write_meta(job, meta)
        return {
            "status": "failed",
            "id": backtest_id,
            "exit_code": proc.returncode,
            # Algorithm errors (syntax, runtime, missing data) land in the engine log.
            "log_tail": _tail(job / "log.txt") or _tail(console),
        }

    data = json.loads(result_file.read_text())
    stats = data.get("statistics", {})
    meta.update(status="completed", statistics=stats)
    _write_meta(job, meta)
    return {
        "status": "completed",
        "id": backtest_id,
        "statistics": stats,
        "runtime_statistics": data.get("runtimeStatistics", {}),
        "orders": len(data.get("orders", {})),
    }


@mcp.tool
def backtest_result(backtest_id: str, include_equity_curve: bool = False) -> dict:
    """Full results of a finished backtest: statistics, per-trade/portfolio stats,
    order events, and (optionally) the equity curve downsampled to ~500 points."""
    job = BACKTESTS / backtest_id
    result_file = _result_path(job, backtest_id)
    if not result_file.exists():
        meta = _read_meta(job)
        return {
            "status": (meta or {}).get("status", "not_found"),
            "id": backtest_id,
            "error": "No results file for this id."
            + ("" if meta else " Unknown backtest id; see list_backtests()."),
            "log_tail": _tail(job / "log.txt"),
        }

    data = json.loads(result_file.read_text())
    total = data.get("totalPerformance") or {}
    out = {
        "status": "completed",
        "id": backtest_id,
        "statistics": data.get("statistics", {}),
        "runtime_statistics": data.get("runtimeStatistics", {}),
        "trade_statistics": total.get("tradeStatistics", {}),
        "portfolio_statistics": total.get("portfolioStatistics", {}),
        "orders": list(data.get("orders", {}).values()),
    }
    if include_equity_curve:
        series = (
            data.get("charts", {}).get("Strategy Equity", {}).get("series", {})
            .get("Equity", {}).get("values", [])
        )
        out["equity_curve"] = _downsample(series)
    return out


@mcp.tool
def list_backtests() -> list[dict]:
    """All backtests on this server (newest first): id, name, status, key stats."""
    entries = []
    if BACKTESTS.exists():
        for job in sorted(BACKTESTS.iterdir(), reverse=True):
            meta = _read_meta(job)
            if meta:
                entries.append(meta)
    return entries


def main() -> None:
    BACKTESTS.mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("MCP_PORT", "8064"))
    # Trusted internal tool: it runs agent-authored code against local data and
    # returns engine output (no untrusted external content -> no guardrail leg).
    serve(mcp, port=port)


if __name__ == "__main__":
    main()
