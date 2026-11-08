#!/usr/bin/env python
"""Quick backtest of strategy v0 on Yahoo's last ~60 days of 5-minute data.

THIS IS A SHORT, LOW-CONFIDENCE SAMPLE. 60 days (about 40 tradable days after a
20-session warm-up for the volume average) cannot prove or disprove an edge.
It exists to check the machinery and get a first feel. Phase 1 does it properly.

Rules for honesty: parameters come from config/strategy.yaml and are NOT tuned
here. All results are after Zerodha charges and slippage.

Usage:  .venv/bin/python scripts/backtest_quick.py [--refresh]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from datetime import date

import pandas as pd

from artha.costs import CostModel
from artha.daybuild import candles_by_time, watch_items
from artha.data.yfinance_source import YFinanceSource
from artha.engine import DaySession
from artha.execution.paper_broker import PaperBroker
from artha.reporting.report import GLOSSARY, inr, max_drawdown_pct, trade_stats
from artha.risk.risk_manager import RiskLimits, RiskManager
from artha.settings import CACHE_DIR, KNOWN_LIMITATIONS, REPORTS_DIR
from artha.strategy.orb import StrategyParams
from artha.strategy.watchlist import build_watchlist, market_filter, stock_metrics


def load_data(refresh: bool):
    cache = CACHE_DIR / f"backtest_data_{date.today():%Y%m%d}.pkl"
    if cache.exists() and not refresh:
        return pickle.loads(cache.read_bytes())
    src = YFinanceSource()
    uni = src.universe()
    print(f"Downloading daily + 5-minute data for {len(uni)} stocks and Nifty ...", flush=True)
    daily = src.daily(uni, period="1y")
    nifty = src.daily(["^NSEI"], period="1y")["^NSEI"]
    intraday = src.intraday(uni, period="60d")
    data = (uni, daily, nifty, intraday, src.problems)
    cache.write_bytes(pickle.dumps(data))
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download data instead of using today's cache")
    args = ap.parse_args()

    p, limits, costs = StrategyParams.from_config(), RiskLimits.from_config(), CostModel.from_config()
    uni, daily, nifty, intraday, problems = load_data(args.refresh)
    problems = list(problems)

    sessions = sorted(set().union(*[set(df.index.date) for df in intraday.values()]))
    test_days = sessions[p.volume_lookback_sessions:]
    equity = peak = limits.capital
    all_trades, curve, day_rows, all_events = [], [], [], []

    for i, day in enumerate(test_days):
        prev = sessions[p.volume_lookback_sessions + i - 1]
        metrics = stock_metrics(daily, prev, p)
        metrics = metrics[metrics.last_date == prev]        # stale daily data -> leave out
        wl = build_watchlist(metrics, set(), p)               # no news exclusions in backtest
        mkt = market_filter(nifty, prev, p)
        items = watch_items(wl, intraday, day, p.volume_lookback_sessions, problems)
        risk = RiskManager(limits, costs, equity, peak)
        broker = PaperBroker(risk, costs, equity)
        s = DaySession(day, items, mkt["ok"], p, risk, broker)
        for t, cs in candles_by_time(intraday, [w.ticker for w in items], day):
            s.process(t, cs)
        if not s.finished:   # data ended before 15:10
            problems.append(f"{day}: no 15:10 candle; open positions closed at last price.")
            s.force_close_all(s.last_time, {}, "end of data")
        day_pnl = sum(t.net_pnl for t in broker.trades)
        equity += day_pnl
        peak = max(peak, equity)
        all_trades += [t.as_row() for t in broker.trades]
        all_events += [{"date": day, "time": e.time.strftime("%H:%M"), "ticker": e.ticker, "kind": e.kind,
                        "detail": e.detail} for e in s.events]
        curve.append({"date": day, "equity": equity, "day_pnl": day_pnl, "trades": len(broker.trades),
                      "market_filter_ok": mkt["ok"], "watchlist": " ".join(w.ticker for w in items)})
        day_rows.append(risk.events)

    trades = pd.DataFrame(all_trades)
    curve_df = pd.DataFrame(curve)
    out = REPORTS_DIR / "backtest"
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "trades.csv", index=False)
    curve_df.to_csv(out / "equity_curve.csv", index=False)
    pd.DataFrame(all_events).to_csv(out / "events.csv", index=False)

    # Benchmark: Nifty 50 bought at the first test day's open, held to the last close.
    first, last = test_days[0], test_days[-1]
    n = nifty[(nifty.index.date >= first) & (nifty.index.date <= last)]
    nifty_ret = (n.Close.iloc[-1] / n.Open.iloc[0] - 1) * 100
    nifty_curve = limits.capital * n.Close / n.Open.iloc[0]
    st = trade_stats(trades)
    strat_ret = (equity / limits.capital - 1) * 100
    strat_dd = max_drawdown_pct(pd.concat([pd.Series([limits.capital]), curve_df.equity]))
    sig = pd.DataFrame(all_events)
    n_signals = int(sig.kind.isin(["signal_taken", "signal_skipped"]).sum()) if not sig.empty else 0
    filter_off_days = int((~curve_df.market_filter_ok).sum())

    md = f"""# Artha quick backtest (SHORT, LOW-CONFIDENCE SAMPLE)

Period: {first} to {last} ({len(test_days)} trading days; the {p.volume_lookback_sessions} sessions before that were
used only to warm up the volume average). Data: Yahoo 5-minute candles. Strategy v0 (opening range breakout),
parameters exactly as in config, not tuned. Capital {inr(limits.capital)}. **All figures are after Zerodha
charges and 0.05% slippage per side** (cost rates not yet verified against Zerodha's calculator).

| | Artha v0 | Nifty 50 buy-and-hold |
|---|---|---|
| Return | {strat_ret:+.2f}% ({inr(equity - limits.capital)}) | {nifty_ret:+.2f}% ({inr(limits.capital * nifty_ret / 100)}) |
| Max drawdown | {strat_dd:.2f}% | {max_drawdown_pct(nifty_curve):.2f}% |

| Trade stats | |
|---|---|
| Trades | {st['n']} |
| Win rate | {st['win_rate']:.1f}% |
| Average R (net) | {st['avg_r']:+.2f} |
| Profit factor (net) | {st['profit_factor']:.2f} |
| P&L before charges (after slippage) | {inr(st['gross'])} |
| Charges paid | {inr(st['charges'])} |
| Net P&L | {inr(st['net'])} |
| Breakout signals seen (taken + skipped) | {n_signals} |
| Days market filter said "no trading" | {filter_off_days} of {len(test_days)} |

Exit reasons: {trades.reason.value_counts().to_dict() if not trades.empty else {}}

Nifty's buy-and-hold figure is the index itself, with no costs; a real index fund/ETF would cost a little.

## Terms
""" + "\n".join(f"- {v}" for v in GLOSSARY.values()) + f"""

## Known limitations of Phase 0
{KNOWN_LIMITATIONS}
- No news exclusions in the backtest (they're produced each evening going forward); only the 2% gap filter.
- Fills are simulated from candle prices; real fills, especially stops in fast markets, can be worse.

## Data problems ({len(problems)})
""" + ("\n".join(f"- {x}" for x in problems[:40]) or "- none") + "\n\nFiles: trades.csv, equity_curve.csv, events.csv in this folder.\n"
    (out / "summary.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
