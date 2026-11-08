# Project Artha: Indian equity trading agent (Zerodha)

The project is called **Project Artha**; the agent itself is **Artha**. Use "Project Artha" in the README title and "Artha" in reports, notifications and the journal. Package name: `artha`.

## Mission
Build a system that researches, backtests, paper trades and, only after it has proven an edge, trades a small Zerodha account automatically. Starting capital: ₹10,000. Success means beating Nifty 50 buy-and-hold **after all costs**, over 6+ months of live trading, within the agreed drawdown limit.

The owner wants maximum automation and is not a market expert. Their daily involvement should be: one Kite login in the morning (later phases), approving trades (Phase 3 only), and reading the end-of-day report. All research, shortlisting and analysis is done by the system. Reports must be written in plain English, explaining any market term the first time it appears.

Capital starts at ₹10,000 and will be topped up from savings only after the system proves itself. Long-term roadmap: intraday first, then short-term swing trading (days to weeks), then long-term investing, each as a separate strategy with its own capital bucket, backtest and phases (see "Strategy roadmap").

## Non-negotiable rules (read before every task)
1. **No real orders until the owner explicitly unlocks Phase 3.** In Phases 0–2 there must be no order-placement code path at all, not just a disabled flag.
2. **The LLM never decides trades.** Strategies are deterministic, backtestable Python. Claude's role in the running system is research, pre-market briefs, sanity notes and journaling.
3. **The risk layer is separate code** that every order (paper or real) must pass through. Nothing, including Claude, can bypass or loosen it at runtime. Risk limits change only by editing `config/risk.yaml`, committed with a reason in the message.
4. **Every result includes costs and slippage.** Never report a gross-only backtest.
5. **No secrets in git.** API keys, tokens and Telegram tokens live in `.env` (gitignored). Provide `.env.example`.
6. **Be honest about results.** If a strategy fails after costs, say so plainly and propose the next hypothesis. Never tune parameters on the same data used to report performance.

## Phases and gates
| Phase | What | Data source | Gate to move on |
|---|---|---|---|
| 0 | Quick backtest + automated paper-trading day runner | yfinance (free, delayed) | Runs a full market day unattended and produces a correct report |
| 1 | Proper backtesting: 2+ years, walk-forward, cost model | Kite Connect historical API | Strategy beats buy-and-hold after costs out-of-sample, max drawdown within limit |
| 2 | Paper trading on live Kite data, running on a cloud VM | Kite Connect WebSocket | 4+ weeks of paper results consistent with the backtest |
| 3 | Approval mode: real orders after owner taps Approve (Telegram) | Kite Connect + static IP | Owner approves moving on after 4+ weeks |
| 4 | Capped autonomy: real orders within risk limits, no approval step | same | Ongoing weekly review |

**Current phase: 0.** Target: a paper-trading run on the next market day.

## Strategy v0: opening range breakout (long only, intraday, cash equity)
- **Universe:** Nifty 50 constituents (try the NSE constituents CSV; fall back to a hardcoded list in `config/universe.yaml`). Yahoo tickers use the `.NS` suffix; Nifty index is `^NSEI`.
- **Daily watchlist (built the evening before):** rank the universe by 20-day average traded value; keep stocks whose 14-day ATR% is between 1.0% and 3.5%; drop anything in `config/exclusions.yaml`; take the top 8. The exclusions file is generated automatically each evening (see Phase 0, deliverable 2).
- **Market filter:** trade only if Nifty's previous close is above its 20-day simple moving average.
- **Opening range:** high and low of the 9:15–9:45 IST window (six 5-minute candles).
- **Gap filter:** skip a stock if it opens more than 2% away from the previous close (proxy for news).
- **Entry:** after 9:45, a 5-minute candle closes above the opening-range high with volume ≥ 1.5× that stock's average 5-minute volume over the previous 20 sessions. Fill at the next candle's open plus slippage.
- **Stop:** opening-range low. **Target:** entry + 2 × (entry − stop).
- **Trail:** once price reaches entry + 1R, move stop to entry (breakeven).
- **Same-candle rule:** if a candle touches both stop and target, assume the stop filled (conservative).
- **Limits:** one trade per stock per day; no new entries after 14:30; close everything at 15:10.

## Risk rules (`config/risk.yaml`)
- capital: 10000
- risk_per_trade_pct: 1.5  (₹150)
- daily_loss_limit_pct: 3.0  (₹300; stop all new entries for the day once realised + open loss reaches this)
- max_open_positions: 2
- max_drawdown_pause_pct: 15  (halt the system, require owner review)
- leverage: none in v0 (position notional capped at available capital)
- position size = floor(risk budget / (entry − stop)), capped by available capital; skip if size < 1 or expected profit at target doesn't clear round-trip costs by 2×

## Cost model (`config/costs.yaml`)
Model Zerodha equity intraday charges per trade: brokerage (lower of ₹20 or 0.03% per executed order), STT on the sell side, exchange transaction charges, SEBI fees, stamp duty on the buy side, and 18% GST on brokerage + transaction + SEBI fees. **Rates change; verify every value against Zerodha's brokerage calculator before Phase 1 and record the date checked.** Slippage default: 0.05% per side, configurable.

## Phase 0 deliverables (build in this order)
1. `scripts/backtest_quick.py` – runs strategy v0 on the last ~60 days of 5-minute yfinance data (Yahoo's intraday history limit), with costs. Outputs trades CSV, equity curve, win rate, average R, profit factor, max drawdown, and comparison with Nifty buy-and-hold over the same period. Clearly label it as a short, low-confidence sample. Show the owner a plain-English summary before moving on.
2. `scripts/evening_prep.sh` (runs ~20:00 on the evening before each market day):
   - Calls Claude Code in headless mode (`claude -p`) with web search to check, for every stock that passes the liquidity and ATR filters, whether the next market day has quarterly results, a board meeting, an ex-dividend/split/bonus date, or major breaking news.
   - Writes `config/exclusions.yaml`: one entry per excluded stock with a one-line reason and the source. The model's output must be parsed and validated (valid tickers, valid YAML) before it is written; if parsing fails, keep the file empty and log it.
   - Builds the watchlist and writes `reports/YYYY-MM-DD-prep.md`: market mood (Nifty vs its 20-day average, and what that means for tomorrow), the shortlist, every exclusion with its reason.
   - If the web check fails, say so in the prep note and rely on the morning gap filter.
3. `scripts/run_day.py` – one command that runs an entire paper-trading day unattended:
   - 08:45 load the watchlist, check for gaps at the open, write a short pre-market note
   - 09:15–15:10 poll 5-minute candles roughly once a minute, generate signals, simulate fills through the risk layer, manage stops and trails
   - 15:10 close all paper positions
   - 15:35 write `reports/YYYY-MM-DD.md` (watchlist, every signal taken or skipped with the reason, fills, P&L after costs, risk-limit events, data problems)
   - If launched after 08:45, catch up from the current time rather than fail. If no watchlist exists, build one without exclusions and flag it. On a market holiday or weekend, exit cleanly with a message.
4. `scripts/journal.sh` – after the report exists, calls Claude Code in headless mode to read the day's report and write `journal/YYYY-MM-DD.md`: a plain-English summary of what happened, whether behaviour matched the rules, anything suspicious in the data. Keep it short enough to read in two minutes.
5. Scheduling: set up the three jobs to run automatically on market days (evening prep ~20:00 the day before, run_day at 08:40, journal at 15:40), skipping weekends and holidays. Use whatever fits the owner's OS (launchd on macOS, Task Scheduler on Windows, cron on Linux). Keep the machine awake during market hours (e.g. `caffeinate -i` on macOS) and remind the owner to keep it plugged in.
6. A desktop notification when the prep note and the day's journal are ready.
7. A `README.md` section: exactly how to check that tonight's prep ran, how to confirm the morning run started, and where to find the reports.

**First run:** after building, run `evening_prep.sh` once tonight for the next market day, do a short dry run of `run_day.py` to confirm it launches, then confirm the schedule is active.

Market holidays: keep `config/holidays_2026.yaml` (NSE equity). Include 10 Nov 2026 (Diwali-Balipratipada), 24 Nov 2026 and 25 Dec 2026; verify against the NSE circular.

## Known limitations of Phase 0 (state these in every report)
- yfinance intraday data is unofficial, can be delayed or have missing candles. Paper results from it are indicative only.
- 60 days of 5-minute history is far too short to prove an edge. Phase 1 exists for that.

## Strategy roadmap (later phases, do not build yet)
- **Intraday (current):** opening range breakout, cash equity, no leverage in v0.
- **Swing (days to weeks):** to be researched after intraday reaches Phase 2. Delivery trades held under 12 months may be taxed as short-term capital gains rather than at slab rates; flag this for the owner to confirm with a CA.
- **Long-term investing:** a separate, slow-moving bucket.
- **F&O:** not in scope until capital and track record justify it. Options backtests need expired-contract data, which likely requires a paid data source.
- Each strategy gets its own capital bucket and risk limits, plus a portfolio-level limit across all buckets. Idle cash may later be parked in liquid funds or ETFs; treat this as a separate design task.

## Tech choices
- Python 3.11+, `pandas`, `numpy`, `yfinance`, `pyyaml`, `python-dotenv`, `pytest`. Add `kiteconnect` in Phase 1.
- All times in Asia/Kolkata. Use timezone-aware datetimes everywhere.
- Strategy, risk, data and execution are separate modules with clear interfaces so the yfinance data source and paper broker can later be swapped for Kite Connect without touching strategy code.
- Unit tests for: position sizing, cost calculation, same-candle stop/target rule, breakeven trail, daily loss limit, no entries after 14:30, forced exit at 15:10.

## Suggested layout
```
artha/
  CLAUDE.md
  README.md
  .env.example
  config/        universe.yaml, exclusions.yaml, risk.yaml, costs.yaml, holidays_2026.yaml
  src/
    data/        yfinance_source.py (later kite_source.py)
    strategy/    orb.py
    risk/        risk_manager.py
    execution/   paper_broker.py (later kite_broker.py, Phase 3+ only)
    reporting/   report.py
  scripts/       backtest_quick.py, run_day.py, journal.sh
  tests/
  reports/  journal/  data_cache/   (gitignored except .gitkeep)
```

## Accounts and access (for reference)
- **Phase 0 needs no accounts or API keys.** yfinance is free and keyless; the headless journal and evening prep use the owner's existing Claude Code login.
- **Secrets:** the owner types keys into `.env` themselves. Never ask the owner to paste a key into chat, never print keys in output or logs, and never commit them.
- **Zerodha trading account:** the owner's existing account. No new brokerage account.
- **Kite Connect (Phase 1+):** developer account at developers.kite.trade, using the owner's Zerodha client ID. The "Connect" plan (paid, per API key per month) includes live and historical data; the free "Personal" plan has no market data. API sessions expire daily and need a fresh login. Order placement requires a whitelisted static IP.
- **Kite MCP (optional):** Zerodha's hosted MCP server for asking questions about the account from Claude. Read-only apart from GTT orders. Useful for inspection, not part of the trading system.

## Working style
- Before writing code for a task, state a short plan. After finishing, run the tests and summarise what changed.
- Prefer small, readable modules over clever ones. The owner is not a full-time developer; explain any non-obvious decision in a comment.
- When something about the market mechanics is uncertain (charges, lot sizes, holidays, regulations), flag it and suggest how to verify instead of guessing.
