"""SIGKILL a real DBOS child after a checkpoint, then verify framework recovery.

Only in-process TestModel is used. Runtime databases remain in a new private
temporary directory, or a new directory chosen with --output.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def git_value(*arguments):
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY), *arguments], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def dependency_versions():
    """Reject a drifted runtime before making any recovery claim."""
    main_lock = tomllib.loads((REPOSITORY / "uv.lock").read_text())
    main_versions = {item["name"]: item["version"] for item in main_lock["package"]}
    requirements = (REPOSITORY / "requirements/durable.lock").read_text()
    pinned = dict(re.findall(r"^([a-zA-Z0-9_.-]+)==([^ \\\n]+)", requirements, re.M))
    if not pinned:
        raise RuntimeError("durable dependency closure is empty")
    checked = {}
    for name, version in pinned.items():
        installed = package_version(name)
        frozen = main_versions.get(name)
        if installed != version or (frozen is not None and frozen != version):
            raise RuntimeError(f"dependency version mismatch: {name}")
        checked[name] = {"durable_lock": version, "installed": installed, "main_lock": frozen}
    return checked


def events(root):
    path = root / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def checkpoint_rows(root):
    with sqlite3.connect(f"file:{root / 'dbos.sqlite'}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT function_id, function_name, output IS NOT NULL AS has_output, "
                "error IS NOT NULL AS has_error FROM operation_outputs ORDER BY function_id"
            )
        ]


def database_summary(root):
    with sqlite3.connect(f"file:{root / 'dbos.sqlite'}?mode=ro", uri=True) as conn:
        return {
            "dbos_schema_version": conn.execute(
                "SELECT MAX(version) FROM dbos_migrations"
            ).fetchone()[0],
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "workflow_statuses": conn.execute(
                "SELECT status, count(*) FROM workflow_status GROUP BY status"
            ).fetchall(),
            "operation_count": conn.execute("SELECT count(*) FROM operation_outputs").fetchone()[0],
        }


def require(condition, message):
    # These checks remain active under python -O.
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new, nonexistent private output directory")
    arguments = parser.parse_args()
    os.umask(0o077)
    versions = dependency_versions()
    if arguments.output is None:
        root = Path(tempfile.mkdtemp(prefix="bidops-b05-durable-probe-"))
    else:
        root = arguments.output.expanduser().resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    version = sha256(HERE / "worker.py")
    started = datetime.now(UTC).isoformat()
    processes = []

    def start(mode):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DBOS_", "OTEL_"))
        }
        with (root / f"{mode}.log").open("w") as log:
            child = subprocess.Popen(
                [sys.executable, "-u", str(HERE / "worker.py"), str(root), mode, version],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=root,
                env=environment,
            )
        processes.append(child)
        return child

    report = {
        "status": "FAIL",
        "directory": str(root),
        "started_at_utc": started,
        "code_sha": git_value("rev-parse", "HEAD"),
        "source_tree_dirty": bool(git_value("status", "--porcelain")),
        "application_version": version,
        "file_sha256": {
            name: sha256(REPOSITORY / name)
            for name in [
                "scripts/durable_probe/worker.py",
                "scripts/durable_probe/probe.py",
                "requirements/durable.lock",
                "uv.lock",
            ]
        },
        "versions": versions,
        "python": sys.version,
        "model_kind": "in_process_TestModel_only",
        "real_model_calls": 0,
        "network_calls_permitted": False,
    }
    try:
        first = start("submit")
        deadline = time.monotonic() + 45
        while not any(item["event"] == "checkpoint_gate_entered" for item in events(root)):
            if first.poll() is not None:
                raise RuntimeError("initial worker exited; inspect submit.log")
            if time.monotonic() > deadline:
                raise TimeoutError("first framework checkpoint not reached")
            time.sleep(0.05)
        before = checkpoint_rows(root)
        require(
            any(
                row["has_output"] and not row["has_error"] and "model" in row["function_name"]
                for row in before
            ),
            "completed model checkpoint not found in framework database",
        )
        (root / "checkpoints-before-kill.json").write_text(json.dumps(before, indent=2))
        first.send_signal(signal.SIGKILL)
        require(first.wait(timeout=10) == -signal.SIGKILL, "initial worker was not SIGKILLed")
        (root / "release-gate").touch()
        recovered = start("recover")
        require(recovered.wait(timeout=45) == 0, "recovery worker failed; inspect recover.log")
        again = start("read-completed")
        require(
            again.wait(timeout=30) == 0, "completed retrieval failed; inspect read-completed.log"
        )
        observed = events(root)
        counts = Counter(
            item["prompt"] for item in observed if item["event"] == "synthetic_model_request"
        )
        require(
            counts == {"SYNTHETIC_FIRST_MODEL_STEP": 1, "SYNTHETIC_SECOND_MODEL_STEP": 1},
            "persisted model call counts differ from expected one execution each",
        )
        gate_calls = sum(item["event"] == "checkpoint_gate_entered" for item in observed)
        final_calls = sum(item["event"] == "final_step_called" for item in observed)
        require(gate_calls == 2, "unfinished gate did not execute twice")
        require(final_calls == 1, "final step did not execute exactly once")
        results = [
            json.loads((root / f"result-{mode}.json").read_text())
            for mode in ["recover", "read-completed"]
        ]
        require(results[0] == results[1] and results[0]["fixture"] is True, "result mismatch")
        summary = database_summary(root)
        (root / "database-summary.json").write_text(json.dumps(summary, indent=2))
        report.update(
            status="PASS",
            model_step_calls=dict(counts),
            unfinished_gate_calls=gate_calls,
            final_step_calls=final_calls,
            first_worker_exit=first.returncode,
            recovery="automatic_by_DBOS.launch; retrieve_only_after_restart",
            completed_run_retrieval_reexecutes_steps=False,
        )
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
