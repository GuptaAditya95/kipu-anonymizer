#!/usr/bin/env bash
# Starts the anonymization service (macOS / Linux). Windows: run.bat
# Settings: .env in this folder (copy .env.example), or environment variables (which take precedence;
# the app loads .env itself without overriding them).
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${PORT:-}" ] && [ -f .env ]; then
  PORT="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2 | tr -d '[:space:]"' || true)"
fi
python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-9000}"
