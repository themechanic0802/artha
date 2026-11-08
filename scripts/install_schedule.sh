#!/bin/bash
# Installs (or re-installs) Artha's three macOS launchd jobs for the current user.
#   20:00 every day  evening_prep.sh  (does nothing unless tomorrow is a market day)
#   08:40 every day  run_day.py       (exits at once on weekends/holidays)
#   15:40 every day  journal.sh       (exits at once on weekends/holidays)
# If the Mac is asleep at those times, launchd runs the job when it wakes
# (run_day then catches up). If it is shut down, the job is missed.
# Remove with:  ~/artha/scripts/install_schedule.sh --uninstall
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
mkdir -p "$AGENTS" "$ROOT/logs"

write_plist() {  # label hour minute program-args...
  local label="$1" hour="$2" minute="$3"; shift 3
  local args=""
  for a in "$@"; do args="$args<string>$a</string>"; done
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd-$label.out</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd-$label.out</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>TZ</key><string>Asia/Kolkata</string>
  </dict>
</dict></plist>
PLIST
}

for label in com.artha.eveningprep com.artha.runday com.artha.journal; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
done
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$AGENTS"/com.artha.{eveningprep,runday,journal}.plist
  echo "Artha schedule removed."; exit 0
fi

write_plist com.artha.eveningprep 20 0  /bin/bash "$ROOT/scripts/evening_prep.sh"
# caffeinate -i: no idle sleep; -s: no system sleep while on mains power; -m: keep disk awake.
# It lasts exactly as long as run_day.py (until ~15:35). Closing the lid still sleeps the Mac.
write_plist com.artha.runday      8 40  /usr/bin/caffeinate -ims "$ROOT/.venv/bin/python" "$ROOT/scripts/run_day.py"
write_plist com.artha.journal    15 40  /bin/bash "$ROOT/scripts/journal.sh"

for label in com.artha.eveningprep com.artha.runday com.artha.journal; do
  launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$label.plist"
done
echo "Installed. Check with:  launchctl list | grep artha"
