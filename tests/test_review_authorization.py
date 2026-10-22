"""Authorization is exercised against live login/identity/subject/ACL tables."""

import json

import pytest
from bidops.company.authorization import CompanyError, ToolContext
from bidops.review import HumanContext
from starlette.requests import Request
from test_review_repository import create_case, decide
from test_review_repository import domain as domain
from test_review_repository import jobs as jobs
from test_review_repository import reviews as reviews
from test_review_repository import sources as sources


@pytest.mark.parametrize(
    "context",
    [
        None,
        ToolContext("bob"),
        {"owner_id": "bob", "actor_kind": "human"},
        HumanContext("bob", "0" * 64),
        HumanContext("alice", "bad"),
    ],
)
def test_tools_and_declared_human_objects_are_not_authenticated_sessions(reviews, context):
    case = create_case(reviews)
    with pytest.raises(CompanyError) as caught:
        reviews[0].get(context, case["case_id"])
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "table,update",
    [
        ("enterprise_logins", "expires_at=0"),
        ("enterprise_login_identities", "fresh_until=0"),
        ("enterprise_login_identities", "issuer='wrong-issuer'"),
        ("enterprise_subjects", "fresh_until=0"),
        ("enterprise_subjects", "status='disabled'"),
    ],
)
def test_each_identity_layer_rechecked_on_existing_context(reviews, table, update):
    case = create_case(reviews)
    db = reviews[0].db
    where = "owner_id='bob'" if table != "enterprise_login_identities" else "subject='bob'"
    db.execute(f"UPDATE {table} SET {update} WHERE {where}")
    with pytest.raises(CompanyError) as caught:
        decide(reviews, case)
    assert caught.value.status_code == 401
    assert db.rows("SELECT * FROM review_events") == []


@pytest.mark.parametrize("which", ["login", "subject"])
def test_role_is_intersection_of_login_and_current_subject(reviews, which):
    case = create_case(reviews)
    table = "enterprise_logins" if which == "login" else "enterprise_subjects"
    reviews[0].db.execute(f"UPDATE {table} SET roles_json='[\"viewer\"]' WHERE owner_id='bob'")
    with pytest.raises(CompanyError) as caught:
        decide(reviews, case)
    assert caught.value.status_code == 403


def test_owner_admin_viewer_have_no_implicit_review_or_private_access(reviews):
    case = create_case(reviews)
    repo, alice, _, admin, viewer, *_ = reviews
    with pytest.raises(CompanyError) as owner_error:
        decide(reviews, case, actor=alice)
    assert owner_error.value.status_code == 403
    for context in (admin, viewer):
        with pytest.raises(CompanyError) as caught:
            repo.get(context, case["case_id"])
        assert caught.value.status_code == 404
        assert repo.list(context)["items"] == []


def test_known_hash_with_forged_owner_rejected(reviews):
    case = create_case(reviews)
    with pytest.raises(CompanyError) as caught:
        reviews[0].get(HumanContext("alice", reviews[2].token_hash), case["case_id"])
    assert caught.value.status_code == 401


def test_from_request_uses_server_login_hash_never_actor_claims(reviews):
    repo = reviews[0]
    request = Request(
        {
            "type": "http",
            "session": {"login": "bob-review-login"},
            "headers": [],
            "method": "GET",
            "path": "/",
        }
    )
    assert repo.authorization.from_request(request) == reviews[2]
    assert repo.authorization.from_request(request).token_hash != "bob-review-login"
    request.scope["session"] = {"owner_id": "bob", "actor_kind": "human"}
    with pytest.raises(CompanyError):
        repo.authorization.from_request(request)


def test_assigned_reviewer_requires_each_private_source(reviews):
    case = create_case(reviews)
    repo, alice, bob, *_ = reviews
    sources = repo.db.rows("SELECT object_id FROM review_sources")
    assert len(sources) >= 2
    repo.sources.company.auth.revoke(
        ToolContext(alice.owner_id), sources[-1]["object_id"], bob.owner_id
    )
    with pytest.raises(CompanyError) as caught:
        repo.get(bob, case["case_id"])
    assert caught.value.status_code == 404


def test_unrecognized_review_role_is_not_admin_approval(reviews):
    case = create_case(reviews)
    repo = reviews[0]
    for table in ("enterprise_logins", "enterprise_subjects"):
        repo.db.execute(
            f"UPDATE {table} SET roles_json=:roles WHERE owner_id='bob'",
            {"roles": json.dumps(["admin"])},
        )
    with pytest.raises(CompanyError) as caught:
        decide(reviews, case)
    assert caught.value.status_code == 403
