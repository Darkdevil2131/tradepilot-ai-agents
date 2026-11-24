#!/usr/bin/env bash
# Run from the repository root. All Docker resources belong to this invocation.
set -euo pipefail
run_id="tradepilot-check-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
volume="$run_id-data"
work=$(mktemp -d)
cleanup() {
  docker rm -f "$run_id" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker image rm "$run_id" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
docker build -t "$run_id" .
docker run --rm -v "$PWD/tests:/app/tests:ro" -v "$PWD/examples:/app/examples:ro" \
  "$run_id" python -m pytest -q -p no:cacheprovider /app/tests
docker volume create "$volume" >/dev/null
# Reproduce a provider-mounted volume owned by root at runtime.
docker run --rm --user 0 --entrypoint sh -v "$volume:/app/data" "$run_id" \
  -c 'chown 0:0 /app/data && chmod 755 /app/data'
docker run --rm -v "$volume:/app/data" "$run_id" sh -c 'test "$(id -u)" = 10001 && test -w /app/data'
export TRADEPILOT_API_KEY
TRADEPILOT_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
start_server() {
  docker run -d --name "$run_id" -e TRADEPILOT_API_KEY -e GEMINI_ENABLED=false \
    -e PORT=8765 -v "$volume:/app/data" -p 127.0.0.1::8765 "$run_id" >/dev/null
  base="http://$(docker port "$run_id" 8765/tcp)/api/v1"
  for ((i=0; i<60; i++)); do
    if curl -fsS --max-time 2 "$base/ready" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  docker logs "$run_id"
  return 1
}
start_server
auth=(-H "Authorization: Bearer $TRADEPILOT_API_KEY")
curl -fsS "${auth[@]}" -H 'Content-Type: application/json' \
  --data-binary @examples/impact_comparison.json "$base/shipments/analyze" > "$work/result.json"
shipment=$(python3 - "$work/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
assert len(result["agents"]) == 6
assert all(agent["status"] == "completed" for agent in result["agents"].values())
plan = result["agents"]["market_procurement"]["data"]["sourced_plan"]
assert (plan["option"], plan["total_impact_inr"]) == ("air/ocean", 78000)
print(result["shipment_id"])
PY
)
docker stop "$run_id" >/dev/null
docker rm "$run_id" >/dev/null
start_server
test "$(curl -sS -o /dev/null -w '%{http_code}' "$base/shipments/$shipment")" = 401
curl -fsS "${auth[@]}" "$base/shipments/$shipment" > "$work/recovered.json"
curl -fsS "${auth[@]}" "$base/shipments/$shipment/history" > "$work/history.json"
python3 - "$work" <<'PY'
from pathlib import Path
import json, sys
folder = Path(sys.argv[1])
expected, recovered, history = [json.loads((folder / name).read_text())
                               for name in ("result.json", "recovered.json", "history.json")]
assert recovered == expected
assert len(history["revisions"]) == 1 and history["revisions"][0]["analysis"] == expected
print("Container replacement recovered the six-agent result and its original revision.")
PY
