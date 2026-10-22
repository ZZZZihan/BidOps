"""Reproducible offline pilot: six URL references, zero manufactured notices."""

import csv
import json
from pathlib import Path

from bidops.config import Settings
from bidops.fetcher import Fetcher
from bidops.storage import Store

store = Store(Settings(network_enabled=False))
with Path("data/seeds/seed_urls.csv").open() as stream:
    seeds = list(csv.DictReader(stream))
for seed in seeds:
    store.register_url(seed["source_url"])
fetcher = Fetcher(store)
try:
    results = [fetcher.fetch(seed["source_url"], execute=True) for seed in seeds]
finally:
    fetcher.close()
export = store.export(Path("data/artifacts/p0-export"))
report = {
    "seed_urls": len(seeds),
    "captured": sum(r["state"] == "captured" for r in results),
    "duplicates": sum(r["state"] == "duplicate" for r in results),
    "failed_requests": sum(r["state"] == "failed" for r in results),
    "blocked_before_request": sum(r["state"] == "blocked" for r in results),
    "results": results,
    "manifest": export,
}
path = Path("data/artifacts/p0-pilot.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(path.read_text())
store.db.engine.dispose()
