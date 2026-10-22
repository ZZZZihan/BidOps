"""Deterministic, fixed-batch triage; no silent preload or model input limit.

All project/package units, including unassociated and incomplete records, survive
in the batch. Filters are user-selected triage criteria, never a qualification verdict.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import AwareDatetime, Field, StrictInt, StrictStr, ValidationError, model_validator
from sqlalchemy import bindparam, text

from bidops.company.authorization import CompanyError
from bidops.qualification.contracts import Digest, QualificationModel
from bidops.qualification.sources import digest
from bidops.screening.schemas import VersionRef


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


class _CandidateItem(QualificationModel):
    unit_id: Digest
    project_id: StrictStr | None
    package_id: StrictStr | None
    title: StrictStr
    package_label: StrictStr | None
    budget_yuan: StrictStr | None
    disposition: Literal["candidate", "excluded", "needs_verification"]
    reasons: list[StrictStr] = Field(min_length=1)
    missing_fields: list[StrictStr]
    notice_revisions: list[VersionRef] = Field(min_length=1)


class _CandidateCounts(QualificationModel):
    total_units: StrictInt = Field(ge=0)
    candidate: StrictInt = Field(ge=0)
    excluded: StrictInt = Field(ge=0)
    needs_verification: StrictInt = Field(ge=0)


class _PublicVersion(QualificationModel):
    version: VersionRef
    payload_sha256: Digest
    text_sha256: Digest
    available_at: AwareDatetime


class _CandidateBatch(QualificationModel):
    kind: Literal["candidate_batch"]
    as_of: AwareDatetime
    profile_revision: VersionRef
    filters: CandidateFilters
    items: list[_CandidateItem]
    counts: _CandidateCounts
    public_versions: list[_PublicVersion]
    qualification_checked: Literal[False]
    manifest_sha256: Digest


def _validate_stored_inventory(notices, batch):
    """Bind each fixed public version and every one of its package units.

    Versions ingested after build_batch's read transaction do not change that
    snapshot. For its recorded versions, however, no package or reference may
    be silently omitted or reassociated. Chunking also avoids SQLite's bind cap.
    """
    versions = {row.version.version_id: row for row in batch.public_versions}
    if len(versions) != len(batch.public_versions):
        raise ValueError("candidate_duplicate_version")
    expected, seen = defaultdict(set), set()
    project_ids = {}
    statement = text(
        "SELECT v.version_id,v.logical_notice_id,v.raw_sha256,v.text_sha256,"
        "v.payload_sha256,v.available_at,v.project_id,p.package_id "
        "FROM nd_versions v LEFT JOIN nd_package_versions p ON p.version_id=v.version_id "
        "WHERE v.version_id IN :ids AND v.available_at<=:at AND EXISTS "
        "(SELECT 1 FROM nd_observations o WHERE o.snapshot_id=v.snapshot_id "
        "AND o.observed_at<=:at)"
    ).bindparams(bindparam("ids", expanding=True))
    ids = list(versions)
    with notices.db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN")
        for offset in range(0, len(ids), 900):
            for row in conn.execute(
                statement, {"ids": ids[offset : offset + 900], "at": batch.as_of.timestamp()}
            ).mappings():
                stored = versions[row["version_id"]]
                ref_key = (row["logical_notice_id"], row["version_id"], row["raw_sha256"])
                if (
                    stored.version.key() != ref_key
                    or stored.text_sha256 != row["text_sha256"]
                    or stored.payload_sha256 != row["payload_sha256"]
                    or stored.available_at.timestamp() != row["available_at"]
                ):
                    raise ValueError("candidate_stored_version_binding")
                seen.add(row["version_id"])
                project = row["project_id"] or "unassociated:" + row["logical_notice_id"]
                unit = digest([project, row["package_id"]])
                expected[unit].add(ref_key)
                project_ids[unit] = row["project_id"]
    if seen != set(versions) or set(expected) != {item.unit_id for item in batch.items}:
        raise ValueError("candidate_stored_unit_inventory")
    for item in batch.items:
        if project_ids[item.unit_id] != item.project_id or expected[item.unit_id] != {
            ref.key() for ref in item.notice_revisions
        }:
            raise ValueError("candidate_stored_unit_binding")


def validate_candidate_batch(manifest, result, notices):
    """Validate the sole larger-storage shape, without inventing qualification.

    This is a server-side persistence contract, not an execution or model-output
    validator. The service constructs this result from the complete fixed corpus.
    Unknown fields, arbitrary bodies and raw model output have no place here.
    """
    try:
        if type(result) is not dict or result.get("qualification_checked") is not False:
            raise ValueError("candidate_shape")
        batch = _CandidateBatch.model_validate(result)
        if (
            manifest.project_id is not None
            or batch.as_of != manifest.as_of
            or batch.profile_revision != manifest.profile_revision
            or result["filters"] != batch.filters.model_dump(mode="json")
        ):
            raise ValueError("candidate_private_binding")
        versions = {row.version.key() for row in batch.public_versions}
        if len(versions) != len(batch.public_versions) or any(
            row.available_at > batch.as_of for row in batch.public_versions
        ):
            raise ValueError("candidate_public_closure")
        unit_ids, used_versions = set(), set()
        counts = {name: 0 for name in ("candidate", "excluded", "needs_verification")}
        missing_codes = {
            "project_association",
            "title",
            "package",
            "budget",
            "budget_version_conflict",
            "revision_precedence",
        }
        filter_codes = {"keyword_mismatch", "below_min_budget", "above_max_budget"}
        for item in batch.items:
            refs = {ref.key() for ref in item.notice_revisions}
            project_key = item.project_id or ("unassociated:" + item.notice_revisions[0].object_id)
            if (
                item.unit_id in unit_ids
                or item.unit_id != digest([project_key, item.package_id])
                or len(refs) != len(item.notice_revisions)
                or not refs <= versions
                or not item.title
                or not set(item.missing_fields) <= missing_codes
                or len(set(item.missing_fields)) != len(item.missing_fields)
                or len(set(item.reasons)) != len(item.reasons)
            ):
                raise ValueError("candidate_unit_binding")
            if item.budget_yuan is not None:
                amount = Decimal(item.budget_yuan)
                if not amount.is_finite() or amount < 0:
                    raise ValueError("candidate_budget")
            if item.disposition == "candidate":
                if item.missing_fields or item.reasons != ["matches_explicit_filters"]:
                    raise ValueError("candidate_disposition")
            elif item.disposition == "excluded":
                if (
                    not set(item.reasons) <= filter_codes
                    or "revision_precedence" in item.missing_fields
                ):
                    raise ValueError("candidate_disposition")
            elif (
                not item.missing_fields
                or not set(item.missing_fields) <= set(item.reasons)
                or not set(item.reasons) <= missing_codes | filter_codes
            ):
                raise ValueError("candidate_disposition")
            unit_ids.add(item.unit_id)
            used_versions.update(refs)
            counts[item.disposition] += 1
        if used_versions != versions or batch.counts.model_dump() != {
            "total_units": len(batch.items),
            **counts,
        }:
            raise ValueError("candidate_counts_or_public_closure")
        expected = digest(
            {
                "private": manifest.closure_sha256,
                **{key: value for key, value in result.items() if key != "manifest_sha256"},
            }
        )
        if batch.manifest_sha256 != expected:
            raise ValueError("candidate_manifest_hash")
        # The persisted JSON representation must remain an aware ISO timestamp.
        if (
            type(result["as_of"]) is not str
            or datetime.fromisoformat(result["as_of"]).tzinfo is None
        ):
            raise ValueError("candidate_timestamp")
        _validate_stored_inventory(notices, batch)
    except (ValidationError, ValueError, TypeError, InvalidOperation, KeyError):
        raise CompanyError("screening_candidate_batch_invalid", 422) from None


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
