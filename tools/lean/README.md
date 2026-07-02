# lean — QuantConnect Lean backtesting over MCP

Self-hosted [QuantConnect Lean](https://github.com/QuantConnect/Lean) engine with a
thin MCP wrapper (`:8064`). Replaces the retired quant tool: instead of a tool schema
expressive enough to describe strategies, **the input is a program** — the agent
writes a complete Python `QCAlgorithm` class and submits it; the whole Lean API
surface lives in that code, not in tools here.

## How it runs

The container's base image IS the pinned `quantconnect/lean` engine image (compiled
launcher, its miniconda Python 3.11, sample data under `/Lean/Data`); the wrapper
lives in its own venv on top and invokes `dotnet QuantConnect.Lean.Launcher.dll`
as a subprocess per backtest, with a generated `config.json`. Deliberately **no
lean-cli**: the CLI runs backtests by launching Docker containers, which a walled
tool container must not do.

Each run gets a job dir under `/app/state/backtests/<id>/` (algorithm, config,
engine log, results JSON) — the state volume is the audit trail of everything the
agent has executed.

## Tools

- `backtest(code, name?, parameters?, timeout_seconds?)` — run a Python QCAlgorithm,
  synchronously; returns the statistics map (or the engine log tail on failure, so
  the agent can iterate). `parameters` surface as `self.get_parameter(...)`.
- `backtest_result(id, include_equity_curve?)` — full stats, trade/portfolio
  statistics, orders, optional downsampled equity curve.
- `list_backtests()` — id/name/status/stats of every run on this server.

## Scope

Backtesting only, against the sample data baked into the engine image (SPY et al.).
Feeding the parquet lake from the data tool into Lean's data format is a planned
follow-up; optimization (`Optimizer.Launcher` is present in the image) and report
generation are natural phase-two tools. Live trading is out of scope — an agent
tool that places real orders needs its own human-in-the-loop design first.

Engine egress: none (local data only); the allowlist carries just the Google OAuth
hosts for auth-on serving. Pinned versions: image by digest in the Dockerfile
(`quantconnect/lean:17886`), matching the read-only reference clone at `vendor/Lean`.
