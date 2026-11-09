#!/usr/bin/env python
"""Rebuild reports/dashboard.html (launchd runs this every minute).

Usage:
  .venv/bin/python scripts/dashboard.py          # rebuild
  .venv/bin/python scripts/dashboard.py --open   # rebuild and open it in your browser
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from artha import dashboard


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    a = ap.parse_args()
    path = dashboard.write()
    if a.open:
        subprocess.run(["open", str(path)], check=False)
    else:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
