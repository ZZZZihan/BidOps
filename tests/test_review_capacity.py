"""Saved synthetic review pages, full export, and the explicit byte boundary."""

import copy
import json
from types import SimpleNamespace

import pytest
from bidops.company.authorization import CompanyError
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import canonical_json, digest
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from test_company_access import ALICE
from test_jobs_repository import jobs as jobs
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources
from test_review_repository import create_case
from test_review_repository import reviews as reviews


def saved_many(reviews):
    repo = reviews[0]
    record = repo._result("review-batch", "alice")
    batch = copy.deepcopy(record["result"])
    first = batch["items"][0]
    # A legacy engineering body exercises the review projection independently of
    # the stricter new candidate-write route (covered by capacity storage tests).
    batch["items"] = [
        {**copy.deepcopy(first), "unit_id": f"UNIT-PAGE-{n:04}", "title": f"UNIT title {n}"}
        for n in range(501)
    ]
    batch["counts"] = {
        "total_units": 501,
        **{
            state: 501 if state == first["disposition"] else 0
            for state in ("candidate", "excluded", "needs_verification")
        },
    }
    batch["manifest_sha256"] = digest(
        {
            "private": record["manifest"]["closure_sha256"],
            **{k: v for k, v in batch.items() if k != "manifest_sha256"},
        }
    )
    QualificationRepository(repo.db, repo.sources).save(
        ALICE, record["manifest"], batch, result_id="UNIT-many-candidates"
    )
    return create_case(reviews, candidate_batch_id="UNIT-many-candidates"), batch


def test_saved_review_pages_reach_501_and_export_complete_for_assigned_reviewer(reviews):
    repo, alice, bob, *_ = reviews
    case, batch = saved_many(reviews)
    first = case["candidate_batch"]
    assert len(first["result"]["items"]) == 20
    assert first["page"] == {
        "offset": 0,
        "limit": 20,
        "total": 501,
        "has_more": True,
        "complete_record": False,
        "public_versions_total": len(batch["public_versions"]),
        "full_record_in_export": True,
    }
    seen = []
    for offset in range(0, 501, 100):
        current = repo.get(bob, case["case_id"], candidate_offset=offset, candidate_limit=100)
        projected = current["candidate_batch"]
        seen.extend(item["unit_id"] for item in projected["result"]["items"])
        assert projected["result"]["counts"]["total_units"] == 501
        assert projected["result"]["manifest_sha256"] == batch["manifest_sha256"]
        assert projected["page"]["has_more"] == (offset + 100 < 501)
    assert seen == [item["unit_id"] for item in batch["items"]]
    assert len(set(seen)) == 501
    exported = repo.export(bob, case["case_id"])["case"]["candidate_batch"]
    assert exported["result"] == batch and "page" not in exported
    assert (
        repo.get(alice, case["case_id"], candidate_offset=501)["candidate_batch"]["result"]["items"]
        == []
    )
    source = repo.db.rows("SELECT object_id FROM review_sources LIMIT 1")[0]["object_id"]
    repo.sources.company.auth.revoke(ALICE, source, "bob")
    with pytest.raises(CompanyError):
        repo.get(bob, case["case_id"], candidate_offset=500)
    with pytest.raises(CompanyError):
        repo.export(bob, case["case_id"])


def test_review_list_skips_unrequested_bodies_and_still_checks_acl(reviews, monkeypatch):
    repo, alice, bob, *_ = reviews
    saved_many(reviews)
    monkeypatch.setattr(repo, "_result", lambda *_: pytest.fail("off-page private body loaded"))
    page = repo.list(alice, offset=1, limit=1)
    assert page["items"] == [] and page["total"] == 1
    source = repo.db.rows("SELECT object_id FROM review_sources LIMIT 1")[0]["object_id"]
    repo.sources.company.auth.revoke(ALICE, source, "bob")
    assert repo.list(bob)["total"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("review_version", 1),
        ("review_epoch", "UNIT-rotated-epoch"),
        ("quarantined", 1),
        ("status", "closed"),
    ],
)
def test_list_rechecks_case_state_after_loading_private_projection(
    reviews, monkeypatch, field, value
):
    repo, alice, *_ = reviews
    case = create_case(reviews)
    original = repo.get

    def changed_after_read(*args, **kwargs):
        projected = original(*args, **kwargs)
        assert projected["candidate_batch"] and projected["items"]
        # Force a real committed state change in the window between this case's
        # final read check and the list's final release check. Grants stay valid.
        repo.db.execute(
            f"UPDATE review_case_state SET {field}=:value WHERE case_id=:id",
            {"value": value, "id": case["case_id"]},
        )
        return projected

    monkeypatch.setattr(repo, "get", changed_after_read)
    with pytest.raises(CompanyError, match="review_version_conflict") as error:
        repo.list(alice)
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "offset,limit", [(-1, 20), (True, 20), (0, 0), (0, 101), (0, True), (1, None)]
)
def test_review_page_invalid_values_are_explicit_errors(reviews, offset, limit):
    case = create_case(reviews)
    with pytest.raises(CompanyError, match="review_invalid_page"):
        reviews[0].get(reviews[1], case["case_id"], candidate_offset=offset, candidate_limit=limit)


@pytest.mark.parametrize(
    "size,accepted", [(10_000_001, True), (60_000_000, True), (60_000_001, False)]
)
def test_export_actual_encoded_bytes_separate_limit_no_truncation(
    reviews, monkeypatch, size, accepted
):
    repo, alice, *_ = reviews
    case = create_case(reviews)
    service = ReviewService(
        SimpleNamespace(repository=SimpleNamespace(db=repo.db), sources=repo.sources),
        settings=ReviewSettings(enabled=True, _env_file=None),
    )
    envelope = {"synthetic_export_boundary": ""}
    overhead = len(canonical_json(envelope).encode())
    envelope["synthetic_export_boundary"] = "U" * (size - overhead)
    # Isolate serialization from the immutable repository; real saved pages and
    # full historical export are checked above and by review API/recovery tests.
    monkeypatch.setattr(service.repository, "export", lambda *_: envelope)
    if accepted:
        data = service.export(alice, case["case_id"])
        assert len(data) == size and json.loads(data) == envelope
    else:
        with pytest.raises(CompanyError, match="review_export_too_large") as error:
            service.export(alice, case["case_id"])
        assert error.value.status_code == 413
