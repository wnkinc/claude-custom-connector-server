"""Tests for the persistence (lake), OpenBB-fetch (feeds), and Lean-export layers.

``lake`` is exercised against an in-process tmp lake (no network). ``feeds`` is tested
with a fake ``obb`` so the right OpenBB endpoint + args are asserted without hitting
the provider. ``lean_export`` is asserted against golden lines taken verbatim from the
data bundled in the quantconnect/lean engine image — the format is Lean's contract.
"""

import json
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest

import feeds
import lake
import lean_export


def _frame(dates, close):
    """An OpenBB-shaped OHLCV frame: a ``date``-indexed DataFrame (persisted as-is)."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="date")
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [100] * len(close),
        },
        index=idx,
    )


# ── lake: generic parquet persistence ───────────────────────────────────────


def test_path_for_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert lake.path_for("crypto", "tiingo", "BTCUSD", "1d") == (
        tmp_path / "crypto" / "tiingo" / "BTCUSD" / "1d.parquet"
    )


def test_path_for_safe_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    # '/' is the one genuinely problematic char on Linux (e.g. "BTC/USD").
    assert "BTC_USD" in str(lake.path_for("crypto", "tiingo", "BTC/USD", "1d"))


def test_ingest_persists_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    s = lake.ingest(
        ("crypto", "tiingo", "BTCUSD", "1d"), _frame(["2024-01-02", "2024-01-03"], [1.5, 2.0])
    )
    assert s["key"] == "crypto/tiingo/BTCUSD/1d"
    assert s["rows"] == 2 and s["fetched"] == 2 and s["added"] == 2

    back = lake.read("crypto", "tiingo", "BTCUSD", "1d")
    assert list(back["close"]) == [1.5, 2.0]
    assert back.index.name == "date"


def test_ingest_merges_dedupes_and_appends(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    key = ("crypto", "tiingo", "BTCUSD", "1d")

    lake.ingest(key, _frame(["2024-01-01", "2024-01-02"], [1.0, 2.0]))
    # Re-fetch overlaps 01-02 (corrected close) and adds 01-03.
    s = lake.ingest(key, _frame(["2024-01-02", "2024-01-03"], [22.0, 3.0]))

    assert s["fetched"] == 2 and s["added"] == 1 and s["rows"] == 3  # only 01-03 is net-new
    back = lake.read(*key)
    assert list(back.index.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert back.loc["2024-01-02", "close"] == 22.0  # fetched row wins the dedupe


def test_refresh_replaces_stored_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    key = ("crypto", "tiingo", "BTCUSD", "1d")

    lake.ingest(key, _frame(["2024-01-01", "2024-01-02"], [1.0, 2.0]))
    s = lake.ingest(key, _frame(["2024-06-01"], [9.0]), refresh=True)

    assert s["rows"] == 1 and s["added"] == 1  # old rows gone, not merged
    assert list(lake.read(*key).index.strftime("%Y-%m-%d")) == ["2024-06-01"]


def test_ingest_empty_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        lake.ingest(("crypto", "tiingo", "BTCUSD", "1d"), _frame([], []))


def test_read_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert lake.read("crypto", "tiingo", "NOPE", "1d") is None


def test_catalog_lists_keys_with_rows_and_span(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    lake.ingest(
        ("crypto", "tiingo", "ETHUSD", "1m"), _frame(["2024-01-02", "2024-01-03"], [1.0, 2.0])
    )
    lake.ingest(("crypto", "tiingo", "BTCUSD", "1d"), _frame(["2024-01-02"], [5.0]))

    cat = {e["key"]: e for e in lake.catalog()}
    assert set(cat) == {"crypto/tiingo/ETHUSD/1m", "crypto/tiingo/BTCUSD/1d"}
    eth = cat["crypto/tiingo/ETHUSD/1m"]
    assert eth["rows"] == 2  # from the parquet footer, no data load
    assert eth["start"].startswith("2024-01-02") and eth["end"].startswith("2024-01-03")


def test_catalog_prefix_narrows_to_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    lake.ingest(("crypto", "tiingo", "BTCUSD", "1d"), _frame(["2024-01-02"], [5.0]))
    lake.ingest(("scratch", "tiingo", "X", "1d"), _frame(["2024-01-02"], [1.0]))

    keys = [e["key"] for e in lake.catalog("crypto")]
    assert keys == ["crypto/tiingo/BTCUSD/1d"]


def test_catalog_empty_lake_is_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert lake.catalog() == []
    assert lake.catalog("crypto") == []


# ── server: data-catalog (inventory) vs data-read (series) ───────────────────


def test_data_catalog_lists_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    lake.ingest(("crypto", "tiingo", "BTCUSD", "1d"), _frame(["2024-01-02"], [1.0]))
    lake.ingest(("crypto", "tiingo", "ETHUSD", "1h"), _frame(["2024-01-02"], [1.0]))
    out = server.data_catalog()  # whole lake
    assert "crypto/tiingo/BTCUSD/1d" in out and "crypto/tiingo/ETHUSD/1h" in out


def test_data_catalog_empty_lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    assert "empty" in server.data_catalog().lower()


def test_data_read_miss_hints_what_is_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    lake.ingest(("crypto", "tiingo", "BTCUSD", "1m"), _frame(["2024-01-02"], [1.0]))
    # ask for the default 1d that doesn't exist -> hint points at the stored 1m
    out = server.data_read("crypto", "BTCUSD")
    assert "No crypto/tiingo/BTCUSD/1d" in out and "crypto/tiingo/BTCUSD/1m" in out


def test_data_read_hit_returns_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    lake.ingest(
        ("crypto", "tiingo", "BTCUSD", "1m"), _frame(["2024-01-02", "2024-01-03"], [1.0, 2.0])
    )
    out = server.data_read("crypto", "BTCUSD", "1m", "tiingo", tail=1)
    assert "1m crypto bars for BTCUSD (tiingo)" in out


def test_data_chart_hit_returns_json_with_capped_bars(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    lake.ingest(
        ("crypto", "tiingo", "BTCUSD", "1d"),
        _frame(["2024-01-02", "2024-01-03", "2024-01-04"], [1.0, 2.0, 3.0]),
    )
    out = json.loads(server.data_chart("crypto", "BTCUSD", bars=2))
    assert out["symbol"] == "BTCUSD" and out["stored_rows"] == 3
    # last `bars` of the stored series, each row [ts, o, h, l, c, v]
    assert [row[4] for row in out["bars"]] == [2.0, 3.0]


def test_data_chart_miss_is_json_error_with_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import server

    lake.ingest(("crypto", "tiingo", "BTCUSD", "1m"), _frame(["2024-01-02"], [1.0]))
    # deliberately JSON (not data-read's plain text): the widget renders the miss too
    out = json.loads(server.data_chart("crypto", "BTCUSD"))
    assert "No crypto/tiingo/BTCUSD/1d" in out["error"]
    assert "crypto/tiingo/BTCUSD/1m" in out["error"]


# ── feeds: OpenBB endpoint wrapper (fake obb, no network) ────────────────────


def _fake_obb(caps, df):
    """A stand-in ``obb`` whose <namespace>.price.historical records its kwargs into caps[ns]."""

    def endpoint(cap):
        def historical(**kwargs):
            cap.update(kwargs)
            return SimpleNamespace(to_df=lambda: df)

        return SimpleNamespace(price=SimpleNamespace(historical=historical))

    return SimpleNamespace(**{ns: endpoint(cap) for ns, cap in caps.items()})


def test_crypto_bars_calls_crypto_endpoint(monkeypatch):
    caps = {"crypto": {}}
    df = _frame(["2024-01-02"], [1.0])
    monkeypatch.setattr(feeds, "_obb", lambda: _fake_obb(caps, df))

    out = feeds.crypto_bars("BTCUSD", "1d", "2024-01-01", "2024-01-10")
    assert out is df
    assert caps["crypto"] == {
        "symbol": "BTCUSD",
        "interval": "1d",
        "start_date": "2024-01-01",
        "end_date": "2024-01-10",
        "provider": "tiingo",  # the fixed default provider
    }


# ── feeds: Tiingo intraday pagination (the 10k-bar cap walk) ──────────────────


def test_intraday_pagination_assembles_full_range(monkeypatch):
    """A capped, end-anchored tiingo intraday endpoint is paged into the full window."""
    monkeypatch.setattr(feeds, "_TIINGO_CAP", 10)  # tiny cap so 25 bars need multiple pages
    universe = pd.DataFrame(
        {"close": range(25)},
        index=pd.DatetimeIndex(
            pd.date_range("2021-01-01", periods=25, freq="D", tz="UTC"), name="date"
        ),
    )
    ends = []

    def fake_fetch(namespace, symbol, interval, start, end, provider):
        ends.append(end)
        sub = universe
        if start is not None:
            sub = sub[sub.index.date >= pd.Timestamp(start).date()]
        if end is not None:
            sub = sub[sub.index.date <= pd.Timestamp(end).date()]
        return sub.tail(feeds._TIINGO_CAP)  # end-anchored cap, like Tiingo

    monkeypatch.setattr(feeds, "_fetch", fake_fetch)

    out = feeds.crypto_bars("BTCUSD", "1m", "2021-01-01", "2021-01-25", provider="tiingo")
    assert len(out) == 25  # full range assembled despite the cap
    assert out.index.is_monotonic_increasing and not out.index.duplicated().any()
    assert len(ends) >= 3  # took several backward pages, not one call


def test_intraday_pagination_partial_on_rate_limit(monkeypatch):
    """A mid-walk failure keeps the pages already fetched and marks the frame partial."""
    monkeypatch.setattr(feeds, "_TIINGO_CAP", 10)
    universe = pd.DataFrame(
        {"close": range(25)},
        index=pd.DatetimeIndex(
            pd.date_range("2021-01-01", periods=25, freq="D", tz="UTC"), name="date"
        ),
    )
    state = {"calls": 0}

    def fake_fetch(namespace, symbol, interval, start, end, provider):
        state["calls"] += 1
        if state["calls"] == 3:  # 3rd request 429s, like Tiingo's hourly cap
            raise RuntimeError("You have run over your hourly request allocation")
        sub = universe
        if start is not None:
            sub = sub[sub.index.date >= pd.Timestamp(start).date()]
        if end is not None:
            sub = sub[sub.index.date <= pd.Timestamp(end).date()]
        return sub.tail(feeds._TIINGO_CAP)

    monkeypatch.setattr(feeds, "_fetch", fake_fetch)

    out = feeds.crypto_bars("BTCUSD", "1m", "2021-01-01", "2021-01-25", provider="tiingo")
    assert out.attrs.get("partial") == "Tiingo hourly rate limit reached"
    assert 0 < len(out) < 25  # kept the 2 pages fetched before the failure, not all 25
    assert not out.index.duplicated().any()


def test_daily_never_paginates(monkeypatch):
    """Daily bars skip the pager even at/over the cap (they don't hit Tiingo's intraday limit)."""
    calls = {"n": 0}

    def fake_fetch(namespace, symbol, interval, start, end, provider):
        calls["n"] += 1
        return _frame([f"2024-01-{d:02d}" for d in range(1, 21)], list(range(20)))

    monkeypatch.setattr(feeds, "_TIINGO_CAP", 5)
    monkeypatch.setattr(feeds, "_fetch", fake_fetch)
    feeds.crypto_bars("BTCUSD", "1d", "2024-01-01", "2024-01-20", provider="tiingo")
    assert calls["n"] == 1  # exactly one fetch — no paging for daily


# ── feeds: provider credential injection (no OpenBB import) ───────────────────


def test_apply_credentials_injects_token_from_env(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "tok-123")
    creds = SimpleNamespace(tiingo_token=None)
    feeds._apply_credentials(SimpleNamespace(user=SimpleNamespace(credentials=creds)))
    assert creds.tiingo_token == "tok-123"


def test_apply_credentials_noop_without_env(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    creds = SimpleNamespace(tiingo_token=None)
    feeds._apply_credentials(SimpleNamespace(user=SimpleNamespace(credentials=creds)))
    assert creds.tiingo_token is None


# ── lean_export: lake -> Lean on-disk format (golden lines from the engine image) ──


def _btc_golden_frame():
    # Matches the first/last rows of the engine image's bundled
    # crypto/coinbase/daily/btcusd_trade.zip exactly.
    return pd.DataFrame(
        {
            "open": [300.0, 6316.0],
            "high": [370.0, 6544.99],
            "low": [300.0, 6139.57],
            "close": [370.0, 6253.67],
            "volume": [0.05655554, 10178.43146644],
        },
        index=pd.to_datetime(["2014-12-01", "2018-08-13"]),
    )


def test_export_daily_matches_bundled_format(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_DATA_ROOT", str(tmp_path))
    s = lean_export.export_crypto(_btc_golden_frame(), "BTCUSD", "1d", market="coinbase")

    assert s["dest"].endswith("crypto/coinbase/daily/btcusd_trade.zip")
    with zipfile.ZipFile(s["dest"]) as z:
        assert z.namelist() == ["btcusd.csv"]
        lines = z.read("btcusd.csv").decode().splitlines()
    assert lines[0] == "20141201 00:00,300,370,300,370,0.05655554"
    assert lines[1] == "20180813 00:00,6316,6544.99,6139.57,6253.67,10178.43146644"


def test_export_is_world_readable(tmp_path, monkeypatch):
    # mkstemp creates 0600; the lean container reads as a DIFFERENT uid, so the
    # exporter must chmod before the atomic rename (regression: engine got EACCES).
    monkeypatch.setenv("LEAN_DATA_ROOT", str(tmp_path))
    s = lean_export.export_crypto(_btc_golden_frame(), "BTCUSD", "1d")
    from pathlib import Path

    assert Path(s["dest"]).stat().st_mode & 0o044 == 0o044


def test_export_minute_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_DATA_ROOT", str(tmp_path))
    m = pd.DataFrame(
        {"open": [100.5], "high": [101.0], "low": [100.0], "close": [100.75], "volume": [2.5]},
        index=pd.to_datetime(["2024-03-05 00:01:00"]),
    )
    s = lean_export.export_crypto(m, "ETHUSD", "1m", market="coinbase")

    with zipfile.ZipFile(f"{s['dest']}/20240305_trade.zip") as z:
        assert z.namelist() == ["20240305_ethusd_minute_trade.csv"]
        assert z.read(z.namelist()[0]).decode().splitlines() == [
            "60000,100.5,101,100,100.75,2.5"  # ms since midnight, raw decimals
        ]


def test_export_rejects_unknown_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_DATA_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="resolution"):
        lean_export.export_crypto(_btc_golden_frame(), "BTCUSD", "5m")


def test_export_rejects_missing_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_DATA_ROOT", str(tmp_path))
    bad = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2024-01-01"]))
    with pytest.raises(ValueError, match="lacks columns"):
        lean_export.export_crypto(bad, "BTCUSD", "1d")
