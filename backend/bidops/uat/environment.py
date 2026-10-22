"""Allowlisted, offline configuration inspection with no secret-bearing output."""

import hashlib
import importlib.metadata
import ipaddress
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from bidops.config import Settings
from bidops.enterprise.config import EnterpriseSettings
from bidops.ingestion.config import IngestionSettings
from bidops.jobs.config import JobsSettings
from bidops.qualification.config import ScreeningSettings
from bidops.review.config import ReviewSettings

LOCK_FILES = {
    "base": ("BIDOPS_LOCK_PATH", "uv.lock"),
    "durable": ("BIDOPS_DURABLE_LOCK_PATH", "requirements/durable.lock"),
    "attachments": ("BIDOPS_ATTACHMENT_LOCK_PATH", "requirements/attachments.lock"),
}


def sha256_file(path, *, max_bytes=50_000_000):
    digest = hashlib.sha256()
    count = 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > max_bytes:
                raise ValueError("file_size_limit_exceeded")
            digest.update(chunk)
    return digest.hexdigest()


def non_placeholder_https(value, *, origin=False):
    # urlsplit strips CR/LF/TAB before parsing. Inspect the original input so
    # a malformed deployment URL cannot silently turn into a different origin.
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        _ = parsed.port  # Validate an explicit port without resolving DNS.
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        return bool(
            parsed.scheme == "https"
            and host
            and not loopback
            and host not in {"localhost", "example.com", "example.org", "example.net"}
            and not host.endswith(
                (
                    ".invalid",
                    ".test",
                    ".localhost",
                    ".example",
                    ".example.com",
                    ".example.org",
                    ".example.net",
                )
            )
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (not origin or parsed.path in {"", "/"})
        )
    except (TypeError, ValueError):
        return False


def _settings(settings_class, environ):
    # Settings must come ONLY from the supplied mapping. In particular, do not
    # read DemoSettings' private/deepseek.env or fall back to the caller's env.
    class LocalSettings(settings_class):
        @classmethod
        def settings_customise_sources(cls, settings_cls, **sources):
            return (sources["init_settings"],)

    prefix = settings_class.model_config.get("env_prefix", "")
    values = {}
    for name, field in settings_class.model_fields.items():
        key = (
            field.validation_alias
            if isinstance(field.validation_alias, str)
            else prefix + name.upper()
        )
        if key in environ:
            values[key if field.validation_alias else name] = environ[key]
    return LocalSettings(_env_file=None, **values)


def _secret_looks_configured(secret):
    if not secret:
        return False
    value = secret.get_secret_value().strip()
    lowered = value.lower()
    return (
        bool(value)
        and not any(
            marker in lowered
            for marker in (
                "not_a_credential",
                "not-a-credential",
                "changeme",
                "change-me",
                "example",
                "unit_",
            )
        )
        and lowered not in {"password", "secret", "development", "test"}
    )


def _git(root, *arguments):
    try:
        return subprocess.check_output(
            ["git", *arguments], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=2
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def check_environment(environ=None, *, project_root=None, inventory=None):
    """Inspect local settings only. No requests, writes, model, Store or DBOS.

    Output contains fixed check codes, flags and public code/lock hashes. Secret
    values, their hashes, raw validation exceptions, paths and URLs are omitted.
    Presence is not proof of credential validity or entropy, permission or uptime.
    """
    environ = os.environ if environ is None else environ
    root = Path(project_root or Path(__file__).resolve().parents[3])
    checks, loaded = [], {}

    def check(code, ok, *, status="BLOCKED"):
        checks.append({"code": code, "status": "PASS" if ok else status})

    for name, cls in (
        ("enterprise", EnterpriseSettings),
        ("storage", Settings),
        ("screening", ScreeningSettings),
        ("jobs", JobsSettings),
        ("review", ReviewSettings),
        ("ingestion", IngestionSettings),
    ):
        try:
            loaded[name] = _settings(cls, environ)
            check(name + "_settings_valid", True)
        except (ValidationError, TypeError, ValueError):
            # Never format/return an exception: validation inputs may be secrets.
            check(name + "_settings_valid", False, status="FAIL")

    enterprise = loaded.get("enterprise")
    if enterprise is not None:
        check("enterprise_enabled", enterprise.enabled)
        check("real_https_origin", non_placeholder_https(enterprise.public_url, origin=True))
        check("real_https_issuer", non_placeholder_https(enterprise.oidc_issuer))
        check("insecure_loopback_disabled", not enterprise.allow_insecure_loopback)
        check("keycloak_adapter", enterprise.identity_provider == "keycloak")
        check("identity_snapshot_at_most_240_seconds", enterprise.identity_snapshot_seconds <= 240)
        check("client_secret_configured", _secret_looks_configured(enterprise.oidc_client_secret))
        check("cookie_secret_configured", _secret_looks_configured(enterprise.cookie_secret))
        check(
            "single_worker_deployment", enterprise.deployment == "single_organization_single_worker"
        )
        if inventory is not None:
            origin = enterprise.public_url.rstrip("/")
            check(
                "inventory_matches_oidc_configuration",
                inventory.origin == origin
                and inventory.issuer == enterprise.oidc_issuer
                and inventory.client_id == enterprise.oidc_client_id
                and inventory.callback_uri == origin + "/api/auth/callback"
                and inventory.backchannel_uri == origin + "/api/auth/backchannel-logout",
            )
    storage = loaded.get("storage")
    if storage is not None:
        try:
            database = make_url(storage.database_url)
            data = storage.data_dir.resolve()
            db_path = Path(database.database or "").resolve()
            check(
                "sqlite_inside_data_directory",
                database.drivername == "sqlite"
                and database.database not in {None, "", ":memory:"}
                and db_path.is_relative_to(data),
            )
        except (TypeError, ValueError):
            check("sqlite_inside_data_directory", False, status="FAIL")
    revision = environ.get("BIDOPS_CODE_SHA") or _git(root, "rev-parse", "HEAD")
    valid_revision = isinstance(revision, str) and bool(re.fullmatch(r"[a-f0-9]{40}", revision))
    check("code_sha_available", valid_revision)
    dirty = _git(root, "status", "--porcelain", "--untracked-files=normal")
    if dirty is not None:
        check("git_tree_clean", not dirty)
        head = _git(root, "rev-parse", "HEAD")
        check("code_sha_matches_checkout", valid_revision and revision == head)
    lock_hashes = {}
    for name, (variable, relative) in LOCK_FILES.items():
        try:
            path = Path(environ.get(variable, str(root / relative)))
            if path.stat().st_size > 2_000_000:
                raise ValueError("lock_too_large")
            lock_hashes[name] = sha256_file(path, max_bytes=2_000_000)
            check(name + "_lock_readable", True)
        except (OSError, TypeError, ValueError):
            check(name + "_lock_readable", False)
    dependencies = {}
    for name, expected in (("authlib", "1.9.1"), ("dbos", "3.3.0"), ("pypdf", "6.21.2")):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        dependencies[name] = version
        check(name + "_locked_version_installed", version == expected)
    screening = loaded.get("screening")
    flags = {
        name + "_enabled": bool(value.enabled)
        for name, value in loaded.items()
        if name != "storage"
    }
    if screening is not None:
        flags.update(
            live_calls_enabled=screening.live_calls_enabled,
            ledger_verified=screening.ledger_verified,
            provider_key_present=bool(screening.api_key),
            restore_ledger_token_present=bool(screening.ledger_restore_token),
        )
    # These are intentional capability gaps for real full-flow UAT, not advice
    # to enable calls. Only an authorized operator may resolve the budget gate.
    for feature in ("screening", "jobs", "review"):
        check(feature + "_flow_enabled", flags.get(feature + "_enabled", False))
    check(
        "current_ledger_and_provider_gate",
        bool(
            screening
            and screening.live_calls_enabled
            and screening.ledger_verified
            and screening.api_key
        ),
    )
    return {
        "kind": "offline_configuration_only",
        "configuration_ready": all(row["status"] == "PASS" for row in checks),
        "checks": checks,
        "code_sha": revision if valid_revision else None,
        "lock_hashes": lock_hashes,
        "installed_versions": dependencies,
        "flags": flags,
        "network_probes": 0,
        "model_calls": 0,
        "business_acceptance": "NOT_VERIFIED_BY_TOOL",
        "release_decision": "NO_GO",
        "limits": [
            "Configuration presence is not proof of credential validity or entropy.",
            "No IdP, HTTPS, account, source, model, alert or deployment has been contacted.",
            "Live calls require authorization and current complete-ledger reconciliation.",
        ],
    }
