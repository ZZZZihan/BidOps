"""Safety checks for the standalone preflight tool, never historical acceptance.

Every database/body below is a temporary TOOL-ONLY syntax fixture. These tests do
not import BidOps, migrate or restore a database, execute a model, or prove that
the original six notices, real conversations, or latest fee ledger are available.
"""

import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "legacy_preflight.py"
SPEC = importlib.util.spec_from_file_location("bidops_legacy_preflight_tool_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load standalone preflight tool")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class TestLegacyPreflight(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bidops-preflight-tool-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def run_tool(self, *arguments):
        output = io.StringIO()
        errors = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), *map(str, arguments)]),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            exit_code = PREFLIGHT.main()
        self.assertEqual(errors.getvalue(), "", "Tool must not leak a traceback to stderr")
        report = json.loads(output.getvalue())
        self.assertEqual(report["b02_06_status"], "NOT_EVALUATED")
        self.assertEqual(report["e06_status"], "NOT_EVALUATED")
        return exit_code, report

    def make_ledger(self):
        database = self.folder / "tool-only-ledger.sqlite3"
        with sqlite3.connect(database) as conn:
            conn.execute(
                "CREATE TABLE agent_runs (run_id TEXT PRIMARY KEY, state TEXT, "
                "reserved_cny REAL, estimated_cny REAL, model_requests INTEGER)"
            )
            conn.executemany(
                "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?)",
                [
                    ("tool-completed", "completed", 9.0, 0.125, 2),
                    ("tool-failed", "failed", 0.25, 0.01, 1),
                    ("tool-unknown", "unknown", 0.5, 0.02, 0),
                ],
            )
        return database

    def ledger_args(self, database):
        return ("--database", database, "--snapshot-confirmed", "--ledger-only")

    def make_capture_fixture(self):
        """Six TOOL-ONLY syntax records, not a historical migration fixture."""
        capture = self.folder / "tool-only-capture"
        (capture / "raw").mkdir(parents=True)
        samples, snapshots = [], []
        for index in range(6):
            body = f"TOOL-ONLY-SYNTAX-FIXTURE-{index}".encode()
            raw_sha = hashlib.sha256(body).hexdigest()
            url = f"https://fixture.invalid/tool-only/{index}"
            snapshot_id = hashlib.sha256((url + "\n" + raw_sha).encode()).hexdigest()
            (capture / "raw" / raw_sha).write_bytes(body)
            sample = {
                "snapshot_id": snapshot_id,
                "url": url,
                "sha256": raw_sha,
                "byte_size": len(body),
            }
            samples.append(sample)
            snapshots.append({**sample, "source_url": sample["url"]})
        manifest = capture / "manifest.json"
        manifest.write_text(
            json.dumps({"format_version": 1, "parser_version": "p0.2", "snapshots": snapshots})
        )
        expected = {
            "description": "TOOL-ONLY metadata, not original historical evidence",
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "samples": samples,
        }
        expected_path = self.folder / "tool-only-expected.json"
        expected_path.write_text(json.dumps(expected))
        return capture, expected_path, expected

    def make_notice_database(self):
        capture, expected_path, expected = self.make_capture_fixture()
        database = self.folder / "tool-only-notices.sqlite3"
        with sqlite3.connect(database) as conn:
            conn.executescript(
                "CREATE TABLE schema_migrations(version TEXT);"
                "CREATE TABLE urls(source_url TEXT);"
                "CREATE TABLE snapshots(snapshot_id TEXT,source_url TEXT,sha256 TEXT,"
                "metadata_json TEXT);"
                "CREATE TABLE notices(notice_id TEXT,snapshot_id TEXT,payload_json TEXT);"
                "CREATE TABLE enterprise_sessions(session_id TEXT,owner_id TEXT);"
                "CREATE TABLE enterprise_turns(session_id TEXT,owner_id TEXT,state TEXT,"
                "response_json TEXT,messages_json TEXT);"
            )
            for sample in expected["samples"]:
                sid, url, raw_sha = sample["snapshot_id"], sample["url"], sample["sha256"]
                metadata = {**sample, "source_url": url}
                conn.execute("INSERT INTO urls VALUES (?)", (url,))
                conn.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                    (sid, url, raw_sha, json.dumps(metadata)),
                )
                conn.execute("INSERT INTO notices VALUES (?, ?, ?)", (sid, sid, "{}"))
        return database, capture, expected_path, expected

    def full_args(self, database, data_dir, expected):
        return (
            "--database",
            database,
            "--data-dir",
            data_dir,
            "--snapshot-confirmed",
            "--expected",
            expected,
        )

    def test_missing_inputs_are_blocked(self):
        cases = (
            ("capture", ("--capture", self.folder / "absent-capture")),
            ("database", self.ledger_args(self.folder / "absent.sqlite3")),
            (
                "expected",
                (
                    "--capture",
                    self.folder / "absent-capture",
                    "--expected",
                    self.folder / "absent-expected.json",
                ),
            ),
        )
        for name, arguments in cases:
            with self.subTest(missing=name):
                exit_code, report = self.run_tool(*arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(report["preflight_status"], "BLOCKED")

    def test_ledger_only_counts_costs_without_raw_or_history_and_preserves_bytes(self):
        database = self.make_ledger()
        original = database.read_bytes()
        # A missing E01 metadata/raw/history bundle must not block fee inventory.
        exit_code, report = self.run_tool(
            *self.ledger_args(database), "--expected", self.folder / "absent-expected.json"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["preflight_status"], "CHECKS_OK_NOT_ACCEPTANCE")
        self.assertNotIn("expected_reference_sha256", report)
        inventory = report["database"]
        ledger = inventory["ledger_snapshot"]
        self.assertEqual(Decimal(ledger["completed_estimated_cny"]), Decimal("0.125"))
        self.assertEqual(Decimal(ledger["noncompleted_reserved_cny"]), Decimal("0.75"))
        self.assertEqual(Decimal(ledger["occupied_cny"]), Decimal("0.875"))
        self.assertEqual(ledger["model_requests"], 3)
        self.assertEqual(ledger["states"], {"completed": 1, "failed": 1, "unknown": 1})
        self.assertFalse(ledger["latest_complete_ledger_verified"])
        self.assertIsNone(ledger["remaining_budget_cny"])
        self.assertTrue(inventory["input_db_unchanged"])
        self.assertEqual(inventory["input_db_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(database.read_bytes(), original)
        self.assertEqual(set(self.folder.iterdir()), {database})

    def test_unconfirmed_snapshot_is_blocked(self):
        database = self.make_ledger()
        original = database.read_bytes()
        exit_code, report = self.run_tool("--database", database, "--ledger-only")
        self.assertEqual(exit_code, 2)
        self.assertIn("snapshot-confirmed", report["database"]["reason"])
        self.assertEqual(database.read_bytes(), original)

    def test_nonempty_wal_and_journal_are_refused_without_deleting_them(self):
        database = self.make_ledger()
        original = database.read_bytes()
        for suffix in ("-wal", "-journal"):
            with self.subTest(sidecar=suffix):
                sidecar = Path(str(database) + suffix)
                sentinel = b"TOOL-ONLY-NONEMPTY-SIDECAR"
                sidecar.write_bytes(sentinel)
                try:
                    exit_code, report = self.run_tool(*self.ledger_args(database))
                    self.assertEqual(exit_code, 2)
                    self.assertIn(suffix, report["database"]["reason"])
                    self.assertEqual(database.read_bytes(), original)
                    self.assertEqual(sidecar.read_bytes(), sentinel)
                finally:
                    sidecar.unlink()

    def test_wal_created_during_inspection_is_refused(self):
        database = self.make_ledger()
        original = database.read_bytes()
        wal = Path(str(database) + "-wal")
        row_digest = PREFLIGHT.row_digest

        def simulate_external_writer(conn, table):
            result = row_digest(conn, table)
            # Test harness simulates a writer after the tool's first WAL check.
            wal.write_bytes(b"TOOL-ONLY-SIMULATED-EXTERNAL-WRITER")
            return result

        with mock.patch.object(PREFLIGHT, "row_digest", side_effect=simulate_external_writer):
            exit_code, report = self.run_tool(*self.ledger_args(database))
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["preflight_status"], "BLOCKED")
        self.assertIn("-wal", report["database"]["reason"])
        self.assertEqual(database.read_bytes(), original)
        self.assertTrue(wal.is_file())

    def test_sqlite_connection_rejects_writes_and_enables_read_barriers(self):
        database = self.make_ledger()
        original = database.read_bytes()
        connect = sqlite3.connect
        ledger_summary = PREFLIGHT.ledger_summary
        observations = []

        def checked_connect(database_uri, **kwargs):
            self.assertIn("mode=ro", database_uri)
            self.assertIn("immutable=1", database_uri)
            self.assertTrue(kwargs.get("uri"))
            conn = connect(database_uri, **kwargs)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    conn.execute(
                        "INSERT INTO agent_runs VALUES ('tool-write-probe', 'completed', 0, 0, 0)"
                    )
            except BaseException:
                conn.close()
                raise
            observations.append("read-only connection rejected write")
            return conn

        def checked_summary(conn, tables):
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone(), (1,))
            self.assertEqual(conn.execute("PRAGMA trusted_schema").fetchone(), (0,))
            observations.append("query-only and untrusted-schema barriers active")
            return ledger_summary(conn, tables)

        with (
            mock.patch.object(PREFLIGHT.sqlite3, "connect", side_effect=checked_connect),
            mock.patch.object(PREFLIGHT, "ledger_summary", side_effect=checked_summary),
        ):
            exit_code, report = self.run_tool(*self.ledger_args(database))
        self.assertEqual(exit_code, 0, report)
        self.assertEqual(len(observations), 2)
        self.assertEqual(database.read_bytes(), original)

    def test_malformed_ledger_is_fail_without_changing_source(self):
        database = self.make_ledger()
        for column, value in (("estimated_cny", "not-a-number"), ("model_requests", -1)):
            with self.subTest(column=column):
                with sqlite3.connect(database) as conn:
                    conn.execute("UPDATE agent_runs SET estimated_cny=0.125, model_requests=2")
                    conn.execute(f"UPDATE agent_runs SET {column}=?", (value,))
                original = database.read_bytes()
                exit_code, report = self.run_tool(*self.ledger_args(database))
                self.assertEqual(exit_code, 1)
                self.assertEqual(report["preflight_status"], "FAIL")
                self.assertEqual(database.read_bytes(), original)

    def test_null_citation_snapshot_is_not_counted_as_verified(self):
        database, capture, expected_path, expected = self.make_notice_database()
        sample = expected["samples"][0]
        citation = {
            "snapshot_id": None,
            "source_url": sample["url"],
            "sha256": sample["sha256"],
            "quote": "",
            "start": 0,
            "end": 0,
            "locator": "normalized_text_v2",
        }
        with sqlite3.connect(database) as conn:
            conn.execute("INSERT INTO enterprise_sessions VALUES ('tool-only', 'tool-only')")
            conn.execute(
                "INSERT INTO enterprise_turns VALUES (?, ?, ?, ?, ?)",
                (
                    "tool-only",
                    "tool-only",
                    "completed",
                    json.dumps({"blocks": [{"items": [{"citations": [citation]}]}]}),
                    "[]",
                ),
            )
        original = database.read_bytes()
        exit_code, report = self.run_tool(
            *self.full_args(database, capture, expected_path),
            "--min-history",
            "1",
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("citation snapshot identity missing", report["database"]["reason"])
        self.assertEqual(database.read_bytes(), original)

    def test_expected_metadata_rejects_invalid_shapes_and_sample_identities(self):
        capture, expected_path, valid = self.make_capture_fixture()
        cases = [("empty-file", ""), ("empty-object", "{}")]
        for label, value in (("list", []), ("null", None), ("string", "TOOL-ONLY")):
            cases.append((label, json.dumps(value)))
        without_manifest = deepcopy(valid)
        del without_manifest["manifest_sha256"]
        cases.append(("missing-manifest", json.dumps(without_manifest)))
        fewer = deepcopy(valid)
        fewer["samples"].pop()
        cases.append(("fewer-than-six", json.dumps(fewer)))
        duplicate = deepcopy(valid)
        duplicate["samples"][-1] = deepcopy(duplicate["samples"][0])
        cases.append(("duplicate-snapshot", json.dumps(duplicate)))
        without_bytes = deepcopy(valid)
        del without_bytes["samples"][0]["byte_size"]
        cases.append(("missing-byte-size", json.dumps(without_bytes)))
        for size in (0, -1, True, 1.5, "25"):
            bad_size = deepcopy(valid)
            bad_size["samples"][0]["byte_size"] = size
            cases.append((f"invalid-byte-size-{size!r}", json.dumps(bad_size)))
        for field, value in (
            ("snapshot_id", "bad-snapshot-id"),
            ("sha256", "bad-raw-hash"),
            ("url", "file:///TOOL-ONLY"),
        ):
            bad_identity = deepcopy(valid)
            bad_identity["samples"][0][field] = value
            cases.append((f"invalid-{field}", json.dumps(bad_identity)))
        for label, content in cases:
            with self.subTest(expected=label):
                expected_path.write_text(content)
                exit_code, report = self.run_tool("--capture", capture, "--expected", expected_path)
                self.assertEqual(exit_code, 1)
                self.assertEqual(report["preflight_status"], "FAIL")
                self.assertNotIn("Traceback", json.dumps(report))

    def test_snapshot_metadata_shape_and_nonfinite_json_return_structured_failure(self):
        database, capture, expected_path, expected = self.make_notice_database()
        sample = expected["samples"][0]
        metadata = {**sample, "source_url": sample["url"]}
        cases = (
            ("array", "[]"),
            ("NaN", json.dumps({**metadata, "tool_only_nonfinite": float("nan")})),
            ("Infinity", json.dumps({**metadata, "tool_only_nonfinite": float("inf")})),
        )
        for label, content in cases:
            with self.subTest(metadata=label):
                with sqlite3.connect(database) as conn:
                    conn.execute(
                        "UPDATE snapshots SET metadata_json=? WHERE snapshot_id=?",
                        (content, sample["snapshot_id"]),
                    )
                original = database.read_bytes()
                exit_code, report = self.run_tool(*self.full_args(database, capture, expected_path))
                self.assertEqual(exit_code, 1)
                self.assertEqual(report["preflight_status"], "FAIL")
                self.assertNotIn("Traceback", json.dumps(report))
                self.assertEqual(database.read_bytes(), original)

    def test_capture_and_full_modes_record_exact_expected_file_hash(self):
        database, capture, expected_path, _ = self.make_notice_database()
        expected_sha = hashlib.sha256(expected_path.read_bytes()).hexdigest()
        cases = (
            ("capture", ("--capture", capture, "--expected", expected_path)),
            ("full", self.full_args(database, capture, expected_path)),
        )
        for mode, arguments in cases:
            with self.subTest(mode=mode):
                exit_code, report = self.run_tool(*arguments)
                self.assertEqual(exit_code, 0, report)
                self.assertEqual(report["expected_reference_sha256"], expected_sha)
                self.assertEqual(report["preflight_status"], "CHECKS_OK_NOT_ACCEPTANCE")

    def test_ledger_only_does_not_expose_private_table_names_or_unknown_states(self):
        database = self.make_ledger()
        table_sentinel = "TOOL_ONLY_PRIVATE_TABLE_SENTINEL"
        state_sentinel = "TOOL_ONLY_PRIVATE_STATE_SENTINEL"
        with sqlite3.connect(database) as conn:
            conn.execute(f'CREATE TABLE "{table_sentinel}" (private_body TEXT)')
            conn.execute(f'INSERT INTO "{table_sentinel}" VALUES (?)', (state_sentinel,))
            conn.execute(
                "UPDATE agent_runs SET state=? WHERE run_id='tool-unknown'", (state_sentinel,)
            )
        original = database.read_bytes()
        exit_code, report = self.run_tool(*self.ledger_args(database))
        self.assertEqual(exit_code, 0, report)
        rendered = json.dumps(report)
        self.assertNotIn(table_sentinel, rendered)
        self.assertNotIn(state_sentinel, rendered)
        self.assertEqual(set(report["database"]["tables"]), {"agent_runs"})
        ledger = report["database"]["ledger_snapshot"]
        self.assertEqual(ledger["states"].get("unknown"), 1)
        self.assertEqual(Decimal(ledger["occupied_cny"]), Decimal("0.875"))
        self.assertEqual(database.read_bytes(), original)

    def test_ledger_only_is_independent_of_unrelated_schema_migration_columns(self):
        database = self.make_ledger()
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE schema_migrations (broken TEXT)")
            conn.execute("INSERT INTO schema_migrations VALUES ('TOOL-ONLY-NOT-A-MIGRATION')")
        original = database.read_bytes()
        exit_code, report = self.run_tool(*self.ledger_args(database))
        self.assertEqual(exit_code, 0, report)
        self.assertEqual(report["preflight_status"], "CHECKS_OK_NOT_ACCEPTANCE")
        self.assertEqual(set(report["database"]["tables"]), {"agent_runs"})
        self.assertNotIn("TOOL-ONLY-NOT-A-MIGRATION", json.dumps(report))
        self.assertEqual(database.read_bytes(), original)

    def test_expected_capture_is_public_fingerprint_metadata_only(self):
        expected = json.loads((ROOT / "data/seeds/p0-capture-20261022.expected.json").read_text())
        public = json.loads((ROOT / "reports/p0-collection-verification-20261022.json").read_text())
        self.assertEqual(expected["samples"], public["samples"])
        self.assertEqual(expected["manifest_sha256"], public["input_manifest_sha256"])
        self.assertEqual(len(expected["samples"]), 6)
        self.assertEqual(len({row["snapshot_id"] for row in expected["samples"]}), 6)
        for sample in expected["samples"]:
            source_identity = (sample["url"] + "\n" + sample["sha256"]).encode()
            identity = hashlib.sha256(source_identity).hexdigest()
            self.assertEqual(identity, sample["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
