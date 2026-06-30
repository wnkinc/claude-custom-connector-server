"""Backtest engines for the quant tool.

``engines.core`` does the engine-agnostic pre-flight (lake → Hamilton target-position
series) once and dispatches to an engine by name. Each engine is a subpackage
(``vectorbt`` vectorized, ``nautilus`` event-driven) exposing ``run(ohlcv, position,
interval) -> stats``: it consumes the shared position and owns only its native
simulation. Adding an engine is a new ``run`` + one ``_ENGINES`` entry in ``core`` — no
new MCP tool. ``engines.lake`` is the shared read-only reader over the data tool's lake.
"""
