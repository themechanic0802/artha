"""macOS desktop notifications (osascript is built in; nothing to install)."""
from __future__ import annotations

import subprocess


def notify(title: str, message: str) -> None:
    safe = lambda s: s.replace("\\", "").replace('"', "'")
    try:
        subprocess.run(["osascript", "-e", f'display notification "{safe(message)}" with title "{safe(title)}"'],
                       timeout=10, capture_output=True)
    except Exception:
        pass  # a missed notification must never break a run
