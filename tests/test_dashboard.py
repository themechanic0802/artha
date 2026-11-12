"""Dashboard, live status and download retries. All offline."""
import json
from datetime import datetime

import pandas as pd

from artha import dashboard, live_status
from artha.data import yfinance_source
from artha.settings import IST
from test_core import at, candle, feed_opening_range, session


def _live_session():
    s, b = session()
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=2000)})  # breakout
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})           # filled
    return s, b


def test_snapshot_describes_open_position():
    s, b = _live_session()
    snap = live_status.snapshot(s, 10000, "live", ["a data problem"])
    json.dumps(snap, default=str)      # must serialise
    assert snap["positions"][0]["ticker"] == "X"
    assert snap["stocks"][0]["state"] == "in trade"
    assert snap["stocks"][0]["or_low"] == 99 and snap["stocks"][0]["or_high"] == 101
    assert any(e["kind"] == "signal_taken" for e in snap["events"])
    assert snap["equity_now"] == 10000 + snap["realised_pnl"] + snap["open_pnl"]


def _fake_root(tmp_path, monkeypatch):
    for d in ("reports", "journal", "data_cache", "logs", "config"):
        (tmp_path / d).mkdir()
    for name, d in {"REPORTS_DIR": "reports", "JOURNAL_DIR": "journal", "CACHE_DIR": "data_cache",
                    "LOGS_DIR": "logs"}.items():
        monkeypatch.setattr(dashboard, name, tmp_path / d)
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "reports" / "dashboard.html")
    monkeypatch.setattr(dashboard, "launchd_jobs", lambda: {j: "scheduled" for j in dashboard.JOBS})
    return tmp_path


def test_dashboard_builds_with_no_files(tmp_path, monkeypatch):
    _fake_root(tmp_path, monkeypatch)
    page = dashboard.build(datetime(2026, 11, 9, 7, 0, tzinfo=IST))
    assert "Artha" in page and "Market day" in page and "No watchlist" in page


def test_dashboard_shows_live_day(tmp_path, monkeypatch):
    root = _fake_root(tmp_path, monkeypatch)
    s, b = _live_session()
    (root / "data_cache" / "live-2026-11-09.json").write_text(
        json.dumps(live_status.snapshot(s, 10000, "live", []), default=str))
    (root / "data_cache" / "watchlist-2026-11-09.json").write_text(json.dumps(
        {"web_check": "ok", "exclusions": {"ABC": {"reason": "results today", "source": "x"}},
         "market": {"ok": True, "close": 25030, "sma": 23116}, "watchlist": []}))
    (root / "journal" / "2026-11-09.md").write_text("<script>alert(1)</script> hi")
    page = dashboard.build(datetime(2026, 11, 9, 10, 0, tzinfo=IST))
    assert "BOUGHT" in page and "in trade" in page and "Excluded: <b>ABC</b>" in page
    assert "<script>alert" not in page          # LLM-written text is never raw HTML
    assert dashboard.write(datetime(2026, 11, 9, 10, 0, tzinfo=IST)).exists()


def test_dashboard_on_holiday_points_to_next_day(tmp_path, monkeypatch):
    _fake_root(tmp_path, monkeypatch)
    page = dashboard.build(datetime(2026, 11, 10, 12, 0, tzinfo=IST))
    assert "Market closed today" in page and "Wednesday 11 Nov" in page


def test_download_retries_missing_symbols(monkeypatch):
    calls = []
    df = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                      index=pd.DatetimeIndex(["2026-11-06"]))

    def fake_once(self, symbols, **kw):
        calls.append(list(symbols))
        return {} if len(calls) == 1 else {s: df for s in symbols}

    monkeypatch.setattr(yfinance_source.YFinanceSource, "_download_once", fake_once)
    monkeypatch.setattr(yfinance_source.time, "sleep", lambda s: None)
    src = yfinance_source.YFinanceSource()
    out = src.daily(["^NSEI"], fill_gaps=False)
    assert "^NSEI" in out and calls == [["^NSEI"], ["^NSEI"]] and not src.problems


def _bars(index, close):
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1},
                        index=pd.DatetimeIndex(index).tz_localize(IST))


def test_index_daily_fills_missing_days_from_5m(monkeypatch):
    daily = _bars(["2026-11-06", "2026-11-09"], [1.0, 2.0])
    intra = _bars(["2026-11-09 15:25", "2026-11-11 09:15", "2026-11-11 15:25"], [2.0, 2.5, 3.0])

    def fake_once(self, symbols, **kw):
        return {symbols[0]: (daily if kw["interval"] == "1d" else intra).copy()}

    monkeypatch.setattr(yfinance_source.YFinanceSource, "_download_once", fake_once)
    src = yfinance_source.YFinanceSource()
    out = src.index_daily("^NSEI")
    assert [str(d) for d in out.index.date] == ["2026-11-06", "2026-11-09", "2026-11-11"]
    assert out.Close.iloc[-1] == 3.0 and out.Open.iloc[-1] == 2.5
    assert any("rebuilt from 5-minute" in p for p in src.problems)
    assert out.Close.iloc[1] == 2.0   # an existing daily bar is never replaced


def test_index_daily_survives_empty_daily(monkeypatch):
    intra = _bars(["2026-11-11 09:15", "2026-11-11 15:25"], [2.5, 3.0])
    monkeypatch.setattr(yfinance_source.YFinanceSource, "_download_once",
                        lambda self, symbols, **kw: {} if kw["interval"] == "1d" else {symbols[0]: intra.copy()})
    monkeypatch.setattr(yfinance_source.time, "sleep", lambda s: None)
    src = yfinance_source.YFinanceSource()
    out = src.index_daily("^NSEI")
    assert len(out) == 1 and out.Close.iloc[0] == 3.0
    assert not any("No data returned" in p for p in src.problems)
