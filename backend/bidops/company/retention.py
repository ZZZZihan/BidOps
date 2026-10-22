from sqlalchemy import text

from bidops.company.authorization import CompanyError, transaction
from bidops.enterprise.auth import audit


def quarantine_after_restore(db):
    """Called by offline restore: no old grant survives, including uploader grants."""
    with transaction(db) as conn:
        conn.execute(text("DELETE FROM company_grants"))
        count = conn.execute(text("UPDATE company_objects SET quarantined=1")).rowcount
        tables = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        derived = 0
        if "qualification_result_lifecycle" in tables:
            derived = conn.execute(
                text("UPDATE qualification_result_lifecycle SET quarantined=1")
            ).rowcount
    audit(db, "company_restore_quarantined", objects=count)
    return {
        "quarantined_objects": count,
        "quarantined_results": derived,
        "old_grants_removed": True,
    }


def prune_company_materials(service):
    """Deny first, then erase files/derived profile bodies; failed erasure is retryable."""
    db, now = service.db, service.auth.clock()
    with transaction(db) as conn:
        conn.execute(
            text(
                "UPDATE company_objects SET purge_state='pending' "
                "WHERE purge_state='live' AND (deleted_at IS NOT NULL "
                "OR retention_until<=:now)"
            ),
            {"now": now},
        )
        # Profile bodies can contain quotes: dependent revisions must not retain them.
        conn.execute(
            text(
                "UPDATE company_objects SET purge_state='pending',"
                "deleted_at=COALESCE(deleted_at,:now) WHERE object_id IN ("
                "SELECT p.object_id FROM company_profile_revisions p "
                "JOIN company_profile_sources d ON d.profile_version=p.version_id "
                "JOIN company_objects m ON m.object_id=d.material_id "
                "WHERE m.purge_state IN ('pending','purged'))"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "DELETE FROM company_grants WHERE object_id IN "
                "(SELECT object_id FROM company_objects WHERE purge_state='pending')"
            )
        )
    # Derived quotes/raw outputs must be erased even if private file unlink fails.
    derived = {"purged_ids": []}
    if db.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qualification_results'"):
        from bidops.qualification.repository import prune_results

        derived = prune_results(db, now)
    pending = db.rows(
        "SELECT * FROM company_objects WHERE purge_state='pending' ORDER BY object_id"
    )
    failures, purged = [], []
    for obj in pending:
        oid = obj["object_id"]
        revisions = db.rows(
            "SELECT * FROM company_material_revisions WHERE object_id=:id", {"id": oid}
        )
        try:
            for revision in revisions:
                for field in ("blob_path", "text_path"):
                    service.materials.path(revision[field]).unlink(missing_ok=True)
        except (OSError, CompanyError):
            failures.append(oid)
            continue
        with transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO company_revision_tombstones "
                    "SELECT version_id,object_id,sha256,text_sha256,:now "
                    "FROM company_material_revisions WHERE object_id=:id"
                ),
                {"id": oid, "now": now},
            )
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO company_revision_tombstones "
                    "SELECT version_id,object_id,sha256,NULL,:now "
                    "FROM company_profile_revisions WHERE object_id=:id"
                ),
                {"id": oid, "now": now},
            )
            conn.execute(
                text(
                    "DELETE FROM company_profile_sources WHERE profile_version IN "
                    "(SELECT version_id FROM company_profile_revisions WHERE object_id=:id)"
                ),
                {"id": oid},
            )
            conn.execute(
                text("DELETE FROM company_material_revisions WHERE object_id=:id"), {"id": oid}
            )
            conn.execute(
                text("DELETE FROM company_profile_revisions WHERE object_id=:id"), {"id": oid}
            )
            conn.execute(
                text("UPDATE company_objects SET purge_state='purged' WHERE object_id=:id"),
                {"id": oid},
            )
        purged.append(oid)
    audit(db, "company_retention_pruned", purged_count=len(purged), failed_object_ids=failures)
    return {
        "status": "FAIL" if failures else "PASS",
        "purged_ids": purged,
        "failure_ids": failures,
        "purged_result_ids": derived["purged_ids"],
    }
