"""Nautilus Trader engine: target-position series → event-driven backtest stats.

Where vectorbt is vectorized, Nautilus is event-driven: it streams bars through a
``BacktestEngine`` and calls a ``Strategy`` per bar. We reuse the same engine-agnostic
position from ``engines.core`` — a small follower strategy just submits market orders to
make the simulated net position match the target each bar (all-in long / flat), so the
*decision* stays identical to every other engine and only the execution differs.

All ``nautilus_trader`` imports are lazy (inside ``run``) so this module imports — and the
vectorbt path keeps working — even where Nautilus isn't installed, and so listing engines
in ``engines.core`` costs nothing until Nautilus is actually used.
"""
from __future__ import annotations

# Canonical interval → Nautilus bar aggregation spec. Months are excluded (no fixed-length
# aggregation + the analyzer annualizes on a 252-day basis), matching the vectorbt engine.
_BAR_SPEC = {
    "1m": "1-MINUTE", "5m": "5-MINUTE", "15m": "15-MINUTE", "30m": "30-MINUTE",
    "1h": "1-HOUR", "1d": "1-DAY", "1wk": "1-WEEK",
}

_CAPITAL = 1_000_000  # starting cash (USD); returns are reported as % so the scale is moot.


def _num(v):
    """Best-effort JSON-safe scalar: float when numeric, else str."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def run(ohlcv, position, interval: str) -> dict:
    """Simulate the position with Nautilus and return native analyzer stats."""
    if interval not in _BAR_SPEC:
        raise ValueError(
            f"Interval {interval!r} isn't supported by the nautilus engine. "
            f"Supported: {sorted(_BAR_SPEC)}. Use daily 1d (the default)."
        )

    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    venue = Venue("XNAS")
    instrument = TestInstrumentProvider.equity(symbol="BT", venue="XNAS")
    bar_type = BarType.from_str(f"{instrument.id}-{_BAR_SPEC[interval]}-LAST-EXTERNAL")

    # Build Bars directly rather than via BarDataWrangler: the wrangler maps over
    # ``DataFrame.values`` as *writable* memoryviews, and an upstream OHLCV array routinely
    # arrives read-only here (numba freezes the buffer's writeable flag when pandas-ta runs
    # in the DAG, and pandas hands back read-only blocks besides). Reading scalars is always
    # allowed, so per-row construction sidesteps that entirely. high/low are clamped to the
    # row's range so minor vendor inconsistencies don't trip Nautilus's bar validation.
    o = ohlcv["open"].to_numpy()
    h = ohlcv["high"].to_numpy()
    low = ohlcv["low"].to_numpy()
    c = ohlcv["close"].to_numpy()
    v = ohlcv["volume"].to_numpy() if "volume" in ohlcv else None
    ts = ohlcv.index.as_unit("ns").asi8  # int64 ns since epoch, UTC
    pp, sp = instrument.price_precision, instrument.size_precision
    bars = []
    for i in range(len(ohlcv)):
        oi, ci = float(o[i]), float(c[i])
        hi = max(oi, ci, float(h[i]))
        lo = min(oi, ci, float(low[i]))
        vol = float(v[i]) if v is not None else 1_000_000.0
        bars.append(
            Bar(
                bar_type,
                Price(oi, pp), Price(hi, pp), Price(lo, pp), Price(ci, pp),
                Quantity(vol, sp),
                ts_event=int(ts[i]), ts_init=int(ts[i]),
            )
        )

    # Target position per bar, in the same chronological order the bars stream.
    # Force flat on the final bar so the ending cash balance reflects realized equity
    # (a CASH account otherwise shows depleted cash while still holding shares).
    targets = [float(x) for x in position.fillna(0.0).values]
    if targets:
        targets[-1] = 0.0

    class _Follower(Strategy):
        def on_start(self) -> None:
            self._i = 0
            self.subscribe_bars(bar_type)

        def on_bar(self, bar) -> None:
            target = targets[self._i] if self._i < len(targets) else 0.0
            self._i += 1
            net = self.portfolio.net_position(instrument.id)
            if target > 0 and net == 0:
                free = self.portfolio.account(venue).balance_free(USD).as_double()
                qty = int((free * 0.95) // bar.close.as_double())
                if qty > 0:
                    self.submit_order(
                        self.order_factory.market(
                            instrument.id, OrderSide.BUY, Quantity.from_int(qty)
                        )
                    )
            elif target <= 0 and net > 0:
                self.close_all_positions(instrument.id)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="BACKTESTER-001",
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    try:
        engine.add_venue(
            venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(_CAPITAL, USD)],
            base_currency=USD,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(_Follower())
        engine.run()

        analyzer = engine.portfolio.analyzer
        account = engine.trader.generate_account_report(venue)
        positions = engine.trader.generate_positions_report()

        try:
            ending = float(account["total"].iloc[-1]) if len(account) else float(_CAPITAL)
        except (KeyError, ValueError, IndexError):
            ending = float(_CAPITAL)

        def _stats(fn) -> dict:
            try:
                return {k: _num(v) for k, v in (fn() or {}).items()}
            except Exception:  # noqa: BLE001 — a missing stat must not sink the whole run
                return {}

        return {
            "starting_balance": float(_CAPITAL),
            "ending_balance": ending,
            "total_return_pct": (ending - _CAPITAL) / _CAPITAL * 100.0,
            "total_positions": int(len(positions)),
            "returns": _stats(analyzer.get_performance_stats_returns),
            "pnl": _stats(lambda: analyzer.get_performance_stats_pnls(USD)),
        }
    finally:
        engine.dispose()
