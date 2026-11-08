"""Paths, timezone and config loading shared by every module."""
from __future__ import annotations

from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
REPORTS_DIR = ROOT / "reports"
JOURNAL_DIR = ROOT / "journal"
CACHE_DIR = ROOT / "data_cache"
LOGS_DIR = ROOT / "logs"

KNOWN_LIMITATIONS = (
    "- yfinance intraday data is unofficial, can be delayed or have missing candles. "
    "Paper results from it are indicative only.\n"
    "- 60 days of 5-minute history is far too short to prove an edge. Phase 1 exists for that."
)


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f) or {}


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))
