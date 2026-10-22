"""Erase review prose by its complete source closure; keep immutable receipts.

Review-only supplemental dependencies do not erase an older calculation that did
not use them. Jobs keep their own source closure and native-history erasure.
"""

from bidops.company.authorization import transaction

TABLES = {
    "review_cases",
    "review_case_state",
    "review_revisions",
    "review_assignments",
    "review_events",
    "review_event_bodies",
    "review_outbox",
    "review_sources",
    "review_successors",
}


def execute(conn, sql, parameters=None):
    if hasattr(conn, "exec_driver_sql"):
        return conn.exec_driver_sql(sql, parameters or {})
    return conn.execute(sql, parameters or {})


def has_review_schema(conn):
    tables = {row[0] for row in execute(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES & tables:
        return False
    if not TABLES <= tables:
        raise ValueError("Review database schema is incomplete")
    return True


def purge_in_connection(conn, now):
    """Caller owns the write transaction; deny and erase before any file work."""
    if not has_review_schema(conn):
        return []
    execute(conn, "PRAGMA secure_delete=ON")
    expired = [
        row[0]
        for row in execute(
            conn,
            "SELECT DISTINCT r.case_id FROM review_sources r "
            "LEFT JOIN company_objects o ON o.object_id=r.object_id "
            "JOIN review_case_state s ON s.case_id=r.case_id "
            "WHERE s.purge_state='live' AND (o.object_id IS NULL "
            "OR o.deleted_at IS NOT NULL OR o.retention_until<=:now "
            "OR o.purge_state!='live')",
            {"now": now},
        )
    ]
    for case_id in expired:
        execute(
            conn,
            "UPDATE review_case_state SET purge_state='purged',"
            "purged_at=COALESCE(purged_at,:now),quarantined=1,"
            "review_version=review_version+1,updated_at=:now WHERE case_id=:id",
            {"id": case_id, "now": now},
        )
    # Retry all previously purged cases. A failed native/file cleanup must never
    # leave review prose readable or make the next maintenance pass skip it.
    purged = [
        row[0]
        for row in execute(
            conn,
            "SELECT case_id FROM review_case_state WHERE purge_state='purged' ORDER BY case_id",
        )
    ]
    for case_id in purged:
        parameters = {"id": case_id}
        execute(
            conn,
            "DELETE FROM review_event_bodies WHERE event_id IN "
            "(SELECT event_id FROM review_events WHERE case_id=:id)",
            parameters,
        )
        execute(conn, "DELETE FROM review_assignments WHERE case_id=:id", parameters)
        execute(
            conn,
            "UPDATE review_outbox SET frozen=1 WHERE event_id IN "
            "(SELECT event_id FROM review_events WHERE case_id=:id)",
            parameters,
        )
    return purged


def prune_reviews(db, now):
    with transaction(db) as conn:
        purged = purge_in_connection(conn, now)
    return {"status": "PASS", "purged_case_ids": purged, "failure_ids": []}
