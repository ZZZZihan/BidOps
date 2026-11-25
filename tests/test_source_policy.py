"""Synthetic permissions exercise gates; none authorize real data acquisition."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from bidops.sources import Source, SourcePolicy, canonical_url, load_sources, network_permission
from pydantic import ValidationError
from test_fetcher import permit


def test_original_sources_remain_unknown_and_unlicensed(store, url):
    assert len(store.sources) == 3
    assert all(s.policy is None for s in store.sources)
    assert all(s.robots_status == s.terms_status == "unknown" for s in store.sources)
    assert network_permission(url, store.sources) == (False, "source_permission_unconfirmed")


def test_legacy_allowed_metadata_does_not_grandfather_permission(store, url):
    permit(store)
    store.sources[0].policy = None
    assert network_permission(url, store.sources) == (False, "source_policy_missing")


@pytest.mark.parametrize("mode", ["manual_fetch", "scheduled_fetch"])
def test_complete_current_policy_authorizes_only_declared_network_modes(store, url, mode):
    permit(store)
    source = store.sources[0]
    assert network_permission(url, [source], mode=mode) == (True, "allowed")
    assert network_permission(url, [source], mode="manual_import") == (
        False,
        "acquisition_mode_not_allowed",
    )
    source.policy = source.policy.model_copy(update={"acquisition_modes": ["manual_import"]})
    assert network_permission(url, [source], mode=mode) == (False, "acquisition_mode_not_allowed")


@pytest.mark.parametrize("status", ["denied", "unknown"])
def test_policy_never_overrides_a_denied_or_unknown_decision(store, url, status):
    permit(store)
    source = store.sources[0]
    source.policy = source.policy.model_copy(update={"status": status})
    assert network_permission(url, [source]) == (False, "source_policy_unconfirmed")
    source.terms_status = status
    assert network_permission(url, [source]) == (False, "source_permission_unconfirmed")


def test_validity_boundary_review_future_and_timezone_are_explicit(store, url):
    permit(store)
    source = store.sources[0]
    policy = source.policy
    assert network_permission(url, [source], now=policy.valid_from) == (True, "allowed")
    assert network_permission(url, [source], now=policy.valid_until) == (
        False,
        "source_policy_expired",
    )
    assert network_permission(url, [source], now=policy.valid_from - timedelta(microseconds=1)) == (
        False,
        "source_policy_not_yet_valid",
    )
    source.policy = policy.model_copy(
        update={"reviewed_at": policy.valid_from + timedelta(seconds=1)}
    )
    assert network_permission(url, [source], now=policy.valid_from) == (
        False,
        "source_policy_not_yet_valid",
    )
    assert network_permission(url, [source], now=datetime(2026, 10, 23)) == (
        False,
        "invalid_policy_clock",
    )


def test_narrow_policy_scope_does_not_expand_source_allowlist(store, url):
    permit(store)
    source = store.sources[0]
    source.policy = source.policy.model_copy(update={"url_scope": [url]})
    assert network_permission(url, [source]) == (True, "allowed")
    assert network_permission(source.allowed_urls[1], [source]) == (
        False,
        "url_outside_policy_scope",
    )
    for outside in ["https://evil.test/file", "https://www.ccgp.gov.cn/unlisted"]:
        raw = source.model_dump(mode="python")
        raw["policy"]["url_scope"] = [outside]
        with pytest.raises(ValueError):
            Source.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", ""),
        ("revision", "with spaces"),
        ("status", "approved"),
        ("evidence_sha256", "not-a-sha"),
        ("evidence_reference", ""),
        ("evidence_reference", "line\nsecret"),
        ("reviewed_by", " "),
        ("reviewed_at", "2026-10-23T10:00:00"),
        ("reviewed_at", True),
        ("valid_until", 10),
        ("rate_limit_seconds", float("nan")),
        ("rate_limit_seconds", float("inf")),
        ("rate_limit_seconds", 4),
        ("max_attempts", 4),
        ("max_attempts", True),
        ("max_requests", 0),
        ("max_requests", 10001),
        ("max_bytes", 10000001),
        ("total_timeout_seconds", 181),
        ("total_timeout_seconds", False),
        ("acquisition_modes", ["crawl"]),
        ("acquisition_modes", ["manual_fetch"] * 2),
        ("url_scope", []),
    ],
)
def test_invalid_policy_fields_fail_closed_even_for_mutated_instances(store, url, field, value):
    permit(store)
    source = store.sources[0]
    payload = source.policy.model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError):
        SourcePolicy.model_validate(payload)
    source.policy = source.policy.model_copy(update={field: value})
    assert network_permission(url, [source]) == (False, "invalid_source_policy")


def test_policy_all_required_fields_and_no_arbitrary_metadata(store):
    permit(store)
    policy = store.sources[0].policy.model_dump(mode="python")
    for field in SourcePolicy.model_fields:
        missing = dict(policy)
        missing.pop(field)
        with pytest.raises(ValidationError):
            SourcePolicy.model_validate(missing)
    with pytest.raises(ValidationError):
        SourcePolicy.model_validate({**policy, "authorization": "never-allowed"})
    with pytest.raises(ValidationError, match="invalid_policy_time_window"):
        SourcePolicy.model_validate({**policy, "valid_until": policy["valid_from"]})


def test_policy_hash_covers_complete_canonical_metadata(store):
    permit(store)
    policy = store.sources[0].policy
    reordered = dict(reversed(list(policy.model_dump(mode="json").items())))
    assert SourcePolicy.model_validate(reordered).sha256 == policy.sha256
    for field, value in [
        ("revision", "v2"),
        ("reviewed_by", "other-reviewer"),
        ("evidence_sha256", "f" * 64),
        ("rate_limit_seconds", 6.0),
        ("max_requests", 101),
        ("valid_until", datetime.now(UTC) + timedelta(days=2)),
        ("url_scope", [policy.url_scope[0]]),
    ]:
        assert policy.model_copy(update={field: value}).sha256 != policy.sha256


def test_duplicate_source_ids_are_rejected_on_load(store, tmp_path):
    values = [source.model_dump(mode="json") for source in store.sources]
    values[1]["id"] = values[0]["id"]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(values))
    with pytest.raises(ValueError, match="duplicate_source_ids"):
        load_sources(path)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ccgp.gov.cn/a?cursor=token",
        "https://www.ccgp.gov.cn/a#b",
        "https://www.ccgp.gov.cn/%2e%2e/file",
        "https://www.ccgp.gov.cn/../file",
        "https://www.ccgp.gov.cn:444/file",
        "https://user:secret@www.ccgp.gov.cn/file",
        "http://www.ccgp.gov.cn/file",
        "https://www.ccgp.gov.cn.evil.test/file",
    ],
)
def test_existing_url_boundary_is_preserved(store, url):
    with pytest.raises(ValueError):
        canonical_url(url, store.sources)
