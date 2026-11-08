"""Shared numbers and markdown helpers for backtest and daily reports."""
from __future__ import annotations

import pandas as pd

from ..engine import DaySession


def inr(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}INR {abs(x):,.2f}"


def max_drawdown_pct(equity: pd.Series) -> float:
    """Biggest fall from a previous high, as a % of that high."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min() * 100)


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0, "win_rate": 0.0, "avg_r": 0.0, "profit_factor": float("nan"),
                "net": 0.0, "charges": 0.0, "gross": 0.0}
    wins = trades[trades.net_pnl > 0].net_pnl.sum()
    losses = -trades[trades.net_pnl < 0].net_pnl.sum()
    return {"n": len(trades), "win_rate": (trades.net_pnl > 0).mean() * 100,
            "avg_r": trades.r_multiple.mean(),
            "profit_factor": wins / losses if losses > 0 else float("inf"),
            "net": trades.net_pnl.sum(), "charges": trades.charges.sum(), "gross": trades.gross_pnl.sum()}


GLOSSARY = {
    "R": "R = the amount risked on a trade (entry price minus stop, times shares). +1R means you made what you risked; -1R means the stop was hit.",
    "opening range": "Opening range = the highest and lowest prices in the first 30 minutes (09:15-09:45).",
    "breakout": "Breakout = a 5-minute candle closing above the opening-range high on strong volume.",
    "stop": "Stop = the price where a losing trade is cut (here, the opening-range low).",
    "slippage": "Slippage = the gap between the price you wanted and the price you'd realistically get (assumed 0.05% each way).",
    "profit factor": "Profit factor = total money won divided by total money lost. Above 1 means winners outweighed losers.",
    "drawdown": "Drawdown = the biggest fall from a previous high point of the account.",
    "SMA": "20-day average (SMA) = the average closing price of the last 20 trading days; above it suggests an uptrend.",
    "gap": "Gap = how far a stock opens from yesterday's close. Big gaps usually mean news, so Artha skips them.",
}


def session_events_md(s: DaySession) -> str:
    if not s.events:
        return "_No events._\n"
    lines = ["| Time | Stock | What | Detail |", "|---|---|---|---|"]
    # "Book full" rejections repeat every 5 minutes while a breakout lasts; show one
    # line per stock with the count instead of dozens of identical rows.
    full: dict[str, list] = {}
    for e in s.events:
        if e.kind == "signal_skipped" and "open positions (max" in e.detail:
            full.setdefault(e.ticker, []).append(e)
    shown: set[str] = set()
    for e in s.events:
        if e.ticker in full and e in full[e.ticker]:
            if e.ticker in shown:
                continue
            shown.add(e.ticker)
            evs = full[e.ticker]
            more = f" ({len(evs)} times, last at {evs[-1].time:%H:%M})" if len(evs) > 1 else ""
            lines.append(f"| {e.time:%H:%M} | {e.ticker} | signal skipped | breakout rejected: maximum open "
                         f"positions already held{more} |")
            continue
        lines.append(f"| {e.time:%H:%M} | {e.ticker} | {e.kind.replace('_', ' ')} | {e.detail} |")
    return "\n".join(lines) + "\n"


def trades_md(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "_No trades._\n"
    lines = ["| Stock | Qty | Entry | Exit | Why exited | Before charges | Charges | Net P&L | R |",
             "|---|---|---|---|---|---|---|---|---|"]
    for t in trades.itertuples():
        lines.append(f"| {t.ticker} | {t.qty} | {pd.Timestamp(t.entry_time):%H:%M} @ {t.entry:.2f} | "
                     f"{pd.Timestamp(t.exit_time):%H:%M} @ {t.exit:.2f} | {t.reason} | {t.gross_pnl:+.2f} | "
                     f"{t.charges:.2f} | {t.net_pnl:+.2f} | {t.r_multiple:+.2f} |")
    return "\n".join(lines) + "\n"
