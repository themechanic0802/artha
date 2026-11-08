#!/bin/bash
# Runs the Claude Code CLI that Artha uses (wherever it is installed).
# One-time setup:  ~/artha/scripts/claude.sh setup-token
exec "$("$(dirname "$0")/../.venv/bin/python" -m artha.claude_cli)" "$@"
