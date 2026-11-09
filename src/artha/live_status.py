"""A small JSON snapshot of the running day, rewritten by run_day.py after every poll.

The dashboard reads it; nothing in the trading path depends on it, and a failure to
write it is logged and ignored.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from .engine import DaySession
from .settings import CACHE_DIR, IST

log = logging.getLogger(__name__)


def status_path(day: date):
    return CACHE_DIR / f"live-{day}.json"


def snapshot(s: DaySession, start_equity: float, phase: str, problems: list[str]) -> dict:
    broker, marks = s.broker, s.marks
    stocks = []
    for tk, st in s.stocks.items():
        if tk in broker.positions:
            state = "in trade"
        elif st.traded:
            state = "traded (closed)"
        elif st.skip_reason:
            state = f"out: {st.skip_reason}"
        elif st.pending_signal is not None:
            state = "breakout: buying at next candle"
        elif st.or_high is None:
            state = "building opening range" if st.or_candles else "waiting for 09:15"
        else:
            state = "watching for breakout"
        or_hi = st.or_high if st.or_high is not None else (max(c.high for c in st.or_candles) if st.or_candles else None)
        or_lo = st.or_low if st.or_low is not None else (min(c.low for c in st.or_candles) if st.or_candles else None)
        stocks.append({"ticker": tk, "prev_close": st.item.prev_close, "last": marks.get(tk),
                       "or_high": or_hi, "or_low": or_lo, "or_final": st.or_high is not None, "state": state})
    positions = []
    for tk, p in broker.positions.items():
        last = marks.get(tk, p.entry)
        positions.append({"ticker": tk, "qty": p.qty, "entry": p.entry, "stop": p.stop, "target": p.target,
                          "last": last, "entry_time": p.entry_time.isoformat(), "trailed": p.trailed,
                          "pnl_before_costs": (last - p.entry) * p.qty})
    realised = broker.realised_pnl
    open_pnl = broker.open_pnl(marks)
    return {
        "day": str(s.day), "updated_at": datetime.now(IST).isoformat(timespec="seconds"), "phase": phase,
        "last_candle": s.last_time.isoformat() if s.last_time else None,
        "market_ok": s.market_ok, "start_equity": start_equity,
        "realised_pnl": realised, "open_pnl": open_pnl, "equity_now": start_equity + realised + open_pnl,
        "stocks": stocks, "positions": positions,
        "trades": [t.as_row() for t in broker.trades],
        "events": [{"time": e.time.isoformat(), "ticker": e.ticker, "kind": e.kind, "detail": e.detail}
                   for e in s.events],
        "risk_events": list(s.risk.events), "problems": list(problems),
    }


def write(s: DaySession, start_equity: float, phase: str, problems: list[str]) -> None:
    try:
        path = status_path(s.day)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot(s, start_equity, phase, problems), indent=1, default=str))
        tmp.replace(path)   # atomic: the dashboard never reads a half-written file
    except Exception:
        log.exception("could not write live status (ignored)")
