#!/usr/bin/env python
"""Evening prep for the next market day. Usually started by scripts/evening_prep.sh.

Usage: .venv/bin/python scripts/evening_prep.py [--for-date YYYY-MM-DD] [--skip-web]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from artha import prep
from artha.data.yfinance_source import YFinanceSource
from artha.market_calendar import is_trading_day, next_trading_day
from artha.notify import notify
from artha.settings import IST, LOGS_DIR, REPORTS_DIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--for-date", type=date.fromisoformat, help="market day to prepare (default: next market day)")
    ap.add_argument("--skip-web", action="store_true", help="skip the Claude news check (testing)")
    a = ap.parse_args()
    today = datetime.now(IST).date()
    day = a.for_date or next_trading_day(today)
    logging.basicConfig(filename=LOGS_DIR / f"prep-{day}.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("artha.prep")
    if not is_trading_day(day):
        print(f"{day} is not a market day; nothing to prepare.")
        return 0
    log.info("prep for %s started", day)
    print(f"Preparing for {day:%a %d %b %Y} ...", flush=True)

    src = YFinanceSource()
    # First pass (no exclusions) gives the candidate list for the news check.
    first = prep.build_plan(day, {}, "pending", src)
    symbols = [c["symbol"] for c in first["candidates"]]
    if a.skip_web:
        excl, status = {}, "FAILED: skipped by --skip-web"
    else:
        print(f"Checking news/events for {len(symbols)} stocks with Claude (can take several minutes) ...", flush=True)
        excl, status = prep.news_check(day, symbols)
    log.info("web check: %s; %d exclusions", status, len(excl))
    # If the check failed, the exclusions file is left empty (as the rules require).
    prep.write_exclusions(day, excl, status)

    plan = dict(first)
    plan["web_check"], plan["exclusions"] = status, excl
    from artha.strategy.orb import StrategyParams
    p = StrategyParams.from_config()
    # Watchlist = candidates (already liquidity-ranked and ATR-filtered) minus exclusions, top N.
    plan["watchlist"] = [c for c in first["candidates"] if c["symbol"] not in excl][: p.watchlist_size]
    prep.save(plan)
    note = REPORTS_DIR / f"{day}-prep.md"
    log.info("wrote %s", note)
    print(f"Wrote {note}\nWeb check: {status}\nWatchlist: {', '.join(w['symbol'] for w in plan['watchlist'])}")
    mood = "ON" if plan["market"] and plan["market"]["ok"] else "OFF"
    if plan["market"] is None:
        mood = "UNKNOWN (no Nifty data, so no trades; re-run prep)"
    notify("Artha: prep note ready", f"{day:%a %d %b}: market filter {mood}, {len(plan['watchlist'])} stocks, "
                                     f"{len(excl)} excluded" + ("" if status == "ok" else " (news check incomplete)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
