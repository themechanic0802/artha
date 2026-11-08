#!/bin/bash
# Evening prep (scheduled ~20:00 by launchd). Runs only when TOMORROW is a market
# day, so Friday's 20:00 run is skipped and Sunday's prepares Monday.
# Run by hand any time with:  ~/artha/scripts/evening_prep.sh --force
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1
if [ "${1:-}" != "--force" ]; then
  if ! "$PY" -c "
import sys; from datetime import datetime, timedelta
from artha.settings import IST; from artha.market_calendar import is_trading_day
sys.exit(0 if is_trading_day(datetime.now(IST).date() + timedelta(days=1)) else 1)"; then
    echo "$(date '+%F %T') tomorrow is not a market day; skipping prep." >> "$ROOT/logs/launchd-prep.log"
    exit 0
  fi
fi
exec "$PY" "$ROOT/scripts/evening_prep.py"
