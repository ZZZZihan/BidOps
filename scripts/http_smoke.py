"""Start real loopback servers and probe them within one execution environment."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/artifacts/http-smoke"
OUTPUT.mkdir(parents=True, exist_ok=True)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(opener, url):
    try:
        with opener.open(url, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def ready(opener, url, process):
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("server_exited_before_ready")
        try:
            if request(opener, url)[0] == 200:
                return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("server_did_not_become_ready")


api_port, ui_port = port(), port()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Loopback only.
results = []
with (
    (OUTPUT / "backend.log").open("w") as backend_log,
    (OUTPUT / "frontend.log").open("w") as ui_log,
):
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bidops.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        ],
        cwd=ROOT,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "BIDOPS_NETWORK_ENABLED": "false"},
    )
    ui = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(ui_port), "--strictPort"],
        cwd=ROOT / "frontend",
        stdout=ui_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "BIDOPS_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}"},
    )
    try:
        ready(opener, f"http://127.0.0.1:{api_port}/health", api)
        ready(opener, f"http://127.0.0.1:{ui_port}/", ui)
        for path, expected in [
            ("/health", 200),
            ("/sources", 200),
            ("/notices", 200),
            ("/notices/nonexistent", 404),
        ]:
            status, body = request(opener, f"http://127.0.0.1:{api_port}{path}")
            assert status == expected, (path, status)
            results.append(
                {
                    "path": path,
                    "status": status,
                    "expected_status": expected,
                    "body": json.loads(body),
                }
            )
        status, body = request(opener, f"http://127.0.0.1:{ui_port}/")
        assert status == 200 and 'id="root"' in body
        results.append({"path": "frontend /", "status": status, "html_root_present": True})
        status, body = request(opener, f"http://127.0.0.1:{ui_port}/api/health")
        assert status == 200 and json.loads(body)["status"] == "ok"
        results.append(
            {"path": "frontend /api/health", "status": status, "proxied_backend_ok": True}
        )
        (OUTPUT / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        api.terminate()
        # npm owns a child Vite process; terminate the dedicated process group.
        os.killpg(ui.pid, 15)
        api.wait(timeout=10)
        ui.wait(timeout=10)
