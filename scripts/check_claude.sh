#!/bin/bash
# Checks that background (headless) Claude works with the token in .env. Never prints the token.
cd "$(dirname "$0")/.." && .venv/bin/python -c "
from artha.claude_cli import run_claude
ok, out = run_claude('Reply with exactly: OK', timeout=120)
print('Headless Claude: WORKING' if ok else 'Headless Claude: NOT WORKING -> ' + out)"
