"""Bounded local dependency observations; no body labels or outbound alerts.

A separate watchdog must detect whole-process death. This in-process sampler
checks dependencies while the ASGI process is alive, and makes stale observation
fail readiness rather than reporting its last successful sample indefinitely.
"""

import json
import logging
import shutil
import sqlite3
import time
from collections import deque
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from prometheus_client import Counter, Gauge
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

COMPONENTS = (
    "database",
    "workflow_database",
    "worker",
    "disk",
    "queue_backlog",
    "source",
    "maintenance",
)
LOGGER = logging.getLogger("bidops.operations")


class HealthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_OPERATIONS_", extra="ignore")
    enabled: bool = False
    interval_seconds: int = Field(default=30, ge=1, le=60)
    stale_seconds: int = Field(default=120, ge=60, le=180)
    minimum_free_bytes: int = Field(default=268435456, ge=1)
    backlog_count: int = Field(default=80, ge=1, le=100)
    oldest_queued_seconds: int = Field(default=120, ge=1, le=180)


class DependencyHealth:
    def __init__(self, settings, registry, app_state, data_settings, *, clock=time.time):
        self.settings, self.state, self.data_settings = settings, app_state, data_settings
        self.clock = clock
        self.lock = Lock()
        self.observed_at = None
        self.checks = {name: False for name in COMPONENTS}
        self.events = deque(maxlen=128)
        self.observations = Gauge(
            "bidops_dependency_ok", "Last local dependency check", ["component"], registry=registry
        )
        self.transitions = Counter(
            "bidops_dependency_events_total",
            "Dependency transitions",
            ["component", "state"],
            registry=registry,
        )
        self.last_sample = Gauge(
            "bidops_dependency_observed_timestamp_seconds",
            "Last observation timestamp",
            registry=registry,
        )
        self.queue_size = Gauge(
            "bidops_queued_jobs", "Queued qualification and ingestion tasks", registry=registry
        )
        self.queue_age = Gauge(
            "bidops_oldest_queued_seconds", "Age of oldest queued task", registry=registry
        )
        self.disk_free = Gauge(
            "bidops_data_disk_free_bytes", "Free bytes on data volume", registry=registry
        )

    def _database(self, path, *, inventory=False):
        # mode=rw refuses to create a replacement empty database. The bounded
        # admission probe rolls back immediately without changing any row.
        uri = Path(path).resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as conn:
            conn.execute("PRAGMA busy_timeout=100")
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
            conn.execute("SELECT 1").fetchone()
            if not inventory:
                return {}
            count, oldest = conn.execute(
                "SELECT COUNT(*),MIN(created_at) FROM ("
                "SELECT j.created_at FROM durable_jobs j JOIN durable_job_state s "
                "ON s.run_id=j.run_id WHERE s.status='queued' AND s.quarantined=0 "
                "AND s.purge_state='live' UNION ALL "
                "SELECT j.created_at FROM ingestion_runs j JOIN ingestion_run_state s "
                "ON s.run_id=j.run_id WHERE s.status='queued' AND s.quarantined=0)"
            ).fetchone()
            failures = conn.execute(
                "SELECT COUNT(*) FROM ingestion_source_state WHERE failure_count>0"
            ).fetchone()[0]
            return {"queue_count": count, "oldest": oldest, "source_failures": failures}

    def sample(self):
        now = self.clock()
        checks = {name: True for name in COMPONENTS}
        try:
            inventory = self._database(
                make_url(self.data_settings.database_url).database, inventory=True
            )
            age = max(0, now - inventory["oldest"]) if inventory["oldest"] is not None else 0
            self.queue_size.set(inventory["queue_count"])
            self.queue_age.set(age)
            checks["queue_backlog"] = (
                inventory["queue_count"] < self.settings.backlog_count
                and age < self.settings.oldest_queued_seconds
            )
            if self.state.ingestion.settings.enabled:
                checks["source"] = inventory["source_failures"] == 0
        except (sqlite3.Error, OSError, ValueError, TypeError):
            checks["database"] = checks["queue_backlog"] = checks["source"] = False
        jobs = self.state.jobs
        if jobs is not None:
            checks["worker"] = jobs.started
            try:
                self._database(jobs.path)
            except (sqlite3.Error, OSError, ValueError, TypeError):
                checks["workflow_database"] = False
        try:
            free = shutil.disk_usage(self.data_settings.data_dir).free
            self.disk_free.set(free)
            checks["disk"] = free >= self.settings.minimum_free_bytes
        except OSError:
            checks["disk"] = False
        checks["maintenance"] = not self.state.maintenance_failed
        with self.lock:
            for component, healthy in checks.items():
                previous = self.checks[component] if self.observed_at is not None else None
                self.observations.labels(component).set(int(healthy))
                if (previous is None and not healthy) or (
                    previous is not None and healthy != previous
                ):
                    state = "resolved" if healthy else "firing"
                    event = {
                        "event": "dependency_alert",
                        "component": component,
                        "state": state,
                        "observed_at": datetime.fromtimestamp(now, UTC).isoformat(),
                    }
                    self.events.append(event)
                    self.transitions.labels(component, state).inc()
                    LOGGER.warning(json.dumps(event))
            self.checks = checks
            self.observed_at = now
            self.last_sample.set(now)
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            current = (
                self.observed_at is not None
                and 0 <= self.clock() - self.observed_at <= self.settings.stale_seconds
            )
            return {
                "enabled": self.settings.enabled,
                "ready": current and all(self.checks.values()),
                "checks": {**self.checks, "observation_current": current},
                "observed_at": self.observed_at,
                "events": list(self.events),
                "delivery": "local_log_only",
                "external_recipient_verified": False,
            }
