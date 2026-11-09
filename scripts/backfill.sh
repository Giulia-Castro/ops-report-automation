#!/usr/bin/env bash
# Runs the n8n "Daily ops report" workflow for a range of days, oldest first, so each
# day uses the previous ones as its baseline. Usage:
#   ./scripts/backfill.sh 2026-10-30 2026-11-07
set -euo pipefail

START="${1:-2026-10-30}"
END="${2:-2026-11-07}"
N8N_URL="${N8N_URL:-http://localhost:5678}"
KEY="${REPORT_ENGINE_API_KEY:-$(grep '^REPORT_ENGINE_API_KEY=' .env | cut -d= -f2-)}"

day="$START"
while [[ "$day" < "$END" || "$day" == "$END" ]]; do
  result=$(curl -s -m 300 -X POST "$N8N_URL/webhook/daily-report" \
    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    -d "{\"reportDate\":\"$day\"}")
  echo "$day $(echo "$result" | python -c 'import json,sys; r=json.load(sys.stdin); print(r["status"], r["health"], r["summarySource"], r["pipelineSeconds"], "s") if "status" in r else print("FAILED:", r.get("message"), "(see the alert e-mail)")' 2>/dev/null || echo "$result")"
  day=$(python -c "import datetime,sys; print(datetime.date.fromisoformat(sys.argv[1]) + datetime.timedelta(days=1))" "$day")
done
