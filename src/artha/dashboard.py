"""Builds reports/dashboard.html: one self-contained page with everything Artha produces.

It only READS files (reports, journal, ledger, live status, logs) and never touches
trading. A launchd job rebuilds it every minute; the page reloads itself every minute.
Open it with:  .venv/bin/python scripts/dashboard.py --open
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
from datetime import date, datetime, time
from pathlib import Path

import markdown

from .market_calendar import is_trading_day, next_trading_day, previous_trading_day, why_closed
from .settings import CACHE_DIR, IST, JOURNAL_DIR, LOGS_DIR, REPORTS_DIR, ROOT, load_yaml

OUT = REPORTS_DIR / "dashboard.html"
JOBS = ["com.artha.eveningprep", "com.artha.runday", "com.artha.journal", "com.artha.dashboard"]
esc = html.escape


# ---- reading inputs (all tolerant of missing / half-written files) -----------------------
def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _json(path: Path) -> dict | None:
    txt = _read(path)
    try:
        return json.loads(txt) if txt else None
    except json.JSONDecodeError:
        return None


def md(text: str) -> str:
    # Reports are ours, but the journal is written by an LLM: never pass raw HTML through.
    return markdown.markdown(text.replace("<", "&lt;"), extensions=["tables", "fenced_code"])


def run_day_running() -> bool:
    lock = CACHE_DIR / "run_day.lock"
    try:
        os.kill(int(lock.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def launchd_jobs() -> dict[str, str]:
    """label -> 'running' / 'scheduled' / 'last run failed (exit N)' / 'NOT INSTALLED'."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {j: "unknown" for j in JOBS}
    res = {j: "NOT INSTALLED" for j in JOBS}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] in res:
            pid, code = parts[0], parts[1]
            res[parts[2]] = ("running" if pid != "-" else
                             "scheduled" if code in ("0", "-15") else f"scheduled; last run exit code {code}")
    return res


def tail(path: Path, n: int = 12) -> str:
    txt = _read(path)
    return "\n".join(txt.splitlines()[-n:]) if txt else ""


def token_configured() -> bool:
    env = _read(ROOT / ".env") or ""
    return bool(re.search(r"^CLAUDE_CODE_OAUTH_TOKEN=\S+", env, re.M))   # presence only, never the value


# ---- small formatting helpers ------------------------------------------------------------
def inr(x: float | None, sign: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{abs(x):,.2f}"
    pre = ("+" if x > 0 else "−" if x < 0 else "") if sign else ("−" if x < 0 else "")
    return f"{pre}₹{s}"


def tone(x: float | None) -> str:
    return "" if not x else ("pos" if x > 0 else "neg")


def px(x) -> str:
    return "–" if x is None else f"{x:,.2f}"


def hhmm(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else "–"


def table(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def doc(title: str, path: Path, open_: bool = False, missing: str = "Not written yet.") -> str:
    txt = _read(path)
    rel = path.relative_to(ROOT)
    body = md(txt) if txt else f'<p class="muted">{esc(missing)}</p>'
    badge = '<span class="pill ok">ready</span>' if txt else '<span class="pill wait">pending</span>'
    return (f'<details class="doc"{" open" if open_ and txt else ""}><summary>{esc(title)} {badge}'
            f'<span class="path">{esc(str(rel))}</span></summary><div class="md">{body}</div></details>')


# ---- the day's timeline ------------------------------------------------------------------
def timeline(day: date, now: datetime, live: dict | None) -> list[tuple[str, str, str, str]]:
    """(when, what, state, detail) where state is done / running / waiting / missed / failed."""
    at = lambda h, m: datetime.combine(day, time(h, m), tzinfo=IST)   # noqa: E731
    prev = previous_trading_day(day)
    prep = _read(REPORTS_DIR / f"{day}-prep.md")
    plan = _json(CACHE_DIR / f"watchlist-{day}.json") or {}
    steps = []

    if prep:
        wc = plan.get("web_check", "")
        mkt = plan.get("market")
        detail = ("market filter " + ("unknown" if not mkt else "ON" if mkt["ok"] else "OFF") +
                  "; news check " + ("done" if wc == "ok" else "FAILED" if wc.startswith("FAILED") else "partial"))
        bad = mkt is None or wc.startswith("FAILED")
        steps.append(("20:00 prev. evening", "Evening prep", "warn" if bad else "done", detail))
    else:
        due = datetime.combine(prev, time(20, 0), tzinfo=IST)
        steps.append(("20:00 prev. evening", "Evening prep", "missed" if now > due + _mins(60) else "waiting",
                      "no prep note (run_day will build a watchlist without news exclusions)"))

    pre = (REPORTS_DIR / f"{day}-premarket.md").exists()
    steps.append(("08:45", "Pre-market note", "done" if pre else ("missed" if now > at(9, 15) else "waiting"),
                  "watchlist loaded, account checked" if pre else ""))

    running = run_day_running()
    phase = (live or {}).get("phase")
    if phase in ("closed", "report ready") or (REPORTS_DIR / f"{day}.md").exists():
        st, det = "done", "all positions closed at or before 15:10"
    elif running and now >= at(9, 15):
        st, det = "running", f"last candle {hhmm((live or {}).get('last_candle'))}" if live else "running"
    elif running:
        st, det = "waiting", "run_day is up, waiting for the 09:15 open"
    else:
        st, det = ("missed" if now > at(9, 0) else "waiting"), "" if now <= at(9, 0) else "run_day is NOT running"
    steps.append(("09:15–15:10", "Live paper trading", st, det))

    rep = (REPORTS_DIR / f"{day}.md").exists()
    steps.append(("15:35", "Day report", "done" if rep else ("missed" if now > at(16, 0) else "waiting"), ""))
    jr = _read(JOURNAL_DIR / f"{day}.md")
    jstate = "waiting" if not jr else ("warn" if "could not" in jr.lower() or "failed" in jr.lower()[:400] else "done")
    steps.append(("15:40", "Journal", "missed" if not jr and now > at(16, 15) else jstate,
                  "Claude token missing: basic summary only" if jstate == "warn" else ""))
    return steps


def _mins(n: int):
    from datetime import timedelta
    return timedelta(minutes=n)


# ---- sections ----------------------------------------------------------------------------
def kpis(ledger: dict | None, live: dict | None, day: date, plan: dict) -> str:
    equity = (ledger or {}).get("equity")
    day_row = next((d for d in (ledger or {}).get("days", []) if d["date"] == str(day)), None)
    if live and live.get("phase") in ("pre-open", "live"):
        today = live["realised_pnl"] + live["open_pnl"]
        equity = live["equity_now"]
        n_open, n_trades = len(live["positions"]), len(live["trades"])
        today_note = "realised + open (after est. costs)"
    elif day_row:
        today, n_open, n_trades, today_note = day_row["net_pnl"], 0, day_row["trades"], "final, after all costs"
    else:
        today = live["realised_pnl"] if live else None
        n_open = len(live["positions"]) if live else 0
        n_trades = len(live["trades"]) if live else 0
        today_note = "no trades yet" if not today else ""
    if equity is None:
        equity = load_yaml("risk.yaml").get("capital")
    mkt = plan.get("market")
    mkt_txt = "unknown" if not mkt else ("ON" if mkt["ok"] else "OFF")
    mkt_note = ("no Nifty data: no trades" if not mkt else
                f"Nifty {mkt['close']:,.0f} vs 20-day avg {mkt['sma']:,.0f}")
    capital = load_yaml("risk.yaml").get("capital", 10000)
    tiles = [
        ("Paper account", inr(equity), f"started at {inr(capital)}", tone((equity or 0) - capital)),
        ("Today's P&L", inr(today, sign=True), today_note, tone(today)),
        ("Open positions", str(n_open), "max 2 at a time", ""),
        ("Trades today", str(n_trades), "", ""),
        ("Market filter", mkt_txt, mkt_note, "pos" if mkt and mkt["ok"] else "neg"),
    ]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="k">{k}</div><div class="v {c}">{esc(v)}</div><div class="n">{esc(n)}</div></div>'
        for k, v, n, c in tiles) + "</div>"


def watchlist_section(plan: dict, live: dict | None) -> str:
    if live and live.get("stocks"):
        rows = []
        for s in live["stocks"]:
            rng = "–" if s["or_low"] is None else f"{s['or_low']:,.2f} – {s['or_high']:,.2f}" + ("" if s["or_final"] else " (forming)")
            chg = None if s["last"] is None or not s["prev_close"] else (s["last"] / s["prev_close"] - 1) * 100
            state = s["state"]
            cls = "pos" if state.startswith("in trade") else "neg" if state.startswith("out") else ""
            rows.append([f"<b>{esc(s['ticker'])}</b>", px(s["prev_close"]), px(s["last"]),
                         f'<span class="{tone(chg)}">{"–" if chg is None else f"{chg:+.2f}%"}</span>',
                         rng, f'<span class="{cls}">{esc(state)}</span>'])
        return table(["Stock", "Prev close", "Last", "Change", "Opening range (09:15–09:45)", "Status"], rows)
    wl = plan.get("watchlist", [])
    if not wl:
        return '<p class="muted">No watchlist for this day yet.</p>'
    rows = [[f"<b>{esc(w['symbol'])}</b>", px(w["prev_close"]), f"{w['atr_pct']:.2f}%",
             f"₹{w['avg_traded_value_cr']:,.0f} cr"] for w in wl]
    return table(["Stock", "Prev close", "ATR% (typical daily move)", "Avg daily traded value"], rows)


def positions_section(live: dict | None) -> str:
    if not live or not live["positions"]:
        return '<p class="muted">No open positions.</p>'
    rows = [[f"<b>{esc(p['ticker'])}</b>", str(p["qty"]), hhmm(p["entry_time"]), px(p["entry"]),
             px(p["last"]), px(p["stop"]) + (" (breakeven)" if p["trailed"] else ""), px(p["target"]),
             f'<span class="{tone(p["pnl_before_costs"])}">{inr(p["pnl_before_costs"], sign=True)}</span>']
            for p in live["positions"]]
    return table(["Stock", "Qty", "Bought at", "Entry", "Last", "Stop", "Target", "P&L (before costs)"], rows)


def trades_section(live: dict | None) -> str:
    if not live or not live["trades"]:
        return '<p class="muted">No closed trades yet.</p>'
    rows = [[f"<b>{esc(t['ticker'])}</b>", str(t["qty"]), f"{hhmm(t['entry_time'])} @ {px(t['entry'])}",
             f"{hhmm(t['exit_time'])} @ {px(t['exit'])}", esc(t["reason"]), inr(t["charges"]),
             f'<span class="{tone(t["net_pnl"])}">{inr(t["net_pnl"], sign=True)}</span>', f"{t['r_multiple']:+.2f}R"]
            for t in live["trades"]]
    return table(["Stock", "Qty", "Entry", "Exit", "Why exited", "Charges", "Net P&L", "R"], rows)


KIND_LABEL = {"signal_taken": "BOUGHT", "signal_skipped": "skipped", "exit": "SOLD", "skip_stock": "dropped",
              "info": "info", "data": "data"}


def feed_section(live: dict | None, day: date) -> str:
    if not live:
        log = tail(LOGS_DIR / f"run_day-{day}.log", 15)
        return ('<p class="muted">The live activity feed starts when run_day writes its first status '
                '(any run started after this dashboard was installed).</p>' +
                (f"<p>Latest lines of <code>logs/run_day-{day}.log</code>:</p><pre>{esc(log)}</pre>" if log else ""))
    evs = live["events"][::-1]
    # Collapse the repetitive "book full" rejections, as the day report does.
    seen, items = set(), []
    for e in evs:
        if e["kind"] == "signal_skipped" and "open positions (max" in e["detail"]:
            if e["ticker"] in seen:
                continue
            seen.add(e["ticker"])
        k = e["kind"]
        items.append(f'<li class="ev {k}"><span class="t">{hhmm(e["time"])}</span>'
                     f'<span class="tag">{KIND_LABEL.get(k, k)}</span><b>{esc(e["ticker"])}</b> '
                     f'<span>{esc(e["detail"])}</span></li>')
    probs = live.get("problems", []) + live.get("risk_events", [])
    out = f'<ul class="feed">{"".join(items) or "<li class=muted>Nothing has happened yet.</li>"}</ul>'
    if probs:
        out += "<h4>Data problems and risk-limit events</h4><ul>" + "".join(f"<li>{esc(p)}</li>" for p in probs) + "</ul>"
    return out


def equity_svg(days: list[dict], capital: float) -> str:
    pts = [capital] + [d["equity"] for d in days]
    if len(pts) < 2:
        return '<p class="muted">The equity curve appears after the first completed paper day.</p>'
    w, h, pad = 640, 160, 24
    lo, hi = min(pts + [capital]), max(pts + [capital])
    span = (hi - lo) or 1
    xy = [(pad + i * (w - 2 * pad) / (len(pts) - 1), h - pad - (v - lo) / span * (h - 2 * pad)) for i, v in enumerate(pts)]
    base_y = h - pad - (capital - lo) / span * (h - 2 * pad)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(xy))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3"/>' for x, y in xy)
    return (f'<svg class="eq" viewBox="0 0 {w} {h}" role="img" aria-label="Paper account equity by day">'
            f'<line x1="{pad}" x2="{w - pad}" y1="{base_y:.1f}" y2="{base_y:.1f}" class="base"/>'
            f'<path d="{path}"/>{dots}<text x="{pad}" y="14">{inr(hi)}</text>'
            f'<text x="{pad}" y="{h - 4}">{inr(lo)}</text></svg>')


def history_tab(ledger: dict | None) -> str:
    capital = load_yaml("risk.yaml").get("capital", 10000)
    days = (ledger or {}).get("days", [])
    out = "<h2>Paper account over time</h2>" + equity_svg(days, capital)
    if days:
        peak = (ledger or {}).get("peak", capital)
        eq = (ledger or {}).get("equity", capital)
        out += (f'<p>Now {inr(eq)} · peak {inr(peak)} · drawdown from peak '
                f'{(eq - peak) / peak * 100:.2f}% (system pauses at −{load_yaml("risk.yaml")["max_drawdown_pause_pct"]}%)</p>')
        out += table(["Day", "Trades", "Net P&L", "Account after"],
                     [[d["date"], str(d["trades"]), f'<span class="{tone(d["net_pnl"])}">{inr(d["net_pnl"], sign=True)}</span>',
                       inr(d["equity"])] for d in days[::-1]])
    out += "<h2>Daily reports</h2>"
    reports = sorted((p for p in REPORTS_DIR.glob("20??-??-??.md")), reverse=True)
    if not reports:
        out += '<p class="muted">No completed days yet.</p>'
    for p in reports:
        d = p.stem
        out += doc(f"{date.fromisoformat(d):%a %d %b %Y}: day report", p)
        if (JOURNAL_DIR / f"{d}.md").exists():
            out += doc(f"{date.fromisoformat(d):%a %d %b %Y}: journal", JOURNAL_DIR / f"{d}.md")
    dry = sorted((REPORTS_DIR / "dryrun").glob("*.md"), reverse=True)
    if dry:
        out += "<h2>Dry-run replays</h2>" + "".join(doc(f"Replay of {p.stem}", p) for p in dry)
    return out


def health_tab(day: date) -> str:
    jobs = launchd_jobs()
    rows = [[f"<code>{j}</code>", f'<span class="{"neg" if "NOT" in s or "exit code" in s else ""}">{esc(s)}</span>']
            for j, s in jobs.items()]
    costs = load_yaml("costs.yaml")
    checks = [
        ("Claude token for background jobs (news check, journal)", token_configured(),
         "set" if token_configured() else "missing: run scripts/claude.sh setup-token, put it in .env"),
        ("Cost rates checked against Zerodha's calculator", bool(costs.get("verified_on")),
         f"verified {costs['verified_on']}" if costs.get("verified_on") else "not yet verified (before Phase 1)"),
    ]
    out = "<h2>Scheduled jobs</h2>" + table(["Job", "State"], rows)
    out += "<h2>Setup checks</h2><ul class='checks'>" + "".join(
        f'<li class="{"ok" if ok else "bad"}">{"✓" if ok else "!"} {esc(k)}: {esc(v)}</li>' for k, ok, v in checks) + "</ul>"
    out += "<h2>Recent log lines</h2>"
    for name in [f"run_day-{day}.log", f"prep-{day}.log", f"journal-{day}.log"]:
        t = tail(LOGS_DIR / name, 10)
        if t:
            out += f"<h4><code>logs/{name}</code></h4><pre>{esc(t)}</pre>"
    return out


# ---- page --------------------------------------------------------------------------------
def build(now: datetime | None = None) -> str:
    now = now or datetime.now(IST)
    today = now.date()
    trading = is_trading_day(today)
    # Focus on today on a market day; otherwise on the next market day (its prep note).
    day = today if trading else next_trading_day(today)
    plan = _json(CACHE_DIR / f"watchlist-{day}.json") or {}
    live = _json(CACHE_DIR / f"live-{day}.json")
    ledger = _json(CACHE_DIR / "paper_ledger.json")

    if trading:
        banner = f"Market day · {today:%A %d %b %Y}"
    else:
        banner = f"Market closed today ({why_closed(today)}) · next market day {day:%A %d %b}"
    nxt = next_trading_day(today)
    tomorrow_prep = (REPORTS_DIR / f"{nxt}-prep.md") if trading else None

    steps = timeline(day, now, live)
    tl = "".join(f'<li class="st {s}"><span class="dot"></span><div><div class="when">{esc(w)}</div>'
                 f'<div class="what">{esc(what)}</div><div class="det">{esc(d)}</div></div></li>'
                 for w, what, s, d in steps)

    end_open = (REPORTS_DIR / f"{day}.md").exists()
    today_tab = f"""
      {kpis(ledger, live, day, plan)}
      <ol class="timeline">{tl}</ol>
      <section><h2>1 · Start of day</h2>
        <p class="muted">Watchlist and exclusions come from the evening prep. News check: {esc(plan.get('web_check', 'n/a'))}</p>
        {''.join(f'<p class="warn">Excluded: <b>{esc(k)}</b>: {esc(v["reason"])}</p>' for k, v in plan.get('exclusions', {}).items())}
        {doc("Evening prep note", REPORTS_DIR / f"{day}-prep.md")}
        {doc("Pre-market note (08:45)", REPORTS_DIR / f"{day}-premarket.md", open_=not live)}
      </section>
      <section><h2>2 · During the day</h2>
        <h3>Watchlist {'' if not live else f'<span class="muted small">updated {hhmm(live["updated_at"])}, last candle {hhmm(live.get("last_candle"))}</span>'}</h3>
        {watchlist_section(plan, live)}
        <h3>Open positions</h3>{positions_section(live)}
        <h3>Closed trades</h3>{trades_section(live)}
        <h3>Activity</h3>{feed_section(live, day)}
      </section>
      <section><h2>3 · End of day</h2>
        {doc("Day report (15:35)", REPORTS_DIR / f"{day}.md", open_=end_open, missing="Written at 15:35.")}
        {doc("Journal (15:40)", JOURNAL_DIR / f"{day}.md", open_=end_open, missing="Written at 15:40.")}
        {doc(f"Prep note for {nxt:%a %d %b} (next market day)", tomorrow_prep, missing="Written at 20:00.") if tomorrow_prep else ''}
      </section>"""

    backtest = _read(REPORTS_DIR / "backtest" / "summary.md")
    tabs = [("today", "Today", today_tab), ("history", "History", history_tab(ledger)),
            ("backtest", "Backtest", f'<div class="md">{md(backtest)}</div>' if backtest else "<p>No backtest yet.</p>"),
            ("health", "System health", health_tab(day))]
    nav = "".join(f'<a href="#{k}" data-tab="{k}">{label}</a>' for k, label, _ in tabs)
    panes = "".join(f'<div class="pane" id="pane-{k}">{body}</div>' for k, _, body in tabs)
    return PAGE.format(banner=esc(banner), updated=f"{now:%d %b %Y, %H:%M:%S}", nav=nav, panes=panes, css=CSS, js=JS)


def write(now: datetime | None = None) -> Path:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(build(now))
    tmp.replace(OUT)
    return OUT


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16191d;--muted:#667085;--line:#e4e7ec;--accent:#3355ff;
--pos:#0a7d45;--neg:#c4320a;--warn:#b54708;--warnbg:#fffaeb;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e7e9ee;--muted:#98a2b3;
--line:#2a2f3a;--accent:#7d93ff;--pos:#47cd89;--neg:#f97066;--warn:#fdb022;--warnbg:#2a2110;--chip:#222733}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--card);border-bottom:1px solid var(--line)}
.hd{max-width:1100px;margin:0 auto;padding:12px 16px;display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center}
.brand{font-weight:700;font-size:17px}.brand small{font-weight:500;color:var(--muted);margin-left:6px}
.banner{color:var(--muted)}.upd{margin-left:auto;color:var(--muted);font-size:13px}
nav{max-width:1100px;margin:0 auto;padding:0 16px;display:flex;gap:4px;overflow-x:auto}
nav a{padding:8px 12px;color:var(--muted);text-decoration:none;border-bottom:2px solid transparent;white-space:nowrap}
nav a.on{color:var(--ink);border-color:var(--accent);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:16px}.pane{display:none}.pane.on{display:block}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 16px 16px;margin:16px 0}
h2{font-size:17px;margin:16px 0 8px}h3{font-size:15px;margin:18px 0 6px}h4{margin:12px 0 4px;font-size:14px}
.muted{color:var(--muted)}.small{font-size:12px;font-weight:400}.pos{color:var(--pos)}.neg{color:var(--neg)}
.warn{color:var(--warn)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .k{color:var(--muted);font-size:13px}.kpi .v{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .n{color:var(--muted);font-size:12px;min-height:16px}
.timeline{list-style:none;padding:0;margin:16px 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}
.st{display:flex;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.st .dot{flex:none;width:12px;height:12px;border-radius:50%;margin-top:5px;background:var(--line)}
.st.done .dot{background:var(--pos)}.st.running .dot{background:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 25%,transparent)}
.st.missed .dot{background:var(--neg)}.st.warn .dot{background:var(--warn)}
.st .when{font-size:12px;color:var(--muted)}.st .what{font-weight:600}.st .det{font-size:12px;color:var(--muted)}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}
details.doc{border:1px solid var(--line);border-radius:10px;margin:8px 0;background:var(--card)}
details.doc summary{cursor:pointer;padding:10px 12px;font-weight:600;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
details.doc .path{margin-left:auto;font-weight:400;font-size:12px;color:var(--muted)}
.md{padding:0 14px 12px;overflow-x:auto}.md h1{font-size:18px}.md h2{font-size:16px}
.pill{font-size:11px;padding:1px 8px;border-radius:99px;background:var(--chip);color:var(--muted);font-weight:500}
.pill.ok{color:var(--pos)}
pre{background:var(--chip);padding:10px;border-radius:8px;overflow-x:auto;font-size:12px;white-space:pre-wrap}
code{font-size:13px}
.feed{list-style:none;padding:0;margin:0}.feed li{padding:6px 0;border-bottom:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap;font-size:14px}
.feed .t{color:var(--muted);font-variant-numeric:tabular-nums}.feed .tag{font-size:11px;padding:1px 6px;border-radius:6px;background:var(--chip);align-self:center}
.feed .signal_taken .tag{background:var(--pos);color:#fff}.feed .exit .tag{background:var(--accent);color:#fff}
.feed .skip_stock .tag,.feed .data .tag{background:var(--warnbg);color:var(--warn)}
.checks{list-style:none;padding:0}.checks li{padding:4px 0}.checks .ok{color:var(--pos)}.checks .bad{color:var(--warn)}
svg.eq{width:100%;max-width:700px;height:auto}svg.eq path{fill:none;stroke:var(--accent);stroke-width:2}
svg.eq circle{fill:var(--accent)}svg.eq .base{stroke:var(--line);stroke-dasharray:4 4}svg.eq text{fill:var(--muted);font-size:11px}
footer{max-width:1100px;margin:0 auto;padding:8px 16px 32px;color:var(--muted);font-size:12px}
"""

JS = """
function show(k){document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.id==='pane-'+k));
document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.tab===k));}
function cur(){return (location.hash||'#today').slice(1)}
window.addEventListener('hashchange',()=>show(cur()));show(cur());
// Remember which reports are expanded across the automatic reloads.
const KEY='artha-open';let open={};try{open=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
document.querySelectorAll('details.doc').forEach(d=>{const id=d.querySelector('.path').textContent;
if(id in open)d.open=open[id];
// Attach after load: 'toggle' also fires for reports open by default, which isn't a user choice.
setTimeout(()=>d.addEventListener('toggle',()=>{open[id]=d.open;try{localStorage.setItem(KEY,JSON.stringify(open))}catch(e){}}),500)});
const y=sessionStorage.getItem('artha-y');if(y)window.scrollTo(0,+y);
setInterval(()=>{sessionStorage.setItem('artha-y',window.scrollY);location.reload()},60000);
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Artha dashboard</title>
<style>{css}</style></head><body>
<header><div class="hd"><div class="brand">Artha<small>Phase 0 · paper trading only</small></div>
<div class="banner">{banner}</div><div class="upd">Updated {updated} IST · refreshes every minute</div></div>
<nav>{nav}</nav></header>
<main>{panes}</main>
<footer>Built from local files by scripts/dashboard.py. No real orders exist in Phase 0. Data: yfinance (unofficial, may be delayed).</footer>
<script>{js}</script></body></html>"""
