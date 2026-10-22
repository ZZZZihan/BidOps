"""Read-only domain queries. No model-generated values, SQL, URLs or evidence."""

from datetime import datetime
from decimal import Decimal

from bidops.agent_demo.schemas import Block, Card, Citation, Focus, Query
from bidops.models import Notice
from bidops.parser import canonical_text
from bidops.storage import Store


class Catalog:
    def __init__(self, store: Store):
        self.notices = [
            Notice.model_validate_json(row["payload_json"])
            for row in store.db.rows(
                "SELECT payload_json FROM notices ORDER BY notice_id LIMIT 501"
            )
        ]
        if len(self.notices) > 500:
            raise ValueError("demo_catalog_limit_exceeded")
        for notice in self.notices:
            source = notice.source
            raw = (store.raw / source.sha256).read_bytes()
            store.validate_snapshot(source, raw)
            normalized = canonical_text(raw, source.content_type, source.encoding)
            spans = [s for group in notice.field_evidence.values() for s in group]
            spans += notice.eligibility_conditions + [p.evidence for p in notice.packages]
            if any(
                s.source_snapshot_id != source.snapshot_id
                or s.locator != "normalized_text_v2"
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
        if (
            query.min_budget_yuan is not None
            and query.max_budget_yuan is not None
            and query.min_budget_yuan > query.max_budget_yuan
        ):
            raise ValueError("minimum_budget_exceeds_maximum")
        if query.deadline_from and query.deadline_to and query.deadline_from > query.deadline_to:
            raise ValueError("invalid_deadline_range")
        result = []
        words = query.keyword.casefold().split()
        for n in self.notices:
            haystack = " ".join(
                [n.project_code or "", n.title or "", n.buyer_name or ""]
            ).casefold()
            if not all(word in haystack for word in words):
                continue
            amount = Decimal(n.budget.amount) if n.budget else None
            if query.min_budget_yuan is not None:
                if amount is None or amount < Decimal(str(query.min_budget_yuan)):
                    continue
                if not query.min_budget_inclusive and amount == Decimal(str(query.min_budget_yuan)):
                    continue
            if query.max_budget_yuan is not None:
                if amount is None or amount > Decimal(str(query.max_budget_yuan)):
                    continue
                if not query.max_budget_inclusive and amount == Decimal(str(query.max_budget_yuan)):
                    continue
            deadline = datetime.fromisoformat(n.bid_deadline_at) if n.bid_deadline_at else None
            if query.deadline_from and (deadline is None or deadline < query.deadline_from):
                continue
            if query.deadline_to and (deadline is None or deadline > query.deadline_to):
                continue
            if query.future_deadline_only and (deadline is None or deadline <= now):
                continue
            result.append(n)

        def sort_value(n):
            return {
                "project_code": n.project_code,
                "budget": Decimal(n.budget.amount) if n.budget else None,
                "deadline": n.bid_deadline_at,
                "publication": n.published_at or n.published_date,
            }[query.sort_by]

        known = [n for n in result if sort_value(n) is not None]
        unknown = [n for n in result if sort_value(n) is None]
        known.sort(key=lambda n: (sort_value(n), n.notice_id), reverse=query.descending)
        selected = (known + unknown)[: query.limit]
        return Block(
            id="",
            operation="search",
            total=len(result),
            items=[self.card(n, "budget") for n in selected],
        )

    def read(self, project_code: str, focus: Focus) -> Block:
        code = project_code.strip().casefold()
        found = [n for n in self.notices if (n.project_code or "").casefold() == code]
        return Block(
            id="",
            operation="read",
            total=len(found),
            items=[self.card(n, focus) for n in found],
            missing_project_codes=[] if found else [project_code],
        )

    def compare(self, project_codes: list[str], metric: str) -> Block:
        codes = list(dict.fromkeys(code.strip().upper() for code in project_codes))
        if not 2 <= len(codes) <= 6:
            raise ValueError("comparison_requires_2_to_6_distinct_projects")
        matches = [
            self.read(code, "packages" if metric == "packages" else "budget") for code in codes
        ]
        # Multiple versions cannot silently be treated as one current project.
        if any(len(m.items) > 1 for m in matches):
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
