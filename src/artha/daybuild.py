"""Helpers that turn raw data into the inputs a DaySession needs.
Shared by the backtest and the live runner."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from .engine import WatchItem
from .strategy.orb import Candle


def avg_5m_volume(intraday: pd.DataFrame, before: date, sessions: int) -> float | None:
    """Average 5-minute volume over the `sessions` trading sessions before `before`."""
    past = intraday[intraday.index.date < before]
    days = sorted(set(past.index.date))[-sessions:]
    if len(days) < sessions:
        return None
    vol = past[pd.Index(past.index.date).isin(days)]["Volume"]
    return float(vol.mean())


def watch_items(watchlist: pd.DataFrame, intraday: dict[str, pd.DataFrame], day: date,
                sessions: int, problems: list[str]) -> list[WatchItem]:
    items = []
    for r in watchlist.itertuples():
        df = intraday.get(r.symbol)
        v = avg_5m_volume(df, day, sessions) if df is not None else None
        if not v:
            problems.append(f"{r.symbol}: not enough 5-minute history for the volume average; left out.")
            continue
        items.append(WatchItem(r.symbol, float(r.prev_close), v))
    return items


def candles_by_time(intraday: dict[str, pd.DataFrame], tickers: list[str], day: date,
                    until: datetime | None = None) -> list[tuple[datetime, dict[str, Candle]]]:
    """Group the day's candles by start time. `until` drops candles that have not
    finished yet (start + 5 min > until)."""
    frames = []
    for tk in tickers:
        df = intraday.get(tk)
        if df is None:
            continue
        d = df[df.index.date == day]
        if until is not None:
            d = d[d.index + pd.Timedelta(minutes=5) <= until]
        for ts, row in d.iterrows():
            frames.append((ts.to_pydatetime(), tk, Candle(ts.to_pydatetime(), float(row.Open), float(row.High),
                                                          float(row.Low), float(row.Close), float(row.Volume))))
    out: dict[datetime, dict[str, Candle]] = {}
    for ts, tk, c in frames:
        out.setdefault(ts, {})[tk] = c
    return sorted(out.items())
