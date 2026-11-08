"""Daily watchlist and market filter. Uses only data up to and including `asof`
(the last completed session), so there is no look-ahead."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .orb import StrategyParams


def _upto(df: pd.DataFrame, asof: date) -> pd.DataFrame:
    return df[df.index.date <= asof]


def atr_pct(df: pd.DataFrame, period: int) -> float:
    """14-day Average True Range as a % of the last close. True range = the day's
    full movement including any gap from the previous close."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev_close).abs(),
                    (df["Low"] - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean() / df["Close"].iloc[-1] * 100)


def stock_metrics(daily: dict[str, pd.DataFrame], asof: date, p: StrategyParams) -> pd.DataFrame:
    rows = []
    for sym, df in daily.items():
        d = _upto(df, asof).dropna(subset=["Close"])
        if len(d) < max(p.liquidity_lookback_days, p.atr_period + 1):
            continue
        tv = (d["Close"] * d["Volume"]).tail(p.liquidity_lookback_days).mean()
        rows.append({"symbol": sym, "avg_traded_value_cr": tv / 1e7,
                     "atr_pct": atr_pct(d, p.atr_period), "prev_close": float(d["Close"].iloc[-1]),
                     "last_date": d.index[-1].date()})
    m = pd.DataFrame(rows)
    if m.empty:
        return m
    return m.sort_values("avg_traded_value_cr", ascending=False).reset_index(drop=True)


def candidates(metrics: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Stocks passing the ATR filter, most liquid first, limited to `prep_candidates`.
    These are the ones the evening news check looks at."""
    ok = metrics[(metrics.atr_pct >= p.atr_pct_min) & (metrics.atr_pct <= p.atr_pct_max)]
    return ok.head(p.prep_candidates).reset_index(drop=True)


def build_watchlist(metrics: pd.DataFrame, exclusions: set[str], p: StrategyParams) -> pd.DataFrame:
    ok = metrics[(metrics.atr_pct >= p.atr_pct_min) & (metrics.atr_pct <= p.atr_pct_max)]
    ok = ok[~ok.symbol.isin(exclusions)]
    return ok.head(p.watchlist_size).reset_index(drop=True)


def market_filter(index_daily: pd.DataFrame, asof: date, p: StrategyParams) -> dict:
    d = _upto(index_daily, asof).dropna(subset=["Close"])
    close = float(d["Close"].iloc[-1])
    sma = float(d["Close"].tail(p.market_sma_days).mean())
    return {"ok": close > sma, "close": close, "sma": sma, "date": d.index[-1].date(),
            "distance_pct": (close - sma) / sma * 100}
