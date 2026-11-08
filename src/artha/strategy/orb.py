"""Strategy v0: opening range breakout (long only, intraday).

Everything here is deterministic and only looks at data up to the candle being
processed, so the same code runs the backtest and the live paper day.
Candles are labelled by their START time (a "09:40" candle covers 09:40-09:45).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..settings import load_yaml, parse_hhmm

CANDLE = timedelta(minutes=5)


@dataclass(frozen=True)
class StrategyParams:
    watchlist_size: int
    liquidity_lookback_days: int
    atr_period: int
    atr_pct_min: float
    atr_pct_max: float
    prep_candidates: int
    market_sma_days: int
    opening_range_start: time
    opening_range_end: time
    gap_filter_pct: float
    volume_multiple: float
    volume_lookback_sessions: int
    reward_r_multiple: float
    breakeven_trigger_r: float

    @classmethod
    def from_config(cls) -> "StrategyParams":
        c = load_yaml("strategy.yaml")
        out = {}
        for k, f in cls.__dataclass_fields__.items():
            v = c[k]
            out[k] = parse_hhmm(v) if f.type == "time" else v
        return cls(**out)

    @property
    def opening_range_candles(self) -> int:
        start = datetime.combine(datetime.today(), self.opening_range_start)
        end = datetime.combine(datetime.today(), self.opening_range_end)
        return int((end - start) / CANDLE)


@dataclass(frozen=True)
class Candle:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    ticker: str
    qty: int
    entry: float          # fill price including slippage
    initial_stop: float
    stop: float
    target: float
    entry_time: datetime
    buy_charges: float
    trailed: bool = False

    @property
    def r_per_share(self) -> float:
        return self.entry - self.initial_stop


def gap_pct(day_open: float, prev_close: float) -> float:
    return (day_open - prev_close) / prev_close * 100


def gap_too_big(day_open: float, prev_close: float, p: StrategyParams) -> bool:
    return abs(gap_pct(day_open, prev_close)) > p.gap_filter_pct


def in_opening_range(c: Candle, p: StrategyParams) -> bool:
    return p.opening_range_start <= c.start.time() < p.opening_range_end


def is_breakout(c: Candle, or_high: float, avg_volume: float, p: StrategyParams) -> bool:
    """Candle CLOSES above the opening-range high on >= 1.5x average 5-min volume."""
    return c.start.time() >= p.opening_range_end and c.close > or_high \
        and c.volume >= p.volume_multiple * avg_volume


def target_for(entry: float, stop: float, p: StrategyParams) -> float:
    return entry + p.reward_r_multiple * (entry - stop)


def check_exit(pos: Position, c: Candle, trigger_r: float = 1.0) -> tuple[float, str] | None:
    """Return (raw exit price, reason) if this candle exits the position, else None.

    Order matters and is deliberately conservative:
    1. Opening below the stop -> out at the open (a gap through the stop).
    2. Touching the stop -> out at the stop. If the same candle also touched the
       target we can't know which came first, so we assume the stop (same-candle rule).
    3. Touching the target -> out at the target (or the open if it gapped above).
    4. Otherwise, if price reached entry + 1R, move the stop to entry (breakeven)
       for the NEXT candles.
    """
    stop_reason = "breakeven stop" if pos.trailed else "stop"
    if c.open <= pos.stop:
        return c.open, stop_reason + " (gapped through)"
    if c.low <= pos.stop:
        return pos.stop, stop_reason
    if c.high >= pos.target:
        return max(pos.target, c.open), "target"
    if not pos.trailed and c.high >= pos.entry + pos.r_per_share * trigger_r:
        pos.stop = max(pos.stop, pos.entry)
        pos.trailed = True
    return None
