import argparse
import csv
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from bidops.config import Settings
from bidops.fetcher import Fetcher
from bidops.sources import canonical_url
from bidops.storage import Store, stable_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="BidOps P0: offline by default")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    source = sub.add_parser("source").add_subparsers(dest="source_command", required=True)
    source.add_parser("inspect")
    urls = sub.add_parser("import-urls", help="Register references only; never fetch")
    group = urls.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv", type=Path)
    group.add_argument("--url", action="append")
    sub.add_parser("parse")
    manual = sub.add_parser("import-file")
    manual.add_argument("--url", required=True)
    manual.add_argument("--file", type=Path, required=True)
    manual.add_argument("--attest-lawful", action="store_true")
    manual.add_argument("--content-type", choices=["text/html", "text/plain"], default="text/plain")
    manual.add_argument("--encoding", choices=["utf-8", "gb18030"], default="utf-8")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--url", action="append", required=True)
    fetch.add_argument(
        "--execute",
        action="store_true",
        help="Also requires enabled config and reviewed source permission",
    )
    export = sub.add_parser("export-manifest")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument(
        "--include-raw", action="store_true", help="Private local bundle only; never commit"
    )
    restore = sub.add_parser("import-bundle")
    restore.add_argument("--input", type=Path, required=True)
    sub.add_parser("stats")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        store = Store(Settings())
        result = {}
        exit_code = 0
        if args.command == "migrate":
            result = {"schema": "001_initial.sql", "status": "ok"}
        elif args.command == "source":
            result = {
                "network_enabled": store.settings.network_enabled,
                "sources": [s.model_dump() for s in store.sources],
            }
        elif args.command == "import-urls":
            urls = args.url
            if args.csv:
                with args.csv.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    if "source_url" not in (reader.fieldnames or []):
                        raise ValueError("csv_requires_source_url_column")
                    urls = [row["source_url"] for row in reader]
            if not 0 < len(urls) <= 50:
                raise ValueError("url_count_must_be_1_to_50")
            # Validate the entire finite input before registration.
            urls = [canonical_url(url, store.sources) for url in urls]
            for url in urls:
                store.register_url(url)
            result = {"registered_unique_urls": len(set(urls)), "snapshots_captured": 0}
            store.db.audit("import_urls", "references_only", count=len(set(urls)))
        elif args.command == "import-file":
            if args.file.stat().st_size > store.settings.max_bytes:
                raise ValueError("input_too_large")
            snapshot, new = store.ingest(
                args.url,
                args.file.read_bytes(),
                content_type=args.content_type,
                encoding=args.encoding,
                manual_attestation=args.attest_lawful,
            )
            result = {"snapshot": snapshot.model_dump(mode="json"), "new": new}
        elif args.command == "fetch":
            if len(args.url) > 50:
                raise ValueError("url_count_exceeds_50")
            urls = list(dict.fromkeys(canonical_url(url, store.sources) for url in args.url))
            fetcher = Fetcher(store)
            try:
                items = [fetcher.fetch(url, execute=args.execute) for url in urls]
            finally:
                fetcher.close()
            result = {"items": items}
            exit_code = 2 if any(x["state"] in {"failed", "blocked"} for x in items) else 0
        elif args.command == "parse":
            result = {"parsed": store.reparse()}
        elif args.command == "export-manifest":
            result = store.export(args.output, args.include_raw)
        elif args.command == "import-bundle":
            result = store.import_bundle(args.input)
        elif args.command == "stats":
            result = {
                table: store.db.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
                for table in ["urls", "snapshots", "notices", "audit_events"]
            }
            result["unique_blobs"] = store.db.rows(
                "SELECT COUNT(DISTINCT sha256) AS n FROM snapshots"
            )[0]["n"]
        print(stable_json(result), end="")
        return exit_code
    except (ValueError, OSError, ValidationError) as exc:
        # No source body, credentials, or raw exception text in logs.
        code = str(exc) if type(exc) is ValueError and len(str(exc)) < 80 else type(exc).__name__
        if store:
            store.db.audit("cli", "invalid_input", error_type=type(exc).__name__)
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2
    finally:
        if store:
            store.db.engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
