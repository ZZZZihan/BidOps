"""No provider or procurement network requests. Used against the CI container only."""

import time
import urllib.error
import urllib.request


def request(path):
    req = urllib.request.Request("http://127.0.0.1:8765" + path, headers={"Host": "bidops.test"})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


for _attempt in range(20):
    try:
        if request("/api/health")[0] == 200:
            break
    except OSError:
        pass
    time.sleep(1)
else:
    raise SystemExit("Container failed to start")

assert request("/api/notices")[0] == 401
assert request("/api/sessions")[0] == 401
assert request("/api/metrics")[0] == 401
assert request("/api/ready")[0] == 503  # No corpus or provider in the packaging test.
assert request("/")[0] == 200
print("Container smoke passed: non-anonymous API, built UI, truthful not-ready state")
