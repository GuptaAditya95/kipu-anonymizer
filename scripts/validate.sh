#!/usr/bin/env bash
# Runs the official validator (Node 20+) on the sample ideal output and on our output.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== sample ideal output"; node scripts/validar.mjs data/sample/expected_output --public data/sample
echo "== our output";          node scripts/validar.mjs data/output --public data/raw
