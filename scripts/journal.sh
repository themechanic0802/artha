#!/bin/bash
# Journal (scheduled 15:40 by launchd): Claude reads today's report and writes a
# 2-minute plain-English summary to journal/YYYY-MM-DD.md.
# By hand:  ~/artha/scripts/journal.sh [YYYY-MM-DD]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
DAY="${1:-$(TZ=Asia/Kolkata date +%F)}"
REPORT="$ROOT/reports/$DAY.md"
OUT="$ROOT/journal/$DAY.md"
LOG="$ROOT/logs/journal-$DAY.log"
cd "$ROOT" || exit 1

notify() { osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true; }

if ! "$PY" -c "import sys,datetime; from artha.market_calendar import is_trading_day; sys.exit(0 if is_trading_day(datetime.date.fromisoformat('$DAY')) else 1)"; then
  echo "$(date '+%F %T') $DAY is not a market day; no journal." >> "$ROOT/logs/launchd-journal.log"
  exit 0
fi
# run_day writes the report at 15:35; give it up to 20 more minutes.
for _ in $(seq 1 20); do [ -f "$REPORT" ] && break; sleep 60; done
if [ ! -f "$REPORT" ]; then
  echo "$(date '+%F %T') no report at $REPORT" >> "$LOG"
  notify "Artha: no day report" "reports/$DAY.md is missing. Check logs/run_day-$DAY.log"
  exit 1
fi

CLAUDE="$("$PY" -m artha.claude_cli)"
PROMPT="You are writing Artha's daily journal for its owner, who is not a market expert.
Below is today's paper-trading report from Artha (an automated opening-range-breakout system for Nifty 50 stocks, Phase 0, paper only).
Write a short markdown journal entry (readable in under two minutes, max ~250 words) with these sections:
## What happened (plain English; explain any market term the first time you use it)
## Did Artha follow its rules? (check the events against the rules: no entries after 14:30, everything closed at 15:10, max 2 positions, stop = opening-range low, target = 2R, breakeven after +1R, gap filter 2%, market filter; say 'yes' or list any mismatch)
## Anything suspicious in the data? (missing candles, lag, odd prices, or 'nothing noticed')
## One-line takeaway
Do not suggest changing strategy parameters based on one day. Output only the markdown, starting with the first heading.

REPORT:
$(cat "$REPORT")"

TMP="$(mktemp)"
if [ -n "$CLAUDE" ] && CLAUDE_CODE_OAUTH_TOKEN="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)" \
     bash -c '[ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || unset CLAUDE_CODE_OAUTH_TOKEN; exec "$0" -p "$1" --output-format text' "$CLAUDE" "$PROMPT" \
     < /dev/null > "$TMP" 2>> "$LOG" && [ -s "$TMP" ] && ! head -c 200 "$TMP" | grep -q "Not logged in"; then
  { echo "# Artha journal, $DAY"; echo; cat "$TMP"; echo; echo "_Source: reports/$DAY.md. Paper trading only._"; } > "$OUT"
  notify "Artha: journal ready" "journal/$DAY.md"
else
  { echo "# Artha journal, $DAY"; echo
    echo "**Claude could not write today's journal** (see logs/journal-$DAY.log). Read reports/$DAY.md directly.";
    echo; grep -E '^- (Net P&L|Trades|Paper account|Market filter)' "$REPORT"; } > "$OUT"
  head -c 300 "$TMP" >> "$LOG"
  notify "Artha: journal FAILED" "Claude unavailable; summary written from the report instead."
fi
rm -f "$TMP"
