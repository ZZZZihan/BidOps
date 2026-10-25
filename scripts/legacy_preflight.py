#!/usr/bin/env python3
"""Read-only BidOps B02-06 preflight. Python 3.12+, standard library only.

Input must be a stopped, consistent COPY, with no nonempty WAL/journal.
No application imports, migration, model/network call, restore, or ledger write.
Output contains counts/hashes, not conversation text, owner IDs or login secrets.
Exit: 0=limited preflight checks OK; 1=invalid/inconsistent; 2=missing input.
Even exit 0 NEVER means B02-06, rollback, or E06 has passed.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

# Public schema names only. Unknown names/values must not leak into reports.
KNOWN_MIGRATIONS = {
    "001_initial.sql",
    "002_agent_demo.sql",
    "003_enterprise.sql",
    "004_notice_versions.sql",
    "005_company_access.sql",
    "006_identity_refresh.sql",
    "007_qualification_screening.sql",
    "008_durable_jobs.sql",
    "009_human_review.sql",
    "010_authorized_ingestion.sql",
    "011_attachment_receipts.sql",
    "012_package_versions_index.sql",
}
FINGERPRINT_TABLES = {
    "schema_migrations",
    "urls",
    "snapshots",
    "notices",
    "audit_events",
    "agent_runs",
    "enterprise_sessions",
    "enterprise_turns",
    "enterprise_audit",
    "nd_projects",
    "nd_logical_notices",
    "nd_versions",
    "nd_package_versions",
    "nd_observations",
    "nd_attachments",
    "company_objects",
    "company_grants",
    "company_profile_revisions",
    "company_material_revisions",
    "company_profile_sources",
    "company_revision_tombstones",
    "durable_jobs",
    "durable_model_attempts",
    "ingestion_runs",
    "ingestion_schedules",
    "ingestion_schedule_versions",
    "ingestion_schedule_ticks",
}


class MissingInput(Exception):
    pass


class InvalidInput(ValueError):
    """A deliberately public, fixed validation reason, never a raw input value."""


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def canon(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def strict_json(value):
    def reject_constant(_value):
        raise InvalidInput("non-JSON numeric constant refused")

    return json.loads(value, parse_constant=reject_constant)


def row_digest(conn, table):
    # Hash serialized values as opaque bytes. Never unpickle framework state.
    cols = conn.execute("PRAGMA table_info(" + quoted(table) + ")").fetchall()
    rows = []
    for row in conn.execute("SELECT * FROM " + quoted(table)):
        values = [{"blob_hex": v.hex()} if isinstance(v, bytes) else v for v in row]
        rows.append(hashlib.sha256(canon(values).encode()).hexdigest())
    return hashlib.sha256(canon({"columns": cols, "row_hashes": sorted(rows)}).encode()).hexdigest()


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def validate_expected(expected):
    if not isinstance(expected, dict) or not valid_hash(expected.get("manifest_sha256")):
        raise InvalidInput("expected metadata requires a valid manifest digest")
    samples = expected.get("samples")
    if not isinstance(samples, list) or len(samples) != 6:
        raise InvalidInput("expected metadata requires exactly six fingerprints")
    identities = set()
    for sample in samples:
        if not isinstance(sample, dict):
            raise InvalidInput("expected fingerprint must be an object")
        sid, sha, url = sample.get("snapshot_id"), sample.get("sha256"), sample.get("url")
        size = sample.get("byte_size")
        if not valid_hash(sid) or not valid_hash(sha):
            raise InvalidInput("expected fingerprint requires valid digests")
        if (
            not isinstance(url, str)
            or urlsplit(url).scheme not in ("http", "https")
            or not urlsplit(url).netloc
        ):
            raise InvalidInput("expected fingerprint requires an HTTP source URL")
        if type(size) is not int or size <= 0:
            raise InvalidInput("expected fingerprint requires a positive integer byte size")
        if hashlib.sha256((url + "\n" + sha).encode()).hexdigest() != sid or sid in identities:
            raise InvalidInput("expected snapshot identity is invalid or duplicated")
        identities.add(sid)
    return expected


def checked_file(root, name, expected, byte_size=None):
    if not valid_hash(expected):
        raise InvalidInput("invalid expected digest")
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise InvalidInput("unsafe file reference")
    if not path.is_file():
        raise MissingInput("missing referenced file; consult the private asset inventory")
    actual = digest(path)
    if actual != expected or (byte_size is not None and path.stat().st_size != byte_size):
        raise InvalidInput("referenced file digest/size mismatch")
    return {"path": name, "sha256": actual, "bytes": path.stat().st_size}


def inspect_capture(folder, expected):
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise MissingInput("capture manifest.json is missing")
    if manifest.is_symlink() or manifest.stat().st_size > 5_000_000:
        raise InvalidInput("unsafe/oversized capture manifest")
    before = digest(manifest)
    if before != expected["manifest_sha256"]:
        raise InvalidInput("capture manifest differs from original published fingerprint")
    data = strict_json(manifest.read_bytes())
    if not isinstance(data, dict):
        raise InvalidInput("capture manifest must be an object")
    samples = data.get("snapshots")
    if data.get("format_version") != 1 or data.get("parser_version") not in ("p0.1", "p0.2"):
        raise InvalidInput("unsupported capture format/parser")
    by_id = {r["snapshot_id"]: r for r in expected["samples"]}
    if not isinstance(samples, list) or len(samples) != len(by_id):
        raise InvalidInput("capture must contain exactly the six original snapshots")
    if any(not isinstance(item, dict) for item in samples):
        raise InvalidInput("capture snapshot must be an object")
    if {r.get("snapshot_id") for r in samples} != set(by_id):
        raise InvalidInput("capture snapshot set differs")
    verified = []
    for item in samples:
        old = by_id[item["snapshot_id"]]
        for key, expected_value in [
            ("source_url", old["url"]),
            ("sha256", old["sha256"]),
            ("byte_size", old["byte_size"]),
        ]:
            if item.get(key) != expected_value:
                raise InvalidInput("capture source identity/size differs")
        if (
            hashlib.sha256((item["source_url"] + "\n" + item["sha256"]).encode()).hexdigest()
            != item["snapshot_id"]
        ):
            raise InvalidInput("capture snapshot identity formula mismatch")
        verified.append(
            checked_file(folder, "raw/" + item["sha256"], item["sha256"], item["byte_size"])
        )
    if digest(manifest) != before:
        raise InvalidInput("capture manifest changed while reading")
    return {
        "status": "CHECKS_OK",
        "manifest_sha256": before,
        "raw_files": verified,
        "limitation": "Raw identity only; no old database, history, accuracy or migration proven.",
    }


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def reject_unmerged_sidecars(path):
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise MissingInput(
                "nonempty " + suffix + ": acquire a consistent snapshot first; do not delete it"
            )


def ledger_summary(conn, tables):
    if "agent_runs" not in tables:
        return {"agent_runs_present": False, "latest_complete_ledger_verified": False}
    states = Counter()
    occupied = Decimal("0")
    completed = Decimal("0")
    unresolved = Decimal("0")
    calls = 0
    for state, reserve, estimate, requests in conn.execute(
        "SELECT state,reserved_cny,estimated_cny,model_requests FROM agent_runs"
    ):
        states[
            state if state in ("started", "completed", "failed", "unconfirmed") else "unknown"
        ] += 1
        reserve, estimate = Decimal(str(reserve)), Decimal(str(estimate))
        if not reserve.is_finite() or not estimate.is_finite() or reserve < 0 or estimate < 0:
            raise InvalidInput("invalid ledger amounts")
        if type(requests) is not int or requests < 0:
            raise InvalidInput("invalid ledger model_requests count")
        if state == "completed":
            completed += estimate
            occupied += estimate
        else:
            unresolved += reserve
            occupied += reserve
        calls += requests
    return {
        "states": dict(states),
        "completed_estimated_cny": str(completed),
        "noncompleted_reserved_cny": str(unresolved),
        "occupied_cny": str(occupied),
        "model_requests": calls,
        "latest_complete_ledger_verified": False,
        "remaining_budget_cny": None,
    }


def inspect_database(path, data_dir, expected, snapshot_confirmed, min_history, ledger_only=False):
    if not path.is_file():
        raise MissingInput("business database file is missing")
    if not snapshot_confirmed:
        raise MissingInput("require --snapshot-confirmed for a stopped, consistent COPY")
    if path.is_symlink():
        raise InvalidInput("database symlink refused")
    # immutable cannot see WAL frames. Refuse them, never silently ignore them.
    reject_unmerged_sidecars(path)
    before = digest(path)
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    report = {
        "sqlite_open": "mode=ro&immutable=1; query_only=ON; trusted_schema=OFF",
        "input_db_sha256": before,
        "limitations": [
            "Operator confirms consistent snapshot; script cannot establish original provenance.",
            "No Pydantic AI deserialization, canonical_text or quote-offset validation.",
            "No migration, idempotence, old-service rollback, identity or latest-cost acceptance.",
            "Table fingerprints may contain hashes of private data; keep reports controlled.",
        ],
    }
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise InvalidInput("SQLite integrity_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise InvalidInput("SQLite foreign_key_check failed")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        required = (
            {"agent_runs"} if ledger_only else {"schema_migrations", "urls", "snapshots", "notices"}
        )
        if not required <= tables:
            raise InvalidInput("missing base schema tables: " + ",".join(sorted(required - tables)))
        fingerprint_tables = {"agent_runs"} if ledger_only else tables & FINGERPRINT_TABLES
        report["tables"] = {
            t: {
                "rows": conn.execute("SELECT count(*) FROM " + quoted(t)).fetchone()[0],
                "logical_sha256": row_digest(conn, t),
            }
            for t in sorted(fingerprint_tables)
        }
        report["ledger_snapshot"] = ledger_summary(conn, tables)
        if ledger_only:
            report["limitations"].append(
                "Ledger-only: raw/history/attempt receipts/DBOS bindings not inspected; "
                "latest provider charges not reconciled."
            )
            reject_unmerged_sidecars(path)
            if digest(path) != before:
                raise InvalidInput("input database changed during inspection")
            report["input_db_unchanged"] = True
            report["status"] = "CHECKS_OK"
            return report
        migrations = [
            r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        report["schema_migrations"] = [
            version for version in migrations if version in KNOWN_MIGRATIONS
        ]
        report["unrecognized_migration_count"] = sum(
            version not in KNOWN_MIGRATIONS for version in migrations
        )
        report["unfingerprinted_table_count"] = len(tables - fingerprint_tables)
        report["limitations"].append(
            "Only listed public tables are fingerprinted; "
            "other tables require controlled owner review."
        )
        report["reference_counts"] = {}
        joins = {
            "snapshot_without_notice": (
                "SELECT count(*) FROM snapshots s LEFT JOIN notices n "
                "ON n.snapshot_id=s.snapshot_id WHERE n.snapshot_id IS NULL"
            ),
            "notice_without_snapshot": (
                "SELECT count(*) FROM notices n LEFT JOIN snapshots s "
                "ON s.snapshot_id=n.snapshot_id WHERE s.snapshot_id IS NULL"
            ),
            "snapshot_without_url": (
                "SELECT count(*) FROM snapshots s LEFT JOIN urls u "
                "ON u.source_url=s.source_url WHERE u.source_url IS NULL"
            ),
        }
        if "nd_versions" in tables:
            joins["snapshot_without_version"] = (
                "SELECT count(*) FROM snapshots s LEFT JOIN nd_versions v "
                "ON v.snapshot_id=s.snapshot_id WHERE v.snapshot_id IS NULL"
            )
        for name, query in joins.items():
            report["reference_counts"][name] = conn.execute(query).fetchone()[0]
            if report["reference_counts"][name]:
                raise InvalidInput("broken reference: " + name)
        snapshots = {}
        files = []
        for sid, url, sha, metadata in conn.execute(
            "SELECT snapshot_id,source_url,sha256,metadata_json FROM snapshots"
        ):
            item = strict_json(metadata)
            if not isinstance(item, dict):
                raise InvalidInput("snapshot metadata must be an object")
            if not valid_hash(sid) or not valid_hash(sha) or not isinstance(url, str):
                raise InvalidInput("invalid snapshot identity fields")
            if type(item.get("byte_size")) is not int or item["byte_size"] <= 0:
                raise InvalidInput("snapshot metadata requires a positive integer byte size")
            if (
                item.get("snapshot_id") != sid
                or item.get("sha256") != sha
                or item.get("source_url") != url
            ):
                raise InvalidInput("snapshot row/metadata mismatch")
            if hashlib.sha256((url + "\n" + sha).encode()).hexdigest() != sid:
                raise InvalidInput("database snapshot identity mismatch")
            files.append(checked_file(data_dir, "raw/" + sha, sha, item["byte_size"]))
            snapshots[sid] = (url, sha)
        missing_original = [
            s["snapshot_id"]
            for s in expected["samples"]
            if snapshots.get(s["snapshot_id"]) != (s["url"], s["sha256"])
        ]
        if missing_original:
            raise MissingInput(
                "original six snapshot set missing/inconsistent: " + str(len(missing_original))
            )
        if "nd_attachments" in tables:
            for (sha,) in conn.execute(
                "SELECT DISTINCT raw_sha256 FROM nd_attachments WHERE raw_sha256 IS NOT NULL"
            ):
                files.append(checked_file(data_dir, "attachments/" + sha, sha))
        if "company_material_revisions" in tables:
            for blob, sha, text_path, text_sha in conn.execute(
                "SELECT blob_path,sha256,text_path,text_sha256 FROM company_material_revisions"
            ):
                for name, value in ((blob, sha), (text_path, text_sha)):
                    if (
                        not isinstance(name, str)
                        or re.fullmatch(r"(blob|text)/[a-f0-9]{32}", name) is None
                    ):
                        raise InvalidInput("invalid company file reference")
                    files.append(checked_file(data_dir, "private/company/" + name, value))
        report["referenced_files"] = files
        histories = {
            "completed_turns": 0,
            "response_json_syntax_valid": 0,
            "messages_json_syntax_valid": 0,
            "snapshot_references_checked": 0,
            "citation_identity_checked": 0,
        }
        if "enterprise_turns" in tables:
            if "enterprise_sessions" not in tables:
                raise InvalidInput("enterprise_turns without enterprise_sessions")
            bad_owner = conn.execute(
                "SELECT count(*) FROM enterprise_turns t LEFT JOIN enterprise_sessions s "
                "ON t.session_id=s.session_id WHERE s.session_id IS NULL "
                "OR t.owner_id IS NULL OR s.owner_id IS NULL OR t.owner_id!=s.owner_id"
            ).fetchone()[0]
            if bad_owner:
                raise InvalidInput("historical session/owner reference mismatch")
            for response, messages in conn.execute(
                "SELECT response_json,messages_json FROM enterprise_turns WHERE state='completed'"
            ):
                histories["completed_turns"] += 1
                if response is None or messages is None:
                    raise InvalidInput(
                        "completed historical turn lacks persisted response/messages"
                    )
                response_obj, messages_obj = strict_json(response), strict_json(messages)
                if not isinstance(response_obj, dict) or not isinstance(messages_obj, list):
                    raise InvalidInput("unexpected historical JSON structure")
                histories["response_json_syntax_valid"] += 1
                histories["messages_json_syntax_valid"] += 1
                for node in walk(response_obj):
                    sid = node.get("snapshot_id", node.get("source_snapshot_id"))
                    if sid is not None:
                        histories["snapshot_references_checked"] += 1
                        if not isinstance(sid, str) or sid not in snapshots:
                            raise InvalidInput("history points to a missing snapshot")
                        url, sha = snapshots[sid]
                        if "source_url" in node and node["source_url"] != url:
                            raise InvalidInput("history source URL mismatch")
                        if "sha256" in node and node["sha256"] != sha:
                            raise InvalidInput("history raw SHA256 mismatch")
                    if {"snapshot_id", "sha256", "quote", "start", "end", "locator"} <= node.keys():
                        if (
                            not isinstance(node["snapshot_id"], str)
                            or node["snapshot_id"] not in snapshots
                        ):
                            raise InvalidInput("citation snapshot identity missing")
                        url, sha = snapshots[node["snapshot_id"]]
                        if node["sha256"] != sha or node.get("source_url") != url:
                            raise InvalidInput("citation source identity mismatch")
                        if node["locator"] != "normalized_text_v2":
                            raise MissingInput(
                                "historical locator needs original normalizer; never relabel it"
                            )
                        histories["citation_identity_checked"] += 1
        report["history"] = histories
        if histories["completed_turns"] < min_history:
            raise MissingInput(
                "insufficient real historical completed turns: " + str(histories["completed_turns"])
            )
        if "durable_model_attempts" in tables:
            if "agent_runs" not in tables:
                raise InvalidInput("durable attempts exist without ledger table")
            bad = conn.execute(
                "SELECT count(*) FROM durable_model_attempts a LEFT JOIN agent_runs l "
                "ON l.run_id=a.ledger_id WHERE a.ledger_id IS NOT NULL AND l.run_id IS NULL"
            ).fetchone()[0]
            if bad:
                raise InvalidInput("durable model attempt ledger reference missing")
            report["durable_attempt_ledger_references_valid"] = True
        durable_tables = (
            "durable_jobs",
            "ingestion_runs",
            "ingestion_schedules",
            "ingestion_schedule_versions",
            "ingestion_schedule_ticks",
        )
        needs_system = any(
            t in tables and conn.execute("SELECT 1 FROM " + quoted(t) + " LIMIT 1").fetchone()
            for t in durable_tables
        )
        report["paired_system_database_required"] = bool(needs_system)
        if needs_system:
            system = data_dir / "private/jobs/dbos.sqlite3"
            if not system.is_file():
                raise MissingInput("paired private/jobs/dbos.sqlite3 missing")
            if system.is_symlink() or not system.resolve().is_relative_to(data_dir.resolve()):
                raise InvalidInput("unsafe paired system database path")
            reject_unmerged_sidecars(system)
            report["paired_system_database_sha256"] = digest(system)
            report["limitations"].append(
                "DBOS presence/hash only; v4 binding/workflow closure "
                "still requires official verifier on a copy."
            )
    reject_unmerged_sidecars(path)
    after = digest(path)
    if before != after:
        raise InvalidInput("input database changed during inspection")
    report["input_db_unchanged"] = True
    report["status"] = "CHECKS_OK"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--snapshot-confirmed", action="store_true")
    parser.add_argument(
        "--ledger-only",
        action="store_true",
        help="Inspect ledger without E01 raw/history; does not verify E06 completeness.",
    )
    parser.add_argument("--min-history", type=int, default=0)
    parser.add_argument(
        "--expected",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data/seeds/p0-capture-20261022.expected.json",
    )
    args = parser.parse_args()
    if not args.capture and not args.database:
        parser.error("specify --capture and/or --database")
    if args.database and not args.data_dir and not args.ledger_only:
        parser.error("--database requires --data-dir")
    if args.ledger_only and (not args.database or args.capture or args.min_history):
        parser.error(
            "--ledger-only requires --database and cannot combine with capture/min-history"
        )
    if args.min_history < 0:
        parser.error("--min-history must be nonnegative")
    report = {
        "scope": "read-only structural preflight, not historical migration acceptance",
        "b02_06_status": "NOT_EVALUATED",
        "e06_status": "NOT_EVALUATED",
        "new_model_calls": 0,
        "database_writes": 0,
    }
    try:
        expected = {}
        if not args.ledger_only:
            expected_bytes = args.expected.read_bytes()
            expected = validate_expected(strict_json(expected_bytes))
            report["expected_reference_sha256"] = hashlib.sha256(expected_bytes).hexdigest()
            report["expected_reference_limit"] = (
                "Compare this digest with the pinned public metadata; "
                "custom expectations do not authenticate the original six notices."
            )
    except OSError:
        report["preflight_status"] = "BLOCKED"
        report["reason"] = "expected-capture metadata file is unavailable"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, TypeError, RecursionError):
        report["preflight_status"] = "FAIL"
        report["reason"] = "expected-capture metadata is invalid or incomplete"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    jobs = []
    if args.capture:
        jobs.append(("capture", lambda: inspect_capture(args.capture, expected)))
    if args.database:
        jobs.append(
            (
                "database",
                lambda: inspect_database(
                    args.database,
                    args.data_dir,
                    expected,
                    args.snapshot_confirmed,
                    args.min_history,
                    args.ledger_only,
                ),
            )
        )
    exit_code = 0
    for name, work in jobs:
        try:
            report[name] = work()
        except MissingInput as exc:
            report[name] = {"status": "BLOCKED", "reason": str(exc)}
            if exit_code != 1:
                exit_code = 2
        except InvalidInput as exc:
            report[name] = {"status": "FAIL", "reason": str(exc)}
            exit_code = 1
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RecursionError,
            OverflowError,
            InvalidOperation,
            sqlite3.DatabaseError,
        ):
            report[name] = {
                "status": "FAIL",
                "reason": "invalid input, schema, encoding or unreadable file; inspect privately",
            }
            exit_code = 1
    report["preflight_status"] = {0: "CHECKS_OK_NOT_ACCEPTANCE", 1: "FAIL", 2: "BLOCKED"}[exit_code]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
