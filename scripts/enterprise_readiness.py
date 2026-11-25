"""Offline B10 configuration/evidence checks. Does not probe a deployment."""

import argparse
import json
import os
from pathlib import Path

from bidops.uat import UatBundle, check_environment, verify_bundle
from bidops.uat.environment import LOCK_FILES, _git, sha256_file
from bidops.uat.evidence import AC_IDS
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MAX_BUNDLE_BYTES = 2_000_000


def template(root=ROOT):
    """An explicitly unexecuted template, using the current public code hashes."""
    revision = _git(root, "rev-parse", "HEAD")
    if not revision:
        raise ValueError("code_version_unavailable")
    return {
        "schema_version": "bidops-uat-v1",
        "code_sha": revision,
        "lock_hashes": {name: sha256_file(root / path) for name, (_, path) in LOCK_FILES.items()},
        "plan_sha256": sha256_file(root / "docs/ENTERPRISE-PR-PLAN.md"),
        "inventory": {"deployment_id": "not-configured"},
        "artifacts": [],
        "records": [
            {
                "ac_id": ac_id,
                "environment_kind": "engineering_fixture",
                "deployment_id": "not-configured",
                "code_sha": revision,
                "claimed_outcome": "NOT_RUN",
            }
            for ac_id in AC_IDS
        ],
    }


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def load_bundle(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("bundle_size_limit_exceeded")
    return json.loads(raw, object_pairs_hook=_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit private env file; never printed")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--schema", action="store_true")
    output.add_argument("--template", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.bundle) != bool(args.evidence_root):
        parser.error("--bundle and --evidence-root must be used together")
    try:
        if args.schema:
            result, code = UatBundle.model_json_schema(), 0
        elif args.template:
            result, code = template(), 0
        else:
            environ = dict(os.environ)
            if args.env_file:
                if args.env_file.stat().st_mode & 0o077:
                    raise ValueError("private_environment_permissions_required")
                # Match the enterprise CLI: already exported variables win.
                environ = {**dotenv_values(args.env_file), **environ}
            if args.bundle:
                result = verify_bundle(
                    load_bundle(args.bundle), args.evidence_root, environ=environ, project_root=ROOT
                )
                code = (
                    0 if result.get("evidence_readiness") == "READY_FOR_INDEPENDENT_REVIEW" else 2
                )
            else:
                result = check_environment(environ, project_root=ROOT)
                code = 0 if result["configuration_ready"] else 2
    except (OSError, ValueError, TypeError, RecursionError):
        # Input paths, parser messages and exception values can contain secrets.
        result, code = {"error": "uat_input_unavailable_or_invalid", "release_decision": "NO_GO"}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
