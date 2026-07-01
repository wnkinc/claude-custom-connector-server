# Nautilus engine

Event-driven backtest engine for the quant tool. Where `vectorbt` is vectorized
(arrays of entries/exits, fast, good for sweeps), Nautilus streams bars through a
`BacktestEngine` and calls a `Strategy` per bar — realistic fills, broker/cash
accounting, path-dependent execution. Both engines are reached through the single
`backtest` MCP tool via `engine=` (`engines/core.py` dispatches by name).

## How the engine-agnostic strategy works

**No strategy logic lives in this folder.** The decision is computed once, upstream,
and the engine only *replays* it.

The pipeline (in `engines/core.py:prepare`) is the same for every engine:

```
lake OHLCV ─► Hamilton DAG (library/) ─► target-position series ─► engine
                                          1.0 = long, 0.0 = flat
```

A "strategy" is a Hamilton node in `library/strategies.py` that outputs a
**target-position series** — the desired exposure per bar, in neutral units. That
series is the only thing an engine receives (`run(ohlcv, position, interval)`); it
carries no entries/exits, no thresholds, no indicator math. So:

- `mean_reversion`'s RSI/threshold/hysteresis logic is written once and is identical
  whether you run vectorbt or Nautilus.
- Adding an engine never touches strategy code, and adding a strategy never touches
  engine code. The position series is the contract between the two halves.

Each engine's job is just to translate that neutral position into its own order
primitives:

- **vectorbt** — `position → boolean entries/exits → Portfolio.from_signals`.
- **nautilus** — the `_Follower` strategy reads the precomputed `targets[i]` on each
  `on_bar` and submits market orders so the simulated **net position** matches the
  target: buy (~95% of free cash) when target is long and we're flat, close when the
  target goes flat. It never recomputes the decision; it only chases the target.

Two Nautilus-specific accounting choices (not strategy logic, just how this engine is
set up): a `CASH` account starting at 1,000,000 USD, and the position is **forced flat
on the final bar** so the ending cash balance equals realized equity (a cash account
otherwise reports depleted cash while still holding shares).

> Because the two engines share the decision but differ in execution (vectorbt fills at
> the signal bar and goes all-in; Nautilus fills on the *next* bar with integer share
> sizing), their returns diverge — slightly on daily data, a lot over hundreds of
> thousands of intraday bars. That divergence is the point of having both, not a bug.

## Two non-obvious things this engine had to solve

### 1. Read-only OHLCV buffers → build `Bar`s directly, skip `BarDataWrangler`

The obvious way to get bars into Nautilus is `BarDataWrangler(...).process(df)`. It
crashes here with:

```
ValueError: buffer source array is read-only
```

The wrangler maps over `DataFrame.values` row-by-row as **writable** Cython
memoryviews. But by the time we reach the engine, the OHLCV arrays routinely arrive
**read-only**:

- numba freezes an array's `writeable` flag when pandas-ta runs inside the Hamilton DAG
  (`catalog.materialize`), and that frozen array is shared by the whole OHLCV block; and
- pandas hands back read-only `.values` blocks of its own accord in several cases, so
  `.copy()` / rebuilding the DataFrame does **not** reliably make `.values` writable
  (confirmed: a writable 2-D array fed to `pd.DataFrame(...)` still yields a read-only
  `.values`). Under numpy 2.x a same-dtype `np.array(x)` returns a *view*, not a copy,
  which made the naive fixes look like they should work but didn't.

**Fix:** bypass the wrangler entirely and construct `Bar` objects in a Python loop,
reading **scalars** out of the arrays (`float(o[i])`, …). Reading from a read-only array
is always allowed — only the wrangler's write-memoryview path is blocked. As a bonus we
clamp `high = max(o,c,h)` / `low = min(o,c,l)` per row so minor vendor OHLC
inconsistencies don't trip Nautilus's strict bar validation.

If you ever revisit this: don't reach for the wrangler again expecting a `.copy()` to
save you — it won't. The direct-construction loop is the reliable path.

### 2. Lazy imports → the vectorbt path survives without Nautilus installed

Every `nautilus_trader` import lives **inside `run()`**, not at module top level.

`engines/core.py` imports this module at startup to register it in `_ENGINES`. If the
imports were at module scope, that registration would:

- import the heavy Rust/JIT stack on every server start (slow), and
- hard-fail a checkout where Nautilus isn't installed — taking the working vectorbt
  engine down with it.

With the imports deferred, `engines.core` lists both engines while importing **neither**
heavy library; Nautilus only loads the first time someone actually runs `engine="nautilus"`.
(Verified: importing `engines.core` pulls in neither `nautilus_trader` nor `vectorbt`.)
Keep new imports inside `run()` for the same reason.

## Stats

Returns Nautilus's native analyzer output (not normalized to vectorbt's shape):
`starting_balance`, `ending_balance`, `total_return_pct`, `total_positions`, plus the
analyzer's `returns` and `pnl` stat dicts (Sharpe, Sortino, win rate, …). The envelope
around the stats (`engine`, `symbol`, `strategy`, …) is added uniformly by
`engines.core` so runs are comparable at the metadata level.

## Deployment note

The live `mcp-quant` container runs on a read-only rootfs (non-root). Nautilus here runs
in-memory (`bypass_logging=True`, no persistence catalog), so it doesn't need disk — but
if a future change makes it want a writable scratch/cache path, point it at the
`/app/state` volume like numba's JIT cache (`NUMBA_CACHE_DIR`) already does.
