"""Find and call the Claude Code CLI in headless mode (`claude -p`).

Used only for research, prep notes and journaling, never for trade decisions.
The token (if any) is read from .env and passed to the child process only;
it is never printed or logged.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from .settings import ROOT


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for p in (home / ".local/bin/claude", home / ".claude/local/claude", Path("/opt/homebrew/bin/claude")):
        if p.exists():
            return str(p)
    # The copy bundled with the Claude desktop app; its folder changes with each app update,
    # so pick the newest version.
    pattern = str(home / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")

    def version_key(path: str):
        v = Path(path).parts[-6]
        return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))
    hits = sorted(glob.glob(pattern), key=version_key)
    return hits[-1] if hits else None


def child_env() -> dict:
    env = os.environ.copy()
    token = dotenv_values(ROOT / ".env").get("CLAUDE_CODE_OAUTH_TOKEN") if (ROOT / ".env").exists() else None
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def run_claude(prompt: str, allowed_tools: list[str] | None = None, timeout: int = 900) -> tuple[bool, str]:
    """Returns (ok, output_or_error_message)."""
    exe = find_claude()
    if not exe:
        return False, "Claude Code CLI not found"
    cmd = [exe, "-p", prompt, "--output-format", "text"]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT,
                           env=child_env(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"Claude CLI timed out after {timeout}s"
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        msg = (out or r.stderr or "").strip().splitlines()
        return False, f"Claude CLI failed (exit {r.returncode}): {msg[0][:200] if msg else 'no output'}"
    if "Not logged in" in out[:200]:
        return False, "Claude CLI is not logged in (see README: one-time setup)"
    return True, out


if __name__ == "__main__":
    # `python -m artha.claude_cli` prints the binary path (used by journal.sh).
    p = find_claude()
    print(p or "")
    sys.exit(0 if p else 1)
