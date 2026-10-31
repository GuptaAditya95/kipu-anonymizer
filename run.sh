#!/usr/bin/env bash
# Starts the anonymization service (required deliverable).
# Local:  ./run.sh            -> http://localhost:9000/v1/health
# Hosts usually inject $PORT.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; source .env; set +a; fi

python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-9000}"
