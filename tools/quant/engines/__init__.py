"""Backtest engines for the quant tool.

Each engine is a subpackage (``vectorbt`` first; nautilus etc. later) that consumes
the shared OHLCV lake (``engines.lake``) and the Hamilton signal library
(``catalog.materialize``). Engines share the lake reader but own their strategy /
entry-exit semantics, expressed in that engine's native form.
"""
