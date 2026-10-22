"""A real Pydantic AI request deadline must retain its timeout classification."""

import asyncio
import time

from bidops.jobs.config import JobsSettings
from bidops.jobs.model import JobModelProxy
from bidops.jobs.runtime import JobsService
from test_company_access import ALICE
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


def test_framework_deadline_before_provider_is_timed_out_not_invalid_output(
    service, settings, monkeypatch
):
    screening, notice, profile, _, _ = service
    screening.settings.max_seconds = 0.05
    calls = []

    async def delayed_dispatch(self, *args, **kwargs):
        # Exercise the installed framework's real request timer outside the
        # inner provider guard, so CPU/DBOS dispatch timing cannot pick a winner.
        await asyncio.sleep(0.5)
        calls.append("provider_would_start")
        raise AssertionError("framework request deadline should cancel dispatch")

    monkeypatch.setattr(JobModelProxy, "request", delayed_dispatch)
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        first = runtime.submit(
            ALICE,
            {"profile_revision": profile, "project_id": notice.project_id},
            "framework-timeout",
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = runtime.repository.get_system(first["run_id"])["status"]
            if state in {"failed", "timed_out", "interrupted"}:
                break
            time.sleep(0.02)
        final = runtime.read(ALICE, first["run_id"])
        assert final["status"] == "timed_out"
        assert final["error_code"] == "screening_timeout"
        assert final["result"]["state"] == "timed_out"
        assert final["result"]["recommendation"] == "not_evaluated"
        assert final["result"]["human_review_state"] == "unreviewed"
        assert final["result"]["inventory"]["unresolved"]
        assert calls == []
        assert runtime.repository.list_attempts_system(first["run_id"]) == []
        assert screening.repository.db.rows("SELECT * FROM agent_runs") == []
    finally:
        runtime.close()
