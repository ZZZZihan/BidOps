"""Offline configuration tests; these credentials and hosts are never contacted."""

import json
import secrets
import socket
from pathlib import Path

import pytest
from bidops.uat import check_environment
from bidops.uat.environment import LOCK_FILES, sha256_file

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


@pytest.fixture
def uat_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for _, path in LOCK_FILES.values():
        target = root / path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    (root / "docs").mkdir()
    (root / "docs/ENTERPRISE-PR-PLAN.md").write_bytes(
        (ROOT / "docs/ENTERPRISE-PR-PLAN.md").read_bytes()
    )
    return root


@pytest.fixture
def uat_env(uat_project):
    return {
        "BIDOPS_CODE_SHA": SHA,
        "BIDOPS_ENTERPRISE_ENABLED": "true",
        "BIDOPS_ENTERPRISE_PUBLIC_URL": "https://bidops.corp.intranet",
        "BIDOPS_ENTERPRISE_OIDC_ISSUER": "https://sso.corp.intranet/realms/bidops",
        "BIDOPS_ENTERPRISE_OIDC_CLIENT_ID": "bidops",
        "BIDOPS_ENTERPRISE_OIDC_CLIENT_SECRET": secrets.token_urlsafe(48),
        "BIDOPS_ENTERPRISE_COOKIE_SECRET": secrets.token_urlsafe(48),
        "BIDOPS_DATA_DIR": str(uat_project / "data"),
        "BIDOPS_DATABASE_URL": f"sqlite:///{uat_project}/data/processed/bidops.sqlite3",
        "BIDOPS_SCREENING_ENABLED": "true",
        "BIDOPS_SCREENING_LIVE_CALLS_ENABLED": "true",
        "BIDOPS_SCREENING_LEDGER_VERIFIED": "true",
        "BIDOPS_JOBS_ENABLED": "true",
        "BIDOPS_REVIEW_ENABLED": "true",
        "DEEPSEEK_API_KEY": secrets.token_urlsafe(48),
    }


def checks(report):
    return {item["code"]: item["status"] for item in report["checks"]}


def test_allowlisted_config_no_network_no_files_written(uat_project, uat_env, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("UAT preparation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    before = sorted(str(path.relative_to(uat_project)) for path in uat_project.rglob("*"))
    report = check_environment(uat_env, project_root=uat_project)
    assert report["configuration_ready"] is True
    assert report["network_probes"] == report["model_calls"] == 0
    assert report["release_decision"] == "NO_GO"
    assert report["business_acceptance"] == "NOT_VERIFIED_BY_TOOL"
    assert report["lock_hashes"]["base"] == sha256_file(uat_project / "uv.lock")
    assert sorted(str(path.relative_to(uat_project)) for path in uat_project.rglob("*")) == before
    output = json.dumps(report)
    for name, value in uat_env.items():
        if name in {
            "DEEPSEEK_API_KEY",
            "BIDOPS_ENTERPRISE_OIDC_CLIENT_SECRET",
            "BIDOPS_ENTERPRISE_COOKIE_SECRET",
            "BIDOPS_ENTERPRISE_PUBLIC_URL",
        }:
            assert value not in output


def test_empty_mapping_never_inherits_developer_environment(uat_project, uat_env, monkeypatch):
    for name, value in uat_env.items():
        monkeypatch.setenv(name, value)
    report = check_environment({}, project_root=uat_project)
    assert not report["configuration_ready"]
    assert checks(report)["enterprise_enabled"] == "BLOCKED"
    assert not report["flags"]["provider_key_present"]
    assert not report["flags"]["ledger_verified"]


@pytest.mark.parametrize(
    "name,value,code",
    [
        ("BIDOPS_ENTERPRISE_PUBLIC_URL", "http://127.0.0.1:8765", "enterprise_settings_valid"),
        ("BIDOPS_ENTERPRISE_PUBLIC_URL", "https://bidops.example.invalid", "real_https_origin"),
        ("BIDOPS_ENTERPRISE_PUBLIC_URL", "https://localhost", "real_https_origin"),
        ("BIDOPS_ENTERPRISE_OIDC_ISSUER", "https://sso.test/realm", "real_https_issuer"),
        ("BIDOPS_ENTERPRISE_IDENTITY_SNAPSHOT_SECONDS", "241", "enterprise_settings_valid"),
        ("BIDOPS_ENTERPRISE_ALLOW_INSECURE_LOOPBACK", "true", "insecure_loopback_disabled"),
        (
            "BIDOPS_ENTERPRISE_OIDC_CLIENT_SECRET",
            "UNIT_NOT_A_CREDENTIAL",
            "client_secret_configured",
        ),
        ("BIDOPS_CODE_SHA", "unknown", "code_sha_available"),
        ("BIDOPS_DATABASE_URL", "sqlite:///:memory:", "sqlite_inside_data_directory"),
        (
            "BIDOPS_DATABASE_URL",
            "sqlite:////tmp/outside-uat.sqlite3",
            "sqlite_inside_data_directory",
        ),
        ("BIDOPS_JOBS_ENABLED", "false", "jobs_flow_enabled"),
        ("BIDOPS_SCREENING_LEDGER_VERIFIED", "false", "current_ledger_and_provider_gate"),
        ("BIDOPS_SCREENING_LIVE_CALLS_ENABLED", "false", "current_ledger_and_provider_gate"),
    ],
)
def test_unsafe_or_missing_configuration_blocks(uat_project, uat_env, name, value, code):
    uat_env[name] = value
    report = check_environment(uat_env, project_root=uat_project)
    assert not report["configuration_ready"]
    assert checks(report)[code] in {"FAIL", "BLOCKED"}
    assert report["release_decision"] == "NO_GO"


def test_invalid_setting_does_not_echo_value_or_exception(uat_project, uat_env):
    secret = secrets.token_urlsafe(50)
    uat_env["BIDOPS_ENTERPRISE_IDENTITY_SNAPSHOT_SECONDS"] = secret
    uat_env["BIDOPS_SCREENING_BUDGET_CNY"] = secret
    uat_env["SOME_UNRELATED_SECRET"] = secret
    report = check_environment(uat_env, project_root=uat_project)
    assert secret not in json.dumps(report)
    assert checks(report)["enterprise_settings_valid"] == "FAIL"
    assert checks(report)["screening_settings_valid"] == "FAIL"


@pytest.mark.parametrize(
    "name,code",
    [
        ("BIDOPS_ENTERPRISE_PUBLIC_URL", "real_https_origin"),
        ("BIDOPS_ENTERPRISE_OIDC_ISSUER", "real_https_issuer"),
    ],
)
@pytest.mark.parametrize("separator", ["\r\n", "\n", "\r", "\t", "\x7f", " "])
def test_raw_url_controls_and_whitespace_cannot_be_normalized_into_readiness(
    uat_project, uat_env, name, code, separator
):
    value = "https://bidops.corp." + separator + "intranet"
    uat_env[name] = value
    report = check_environment(uat_env, project_root=uat_project)
    assert checks(report)[code] == "BLOCKED"
    assert not report["configuration_ready"]
    assert report["release_decision"] == "NO_GO"
    assert value not in json.dumps(report)


def test_lock_missing_and_dependency_version_mismatch(uat_project, uat_env, monkeypatch):
    (uat_project / "requirements/durable.lock").unlink()
    monkeypatch.setattr("bidops.uat.environment.importlib.metadata.version", lambda _: "0.0")
    report = check_environment(uat_env, project_root=uat_project)
    assert checks(report)["durable_lock_readable"] == "BLOCKED"
    assert checks(report)["dbos_locked_version_installed"] == "BLOCKED"
    assert not report["configuration_ready"]


def test_dirty_checkout_and_forged_build_sha_are_disclosed(uat_project, uat_env, monkeypatch):
    monkeypatch.setattr(
        "bidops.uat.environment._git",
        lambda root, *args: " M changed.py" if args[0] == "status" else "b" * 40,
    )
    report = check_environment(uat_env, project_root=uat_project)
    assert checks(report)["git_tree_clean"] == "BLOCKED"
    assert checks(report)["code_sha_matches_checkout"] == "BLOCKED"
    assert "changed.py" not in json.dumps(report)
