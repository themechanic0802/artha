"""The risk layer. Every entry (paper now, real later) must be approved here.

Design notes for the owner:
- Limits are loaded once from config/risk.yaml into a *frozen* dataclass, so no code
  (and no LLM) can loosen them while the system runs. To change a limit, edit the
  YAML file and commit it with the reason.
- The paper broker calls `check_entry` itself before opening anything, so there is
  no route to a position that skips this check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time

from ..costs import CostModel
from ..settings import load_yaml, parse_hhmm


@dataclass(frozen=True)
class RiskLimits:
    capital: float
    risk_per_trade_pct: float
    daily_loss_limit_pct: float
    max_open_positions: int
    max_drawdown_pause_pct: float
    min_profit_to_cost_ratio: float
    no_new_entries_after: time
    force_exit_at: time

    @classmethod
    def from_config(cls) -> "RiskLimits":
        c = load_yaml("risk.yaml")
        if str(c.get("leverage", "none")).lower() != "none":
            raise ValueError("Leverage is not allowed in v0 (config/risk.yaml).")
        return cls(
            capital=float(c["capital"]),
            risk_per_trade_pct=float(c["risk_per_trade_pct"]),
            daily_loss_limit_pct=float(c["daily_loss_limit_pct"]),
            max_open_positions=int(c["max_open_positions"]),
            max_drawdown_pause_pct=float(c["max_drawdown_pause_pct"]),
            min_profit_to_cost_ratio=float(c["min_profit_to_cost_ratio"]),
            no_new_entries_after=parse_hhmm(c["no_new_entries_after"]),
            force_exit_at=parse_hhmm(c["force_exit_at"]),
        )


@dataclass(frozen=True)
class EntryDecision:
    approved: bool
    qty: int
    reason: str


@dataclass
class RiskManager:
    limits: RiskLimits
    costs: CostModel
    day_start_equity: float
    peak_equity: float
    events: list[str] = field(default_factory=list)
    _daily_limit_hit: bool = False

    # ---- account-level checks -------------------------------------------------
    @property
    def risk_budget(self) -> float:
        return self.day_start_equity * self.limits.risk_per_trade_pct / 100

    @property
    def daily_loss_limit(self) -> float:
        return self.day_start_equity * self.limits.daily_loss_limit_pct / 100

    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.day_start_equity) / self.peak_equity * 100)

    def system_paused(self) -> bool:
        return self.drawdown_pct() >= self.limits.max_drawdown_pause_pct

    def update_daily_loss(self, now: datetime, realised_pnl: float, open_pnl: float) -> bool:
        """Returns True once realised + open loss has reached the daily limit.
        The block stays on for the rest of the day even if prices recover."""
        if not self._daily_limit_hit and realised_pnl + open_pnl <= -self.daily_loss_limit:
            self._daily_limit_hit = True
            self.events.append(
                f"{now:%H:%M} Daily loss limit reached (realised {realised_pnl:+.2f} + open "
                f"{open_pnl:+.2f} <= -{self.daily_loss_limit:.2f}). No new entries today.")
        return self._daily_limit_hit

    def must_force_exit(self, now: datetime) -> bool:
        return now.time() >= self.limits.force_exit_at

    # ---- per-order check --------------------------------------------------------
    def size_position(self, entry: float, stop: float, available_cash: float) -> tuple[int, str]:
        """floor(risk budget / risk per share), capped so notional + buy charges fit in cash."""
        per_share = entry - stop
        if per_share <= 0:
            return 0, "stop is not below entry"
        qty = math.floor(self.risk_budget / per_share)
        capped = False
        cash_cap = math.floor(available_cash / entry) if entry > 0 else 0
        if cash_cap < qty:
            qty, capped = cash_cap, True
        # No leverage: make sure the buy charges also fit in the cash we have.
        while qty > 0 and qty * entry + self.costs.order_charges("buy", qty, entry)["total"] > available_cash:
            qty -= 1
            capped = True
        return qty, ("capped by available cash" if capped else "full risk budget")

    def check_entry(self, now: datetime, entry: float, stop: float, target: float,
                    available_cash: float, open_positions: int,
                    realised_pnl: float, open_pnl: float) -> EntryDecision:
        if self.system_paused():
            return EntryDecision(False, 0, f"system paused: drawdown {self.drawdown_pct():.1f}% "
                                           f">= {self.limits.max_drawdown_pause_pct}% (owner review needed)")
        if now.time() > self.limits.no_new_entries_after:
            return EntryDecision(False, 0, f"no new entries after {self.limits.no_new_entries_after:%H:%M}")
        if now.time() >= self.limits.force_exit_at:
            return EntryDecision(False, 0, "past forced-exit time")
        if self.update_daily_loss(now, realised_pnl, open_pnl):
            return EntryDecision(False, 0, "daily loss limit reached")
        if open_positions >= self.limits.max_open_positions:
            return EntryDecision(False, 0, f"already {open_positions} open positions (max {self.limits.max_open_positions})")
        qty, how = self.size_position(entry, stop, available_cash)
        if qty < 1:
            return EntryDecision(False, 0, f"position size < 1 share ({how})")
        exit_at_target = self.costs.apply_slippage("sell", target)
        expected_profit = (exit_at_target - entry) * qty
        round_trip = self.costs.round_trip(qty, entry, exit_at_target)
        if expected_profit < self.limits.min_profit_to_cost_ratio * round_trip:
            return EntryDecision(False, 0, f"profit at target INR {expected_profit:.2f} does not clear "
                                           f"{self.limits.min_profit_to_cost_ratio:g}x round-trip costs INR {round_trip:.2f}")
        return EntryDecision(True, qty, f"approved: {qty} shares, {how}")
