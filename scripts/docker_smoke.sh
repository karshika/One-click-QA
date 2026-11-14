#!/usr/bin/env bash
# End-to-end check of the Docker setup: `docker compose up`, add the demo app as a project,
# press "Run QA" through the API, wait for completion, fetch the report.
# Usage: ./scripts/docker_smoke.sh   (requires Docker with the compose plugin, curl, python3)
set -euo pipefail
cd "$(dirname "$0")/.."
API=http://localhost:8000
cleanup() { docker compose logs --tail=80 backend || true; docker compose down -v || true; }
trap cleanup EXIT

docker compose up -d --build

echo "waiting for backend..."
for i in $(seq 1 120); do curl -fsS "$API/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "$API/health"; echo
for i in $(seq 1 60); do curl -fsS http://localhost:3000/ >/dev/null 2>&1 && break; sleep 2; done
curl -fsS -o /dev/null -w "dashboard HTTP %{http_code}\n" http://localhost:3000/

PID=$(curl -fsS -X POST "$API/projects" -H 'Content-Type: application/json' -d '{
  "name":"ShopLite (docker)","base_url":"http://demo-app:8001","openapi_url":"/openapi.json",
  "username":"demo@example.com","password":"Demo-Pass-2024","consent":true}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
RID=$(curl -fsS -X POST "$API/projects/$PID/runs" -H 'Content-Type: application/json' -d '{"perf_runs":1}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "project $PID run $RID started"

for i in $(seq 1 180); do
  STATUS=$(curl -fsS "$API/runs/$RID" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = completed ] || [ "$STATUS" = failed ] || [ "$STATUS" = cancelled ] && break
  sleep 5
done
echo "run status: $STATUS"
[ "$STATUS" = completed ]

curl -fsS "$API/runs/$RID" | python3 -c '
import sys, json
r = json.load(sys.stdin)
print("score:", r["score"], "llm_calls:", r["llm_calls"], "cache_hits:", r["cache_hits"])
for s in r["stages"]:
    print(f"  {s[\"stage\"]:9} {s[\"status\"]:9} {s.get(\"error\") or \"\"}")
bad = [s for s in r["stages"] if s["status"] == "failed"]
sys.exit(1 if bad else 0)'
curl -fsS "$API/runs/$RID/findings" | python3 -c '
import sys, json
f = json.load(sys.stdin)
print(len(f), "findings:")
for x in f: print("  -", x["severity"], x["category"], x["title"])
assert len(f) >= 8, "expected the planted bugs to be found"'
curl -fsS "$API/runs/$RID/report?format=md" | head -n 12
curl -fsS -o /tmp/report.pdf "$API/runs/$RID/report?format=pdf" && head -c 4 /tmp/report.pdf && echo
echo "DOCKER SMOKE TEST PASSED"
