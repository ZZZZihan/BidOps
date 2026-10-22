"""Deterministic, fixed-batch triage; no silent preload or model input limit.

All project/package units, including unassociated and incomplete records, survive
in the batch. Filters are user-selected triage criteria, never a qualification verdict.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from pydantic import Field, model_validator
from sqlalchemy import text

from bidops.qualification.contracts import QualificationModel
from bidops.qualification.sources import digest


class CandidateFilters(QualificationModel):
    keyword: str = Field(default="", max_length=120)
    min_budget_yuan: Decimal | None = Field(default=None, ge=0)
    max_budget_yuan: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bounds(self):
        if self.min_budget_yuan is not None and self.max_budget_yuan is not None:
            if self.min_budget_yuan > self.max_budget_yuan:
                raise ValueError("budget_bounds_reversed")
        return self


def _money(value):
    if value is None:
        return None
    try:
        amount = Decimal(value.amount)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or value.currency != "CNY":
        return None
    # The parser's amount is already normalized to yuan; unit retains source text.
    return amount


def build_batch(adapter, bundle, filters):
    filters = CandidateFilters.model_validate(filters)
    notices = adapter.notices
    # One read transaction fixes the complete set. Paging happens only after saving.
    with notices.db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN")
        ids = (
            conn.execute(
                text(
                    "SELECT version_id FROM nd_versions v WHERE available_at<=:at AND EXISTS "
                    "(SELECT 1 FROM nd_observations o WHERE o.snapshot_id=v.snapshot_id "
                    "AND observed_at<=:at) ORDER BY version_id"
                ),
                {"at": bundle.manifest.as_of.timestamp()},
            )
            .scalars()
            .all()
        )
        versions = [notices._get_version(conn, key, as_of=bundle.manifest.as_of) for key in ids]
    units = defaultdict(list)
    project_versions = defaultdict(set)
    for version in versions:
        # Null association never merges unrelated notices into one fake project.
        project_key = version.project_id or "unassociated:" + version.logical_notice_id
        project_versions[project_key].add(version.version_id)
        if version.packages:
            for package in version.packages:
                units[(project_key, package.package_id)].append((version, package))
        else:
            units[(project_key, None)].append((version, None))
    items = []
    for (project_key, package_id), rows in sorted(units.items(), key=lambda v: str(v[0])):
        missing, excluded = set(), []
        titles, budgets = [], []
        for version, package in rows:
            notice = version.notice
            titles.append(notice.title or "未识别标题")
            if version.project_id is None:
                missing.add("project_association")
            if not notice.title:
                missing.add("title")
            if package is None:
                missing.add("package")
                budgets.append(_money(notice.budget))
            else:
                matches = [p for p in notice.packages if p.label == package.label]
                budgets.append(_money(matches[0].budget) if len(matches) == 1 else None)
        # Mixed history is retained; conflicting or absent amounts never become zero.
        if any(v is None for v in budgets):
            missing.add("budget")
        known_budgets = {v for v in budgets if v is not None}
        if len(known_budgets) > 1:
            missing.add("budget_version_conflict")
        logical = {v.logical_notice_id for v, _ in rows}
        if len(project_versions[project_key]) > 1 or len(logical) > 1:
            missing.add("revision_precedence")
        keyword = filters.keyword.strip().casefold()
        searchable = "\n".join(
            " ".join(
                [
                    v.notice.title or "",
                    v.notice.project_code or "",
                    v.notice.buyer_name or "",
                    p.label if p else "",
                ]
            )
            for v, p in rows
        ).casefold()
        if keyword and keyword not in searchable and "title" not in missing:
            excluded.append("keyword_mismatch")
        amount = (
            next(iter(known_budgets))
            if len(known_budgets) == 1 and "budget" not in missing
            else None
        )
        if amount is not None:
            if filters.min_budget_yuan is not None and amount < filters.min_budget_yuan:
                excluded.append("below_min_budget")
            if filters.max_budget_yuan is not None and amount > filters.max_budget_yuan:
                excluded.append("above_max_budget")
        # Ambiguous revisions cannot be conclusively excluded using an old field.
        if excluded and "revision_precedence" not in missing:
            disposition, reasons = "excluded", excluded
        elif missing:
            disposition, reasons = "needs_verification", sorted(missing) + excluded
        else:
            disposition, reasons = "candidate", ["matches_explicit_filters"]
        items.append(
            {
                "unit_id": digest([project_key, package_id]),
                "project_id": rows[0][0].project_id,
                "package_id": package_id,
                "title": " / ".join(dict.fromkeys(titles)),
                "package_label": rows[0][1].label if rows[0][1] else None,
                "budget_yuan": str(amount) if amount is not None else None,
                "disposition": disposition,
                "reasons": reasons,
                "missing_fields": sorted(missing),
                "notice_revisions": [v.version_ref.model_dump(mode="json") for v, _ in rows],
            }
        )
    public_versions = [
        {
            "version": v.version_ref.model_dump(mode="json"),
            "payload_sha256": v.payload_sha256,
            "text_sha256": v.text_sha256,
            "available_at": v.available_at.isoformat(),
        }
        for v in versions
    ]
    result = {
        "kind": "candidate_batch",
        "as_of": bundle.manifest.as_of.isoformat(),
        "profile_revision": bundle.manifest.profile_revision.model_dump(mode="json"),
        "filters": filters.model_dump(mode="json"),
        "items": items,
        "counts": {
            "total_units": len(items),
            **{
                state: sum(item["disposition"] == state for item in items)
                for state in ("candidate", "excluded", "needs_verification")
            },
        },
        "public_versions": public_versions,
        "qualification_checked": False,
    }
    result["manifest_sha256"] = digest({"private": bundle.manifest.closure_sha256, **result})
    return result


def page_batch(record, offset=0, limit=20):
    from bidops.company.authorization import CompanyError

    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise CompanyError("screening_invalid_pagination", 422)
    result = record["result"]
    if result["kind"] != "candidate_batch":
        from bidops.company.authorization import CompanyError

        raise CompanyError("screening_batch_not_found")
    total = len(result["items"])
    return {
        "batch_id": record["result_id"],
        "manifest_sha256": result["manifest_sha256"],
        "as_of": result["as_of"],
        "profile_revision": result["profile_revision"],
        "filters": result["filters"],
        "counts": result["counts"],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "truncated": offset > 0 or limit < total,
        "items": result["items"][offset : offset + limit],
        "qualification_checked": False,
        "source_state": record["source_state"],
    }
