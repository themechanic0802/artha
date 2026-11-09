#!/bin/bash
# Installs (or re-installs) Artha's three macOS launchd jobs for the current user.
#   20:00 every day  evening_prep.sh  (does nothing unless tomorrow is a market day)
#   08:40 every day  run_day.py       (exits at once on weekends/holidays)
#   15:40 every day  journal.sh       (exits at once on weekends/holidays)
#   every minute     dashboard.py     (rebuilds reports/dashboard.html; read-only)
# If the Mac is asleep at those times, launchd runs the job when it wakes
# (run_day then catches up). If it is shut down, the job is missed.
# Remove with:  ~/artha/scripts/install_schedule.sh --uninstall
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
mkdir -p "$AGENTS" "$ROOT/logs"

write_plist() {  # label "HH:MM" or "every:SECONDS", then program-args...
  local label="$1" when="$2"; shift 2
  local sched
  if [ "${when%%:*}" = "every" ]; then
    sched="<key>StartInterval</key><integer>${when#every:}</integer>"
  else
    sched="<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$((10#${when%%:*}))</integer><key>Minute</key><integer>$((10#${when#*:}))</integer></dict>"
  fi
  local args=""
  for a in "$@"; do args="$args<string>$a</string>"; done
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  $sched
  <key>StandardOutPath</key><string>$ROOT/logs/launchd-$label.out</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd-$label.out</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>TZ</key><string>Asia/Kolkata</string>
  </dict>
</dict></plist>
PLIST
}

LABELS="com.artha.eveningprep com.artha.runday com.artha.journal com.artha.dashboard"
# Never kill a trading day in progress: reloading com.artha.runday would stop run_day.py.
RUNNING=0
if [ -f "$ROOT/data_cache/run_day.lock" ] && kill -0 "$(cat "$ROOT/data_cache/run_day.lock")" 2>/dev/null; then
  RUNNING=1
fi
if [ "${1:-}" = "--uninstall" ]; then
  if [ $RUNNING = 1 ]; then echo "run_day.py is running; uninstall after 15:35."; exit 1; fi
  for label in $LABELS; do launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true; done
  rm -f "$AGENTS"/com.artha.{eveningprep,runday,journal,dashboard}.plist
  echo "Artha schedule removed."; exit 0
fi

install_job() {  # label, then write_plist args
  local label="$1"
  if [ "$label" = com.artha.runday ] && [ $RUNNING = 1 ]; then
    echo "Skipped $label: today's run is in progress (its schedule is unchanged)."; return
  fi
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  write_plist "$@"
  launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$label.plist"
}

install_job com.artha.eveningprep 20:00  /bin/bash "$ROOT/scripts/evening_prep.sh"
# caffeinate -i: no idle sleep; -s: no system sleep while on mains power; -m: keep disk awake.
# It lasts exactly as long as run_day.py (until ~15:35). Closing the lid still sleeps the Mac.
install_job com.artha.runday 08:40  /usr/bin/caffeinate -ims "$ROOT/.venv/bin/python" "$ROOT/scripts/run_day.py"
install_job com.artha.journal 15:40  /bin/bash "$ROOT/scripts/journal.sh"
install_job com.artha.dashboard every:60  "$ROOT/.venv/bin/python" "$ROOT/scripts/dashboard.py"
echo "Installed. Check with:  launchctl list | grep artha"
