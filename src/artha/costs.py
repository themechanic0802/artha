"""Zerodha equity intraday cost model (config/costs.yaml).

Every backtest and paper result goes through this module, so no result is ever
reported gross of costs. Slippage is applied to fill prices separately (see
`apply_slippage`), and charges are computed on the slipped prices.
"""
from __future__ import annotations

from dataclasses import dataclass

from .settings import load_yaml


@dataclass(frozen=True)
class CostModel:
    brokerage_flat_inr: float
    brokerage_pct: float
    stt_sell_pct: float
    exchange_txn_pct: float
    sebi_per_crore_inr: float
    stamp_duty_buy_pct: float
    gst_pct: float
    slippage_pct_per_side: float

    @classmethod
    def from_config(cls) -> "CostModel":
        c = load_yaml("costs.yaml")
        return cls(**{k: float(c[k]) for k in cls.__dataclass_fields__})

    def order_charges(self, side: str, qty: int, price: float) -> dict[str, float]:
        """Charges for one executed order. side is 'buy' or 'sell'."""
        value = qty * price
        brokerage = min(self.brokerage_flat_inr, value * self.brokerage_pct / 100)
        stt = value * self.stt_sell_pct / 100 if side == "sell" else 0.0
        txn = value * self.exchange_txn_pct / 100
        sebi = value * self.sebi_per_crore_inr / 1e7
        stamp = value * self.stamp_duty_buy_pct / 100 if side == "buy" else 0.0
        gst = (brokerage + txn + sebi) * self.gst_pct / 100
        total = brokerage + stt + txn + sebi + stamp + gst
        return {"brokerage": brokerage, "stt": stt, "txn": txn, "sebi": sebi,
                "stamp": stamp, "gst": gst, "total": total}

    def round_trip(self, qty: int, buy_price: float, sell_price: float) -> float:
        return (self.order_charges("buy", qty, buy_price)["total"]
                + self.order_charges("sell", qty, sell_price)["total"])

    def apply_slippage(self, side: str, price: float) -> float:
        """Buys fill a little higher, sells a little lower."""
        s = self.slippage_pct_per_side / 100
        return price * (1 + s) if side == "buy" else price * (1 - s)
