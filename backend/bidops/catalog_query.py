"""Full-database, parameterized notice queries with exact money ordering.

Limits bound the returned page, never the set searched or counted. SQLite's
decimal collation compares the stored decimal strings without REAL rounding.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from bidops.agent_demo.schemas import Query
from bidops.models import Notice


def decimal_compare(left: str, right: str) -> int:
    a, b = Decimal(left), Decimal(right)
    if not a.is_finite() or not b.is_finite():
        raise ValueError("invalid_stored_money")
    return (a > b) - (a < b)


def epoch(value: str | None) -> float | None:
    if value is None:
        return None
    date = datetime.fromisoformat(value)
    # A publication date is intentionally date-only, unlike a bid deadline.
    if date.tzinfo is None:
        if len(value) != 10:
            raise ValueError("stored_datetime_requires_timezone")
        date = date.replace(tzinfo=UTC)
    return date.timestamp()


def register_query_functions(connection):
    connection.create_function("bidops_fold", 1, lambda s: (s or "").casefold(), deterministic=True)
    connection.create_function("bidops_epoch", 1, epoch, deterministic=True)
    connection.create_collation("bidops_decimal", decimal_compare)


def query_notices(db, query: Query, now: datetime, *, limit=None, exact_code=None) -> dict:
    """Read a consistent count and page, ordered with a unique ID tie-breaker."""
    query = Query.model_validate(query.model_dump())
    limit = query.limit if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("page_limit_out_of_range")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("explicit_timezone_required")
    if (
        query.min_budget_yuan is not None
        and query.max_budget_yuan is not None
        and query.min_budget_yuan > query.max_budget_yuan
    ):
        raise ValueError("minimum_budget_exceeds_maximum")
    if query.deadline_from and query.deadline_to and query.deadline_from > query.deadline_to:
        raise ValueError("invalid_deadline_range")
    money = "CAST(json_extract(payload_json,'$.budget.amount') AS TEXT) COLLATE bidops_decimal"
    deadline = "bidops_epoch(json_extract(payload_json,'$.bid_deadline_at'))"
    publication = (
        "bidops_epoch(COALESCE(json_extract(payload_json,'$.published_at'),"
        "json_extract(payload_json,'$.published_date')))"
    )
    filters = []
    params = {"limit": limit, "offset": query.offset}
    if exact_code is not None:
        filters.append("bidops_fold(project_code)=:code")
        params["code"] = exact_code.strip().casefold()
    for index, word in enumerate(query.keyword.casefold().split()):
        filters.append(
            "instr(bidops_fold(COALESCE(project_code,'') || ' ' || "
            "COALESCE(json_extract(payload_json,'$.title'),'') || ' ' || "
            f"COALESCE(json_extract(payload_json,'$.buyer_name'),'')),:word{index})>0"
        )
        params[f"word{index}"] = word
    for kind, value, inclusive in (
        ("min", query.min_budget_yuan, query.min_budget_inclusive),
        ("max", query.max_budget_yuan, query.max_budget_inclusive),
    ):
        if value is not None:
            operator = (">" if kind == "min" else "<") + ("=" if inclusive else "")
            filters.append(f"{money} {operator} :{kind}")
            params[kind] = str(value)
    for name, value, operator in (
        ("deadline_from", query.deadline_from, ">="),
        ("deadline_to", query.deadline_to, "<="),
        ("future", now if query.future_deadline_only else None, ">"),
    ):
        if value is not None:
            filters.append(f"{deadline}{operator}:{name}")
            params[name] = value.timestamp()
    field = {
        "project_code": "project_code",
        "budget": money,
        "deadline": deadline,
        "publication": publication,
    }[query.sort_by]
    direction = "DESC" if query.descending else "ASC"
    where = " WHERE " + " AND ".join(filters) if filters else ""
    # Explicit BEGIN is needed for a read snapshot with sqlite3 legacy transaction
    # mode: count and rows must not straddle a concurrent ingest.
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN")
        total = conn.execute(text("SELECT COUNT(*) FROM notices" + where), params).scalar_one()
        rows = conn.execute(
            text(
                "SELECT payload_json FROM notices"
                + where
                + f" ORDER BY ({field}) IS NULL, {field} {direction}, notice_id {direction}"
                + " LIMIT :limit OFFSET :offset"
            ),
            params,
        ).scalars()
        items = [Notice.model_validate_json(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": query.offset,
        "has_more": query.offset + len(items) < total,
        "truncated": len(items) < total,
    }
