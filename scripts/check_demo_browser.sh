#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root. This opt-in check makes two real DeepSeek requests.
if [[ "${1:-}" != "--execute" ]]; then
  echo 'Pass --execute to authorize the live browser check.'
  exit 2
fi
mkdir -p data/artifacts/demo-browser
PYDANTIC_AI_NO_BANNER=1 uv run bidops-demo --port 8765 >data/artifacts/demo-browser/server.log 2>&1 &
demo_server_pid=$!
trap 'kill "$demo_server_pid" 2>/dev/null || true' EXIT
uv run python - <<'PY'
import time
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(50):
    try:
        with opener.open('http://127.0.0.1:8765/health', timeout=1) as response:
            if response.status == 200:
                break
    except OSError:
        time.sleep(0.2)
else:
    raise SystemExit('Demo server did not become ready')
PY
node frontend/scripts/demo-live.mjs --execute
