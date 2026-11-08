"""One trading day, processed candle by candle.

The backtest and the live paper runner both feed candles into a DaySession in
time order, so they share exactly the same strategy, risk and fill logic.

Per 5-minute candle time t (in this order):
  1. If t >= 15:10: close every open position at this candle's open. Day over.
  2. Fill pending entries (signalled on the previous candle) at this candle's open
     + slippage, through the risk layer.
  3. Manage open positions with this candle (stop / target / breakeven trail).
  4. Mark open positions to the candle close and check the daily loss limit.
  5. Build the opening range (09:15-09:45) and gap-check the first candle.
  6. After 09:45, look for breakout signals; a signal fills on the NEXT candle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .execution.paper_broker import PaperBroker
from .risk.risk_manager import RiskManager
from .strategy import orb
from .strategy.orb import Candle, StrategyParams


@dataclass
class WatchItem:
    ticker: str
    prev_close: float
    avg_5m_volume: float


@dataclass
class _StockState:
    item: WatchItem
    or_candles: list[Candle] = field(default_factory=list)
    or_high: float | None = None
    or_low: float | None = None
    skip_reason: str | None = None      # stock is out for the day
    traded: bool = False                 # one trade per stock per day
    pending_signal: Candle | None = None


@dataclass
class Event:
    time: datetime
    ticker: str
    kind: str      # signal_taken, signal_skipped, exit, skip_stock, info, data
    detail: str


class DaySession:
    def __init__(self, day: date, watchlist: list[WatchItem], market_ok: bool,
                 params: StrategyParams, risk: RiskManager, broker: PaperBroker,
                 live_from: datetime | None = None):
        self.day = day
        self.params = params
        self.risk = risk
        self.broker = broker
        self.market_ok = market_ok
        self.live_from = live_from   # signals that would fill before this are "missed"
        self.stocks = {w.ticker: _StockState(w) for w in watchlist}
        self.events: list[Event] = []
        self.marks: dict[str, float] = {}
        self.last_time: datetime | None = None
        self.finished = False

    def _log(self, t, ticker, kind, detail):
        self.events.append(Event(t, ticker, kind, detail))

    # ------------------------------------------------------------------------
    def process(self, t: datetime, candles: dict[str, Candle]) -> None:
        """Feed all watchlist candles that START at time t (must be called in time order)."""
        if self.finished:
            return
        if self.last_time is not None and t <= self.last_time:
            return  # already processed (live polling re-sends old candles)
        self.last_time = t
        p = self.params

        # 1. forced exit
        if self.risk.must_force_exit(t):
            self.force_close_all(t, {k: c.open for k, c in candles.items()}, "forced exit 15:10")
            return

        # 2. pending entries fill at this candle's open
        for tk, st in self.stocks.items():
            if st.pending_signal is None:
                continue
            sig, st.pending_signal = st.pending_signal, None
            c = candles.get(tk)
            if c is None:
                self._log(t, tk, "signal_skipped", "no candle to fill at (missing data)")
                continue
            if self.live_from is not None and t < self.live_from:
                self._log(t, tk, "signal_skipped", "missed: Artha was not running yet (late start)")
                continue
            fill = self.broker.costs.apply_slippage("buy", c.open)
            stop = st.or_low
            target = orb.target_for(fill, stop, p)
            d = self.broker.open(tk, t, fill, stop, target, self.marks)
            if d.approved:
                st.traded = True
                self._log(t, tk, "signal_taken",
                          f"breakout at {sig.start:%H:%M} close {sig.close:.2f} > OR high {st.or_high:.2f}, "
                          f"vol {sig.volume:,.0f} vs avg {st.item.avg_5m_volume:,.0f}. Bought {d.qty} @ {fill:.2f} "
                          f"(open {c.open:.2f} + slippage), stop {stop:.2f}, target {target:.2f}. {d.reason}")
            else:
                self._log(t, tk, "signal_skipped", f"breakout at {sig.start:%H:%M} rejected by risk layer: {d.reason}")
                # A full book can free up later, so allow another signal then. Any other
                # rejection (time cutoff, daily loss, size/cost) ends this stock's day,
                # which keeps the report from repeating the same rejection every candle.
                if not d.reason.startswith("already"):
                    st.skip_reason = f"risk layer: {d.reason}"

        # 3. manage open positions
        for tk in list(self.broker.positions):
            c = candles.get(tk)
            if c is None:
                continue
            pos = self.broker.positions[tk]
            was_trailed = pos.trailed
            hit = orb.check_exit(pos, c, p.breakeven_trigger_r)
            if hit:
                price, reason = hit
                tr = self.broker.close(tk, t, price, reason)
                self._log(t, tk, "exit", f"{reason}: sold {tr.qty} @ {tr.exit:.2f}, net P&L {tr.net_pnl:+.2f} "
                                         f"(charges {tr.charges:.2f}), {tr.r_multiple:+.2f}R")
            elif pos.trailed and not was_trailed:
                self._log(t, tk, "info", f"reached +1R; stop moved to breakeven {pos.stop:.2f}")

        # 4. mark to market + daily loss check
        for tk, c in candles.items():
            self.marks[tk] = c.close
        self.risk.update_daily_loss(t, self.broker.realised_pnl, self.broker.open_pnl(self.marks))

        # 5 + 6. opening range, gap filter, signals
        for tk, st in self.stocks.items():
            c = candles.get(tk)
            if c is None or st.skip_reason:
                continue
            if orb.in_opening_range(c, p):
                if not st.or_candles:
                    if c.start.time() != p.opening_range_start:
                        st.skip_reason = f"first candle at {c.start:%H:%M}, not {p.opening_range_start:%H:%M} (missing data)"
                        self._log(t, tk, "skip_stock", st.skip_reason)
                        continue
                    g = orb.gap_pct(c.open, st.item.prev_close)
                    if orb.gap_too_big(c.open, st.item.prev_close, p):
                        st.skip_reason = f"gap filter: opened {g:+.2f}% vs previous close (limit +/-{p.gap_filter_pct}%)"
                        self._log(t, tk, "skip_stock", st.skip_reason)
                        continue
                st.or_candles.append(c)
                continue
            if st.or_high is None:
                n = len(st.or_candles)
                if n < p.opening_range_candles:
                    st.skip_reason = f"opening range incomplete ({n}/{p.opening_range_candles} candles)"
                    self._log(t, tk, "skip_stock", st.skip_reason)
                    continue
                st.or_high = max(x.high for x in st.or_candles)
                st.or_low = min(x.low for x in st.or_candles)
                self._log(t, tk, "info", f"opening range {st.or_low:.2f} - {st.or_high:.2f}")
            if st.traded or tk in self.broker.positions:
                continue
            if orb.is_breakout(c, st.or_high, st.item.avg_5m_volume, p):
                if not self.market_ok:
                    self._log(t, tk, "signal_skipped", f"breakout at {c.start:%H:%M} ignored: market filter is off")
                    st.skip_reason = "market filter off"
                    continue
                st.pending_signal = c

    def force_close_all(self, t: datetime, prices: dict[str, float], reason: str) -> None:
        for tk in list(self.broker.positions):
            px = prices.get(tk, self.marks.get(tk))
            if px is None:
                px = self.broker.positions[tk].entry
                self._log(t, tk, "data", "no price for forced exit; closed at entry price (check data)")
            tr = self.broker.close(tk, t, px, reason)
            self._log(t, tk, "exit", f"{reason}: sold {tr.qty} @ {tr.exit:.2f}, net P&L {tr.net_pnl:+.2f} "
                                     f"(charges {tr.charges:.2f}), {tr.r_multiple:+.2f}R")
        for st in self.stocks.values():
            st.pending_signal = None
        self.finished = True
