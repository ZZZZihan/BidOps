"""Run and retain actual command outputs; no aspirational PASS statuses."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/artifacts/verification"
OUTPUT.mkdir(parents=True, exist_ok=True)
commands = [
    ["uv", "sync", "--frozen"],
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "ruff", "format", "--check", "."],
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "bidops", "migrate"],
    ["uv", "run", "bidops", "source", "inspect"],
    ["uv", "run", "bidops", "import-urls", "--csv", "data/seeds/seed_urls.csv"],
    ["uv", "run", "bidops", "parse"],
    ["uv", "run", "python", "scripts/p0_pilot.py"],
    ["npm", "ci", "--prefix", "frontend"],
    ["npm", "run", "build", "--prefix", "frontend"],
    ["uv", "run", "python", "scripts/http_smoke.py"],
]
results = []
for index, command in enumerate(commands):
    started = datetime.now(UTC).isoformat()
    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=300,
        env={**os.environ, "BIDOPS_NETWORK_ENABLED": "false"},
    )
    log_name = f"{index:02d}.txt"
    (OUTPUT / log_name).write_text(process.stdout + process.stderr)
    result = {
        "command": command,
        "exit_code": process.returncode,
        "started_at": started,
        "log": log_name,
    }
    results.append(result)
    print(json.dumps(result), flush=True)
(OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
sys.exit(int(any(result["exit_code"] != 0 for result in results)))
