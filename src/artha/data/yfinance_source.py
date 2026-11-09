"""Free, delayed market data from Yahoo Finance (Phase 0 only).

Later a `kite_source.py` with the same methods replaces this without touching
strategy code. yfinance is unofficial: candles can be late, missing or revised.
"""
from __future__ import annotations

import io
import logging
import time
import urllib.request

import pandas as pd
import yfinance as yf

from ..settings import IST, load_yaml

log = logging.getLogger(__name__)


def to_yahoo(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


class YFinanceSource:
    name = "yfinance (free, unofficial, may be delayed)"

    def __init__(self):
        self.problems: list[str] = []   # data issues worth putting in the report

    # ---- universe -------------------------------------------------------------
    def universe(self) -> list[str]:
        cfg = load_yaml("universe.yaml")
        try:
            req = urllib.request.Request(cfg["nse_csv_url"], headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode()
            syms = pd.read_csv(io.StringIO(raw))["Symbol"].str.strip().tolist()
            if 45 <= len(syms) <= 55:
                return syms
            self.problems.append(f"NSE constituents CSV had {len(syms)} rows; used the hardcoded list.")
        except Exception as e:  # network/format problems -> fall back
            self.problems.append(f"Could not fetch NSE constituents CSV ({type(e).__name__}); used the hardcoded list.")
        return list(cfg["symbols"])

    # ---- downloads ------------------------------------------------------------
    def _download(self, symbols: list[str], retries: int = 0, **kw) -> dict[str, pd.DataFrame]:
        """Download candles. Symbols that come back empty are retried `retries` times:
        Yahoo sometimes fails one ticker at random (e.g. ^NSEI on 8 Nov 2026)."""
        out = self._download_once(symbols, **kw)
        for attempt in range(retries):
            missing = [s for s in symbols if s not in out]
            if not missing:
                break
            time.sleep(3 * (attempt + 1))
            log.info("retrying %s (attempt %d)", missing, attempt + 2)
            out.update(self._download_once(missing, **kw))
        for sym in symbols:
            if sym not in out:
                self.problems.append(f"No data returned for {sym} ({kw.get('interval')}).")
        return out

    def _download_once(self, symbols: list[str], **kw) -> dict[str, pd.DataFrame]:
        tickers = [to_yahoo(s) for s in symbols]
        raw = yf.download(tickers, group_by="ticker", auto_adjust=False, progress=False,
                          threads=True, **kw)
        out: dict[str, pd.DataFrame] = {}
        for sym, t in zip(symbols, tickers):
            try:
                # group_by="ticker" gives columns like (TICKER, Open); single downloads may be flat.
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
            except Exception:
                df = pd.DataFrame()
            if df.empty:
                continue
            idx = df.index
            df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
            out[sym] = df.sort_index()
        return out

    def daily(self, symbols: list[str], period: str = "6mo") -> dict[str, pd.DataFrame]:
        # Daily data feeds the evening plan, so it is worth a couple of retries.
        return self._download(symbols, retries=2, period=period, interval="1d")

    def intraday(self, symbols: list[str], period: str = "60d") -> dict[str, pd.DataFrame]:
        """5-minute candles labelled by start time. The newest candle may still be forming."""
        data = self._download(symbols, period=period, interval="5m")
        for sym, df in data.items():
            # Keep regular session candles only (09:15 to 15:25 start times).
            t = df.index.time
            data[sym] = df[(t >= pd.Timestamp("09:15").time()) & (t <= pd.Timestamp("15:25").time())]
        return data
