"""A restore blocks paid calls until deployment explicitly acknowledges reconciliation.

The token is an acknowledgement marker, not proof that missing usage was recovered.
No ledger rows or historical cost/reservation values are changed here.
"""

import hmac
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path


class BudgetReviewRequired(RuntimeError):
    def __init__(self):
        super().__init__("budget_review_required")


def require_budget_review(conn, ack_token: str | None) -> None:
    """Check in the same SQLAlchemy transaction that admits the paid reservation."""
    present = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_review_barrier'"
    ).first()
    if not present:
        return  # Fresh databases do not inherit an unverifiable restored usage interval.
    rows = conn.exec_driver_sql("SELECT id,restore_token FROM budget_review_barrier").all()
    if len(rows) != 1 or rows[0][0] != 1:
        raise BudgetReviewRequired()
    expected = rows[0][1]
    if (
        not isinstance(expected, str)
        or not re.fullmatch(r"[a-f0-9]{32}", expected)
        or not isinstance(ack_token, str)
        or not re.fullmatch(r"[a-f0-9]{32}", ack_token)
        or not hmac.compare_digest(expected, ack_token)
    ):
        raise BudgetReviewRequired()


def install_budget_review_barrier(db_path: Path) -> str:
    """Rotate every staged restore, including versions that predate the usage ledger."""
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS budget_review_barrier ("
            "id INTEGER PRIMARY KEY CHECK (id=1),restore_token TEXT NOT NULL,"
            "created_at TEXT NOT NULL)"
        )
        token = uuid.uuid4().hex
        conn.execute("DELETE FROM budget_review_barrier")
        conn.execute(
            "INSERT INTO budget_review_barrier(id,restore_token,created_at) VALUES (1,?,?)",
            (token, datetime.now(UTC).isoformat()),
        )
        return token
