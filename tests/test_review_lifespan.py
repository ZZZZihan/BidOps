"""Actual FastAPI/DBOS shutdown must join an in-flight review relay thread.

Only the API module's timer is accelerated. The real asyncio module, production
lifespan, relay method, SQLite, DBOS close and exclusive worker lock remain live.
"""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from bidops import api
from bidops.agent_demo.runtime import DemoSettings
from bidops.enterprise.operations import SingleWorker
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.config import ScreeningSettings
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from dbos import DBOS
from pydantic_ai.models.test import TestModel
from test_enterprise import enterprise


@pytest.fixture
def lifecycle(settings, monkeypatch):
    order = []
    start, close = JobsService.start, JobsService.close

    def start_offline(jobs):
        jobs.screening.model_override = TestModel()
        start(jobs)

    def close_recorded(jobs):
        order.append("jobs_close")
        close(jobs)
        order.append("jobs_closed")

    class Worker(SingleWorker):
        def close(self):
            order.append("worker_close")
            super().close()

    monkeypatch.setattr(JobsService, "start", start_offline)
    monkeypatch.setattr(JobsService, "close", close_recorded)
    monkeypatch.setattr(api, "SingleWorker", Worker)
    app = api.create_app(
        settings,
        DemoSettings(enabled=False, _env_file=None),
        enterprise(_env_file=None),
        screening_settings=ScreeningSettings(
            enabled=True, live_calls_enabled=False, _env_file=None
        ),
        jobs_settings=JobsSettings(enabled=True, _env_file=None),
        review_settings=ReviewSettings(enabled=True, _env_file=None),
    )
    try:
        yield app, order
    finally:
        # Also release the singleton if a regression assertion fails mid-lifespan.
        DBOS.destroy(destroy_registry=True)


def accelerated_timer(monkeypatch, timer):
    # Do not patch asyncio.sleep itself: DBOS and other tasks share that module.
    monkeypatch.setattr(
        api,
        "asyncio",
        SimpleNamespace(
            sleep=timer,
            create_task=asyncio.create_task,
            to_thread=asyncio.to_thread,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
        ),
    )


@pytest.mark.parametrize("relay_fails", [False, True], ids=["returns", "raises"])
def test_shutdown_waits_for_real_relay_before_dbos_dispose_and_unlock(
    lifecycle, settings, monkeypatch, relay_fails
):
    app, order = lifecycle
    release = Event()
    deliver = ReviewService.deliver_pending

    async def exercise():
        entered, pulse = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()

        async def timer(seconds):
            if seconds == 30:
                await pulse.wait()
                pulse.clear()
            else:
                await asyncio.sleep(seconds)

        def blocked_real_delivery(service):
            order.append("relay_entered")
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(5):
                    raise AssertionError("test did not release the controlled relay")
                # Run the actual relay against its production DB and DBOS worker.
                assert service.jobs.started
                deliver(service)
                if relay_fails:
                    raise RuntimeError("UNIT relay failure after its real call")
            finally:
                order.append("relay_returned")

        accelerated_timer(monkeypatch, timer)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        assert app.state.jobs.started
        monkeypatch.setattr(ReviewService, "deliver_pending", blocked_real_delivery)
        engine = app.state.store.db.engine
        dispose = engine.dispose

        def dispose_recorded():
            order.append("dispose")
            dispose()

        monkeypatch.setattr(engine, "dispose", dispose_recorded)
        shutdown = None
        try:
            pulse.set()
            await asyncio.wait_for(entered.wait(), timeout=3)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            done, _ = await asyncio.wait({shutdown}, timeout=0.2)
            assert not done, f"shutdown completed before relay return: {order}"
            assert "jobs_close" not in order, f"DBOS close raced the relay: {order}"
            assert "dispose" not in order
            assert "worker_close" not in order
            assert app.state.jobs.started
            # A replacement service must still be excluded while the old thread runs.
            with pytest.raises(RuntimeError, match="exactly one worker"):
                SingleWorker(settings.database_url)
        finally:
            release.set()
            if shutdown is None:
                await lifespan.__aexit__(None, None, None)
            else:
                await asyncio.wait_for(shutdown, timeout=5)

        assert order == [
            "relay_entered",
            "relay_returned",
            "jobs_close",
            "jobs_closed",
            "dispose",
            "worker_close",
        ]
        assert not app.state.jobs.started
        replacement = SingleWorker(settings.database_url)
        replacement.close()

    asyncio.run(exercise())


def test_shutdown_cancels_idle_relay_without_waiting_for_its_interval(lifecycle, monkeypatch):
    app, order = lifecycle

    async def exercise():
        sleeping = asyncio.Event()

        async def timer(seconds):
            if seconds == 30:
                sleeping.set()
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(seconds)

        def unexpected_delivery(service):
            pytest.fail("idle relay must not start a delivery during shutdown")

        accelerated_timer(monkeypatch, timer)
        async with app.router.lifespan_context(app):
            assert app.state.jobs.started
            monkeypatch.setattr(ReviewService, "deliver_pending", unexpected_delivery)
            await asyncio.wait_for(sleeping.wait(), timeout=3)
        assert order == ["jobs_close", "jobs_closed", "worker_close"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
