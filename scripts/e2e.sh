#!/usr/bin/env bash
# End-to-end check on a fresh stack (used by CI): n8n webhook -> report engine -> SMTP.
# Runs two good days and one day without logs, then checks e-mails and metrics.
set -euo pipefail

KEY="${REPORT_ENGINE_API_KEY:-$(grep '^REPORT_ENGINE_API_KEY=' .env | cut -d= -f2-)}"
export REPORT_ENGINE_API_KEY="$KEY"

echo "Waiting for the n8n webhook to be published..."
code=""
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:5678/webhook/daily-report || true)
  [[ "$code" == "403" ]] && break
  sleep 5
done
[[ "$code" == "403" ]] || { echo "n8n webhook not ready (last status: $code)"; exit 1; }

bash scripts/backfill.sh 2026-11-03 2026-11-04
bash scripts/backfill.sh 2026-10-01 2026-10-01 # no logs for this day: must alert on-call
sleep 10

MAILPIT_JSON=$(curl -s http://localhost:8025/api/v1/messages)
METRICS_JSON=$(curl -s http://localhost:8000/v1/metrics/summary -H "X-API-Key: $KEY")
export MAILPIT_JSON METRICS_JSON

python - <<'EOF'
import json
import os

mail = json.loads(os.environ["MAILPIT_JSON"])
metrics = json.loads(os.environ["METRICS_JSON"])
subjects = [m["Subject"] for m in mail["messages"]]
print("e-mails:", *subjects, sep="\n  ")
print("metrics:", metrics)

assert metrics["reportsDelivered"] == 2, metrics
assert metrics["pipelineFailures"] == 1, metrics
assert any("2026-11-04" in s and s.startswith("[CR") for s in subjects), subjects
assert any(s.startswith("[FALHA]") or s.startswith("[FAILURE]") for s in subjects), subjects
print("E2E OK")
EOF
