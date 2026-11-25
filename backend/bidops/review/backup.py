"""Review snapshot closure and restoration barriers, without launching DBOS."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing

from bidops.qualification.sources import ScreeningInputManifest, canonical_json
from bidops.review.retention import has_review_schema, purge_in_connection
from bidops.screening.schemas import ReviewEvent


def _rows(conn, sql, parameters=()):
    cursor = conn.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _invalid():
    raise ValueError("Review backup reference closure failed")


def inventory(conn):
    """Validate business references; framework payloads remain opaque hashes."""
    if not has_review_schema(conn):
        return None
    cases = {r["case_id"]: r for r in _rows(conn, "SELECT * FROM review_cases")}
    states = {r["case_id"]: r for r in _rows(conn, "SELECT * FROM review_case_state")}
    revisions = {r["revision_id"]: r for r in _rows(conn, "SELECT * FROM review_revisions")}
    events = {r["event_id"]: r for r in _rows(conn, "SELECT * FROM review_events")}
    bodies = {
        r["event_id"]: r["body_json"] for r in _rows(conn, "SELECT * FROM review_event_bodies")
    }
    jobs = {r["run_id"]: r for r in _rows(conn, "SELECT * FROM durable_jobs")}
    results = {r["result_id"]: r for r in _rows(conn, "SELECT * FROM qualification_results")}
    if set(cases) != set(states) or not set(bodies) <= set(events):
        _invalid()
    sources = {}
    for row in _rows(conn, "SELECT * FROM review_sources"):
        if row["case_id"] not in cases:
            _invalid()
        sources.setdefault(row["case_id"], set()).add(
            (row["object_id"], row["version_id"], row["sha256"])
        )
    expected = {case_id: set() for case_id in cases}
    fixed_results = {}

    def fixed_result(result_id, case_id):
        if result_id not in fixed_results:
            saved = conn.execute(
                "SELECT body_json FROM qualification_result_bodies WHERE result_id=?",
                (result_id,),
            ).fetchone()
            value = None
            if saved:
                try:
                    value = json.loads(saved[0])
                    manifest = ScreeningInputManifest.model_validate(value["manifest"])
                    actual = set(
                        conn.execute(
                            "SELECT object_id,version_id,sha256 FROM qualification_result_sources "
                            "WHERE result_id=?",
                            (result_id,),
                        )
                    )
                    if (
                        _sha(saved[0]) != results[result_id]["body_sha256"]
                        or value["result"]["kind"] != results[result_id]["kind"]
                        or not isinstance(value.get("provenance"), dict)
                        or actual != {ref.key() for ref in manifest.company_revisions}
                    ):
                        _invalid()
                except (KeyError, TypeError, ValueError):
                    raise ValueError("Review backup result integrity failed") from None
            fixed_results[result_id] = value
        value = fixed_results[result_id]
        if value is None and states[case_id]["purge_state"] == "live":
            raise ValueError("Review backup result body is missing")
        return value

    def add_sources(case_id, table, column, value):
        expected[case_id].update(
            conn.execute(
                f"SELECT object_id,version_id,sha256 FROM {table} WHERE {column}=?", (value,)
            )
        )

    for case_id, case in cases.items():
        batch = results.get(case["candidate_batch_id"])
        head = revisions.get(states[case_id]["head_revision_id"])
        if (
            batch is None
            or batch["owner_id"] != case["owner_id"]
            or batch["kind"] != "candidate_batch"
            or head is None
            or head["case_id"] != case_id
        ):
            _invalid()
        fixed_result(case["candidate_batch_id"], case_id)
        add_sources(
            case_id, "qualification_result_sources", "result_id", case["candidate_batch_id"]
        )
    for revision in revisions.values():
        case = cases.get(revision["case_id"])
        job, result = jobs.get(revision["run_id"]), results.get(revision["result_id"])
        parent = revisions.get(revision["parent_revision_id"])
        trigger = events.get(revision["trigger_event_id"])
        if (
            case is None
            or job is None
            or result is None
            or job["owner_id"] != case["owner_id"]
            or result["owner_id"] != case["owner_id"]
            or result["kind"] != "checklist"
            or job["inputs_manifest_sha256"] != revision["inputs_manifest_sha256"]
            or (
                revision["parent_revision_id"] is not None
                and (parent is None or parent["case_id"] != case["case_id"])
            )
            or (
                revision["trigger_event_id"] is not None
                and (trigger is None or trigger["case_id"] != case["case_id"])
            )
        ):
            _invalid()
        saved = conn.execute(
            "SELECT result_id,result_revision FROM durable_job_state WHERE run_id=?",
            (revision["run_id"],),
        ).fetchone()
        if saved != (revision["result_id"], revision["result_revision"]):
            _invalid()
        fixed = fixed_result(revision["result_id"], case["case_id"])
        if fixed is not None and (
            fixed["manifest"]["closure_sha256"] != revision["inputs_manifest_sha256"]
            or fixed["result"].get("result_revision") != revision["result_revision"]
            or fixed["result"].get("run_id") != revision["result_id"]
            or fixed.get("provenance", {}).get("business_run_id") != revision["run_id"]
        ):
            _invalid()
        # Detect cycles without imposing timestamp order on clock-skewed history.
        seen, current = set(), revision
        while current is not None:
            if current["revision_id"] in seen:
                _invalid()
            seen.add(current["revision_id"])
            current = revisions.get(current["parent_revision_id"])
        add_sources(case["case_id"], "durable_job_sources", "run_id", revision["run_id"])
    for row in _rows(conn, "SELECT * FROM review_assignments"):
        if row["case_id"] not in cases or not row["subject_id"]:
            _invalid()
    for event_id, event in events.items():
        case_id = event["case_id"]
        revision = revisions.get(event["revision_id"])
        supersedes = events.get(event["supersedes_event_id"])
        if (
            case_id not in cases
            or revision is None
            or revision["case_id"] != case_id
            or event["review_version"] > states[case_id]["review_version"]
            or (
                event["supersedes_event_id"] is not None
                and (
                    supersedes is None
                    or supersedes["case_id"] != case_id
                    or supersedes["revision_id"] != event["revision_id"]
                    or supersedes["assessment_id"] != event["assessment_id"]
                    or supersedes["condition_id"] != event["condition_id"]
                    or supersedes["review_version"] >= event["review_version"]
                )
            )
        ):
            _invalid()
        body = bodies.get(event_id)
        if states[case_id]["purge_state"] == "purged":
            if body is not None:
                _invalid()
            continue
        if body is None or _sha(body) != event["event_sha256"]:
            raise ValueError("Review backup event integrity failed")
        try:
            value = json.loads(body)
            if value["kind"] != event["event_kind"] or canonical_json(value) != body:
                _invalid()
            if event["event_kind"] == "decision":
                review = ReviewEvent.model_validate(value["review_event"])
                if (
                    review.origin != "server_authenticated"
                    or review.event_id != event_id
                    or review.run_id != revision["run_id"]
                    or review.assessment_id != event["assessment_id"]
                    or review.result_revision != revision["result_revision"]
                    or review.inputs_manifest_sha256 != revision["inputs_manifest_sha256"]
                    or review.actor.principal_id != event["actor_id"]
                    or review.actor.authorization_context_id != event["authorization_context_id"]
                    or review.occurred_at.timestamp() != event["occurred_at"]
                    or review.supersedes_event_id != event["supersedes_event_id"]
                    or review.command.idempotency_key != event["idempotency_key"]
                    or review.command.reason != value["reason"]
                    or value["condition_id"] != event["condition_id"]
                ):
                    _invalid()
                fixed = fixed_results[revision["result_id"]]
                if fixed is not None:
                    groups = fixed["result"]["groups"]
                    matches = []
                    for group in groups:
                        assessment = group["assessment"]
                        if assessment["assessment_id"] != event["assessment_id"]:
                            continue
                        conditions = [
                            item
                            for item in assessment["conditions"]
                            if item["condition_id"] == event["condition_id"]
                        ]
                        judgments = [
                            item
                            for item in assessment["assessments"]
                            if item["condition_id"] == event["condition_id"]
                        ]
                        if len(conditions) == len(judgments) == 1:
                            matches.append(
                                {"condition": conditions[0], "agent_assessment": judgments[0]}
                            )
                    if (
                        len(matches) != 1
                        or _sha(canonical_json(matches[0])) != value["item_sha256"]
                    ):
                        _invalid()
        except (KeyError, TypeError, ValueError):
            raise ValueError("Review backup event integrity failed") from None
    for row in _rows(conn, "SELECT * FROM review_outbox"):
        event = events.get(row["event_id"])
        revision = revisions.get(event["revision_id"]) if event else None
        expected_kind = {
            "decision": "decision",
            "close": "closed",
            "successor_linked": "superseded",
        }
        if (
            event is None
            or revision is None
            or row["run_id"] != revision["run_id"]
            or row["kind"] != expected_kind.get(event["event_kind"])
            or row["review_epoch"] != event["review_epoch"]
            or (states[event["case_id"]]["purge_state"] == "purged" and not row["frozen"])
        ):
            _invalid()
    successors = _rows(conn, "SELECT * FROM review_successors ORDER BY run_id")
    for successor in successors:
        case = cases.get(successor["case_id"])
        parent = revisions.get(successor["parent_revision_id"])
        trigger, link = (
            events.get(successor["trigger_event_id"]),
            events.get(successor["link_event_id"]),
        )
        job = jobs.get(successor["run_id"])
        if (
            case is None
            or parent is None
            or parent["case_id"] != case["case_id"]
            or job is None
            or job["run_id"] == parent["run_id"]
            or job["owner_id"] != case["owner_id"]
            or job["inputs_manifest_sha256"] != successor["inputs_manifest_sha256"]
            or trigger is None
            or trigger["case_id"] != case["case_id"]
            or trigger["revision_id"] != parent["revision_id"]
            or link is None
            or link["case_id"] != case["case_id"]
            or link["event_kind"] != "successor_linked"
            or link["revision_id"] != parent["revision_id"]
        ):
            _invalid()
        add_sources(case["case_id"], "durable_job_sources", "run_id", successor["run_id"])
    if any(sources.get(case_id, set()) != refs for case_id, refs in expected.items()):
        raise ValueError("Review backup source closure failed")
    return {
        "cases": sorted(cases),
        "revisions": sorted(revisions),
        "events": sorted(events),
        "successor_run_ids": [r["run_id"] for r in successors],
    }


def prune_expired_reviews(path, *, now):
    with closing(sqlite3.connect(path)) as conn, conn:
        purged = purge_in_connection(conn, now)
    if purged:
        # Offline copied/staged database only; never VACUUM a running service.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return purged


def quarantine_after_restore(path, *, now):
    with closing(sqlite3.connect(path)) as conn, conn:
        if not has_review_schema(conn):
            return {"quarantined_review_cases": 0, "review_assignments_revoked": 0}
        cases = conn.execute("SELECT case_id FROM review_cases ORDER BY case_id").fetchall()
        for (case_id,) in cases:
            conn.execute(
                "UPDATE review_case_state SET quarantined=1,review_epoch=?,"
                "review_version=review_version+1,updated_at=? WHERE case_id=?",
                (uuid.uuid4().hex, now, case_id),
            )
        revoked = conn.execute("DELETE FROM review_assignments").rowcount
        conn.execute("UPDATE review_outbox SET frozen=1")
    return {"quarantined_review_cases": len(cases), "review_assignments_revoked": revoked}
