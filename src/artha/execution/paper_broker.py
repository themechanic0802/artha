"""Paper broker: simulates fills, cash and costs. NO real orders exist anywhere in
this codebase in Phases 0-2 (CLAUDE.md rule 1); a Kite broker is a Phase 3 task.

Every entry goes through RiskManager.check_entry inside `open`, so nothing can
open a position without the risk layer's approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..costs import CostModel
from ..risk.risk_manager import EntryDecision, RiskManager
from ..strategy.orb import Position


@dataclass
class ClosedTrade:
    ticker: str
    qty: int
    entry_time: datetime
    entry: float
    initial_stop: float
    target: float
    exit_time: datetime
    exit: float
    reason: str
    gross_pnl: float
    charges: float
    net_pnl: float
    r_multiple: float     # net P&L divided by the amount risked at entry

    def as_row(self) -> dict:
        d = self.__dict__.copy()
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat()
        return d


class PaperBroker:
    def __init__(self, risk: RiskManager, costs: CostModel, equity: float):
        self.risk = risk
        self.costs = costs
        self.cash = equity
        self.positions: dict[str, Position] = {}
        self.trades: list[ClosedTrade] = []

    @property
    def realised_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    def open_pnl(self, marks: dict[str, float]) -> float:
        """Unrealised P&L, including estimated exit costs (conservative)."""
        total = 0.0
        for t, p in self.positions.items():
            px = self.costs.apply_slippage("sell", marks.get(t, p.entry))
            total += (px - p.entry) * p.qty - p.buy_charges - self.costs.order_charges("sell", p.qty, px)["total"]
        return total

    def open(self, ticker: str, now: datetime, fill: float, stop: float, target: float,
             marks: dict[str, float]) -> EntryDecision:
        decision = self.risk.check_entry(now, fill, stop, target, available_cash=self.cash,
                                         open_positions=len(self.positions),
                                         realised_pnl=self.realised_pnl, open_pnl=self.open_pnl(marks))
        if not decision.approved:
            return decision
        charges = self.costs.order_charges("buy", decision.qty, fill)["total"]
        self.cash -= decision.qty * fill + charges
        self.positions[ticker] = Position(ticker, decision.qty, fill, stop, stop, target, now, charges)
        return decision

    def close(self, ticker: str, now: datetime, raw_price: float, reason: str) -> ClosedTrade:
        p = self.positions.pop(ticker)
        fill = self.costs.apply_slippage("sell", raw_price)
        sell_charges = self.costs.order_charges("sell", p.qty, fill)["total"]
        self.cash += p.qty * fill - sell_charges
        gross = (fill - p.entry) * p.qty
        charges = p.buy_charges + sell_charges
        risked = p.r_per_share * p.qty
        t = ClosedTrade(ticker, p.qty, p.entry_time, p.entry, p.initial_stop, p.target, now, fill,
                        reason, gross, charges, gross - charges, (gross - charges) / risked if risked else 0.0)
        self.trades.append(t)
        return t
