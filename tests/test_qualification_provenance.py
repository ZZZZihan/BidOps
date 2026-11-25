"""Installed deployments retain immutable build and lock references without Git."""

import hashlib
import subprocess
from pathlib import Path

from bidops.qualification.service import implementation_version


def test_installed_build_metadata_does_not_require_git(monkeypatch, tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"immutable dependency artifact")
    monkeypatch.setenv("BIDOPS_CODE_SHA", "a" * 40)
    monkeypatch.setenv("BIDOPS_LOCK_PATH", str(lock))

    def no_git(*args, **kwargs):
        raise AssertionError("Installed build must use the supplied revision")

    monkeypatch.setattr(subprocess, "check_output", no_git)
    actual = implementation_version()
    assert actual["code_sha"] == "a" * 40
    assert actual["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert len(actual["implementation_sha256"]) == 64


def test_unknown_build_does_not_invent_revision_or_lock(monkeypatch, tmp_path):
    monkeypatch.delenv("BIDOPS_CODE_SHA", raising=False)
    monkeypatch.setenv("BIDOPS_LOCK_PATH", str(tmp_path / "absent.lock"))

    def no_git(*args, **kwargs):
        raise FileNotFoundError("No Git in installed runtime")

    monkeypatch.setattr(subprocess, "check_output", no_git)
    actual = implementation_version()
    assert actual["code_sha"] is None and actual["lock_sha256"] is None
    assert len(actual["implementation_sha256"]) == 64


def test_source_checkout_uses_exact_frozen_lock(monkeypatch):
    monkeypatch.delenv("BIDOPS_LOCK_PATH", raising=False)
    monkeypatch.setenv("BIDOPS_CODE_SHA", "invalid_short_sha")
    actual = implementation_version()
    assert actual["code_sha"] is None
    assert (
        actual["lock_sha256"]
        == hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "uv.lock").read_bytes()
        ).hexdigest()
    )
