import json
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.drivername != "sqlite" or not parsed.database or parsed.database == ":memory:":
            raise ValueError("P0 requires a local file-backed SQLite database")
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(self.engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")

    def migrate(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)"
            )
            for item in sorted(
                files("bidops").joinpath("migrations").iterdir(), key=lambda x: x.name
            ):
                if not item.name.endswith(".sql"):
                    continue
                if conn.execute(
                    text("SELECT 1 FROM schema_migrations WHERE version=:v"), {"v": item.name}
                ).first():
                    continue
                statement = ""
                for line in item.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.exec_driver_sql(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Incomplete migration: {item.name}")
                conn.execute(text("INSERT INTO schema_migrations VALUES (:v)"), {"v": item.name})

    def rows(self, sql: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]

    def execute(self, sql: str, params: dict | None = None):
        with self.engine.begin() as conn:
            conn.execute(text(sql), params or {})

    def audit(self, event_name: str, code: str, url: str | None = None, **metadata):
        self.execute(
            "INSERT INTO audit_events(observed_at,event,code,source_url,metadata_json) "
            "VALUES (:at,:event,:code,:url,:meta)",
            {
                "at": datetime.now(UTC).isoformat(),
                "event": event_name,
                "code": code,
                "url": url,
                "meta": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            },
        )
