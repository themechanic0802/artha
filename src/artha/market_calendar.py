"""NSE trading calendar: weekends + config/holidays_2026.yaml."""
from __future__ import annotations

from datetime import date, timedelta

from .settings import load_yaml


def holidays() -> dict[date, str]:
    cfg = load_yaml("holidays_2026.yaml")
    return {h["date"]: h["name"] for h in cfg.get("holidays", [])}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays()


def why_closed(d: date) -> str | None:
    if d.weekday() >= 5:
        return "weekend"
    return holidays().get(d)


def next_trading_day(after: date) -> date:
    d = after + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(before: date) -> date:
    d = before - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
