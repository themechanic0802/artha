#!/usr/bin/env python
"""Run one full paper-trading day unattended (Phase 0: yfinance data, paper fills).

Timeline (IST):
  08:45  load the watchlist, write a short pre-market note
  09:15-15:10  poll 5-minute candles ~once a minute; signals, risk checks, paper fills, stops/trails
  15:10  close all paper positions
  15:35  write reports/YYYY-MM-DD.md

Launched late? It catches up: past candles rebuild the opening range, but any signal
that would have filled before launch is reported as "missed", never traded.
Weekend / holiday: exits cleanly with a message.

Usage:
  .venv/bin/python scripts/run_day.py                 # live paper day (what launchd runs)
  .venv/bin/python scripts/run_day.py --dry-run       # replay the last completed market day in seconds
  .venv/bin/python scripts/run_day.py --dry-run --date 2026-11-06
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time as _time
from datetime import date, datetime, time, timedelta

import pandas as pd

from artha import live_status, prep
from artha.costs import CostModel
from artha.daybuild import candles_by_time, watch_items
from artha.data.yfinance_source import YFinanceSource
from artha.engine import DaySession
from artha.execution.paper_broker import PaperBroker
from artha.market_calendar import is_trading_day, previous_trading_day, why_closed
from artha.notify import notify
from artha.reporting.report import GLOSSARY, inr, session_events_md, trades_md
from artha.risk.risk_manager import RiskLimits, RiskManager
from artha.settings import CACHE_DIR, IST, KNOWN_LIMITATIONS, LOGS_DIR, REPORTS_DIR
from artha.strategy.orb import StrategyParams

log = logging.getLogger("artha.run_day")
LEDGER = CACHE_DIR / "paper_ledger.json"
POLL_SECONDS = 60
PREMARKET_AT, OPEN_AT, REPORT_AT = time(8, 45), time(9, 15), time(15, 35)


def now() -> datetime:
    return datetime.now(IST)


def sleep_until(t: datetime) -> None:
    while (left := (t - now()).total_seconds()) > 0:
        _time.sleep(min(left, 30))


def at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=IST)


# ---- paper account ledger (equity and peak carried across days) ---------------------
def load_ledger(capital: float) -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"equity": capital, "peak": capital, "days": []}


def save_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, indent=2))


# ---- inputs --------------------------------------------------------------------------
def load_plan(day: date, problems: list[str], src: YFinanceSource) -> dict:
    path = prep.plan_path(day)
    if path.exists():
        return json.loads(path.read_text())
    problems.append("No evening watchlist found: built one now WITHOUT news exclusions. "
                    "Check that evening prep ran (see README).")
    return prep.build_plan(day, {}, "FAILED: no evening prep for this day", src)


def premarket_note(day: date, plan: dict, equity: float, flagged: list[str]) -> str:
    m = plan.get("market")
    mood = "unknown (no Nifty data) - treated as OFF" if not m else \
        f"{'ON' if m['ok'] else 'OFF'} (Nifty {m['close']:,.0f} vs 20-day average {m['sma']:,.0f})"
    lines = [f"# Artha pre-market note, {day:%a %d %b %Y}", "",
             f"- Paper account: {inr(equity)}",
             f"- Market filter: {mood}",
             f"- Watchlist: {', '.join(w['symbol'] for w in plan['watchlist']) or 'empty'}",
             f"- Excluded for news/events: {', '.join(plan['exclusions']) or 'none'} "
             f"(news check: {plan['web_check']})",
             "- Gap check happens at 09:15 using the opening price (skip if > 2% from previous close).", ""]
    lines += [f"- Note: {x}" for x in flagged]
    return "\n".join(lines) + "\n"


# ---- report ------------------------------------------------------------------------
def day_report(day: date, plan: dict, s: DaySession, risk: RiskManager, broker: PaperBroker,
               start_equity: float, problems: list[str], mode: str) -> str:
    trades = pd.DataFrame([t.as_row() for t in broker.trades])
    net = trades.net_pnl.sum() if not trades.empty else 0.0
    gross = trades.gross_pnl.sum() if not trades.empty else 0.0
    charges = trades.charges.sum() if not trades.empty else 0.0
    m = plan.get("market")
    taken = sum(e.kind == "signal_taken" for e in s.events)
    skipped = sum(e.kind == "signal_skipped" for e in s.events)
    gap_skips = [e for e in s.events if e.kind == "skip_stock"]
    used_terms = ["opening range", "breakout", "stop", "R", "slippage", "gap", "SMA"]
    wl_rows = ["| Stock | Prev close | Avg 5-min volume (20 sessions) |", "|---|---|---|"]
    for w in s.stocks.values():
        wl_rows.append(f"| {w.item.ticker} | {w.item.prev_close:,.2f} | {w.item.avg_5m_volume:,.0f} |")
    md = [f"# Artha day report, {day:%A %d %b %Y}", "",
          f"_{mode}. Paper trading only: no real orders exist in Phase 0._", "",
          "## Summary",
          f"- Net P&L after all costs: **{inr(net)}** ({net / start_equity * 100:+.2f}%)",
          f"- Trades: {len(trades)}; signals taken {taken}, skipped {skipped}; stocks dropped for the day {len(gap_skips)}",
          f"- Paper account: {inr(start_equity)} -> {inr(start_equity + net)}",
          f"- Market filter: " + ("unknown (treated as OFF)" if not m else
                                  f"{'ON' if m['ok'] else 'OFF'}: Nifty {m['close']:,.2f} vs 20-day average {m['sma']:,.2f}"),
          f"- News check from evening prep: {plan['web_check']}", "",
          "## Watchlist", *wl_rows, "",
          "Excluded for news/events: " + (", ".join(f"{k} ({v['reason']})" for k, v in plan["exclusions"].items()) or "none"),
          "", "## Every event (signals taken or skipped, with reasons)", session_events_md(s),
          "## Fills and P&L", trades_md(trades),
          f"Before charges (after slippage): {inr(gross)}; charges: {inr(charges)}; **net: {inr(net)}**.", "",
          "## Risk-limit events", *([f"- {e}" for e in risk.events] or ["- None."]), "",
          "## Data problems", *([f"- {p}" for p in problems] or ["- None noticed."]), "",
          "## Terms used", *[f"- {GLOSSARY[t]}" for t in used_terms], "",
          "## Known limitations of Phase 0", KNOWN_LIMITATIONS, ""]
    return "\n".join(md)


# ---- the day ------------------------------------------------------------------------
def make_session(day: date, plan: dict, equity: float, peak: float, intraday: dict, problems: list[str],
                 live_from: datetime | None):
    p, limits, costs = StrategyParams.from_config(), RiskLimits.from_config(), CostModel.from_config()
    wl = pd.DataFrame(plan["watchlist"]) if plan["watchlist"] else pd.DataFrame(columns=["symbol", "prev_close"])
    items = watch_items(wl, intraday, day, p.volume_lookback_sessions, problems)
    risk = RiskManager(limits, costs, equity, peak)
    if risk.system_paused():
        problems.append(f"SYSTEM PAUSED: drawdown {risk.drawdown_pct():.1f}% >= {limits.max_drawdown_pause_pct}%. "
                        "No entries until the owner reviews.")
    broker = PaperBroker(risk, costs, equity)
    mkt_ok = bool(plan.get("market") and plan["market"]["ok"])
    return DaySession(day, items, mkt_ok, p, risk, broker, live_from), risk, broker


def run_replay(day: date, src: YFinanceSource) -> int:
    """Dry run: replay a completed day in seconds through the same engine and report code."""
    problems: list[str] = []
    plan = prep.build_plan(day, {}, "skipped (dry run replay)", src)
    tickers = [w["symbol"] for w in plan["watchlist"]]
    intraday = src.intraday(tickers, period="60d")
    problems += src.problems
    limits = RiskLimits.from_config()
    s, risk, broker = make_session(day, plan, limits.capital, limits.capital, intraday, problems, None)
    for t, cs in candles_by_time(intraday, list(s.stocks), day):
        s.process(t, cs)
    if not s.finished:
        problems.append("No 15:10 candle in the data; open positions closed at last price.")
        s.force_close_all(s.last_time or at(day, time(15, 10)), {}, "end of data")
    out = REPORTS_DIR / "dryrun"
    out.mkdir(exist_ok=True)
    path = out / f"{day}.md"
    path.write_text(day_report(day, plan, s, risk, broker, limits.capital, problems,
                               "DRY RUN: replay of a past day, does not touch the paper ledger"))
    print(f"Dry run OK for {day}: watchlist {tickers}, {len(broker.trades)} trades, "
          f"net {inr(sum(t.net_pnl for t in broker.trades))}. Report: {path}")
    return 0


def run_live(day: date, src: YFinanceSource) -> int:
    problems: list[str] = []
    launched = now()
    if launched < at(day, PREMARKET_AT):
        log.info("waiting until 08:45")
        sleep_until(at(day, PREMARKET_AT))

    plan = load_plan(day, problems, src)
    limits = RiskLimits.from_config()
    ledger = load_ledger(limits.capital)
    start_equity = ledger["equity"]
    late = launched > at(day, OPEN_AT)
    flagged = list(problems) + ([f"Started late at {launched:%H:%M}; catching up from now."] if late else [])
    (REPORTS_DIR / f"{day}-premarket.md").write_text(premarket_note(day, plan, start_equity, flagged))
    notify("Artha: morning run started", f"{len(plan['watchlist'])} stocks on watch" + (" (late start)" if late else ""))
    log.info("pre-market note written; watchlist %s", [w["symbol"] for w in plan["watchlist"]])

    tickers = [w["symbol"] for w in plan["watchlist"]]
    hist = src.intraday(tickers, period="30d")          # for the 20-session volume average
    s, risk, broker = make_session(day, plan, start_equity, ledger["peak"], hist, problems,
                                   launched if late else None)
    if late:
        problems.append(f"Started late at {launched:%H:%M}: signals before then are listed as missed.")
    live_tickers = list(s.stocks)
    live_status.write(s, start_equity, "pre-open", problems)

    sleep_until(at(day, OPEN_AT) + timedelta(seconds=30))
    force_at = at(day, risk.limits.force_exit_at)
    lag_flagged = False
    no_data_flagged = False

    def poll(until: datetime) -> dict:
        data = src.intraday(live_tickers, period="1d")
        today = {k: v[v.index.date == day] for k, v in data.items()}
        for t, cs in candles_by_time(today, live_tickers, day, until=until):
            s.process(t, cs)
        return today

    while now() < force_at and live_tickers:
        try:
            src.problems.clear()
            today = poll(now())
            latest = max((v.index.max() for v in today.values() if not v.empty), default=None)
            if latest is None and now() > at(day, time(9, 40)) and not no_data_flagged:
                problems.append(f"{now():%H:%M} no candles for today yet: Yahoo outage or an unlisted holiday?")
                no_data_flagged = True
            elif latest is not None and (now() - latest) > timedelta(minutes=12) and not lag_flagged:
                problems.append(f"{now():%H:%M} data is lagging: newest candle starts {latest:%H:%M}.")
                lag_flagged = True
        except Exception as e:   # a bad poll must not kill the day
            problems.append(f"{now():%H:%M} data fetch failed: {type(e).__name__}: {e}")
            log.exception("poll failed")
        live_status.write(s, start_equity, "live", problems)
        _time.sleep(POLL_SECONDS)

    # 15:10: close everything at the 15:10 candle's open (retry briefly for it to appear).
    prices: dict[str, float] = {}
    for _ in range(4):
        try:
            # First let the last full candle (15:05-15:10) hit any stops or targets.
            data = poll(force_at) if live_tickers else {}
            if not broker.positions:
                break
            for tk, df in data.items():
                if tk not in broker.positions:
                    continue
                row = df[df.index == force_at]
                if not row.empty:
                    prices[tk] = float(row.Open.iloc[0])
            if all(tk in prices for tk in broker.positions):
                break
        except Exception as e:
            problems.append(f"forced-exit price fetch failed: {e}")
        _time.sleep(45)
    missing = [tk for tk in broker.positions if tk not in prices]
    if missing:
        problems.append(f"No 15:10 price for {', '.join(missing)}; closed at the last known price.")
    s.force_close_all(force_at, prices, "forced exit 15:10")
    live_status.write(s, start_equity, "closed", problems)

    net = sum(t.net_pnl for t in broker.trades)
    ledger["equity"] = start_equity + net
    ledger["peak"] = max(ledger["peak"], ledger["equity"])
    ledger["days"].append({"date": str(day), "net_pnl": round(net, 2), "equity": round(ledger["equity"], 2),
                           "trades": len(broker.trades)})
    save_ledger(ledger)

    sleep_until(at(day, REPORT_AT))
    path = REPORTS_DIR / f"{day}.md"
    path.write_text(day_report(day, plan, s, risk, broker, start_equity, problems + src.problems,
                               "Live paper-trading day (yfinance data)"))
    log.info("report written: %s", path)
    live_status.write(s, start_equity, "report ready", problems + src.problems)
    notify("Artha: day report ready", f"Net P&L {inr(net)} from {len(broker.trades)} trades")
    print(f"Report: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="replay a past day quickly; no waiting, ledger untouched")
    ap.add_argument("--date", type=date.fromisoformat)
    a = ap.parse_args()
    today = now().date()
    logging.basicConfig(filename=LOGS_DIR / f"run_day-{a.date or today}.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    src = YFinanceSource()

    if a.dry_run:
        day = a.date or (today if is_trading_day(today) and now().time() > time(15, 35) else previous_trading_day(today))
        print(f"Dry run: replaying {day:%a %d %b %Y} ...", flush=True)
        return run_replay(day, src)

    day = today
    if not is_trading_day(day):
        msg = f"{day:%a %d %b %Y}: market closed ({why_closed(day)}). Nothing to do."
        print(msg)
        log.info(msg)
        return 0
    if now() > at(day, REPORT_AT) and (REPORTS_DIR / f"{day}.md").exists():
        print("Today's report already exists; nothing to do.")
        return 0

    lock = CACHE_DIR / "run_day.lock"
    if lock.exists():
        try:
            os.kill(int(lock.read_text()), 0)
            print("Another run_day is already running; exiting.")
            return 0
        except (OSError, ValueError):
            pass   # stale lock from a crashed run
    lock.write_text(str(os.getpid()))
    try:
        log.info("live run started")
        return run_live(day, src)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
