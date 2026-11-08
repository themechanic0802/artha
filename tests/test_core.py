from datetime import date, datetime, time

import pytest

from artha.costs import CostModel
from artha.engine import DaySession, WatchItem
from artha.execution.paper_broker import PaperBroker
from artha.risk.risk_manager import RiskLimits, RiskManager
from artha.settings import IST
from artha.strategy import orb
from artha.strategy.orb import Candle, Position, StrategyParams

COSTS = CostModel(brokerage_flat_inr=20, brokerage_pct=0.03, stt_sell_pct=0.025,
                  exchange_txn_pct=0.00297, sebi_per_crore_inr=10, stamp_duty_buy_pct=0.003,
                  gst_pct=18, slippage_pct_per_side=0.05)
LIMITS = RiskLimits(capital=10000, risk_per_trade_pct=1.5, daily_loss_limit_pct=3.0,
                    max_open_positions=2, max_drawdown_pause_pct=15, min_profit_to_cost_ratio=2.0,
                    no_new_entries_after=time(14, 30), force_exit_at=time(15, 10))
DAY = date(2026, 11, 9)


def at(h, m):
    return datetime(2026, 11, 9, h, m, tzinfo=IST)


def risk(equity=10000, peak=10000):
    return RiskManager(LIMITS, COSTS, equity, peak)


def candle(h, m, o, hi, lo, c, v=1000):
    return Candle(at(h, m), o, hi, lo, c, v)


# ---- config files load ------------------------------------------------------
def test_configs_load():
    assert RiskLimits.from_config().capital == 10000
    assert CostModel.from_config().slippage_pct_per_side == 0.05
    p = StrategyParams.from_config()
    assert p.opening_range_candles == 6 and p.watchlist_size == 8


# ---- cost calculation -------------------------------------------------------
def test_costs_small_order_uses_percentage_brokerage():
    buy = COSTS.order_charges("buy", 10, 1000)       # INR 10,000 order
    assert buy["brokerage"] == pytest.approx(3.0)     # 0.03% < INR 20
    assert buy["stt"] == 0
    assert buy["stamp"] == pytest.approx(0.30)
    assert buy["txn"] == pytest.approx(0.297)
    assert buy["sebi"] == pytest.approx(0.01)
    assert buy["gst"] == pytest.approx(0.18 * (3.0 + 0.297 + 0.01))
    sell = COSTS.order_charges("sell", 10, 1000)
    assert sell["stt"] == pytest.approx(2.5) and sell["stamp"] == 0


def test_costs_large_order_caps_brokerage_at_20():
    assert COSTS.order_charges("buy", 100, 1000)["brokerage"] == 20


def test_slippage_direction():
    assert COSTS.apply_slippage("buy", 100) == pytest.approx(100.05)
    assert COSTS.apply_slippage("sell", 100) == pytest.approx(99.95)


# ---- position sizing --------------------------------------------------------
def test_sizing_full_risk_budget():
    qty, how = risk().size_position(entry=100, stop=98, available_cash=10000)
    assert qty == 75 and how == "full risk budget"   # floor(150 / 2)


def test_sizing_capped_by_cash_no_leverage():
    qty, how = risk().size_position(entry=1000, stop=995, available_cash=10000)
    assert qty == 9 and "capped" in how             # 30 by risk, but only 9 fit in cash with charges
    assert qty * 1000 + COSTS.order_charges("buy", qty, 1000)["total"] <= 10000


def test_entry_rejected_when_size_below_one():
    d = risk().check_entry(at(10, 0), 12000, 11900, 12200, 10000, 0, 0, 0)
    assert not d.approved and "size < 1" in d.reason


def test_entry_rejected_when_profit_does_not_clear_costs():
    # Tiny R: 0.02 per share -> target profit far below 2x costs
    d = risk().check_entry(at(10, 0), 100.0, 99.98, 100.04, 10000, 0, 0, 0)
    assert not d.approved and "round-trip" in d.reason


# ---- time rules -------------------------------------------------------------
def test_no_entries_after_1430():
    r = risk()
    assert r.check_entry(at(14, 30), 100, 98, 104, 10000, 0, 0, 0).approved
    d = r.check_entry(at(14, 35), 100, 98, 104, 10000, 0, 0, 0)
    assert not d.approved and "14:30" in d.reason


def test_max_open_positions():
    d = risk().check_entry(at(10, 0), 100, 98, 104, 10000, 2, 0, 0)
    assert not d.approved and "open positions" in d.reason


def test_daily_loss_limit_blocks_entries_and_stays_on():
    r = risk()
    assert r.check_entry(at(11, 0), 100, 98, 104, 10000, 0, -200, -50).approved
    d = r.check_entry(at(11, 5), 100, 98, 104, 10000, 0, -200, -100)   # -300 reached
    assert not d.approved and "daily loss" in d.reason
    # Prices recover, but the block stays for the rest of the day.
    assert not r.check_entry(at(12, 0), 100, 98, 104, 10000, 0, -200, +150).approved
    assert any("Daily loss limit" in e for e in r.events)


def test_drawdown_pause():
    d = RiskManager(LIMITS, COSTS, 8400, 10000).check_entry(at(10, 0), 100, 98, 104, 10000, 0, 0, 0)
    assert not d.approved and "paused" in d.reason


# ---- exits: same-candle rule and breakeven trail ------------------------------
def pos():
    return Position("X", 10, entry=100, initial_stop=98, stop=98, target=104, entry_time=at(10, 0), buy_charges=0)


def test_same_candle_stop_and_target_assumes_stop():
    assert orb.check_exit(pos(), candle(10, 5, 100, 105, 97, 103)) == (98, "stop")


def test_target_hit():
    assert orb.check_exit(pos(), candle(10, 5, 100, 104.5, 99, 104)) == (104, "target")


def test_gap_through_stop_fills_at_open():
    price, reason = orb.check_exit(pos(), candle(10, 5, 97, 99, 96, 98))
    assert price == 97 and "gapped" in reason


def test_breakeven_trail():
    p = pos()
    assert orb.check_exit(p, candle(10, 5, 100, 102.1, 99, 101.5)) is None   # reached +1R (102)
    assert p.stop == 100 and p.trailed
    assert orb.check_exit(p, candle(10, 10, 101, 101.5, 99.5, 100)) == (100, "breakeven stop")


def test_trail_not_applied_before_1r():
    p = pos()
    orb.check_exit(p, candle(10, 5, 100, 101.9, 99, 101))
    assert p.stop == 98 and not p.trailed


# ---- full day through the engine ----------------------------------------------
PARAMS = StrategyParams(watchlist_size=8, liquidity_lookback_days=20, atr_period=14, atr_pct_min=1.0,
                        atr_pct_max=3.5, prep_candidates=16, market_sma_days=20,
                        opening_range_start=time(9, 15), opening_range_end=time(9, 45),
                        gap_filter_pct=2.0, volume_multiple=1.5, volume_lookback_sessions=20,
                        reward_r_multiple=2.0, breakeven_trigger_r=1.0)


def session(market_ok=True, prev_close=100, live_from=None):
    r = risk()
    b = PaperBroker(r, COSTS, 10000)
    return DaySession(DAY, [WatchItem("X", prev_close, 1000)], market_ok, PARAMS, r, b, live_from), b


def feed_opening_range(s):
    for m in range(15, 45, 5):   # 09:15 .. 09:40, range 99-101
        s.process(at(9, m), {"X": candle(9, m, 100, 101, 99, 100)})


def test_forced_exit_at_1510():
    s, b = session()
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=2000)})  # breakout
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})           # fill at 101.5 + slip
    assert "X" in b.positions
    t = 9 * 60 + 55
    while t < 15 * 60 + 10:      # drift sideways all day: no stop, no target
        s.process(at(t // 60, t % 60), {"X": candle(t // 60, t % 60, 101.6, 101.9, 101.3, 101.6)})
        t += 5
    assert "X" in b.positions
    s.process(at(15, 10), {"X": candle(15, 10, 101.7, 101.8, 101.6, 101.7)})
    assert not b.positions and s.finished
    tr = b.trades[0]
    assert tr.reason == "forced exit 15:10" and tr.exit == pytest.approx(101.7 * 0.9995)
    assert tr.net_pnl == pytest.approx(tr.gross_pnl - tr.charges)


def test_gap_filter_skips_stock():
    s, b = session(prev_close=97)            # opens at 100: +3.1% gap
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=2000)})
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})
    assert not b.positions and any("gap filter" in e.detail for e in s.events)


def test_market_filter_off_means_no_trades():
    s, b = session(market_ok=False)
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=2000)})
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})
    assert not b.positions


def test_low_volume_breakout_ignored():
    s, b = session()
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=1400)})  # < 1.5x
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})
    assert not b.positions


def test_late_start_marks_earlier_signals_as_missed():
    s, b = session(live_from=at(11, 0))
    feed_opening_range(s)
    s.process(at(9, 45), {"X": candle(9, 45, 100.5, 101.6, 100.4, 101.5, v=2000)})
    s.process(at(9, 50), {"X": candle(9, 50, 101.5, 101.8, 101.2, 101.6)})
    assert not b.positions and any("missed" in e.detail for e in s.events)


def test_no_order_placement_code_exists():
    """CLAUDE.md rule 1: no real order path in Phases 0-2."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    text = "".join(p.read_text() for p in src.rglob("*.py"))
    for forbidden in ("kiteconnect", "place_order", "KiteConnect"):
        assert forbidden not in text
