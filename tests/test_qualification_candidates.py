"""Full SQLite candidate batches and immutable pages; synthetic, zero model calls."""

import copy
from datetime import timedelta
from decimal import Decimal

import pytest
from bidops.company.authorization import CompanyError
from bidops.notice_data import NoticeRepository
from bidops.qualification.candidates import CandidateFilters, build_batch, page_batch
from bidops.qualification.sources import SourceAdapter
from pydantic import ValidationError
from test_company_access import ALICE, payload
from test_company_access import domain as _domain_fixture
from test_notice_versions import AT, persist

domain = _domain_fixture


@pytest.fixture
def candidates(domain, settings):
    raw = settings.data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    adapter = SourceAdapter(domain, NoticeRepository(domain.db, raw))
    profile = domain.repo.put(ALICE, payload())
    bundle = adapter.resolve_profile(ALICE, profile, AT + timedelta(days=1))
    return adapter, bundle


def body(code="BATCH-001", title="设备采购", budget="10元", *, packages=True, buyer="示例采购中心"):
    amount = "" if budget is None else f"预算金额：{budget}\n"
    return (
        f"项目编号：{code}\n项目名称：{title}\n采购人名称：{buyer}\n公告类型：招标公告\n"
        "投标截止时间：2026年11月30日10:00\n" + ("第1包\n" if packages else "") + amount
    )


def record(result):
    return {
        "result_id": "fixed-batch",
        "result": result,
        "source_state": {"current": True, "stale_reasons": []},
    }


def test_full_batch_exceeds_old_five_hundred_limit_and_pages_every_unit_once(candidates):
    adapter, bundle = candidates
    for index in range(507):
        persist(
            adapter.notices,
            body(code=f"UNIT-BATCH-{index:04d}", title=f"工程样例{index}"),
            path=f"batch-{index:04d}",
        )
    result = build_batch(adapter, bundle, CandidateFilters(keyword="UNIT-BATCH-0506"))
    assert result["counts"] == {
        "total_units": 507,
        "candidate": 1,
        "excluded": 506,
        "needs_verification": 0,
    }
    assert len(result["public_versions"]) == 507
    matched = [item for item in result["items"] if item["disposition"] == "candidate"]
    assert matched[0]["title"] == "工程样例506"
    saved = record(result)
    first_id, all_ids = result["manifest_sha256"], []
    for offset in range(0, 507, 23):
        page = page_batch(saved, offset=offset, limit=23)
        assert page["manifest_sha256"] == first_id
        assert page["total"] == 507
        assert page["has_more"] is (offset + len(page["items"]) < 507)
        assert page["qualification_checked"] is False
        all_ids.extend(item["unit_id"] for item in page["items"])
    assert len(all_ids) == len(set(all_ids)) == 507
    final = page_batch(saved, offset=1000, limit=23)
    assert final["items"] == [] and not final["has_more"]
    assert (
        sum(result["counts"][key] for key in ("candidate", "excluded", "needs_verification")) == 507
    )


@pytest.mark.parametrize("packages", [True, False])
def test_money_is_already_normalized_to_yuan_by_parser(candidates, packages):
    adapter, bundle = candidates
    value = persist(adapter.notices, body(budget="1万元", packages=packages))
    stored = value.notice.packages[0].budget if packages else value.notice.budget
    assert stored.amount == "10000.00" and stored.unit == "万元"
    result = build_batch(
        adapter, bundle, CandidateFilters(min_budget_yuan="9999", max_budget_yuan="10001")
    )
    item = result["items"][0]
    assert Decimal(item["budget_yuan"]) == Decimal("10000.00")
    assert "above_max_budget" not in item["reasons"]
    assert item["disposition"] == ("candidate" if packages else "needs_verification")


def test_unknown_budget_is_retained_for_verification_not_compared_to_zero(candidates):
    adapter, bundle = candidates
    persist(adapter.notices, body(budget=None))
    result = build_batch(adapter, bundle, CandidateFilters(min_budget_yuan=1))
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["budget_yuan"] is None
    assert item["disposition"] == "needs_verification"
    assert "budget" in item["missing_fields"]
    assert "below_min_budget" not in item["reasons"]


def test_missing_and_ambiguous_association_remain_distinct_visible_units(candidates):
    adapter, bundle = candidates
    for path in ("uncertain-a", "uncertain-b"):
        persist(adapter.notices, "项目名称：疑似同名项目\n第1包\n预算金额：10元", path=path)
    persist(adapter.notices, body(code="BATCH-001"), path="buyer-a")
    persist(adapter.notices, body(code="BATCH-001", buyer="另一采购单位"), path="buyer-b")
    result = build_batch(adapter, bundle, CandidateFilters())
    assert result["counts"]["total_units"] == 4
    unassociated = [item for item in result["items"] if item["project_id"] is None]
    assert len(unassociated) == 2
    assert len({item["unit_id"] for item in result["items"]}) == 4
    assert all("project_association" in item["missing_fields"] for item in unassociated)
    assert all(item["disposition"] == "needs_verification" for item in unassociated)


def test_changed_budget_and_nonmatching_revision_never_exclude_from_old_field(candidates):
    adapter, bundle = candidates
    original = persist(adapter.notices, body(budget="1元"))
    updated = persist(
        adapter.notices, body(budget="100元", title="新名称"), at=AT + timedelta(hours=1)
    )
    assert original.logical_notice_id == updated.logical_notice_id
    result = build_batch(
        adapter, bundle, CandidateFilters(keyword="完全不匹配", min_budget_yuan=50)
    )
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["disposition"] == "needs_verification"
    assert item["budget_yuan"] is None
    assert {"revision_precedence", "budget_version_conflict"} <= set(item["missing_fields"])
    assert len(item["notice_revisions"]) == 2
    assert "keyword_mismatch" in item["reasons"]


def test_fixed_page_hash_and_contents_survive_later_ingest(candidates):
    adapter, bundle = candidates
    persist(adapter.notices, body())
    original = build_batch(adapter, bundle, CandidateFilters())
    saved = record(copy.deepcopy(original))
    before = page_batch(saved)
    persist(adapter.notices, body(code="NEW", title="新增项目"), path="new")
    current = build_batch(adapter, bundle, CandidateFilters())
    assert current["counts"]["total_units"] == 2
    assert current["manifest_sha256"] != original["manifest_sha256"]
    assert page_batch(saved) == before
    assert before["total"] == 1


def test_as_of_excludes_later_observation_and_later_reparse(candidates):
    adapter, bundle = candidates
    original = persist(adapter.notices, body())
    persist(
        adapter.notices,
        body(code="FUTURE"),
        path="future",
        at=bundle.manifest.as_of + timedelta(seconds=1),
    )
    snapshot, notice, normalized = persist(adapter.notices, body(), capture=False)
    notice.parse_warnings.append("new_parser_interpretation")
    adapter.notices.capture(
        snapshot, notice, normalized, interpreted_at=bundle.manifest.as_of + timedelta(seconds=2)
    )
    result = build_batch(adapter, bundle, CandidateFilters())
    assert result["counts"]["total_units"] == 1
    assert [row["version"]["version_id"] for row in result["public_versions"]] == [
        original.version_id
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_budget_yuan": "NaN"},
        {"max_budget_yuan": "Infinity"},
        {"min_budget_yuan": -1},
        {"min_budget_yuan": 2, "max_budget_yuan": 1},
        {"keyword": "x" * 121},
    ],
)
def test_invalid_filters_cannot_change_database_query(kwargs):
    with pytest.raises(ValidationError):
        CandidateFilters(**kwargs)


@pytest.mark.parametrize(
    "offset,limit", [(-1, 20), (True, 20), (0, 0), (0, -1), (0, 101), (0, True), (0.5, 20)]
)
def test_page_rejects_invalid_bounds_as_deterministic_request_error(offset, limit):
    saved = record(
        {
            "kind": "candidate_batch",
            "items": [],
            "manifest_sha256": "fixed",
            "as_of": AT.isoformat(),
            "profile_revision": {},
            "filters": {},
            "counts": {},
        }
    )
    with pytest.raises(CompanyError):
        page_batch(saved, offset=offset, limit=limit)


def test_page_rejects_checklist_kind():
    with pytest.raises(CompanyError, match="screening_batch_not_found"):
        page_batch(record({"kind": "checklist"}))


def test_package_set_changed_between_versions_is_ambiguous_for_every_project_unit(candidates):
    adapter, bundle = candidates
    old = persist(adapter.notices, body(budget="1元"))
    new = persist(
        adapter.notices, body(budget="100元").replace("第1包", "第2包"), at=AT + timedelta(hours=1)
    )
    assert old.project_id == new.project_id
    result = build_batch(adapter, bundle, CandidateFilters(min_budget_yuan=50))
    assert len(result["items"]) == 2
    assert all(item["disposition"] == "needs_verification" for item in result["items"])
    assert all("revision_precedence" in item["missing_fields"] for item in result["items"])


def test_candidate_final_release_cannot_lose_an_earlier_private_source(candidates, monkeypatch):
    from bidops.qualification.config import ScreeningSettings
    from bidops.qualification.service import ScreeningService
    from test_company_access import BOB, supported_profile

    adapter, _ = candidates
    document, profile, _ = supported_profile(adapter.company)
    persist(adapter.notices, body())
    for value in (document, profile):
        adapter.company.auth.grant(ALICE, value["object_id"], BOB.owner_id, ["read"])
    service = ScreeningService(adapter.company, adapter.notices, ScreeningSettings(enabled=True))
    batch = service.candidates(BOB, profile, CandidateFilters(), as_of=AT + timedelta(days=1))
    deps = service.repository.db.rows(
        "SELECT object_id FROM qualification_result_sources WHERE result_id=:id",
        {"id": batch["batch_id"]},
    )
    assert len(deps) == 2
    first, second = [row["object_id"] for row in deps]
    public_complete, revoked = [], []
    original_read, original_require = adapter.notices.read_text, adapter.company.auth.require

    def public_read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        public_complete.append(True)
        return result

    def revoke_previous(context, object_id, action, conn=None):
        # Before the final phase no source is changed. Inside a locked transaction
        # a concurrent writer cannot commit between these checks. Reproduce the
        # formerly unprotected path using an actual committed ACL revocation.
        if (
            public_complete
            and context == BOB
            and object_id == second
            and action == "read"
            and conn is None
            and not revoked
        ):
            adapter.company.auth.revoke(ALICE, first, BOB.owner_id)
            revoked.append(first)
        return original_require(context, object_id, action, conn)

    monkeypatch.setattr(adapter.notices, "read_text", public_read)
    monkeypatch.setattr(adapter.company.auth, "require", revoke_previous)
    try:
        service.candidate_page(BOB, batch["batch_id"])
    except CompanyError:
        return  # A revocation observed before the release must fail closed.
    assert not revoked, "candidate body was released after an earlier source was revoked"
