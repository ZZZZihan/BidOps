import importlib.util
import json
from types import SimpleNamespace

import pytest
from bidops.uat import UatBundle
from test_uat_environment import ROOT
from test_uat_environment import uat_project as uat_project


@pytest.fixture
def cli(uat_project, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "uat_cli", ROOT / "scripts/enterprise_readiness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", uat_project)
    monkeypatch.setattr(module, "os", SimpleNamespace(environ={}))
    return module


def test_cli_default_is_offline_blocked_and_non_secret(cli, capsys):
    assert cli.main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["configuration_ready"]
    assert report["kind"] == "offline_configuration_only"
    assert report["network_probes"] == report["model_calls"] == 0
    assert report["release_decision"] == "NO_GO"


def test_cli_schema_has_six_discriminated_records_and_no_credentials(cli, capsys):
    assert cli.main(["--schema"]) == 0
    output = capsys.readouterr().out
    schema = json.loads(output)
    assert len(schema["properties"]["records"]["items"]["discriminator"]["mapping"]) == 6
    assert "client_secret" not in output
    assert "api_key" not in output
    assert "csrf" not in schema["properties"]


def test_cli_example_all_six_blocked(cli, uat_project, tmp_path, capsys):
    bundle = tmp_path / "input.json"
    bundle.write_text((ROOT / "config/uat.example.json").read_text())
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    assert cli.main(["--bundle", str(bundle), "--evidence-root", str(root)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["schema_valid"]
    assert len(report["acs"]) == 6
    assert all(row["status"] == "BLOCKED" for row in report["acs"])
    assert report["release_decision"] == "NO_GO"
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize(
    "body",
    [
        b'{"code_sha":"SECRET_SENTINEL",',
        b'{"x":1,"x":2}',
        b" " * 2_000_001,
        b"[" * 1500 + b"]" * 1500,
    ],
)
def test_invalid_bounded_input_never_echoes_body_or_path(cli, tmp_path, capsys, body):
    bundle = tmp_path / "SECRET_SENTINEL.json"
    bundle.write_bytes(body)
    assert cli.main(["--bundle", str(bundle), "--evidence-root", str(tmp_path)]) == 2
    report = capsys.readouterr().out
    assert "SECRET_SENTINEL" not in report
    assert str(tmp_path) not in report
    assert json.loads(report)["release_decision"] == "NO_GO"


def test_explicit_env_file_requires_private_mode(cli, tmp_path, capsys):
    private = tmp_path / "SECRET_SENTINEL.env"
    private.write_text("BIDOPS_ENTERPRISE_COOKIE_SECRET=SECRET_SENTINEL\n")
    private.chmod(0o644)
    assert cli.main(["--env-file", str(private)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "uat_input_unavailable_or_invalid"
    private.chmod(0o600)
    assert cli.main(["--env-file", str(private)]) == 2
    report = capsys.readouterr().out
    assert "SECRET_SENTINEL" not in report
    assert str(private) not in report
    assert json.loads(report)["release_decision"] == "NO_GO"


def test_template_uses_current_public_versions_without_claiming_execution(
    cli, uat_project, monkeypatch
):
    monkeypatch.setitem(cli.template.__globals__, "_git", lambda *args: "a" * 40)
    template = UatBundle.model_validate(cli.template(uat_project))
    assert len(template.records) == 6
    assert all(record.claimed_outcome == "NOT_RUN" for record in template.records)
    assert all(record.environment_kind == "engineering_fixture" for record in template.records)
    assert not template.artifacts
    assert not template.inventory.accounts
