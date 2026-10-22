"""Read-only domain queries. No model-generated values, SQL, URLs or evidence."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from bidops.agent_demo.schemas import Block, Card, Citation, Focus, Query
from bidops.catalog_query import query_notices
from bidops.models import Notice
from bidops.parser import canonical_text
from bidops.storage import Store


class Catalog:
    def __init__(self, store: Store):
        self.store = store
        # Verify the full legacy projection without retaining a capped preload.
        # Results are verified again when released, including changes after init.
        with store.db.engine.connect() as conn:
            for payload in conn.execute(
                text("SELECT payload_json FROM notices ORDER BY notice_id")
            ).scalars():
                self.verify_notice(Notice.model_validate_json(payload))

    @property
    def count(self) -> int:
        return self.store.db.rows("SELECT COUNT(*) AS n FROM notices")[0]["n"]

    def verify_notice(self, notice: Notice) -> None:
        source = notice.source
        path = self.store.raw / source.sha256
        if path.is_symlink():
            raise ValueError("evidence_integrity_failed_reparse_required")
        raw = path.read_bytes()
        self.store.validate_snapshot(source, raw)
        normalized = canonical_text(raw, source.content_type, source.encoding)
        spans = [s for group in notice.field_evidence.values() for s in group]
        spans += notice.eligibility_conditions + [p.evidence for p in notice.packages]
        if any(
            s.source_snapshot_id != source.snapshot_id
            or s.locator != "normalized_text_v2"
            or s.start < 0
            or not s.start < s.end <= len(normalized)
            or normalized[s.start : s.end] != s.text
            for s in spans
        ):
            raise ValueError("evidence_integrity_failed_reparse_required")

    @staticmethod
    def card(notice: Notice, focus: Focus = "overview") -> Card:
        groups = dict(notice.field_evidence)
        if focus in {"eligibility", "overview"}:
            groups["eligibility"] = notice.eligibility_conditions
        if focus in {"packages", "overview"}:
            groups["packages"] = [p.evidence for p in notice.packages]
        citations = []
        for field, spans in groups.items():
            for index, span in enumerate(spans):
                citations.append(
                    Citation(
                        id=f"{notice.notice_id}:{field}:{index}",
                        project_code=notice.project_code,
                        field=field,
                        source_url=notice.source.source_url,
                        snapshot_id=notice.notice_id,
                        sha256=notice.source.sha256,
                        quote=span.text,
                        start=span.start,
                        end=span.end,
                        locator=span.locator,
                    )
                )
        return Card(
            project_code=notice.project_code,
            title=notice.title,
            buyer_name=notice.buyer_name,
            budget_yuan=notice.budget.amount if notice.budget else None,
            bid_deadline_at=notice.bid_deadline_at,
            published_at=notice.published_at or notice.published_date,
            document_deadline_at=notice.document_deadline_at,
            notice_kind=notice.notice_kind,
            package_count=len(notice.packages),
            packages=[
                {"label": p.label, "budget_yuan": p.budget.amount if p.budget else None}
                for p in notice.packages
            ]
            if focus in {"packages", "overview"}
            else [],
            eligibility=[e.text for e in notice.eligibility_conditions]
            if focus in {"eligibility", "overview"}
            else [],
            source_url=notice.source.source_url,
            snapshot_id=notice.notice_id,
            source_observed_at=notice.source.fetched_at.isoformat(),
            citations=citations,
        )

    def search(self, query: Query, now: datetime) -> Block:
        page = query_notices(self.store.db, query, now)
        for notice in page["items"]:
            self.verify_notice(notice)
        return Block(
            id="",
            operation="search",
            **{**page, "items": [self.card(n, "budget") for n in page["items"]]},
        )

    def read(self, project_code: str, focus: Focus, *, offset: int = 0) -> Block:
        from datetime import UTC

        page = query_notices(
            self.store.db,
            Query(offset=offset),
            datetime.now(UTC),
            limit=100,
            exact_code=project_code,
        )
        for notice in page["items"]:
            self.verify_notice(notice)
        return Block(
            id="",
            operation="read",
            **{**page, "items": [self.card(n, focus) for n in page["items"]]},
            missing_project_codes=[] if page["total"] else [project_code],
        )

    def compare(self, project_codes: list[str], metric: str) -> Block:
        codes = list(dict.fromkeys(code.strip().upper() for code in project_codes))
        if not 2 <= len(codes) <= 6:
            raise ValueError("comparison_requires_2_to_6_distinct_projects")
        matches = [
            self.read(code, "packages" if metric == "packages" else "budget") for code in codes
        ]
        # Multiple versions cannot silently be treated as one current project.
        if any(m.total > 1 for m in matches):
            raise ValueError("multiple_versions_require_review")
        items = [item for match in matches for item in match.items]
        metrics = {}
        if len(items) == len(codes) and metric == "budget" and all(c.budget_yuan for c in items):
            ordered = sorted(items, key=lambda c: Decimal(c.budget_yuan))
            metrics = {
                "lowest_budget_project": ordered[0].project_code,
                "highest_budget_project": ordered[-1].project_code,
                "budget_difference_yuan": str(
                    Decimal(ordered[-1].budget_yuan) - Decimal(ordered[0].budget_yuan)
                ),
                "budget_sum_yuan": str(sum(Decimal(c.budget_yuan) for c in items)),
            }
        if (
            len(items) == len(codes)
            and metric == "deadline"
            and all(c.bid_deadline_at for c in items)
        ):
            ordered = sorted(items, key=lambda c: datetime.fromisoformat(c.bid_deadline_at))
            metrics = {
                "earliest_deadline_project": ordered[0].project_code,
                "latest_deadline_project": ordered[-1].project_code,
            }
        if len(items) == len(codes) and metric == "packages":
            metrics = {"total_packages": str(sum(c.package_count for c in items))}
        return Block(
            id="",
            operation=f"compare_{metric}",
            total=len(items),
            items=items,
            metrics=metrics,
            missing_project_codes=[c for m in matches for c in m.missing_project_codes],
        )


def model_view(block: Block) -> dict:
    """Small untrusted tool data view; full quotes stay in the server evidence ledger."""
    data = block.model_dump(mode="json")
    for item in data["items"]:
        item["citations"] = [
            {"id": c["id"], "field": c["field"], "quote": c["quote"][:300]}
            for c in item["citations"][:12]
        ]
        item["eligibility"] = [quote[:700] for quote in item["eligibility"][:3]]
    return data
