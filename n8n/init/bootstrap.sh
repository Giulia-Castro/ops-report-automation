#!/bin/sh
# One-shot: import credentials (from env) and workflows (from Git), then publish both
# workflows (n8n 2.x only runs an error workflow that is published). Safe to re-run.
set -eu

umask 077
CREDS_FILE="$(mktemp)"
trap 'rm -f "$CREDS_FILE"' EXIT

node /bootstrap/render-credentials.js > "$CREDS_FILE"
n8n import:credentials --input="$CREDS_FILE"
n8n import:workflow --separate --input=/workflows
n8n publish:workflow --id=OpsDailyReport01
n8n publish:workflow --id=OpsErrorAlert001

echo "n8n bootstrap done: credentials and workflows imported, both workflows published."
