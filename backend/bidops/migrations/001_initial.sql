CREATE TABLE IF NOT EXISTS urls (
    source_url TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'reference_only'
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL REFERENCES urls(source_url),
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE(source_url, sha256)
);
CREATE INDEX IF NOT EXISTS snapshots_hash ON snapshots(sha256);
CREATE TABLE IF NOT EXISTS notices (
    notice_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE REFERENCES snapshots(snapshot_id),
    project_code TEXT,
    notice_kind TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notices_project ON notices(project_code);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    event TEXT NOT NULL,
    code TEXT NOT NULL,
    source_url TEXT,
    metadata_json TEXT NOT NULL
);
