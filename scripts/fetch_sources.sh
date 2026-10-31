#!/usr/bin/env bash
# Downloads the sample + public tests + record_index for local development.
# Full sources are downloaded by the pipeline itself (python -m pipeline.run).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; source .env; set +a; fi
: "${SOURCES_URL:?set SOURCES_URL}" "${SOURCES_TOKEN:?set SOURCES_TOKEN}"
H="Authorization: Bearer $SOURCES_TOKEN"
mkdir -p data/public
curl -sSf -H "$H" "$SOURCES_URL/sample.zip" -o data/sample.zip
rm -rf data/sample && unzip -oq data/sample.zip -d data/sample
curl -sSf -H "$H" "$SOURCES_URL/tests/public" -o data/public/tests_public.json
curl -sSf -H "$H" "$SOURCES_URL/record_index" -o data/public/record_index.json
echo "OK: data/sample, data/public"
