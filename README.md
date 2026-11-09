# Project Artha

Artha is an automated research and paper-trading system for Nifty 50 stocks (Zerodha), built in phases. **Current phase: 0.** Artha trades on paper only, using free yfinance data. No real-order code exists. See `CLAUDE.md` for the rules and the roadmap.

## Dashboard

Everything Artha produces is on one page: `reports/dashboard.html`. To open it:

```bash
open ~/artha/reports/dashboard.html
```

Bookmark it. A background job rebuilds it every minute, and the page reloads itself every minute.

- **Today**
  - Account, today's P&L, open positions and market filter.
  - A timeline of the day's jobs (done / running / missed).
  - **1 · Start of day:** prep note, pre-market note, exclusions.
  - **2 · During the day:** each stock's price, opening range and status, open positions, closed trades and a live activity feed. This is updated by `run_day.py` after every poll.
  - **3 · End of day:** day report and journal.
- **History:** the paper account curve, and every past day report and journal.
- **Backtest:** the quick backtest summary.
- **System health:** whether each scheduled job is loaded, missing setup (Claude token, cost check), and recent log lines.

The dashboard only reads files. It cannot affect trading.

## Daily routine (Phase 0)

| When (IST) | What runs | What you get |
|---|---|---|
| 20:00 the evening before a market day | `scripts/evening_prep.sh` | `reports/YYYY-MM-DD-prep.md` + notification "Artha: prep note ready" |
| 08:40 on market days | `scripts/run_day.py` (wrapped in `caffeinate`) | notification "Artha: morning run started", `reports/YYYY-MM-DD-premarket.md` |
| 15:35 | (same run) | `reports/YYYY-MM-DD.md` + notification "Artha: day report ready" |
| 15:40 | `scripts/journal.sh` | `journal/YYYY-MM-DD.md` + notification "Artha: journal ready" |
| every minute | `scripts/dashboard.py` | `reports/dashboard.html` refreshed |

All three jobs fire every day, but they do nothing on weekends and NSE holidays (`config/holidays_2026.yaml`). Evening prep runs only when the *next* day is a market day: Friday's prep is skipped and Sunday's prepares Monday.

**Keep the Mac plugged in, lid open and logged in on market days.** `caffeinate` stops it sleeping while on mains power, but closing the lid still puts it to sleep. If the Mac is asleep at 08:40, the run starts as soon as it wakes and catches up, but any trades it would have taken before then are listed as "missed". If the Mac is shut down, the job doesn't run at all.

Optional: to wake the Mac automatically before the run, type this in Terminal (it asks for your Mac password):

```bash
sudo pmset repeat wakeorpoweron MTWRF 08:30:00
```

## How to check that tonight's prep ran

1. You should have had a notification, "Artha: prep note ready".
2. Open `reports/<tomorrow's date>-prep.md`. It should have tomorrow's date, the market mood, 8 stocks and an Exclusions section.
3. In the Exclusions section, "News/events check: done." means the Claude web check worked. "FAILED" means it didn't, and Artha will rely on the 2% gap filter instead (see the one-time setup below).
4. If there's no file, look at `logs/launchd-com.artha.eveningprep.out` and `logs/prep-<date>.log`. To re-run prep by hand:

```bash
~/artha/scripts/evening_prep.sh --force
```

## How to confirm the morning run started

1. Around 08:45 you get a notification, "Artha: morning run started".
2. `reports/<today>-premarket.md` exists.
3. This command shows a number (a process ID) in the first column while the run is going:

```bash
launchctl list | grep com.artha.runday
```

4. The live log is `logs/run_day-<today>.log`. If something looks wrong, check `logs/launchd-com.artha.runday.out`.

If it didn't start (for example, the Mac was off), start it by hand. It catches up from the current time:

```bash
cd ~/artha && .venv/bin/python scripts/run_day.py
```

## Where to find things

- `reports/YYYY-MM-DD-prep.md` is the evening prep note.
- `reports/YYYY-MM-DD-premarket.md` is the 08:45 note.
- `reports/YYYY-MM-DD.md` is the full day report: every signal taken or skipped with the reason, fills, P&L after costs, risk events and data problems.
- `journal/YYYY-MM-DD.md` is the 2-minute summary written by Claude.
- `reports/backtest/` holds the quick backtest (summary.md, trades.csv, equity_curve.csv, events.csv).
- `reports/dryrun/` holds replays of past days.
- `data_cache/paper_ledger.json` tracks the paper account balance across days.
- `logs/` has one log per job per day.

## One-time setup: let background jobs use Claude

The evening news check and the journal call Claude Code in the background (`claude -p`). Background jobs can't use the desktop app's login, so they need a long-lived token. You do this once:

1. In Terminal, run the command below and follow the browser login. It prints a token.

```bash
~/artha/scripts/claude.sh setup-token
```

2. Create the file `~/artha/.env` (copy `.env.example`) and paste the token after `CLAUDE_CODE_OAUTH_TOKEN=`. Never paste it into a chat, and never commit it.
3. Check that it works (this never prints the token):

```bash
~/artha/scripts/check_claude.sh
```

If this isn't done, everything else still runs. The prep note then says the news check FAILED, and the journal says Claude couldn't write it.

## Commands

```bash
cd ~/artha && .venv/bin/pytest -q
```

```bash
cd ~/artha && .venv/bin/python scripts/backtest_quick.py
```

```bash
cd ~/artha && .venv/bin/python scripts/run_day.py --dry-run
```

```bash
~/artha/scripts/install_schedule.sh
```

The first runs the unit tests and the second runs the quick backtest. The third replays the last market day in seconds. The fourth (re)installs the schedule, and `--uninstall` removes it. While a trading day is running, the installer leaves `com.artha.runday` alone so it can't stop the run.

## Layout

```
config/    risk.yaml (risk limits), costs.yaml (Zerodha charges), strategy.yaml, universe.yaml,
           exclusions.yaml (written each evening), holidays_2026.yaml
src/artha/ data/ (yfinance), strategy/ (ORB + watchlist), risk/ (risk layer), execution/ (paper broker),
           reporting/, engine.py (one trading day, candle by candle; shared by backtest and live)
scripts/   backtest_quick.py, evening_prep.sh/.py, run_day.py, journal.sh, install_schedule.sh
tests/
```

## To verify before Phase 1

- `config/costs.yaml`: check every rate against Zerodha's brokerage calculator and fill in `verified_on`.
- `config/holidays_2026.yaml`: check 10 Nov, 24 Nov and 25 Dec against the NSE holiday circular. Jan–Oct dates were cross-checked against Yahoo's data.
- Yahoo's "previous close" is the last traded price, which can differ slightly from NSE's official closing price (a 30-minute average). This affects the 2% gap filter only marginally.

## Known limitations of Phase 0

- yfinance intraday data is unofficial and can be delayed or have missing candles. Paper results from it are indicative only.
- 60 days of 5-minute history is far too short to prove an edge. Phase 1 exists for that.
