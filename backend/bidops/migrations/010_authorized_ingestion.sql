CREATE TABLE ingestion_policy_revisions (
    policy_sha256 TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    revision TEXT,
    body_json TEXT NOT NULL,
    recorded_at REAL NOT NULL
);
CREATE TABLE ingestion_source_state (
    source_id TEXT PRIMARY KEY,
    cursor_json TEXT NOT NULL DEFAULT 'null',
    cursor_version INTEGER NOT NULL DEFAULT 0,
    active_run_id TEXT,
    last_attempt REAL,
    last_success REAL,
    next_request_at REAL NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE ingestion_runs (
    run_id TEXT PRIMARY KEY,
    native_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES ingestion_source_state(source_id),
    method TEXT NOT NULL CHECK(method IN ('manual_import','http_fetch')),
    trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('manual','schedule')),
    schedule_native_name TEXT REFERENCES ingestion_schedule_versions(native_name),
    policy_sha256 TEXT NOT NULL REFERENCES ingestion_policy_revisions(policy_sha256),
    policy_revision TEXT,
    input_sha256 TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    application_version TEXT NOT NULL,
    created_at REAL NOT NULL,
    idempotency_key TEXT NOT NULL,
    UNIQUE(owner_id,idempotency_key),
    CHECK((trigger_kind='schedule' AND schedule_native_name IS NOT NULL) OR
        (trigger_kind='manual' AND schedule_native_name IS NULL))
);
CREATE TABLE ingestion_inputs (
    run_id TEXT PRIMARY KEY REFERENCES ingestion_runs(run_id),
    body_json TEXT NOT NULL
);
CREATE TABLE ingestion_run_state (
    run_id TEXT PRIMARY KEY REFERENCES ingestion_runs(run_id),
    status TEXT NOT NULL CHECK(status IN ('queued','running','cancel_requested','cancelled',
        'completed','partial','failed','interrupted')),
    state_version INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    error_code TEXT,
    next_page INTEGER NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    counts_json TEXT NOT NULL DEFAULT '{}',
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1))
);
CREATE TABLE ingestion_pages (
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    page_seq INTEGER NOT NULL,
    page_sha256 TEXT NOT NULL,
    from_cursor_version INTEGER NOT NULL,
    cursor_after_json TEXT NOT NULL,
    observed_at REAL NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(run_id,page_seq)
);
CREATE TABLE ingestion_page_attempts (
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    page_seq INTEGER NOT NULL CHECK(page_seq>=0),
    from_cursor_version INTEGER NOT NULL CHECK(from_cursor_version>=0),
    cursor_before_json TEXT NOT NULL,
    observed_at REAL NOT NULL,
    PRIMARY KEY(run_id,page_seq)
);
CREATE TABLE ingestion_failures (
    event_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES ingestion_runs(run_id),
    source_id TEXT NOT NULL,
    page_index INTEGER,
    code TEXT NOT NULL,
    observed_at REAL NOT NULL
);
CREATE TABLE ingestion_schedules (
    schedule_id TEXT PRIMARY KEY,
    native_name TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL REFERENCES ingestion_policy_revisions(policy_sha256),
    application_version TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    updated_at REAL NOT NULL,
    last_run_id TEXT,
    last_error_code TEXT
);
CREATE TABLE ingestion_schedule_commands (
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    schedule_id TEXT NOT NULL REFERENCES ingestion_schedules(schedule_id),
    application_version TEXT NOT NULL,
    observed_at REAL NOT NULL,
    PRIMARY KEY(owner_id,idempotency_key)
);
CREATE TABLE ingestion_schedule_versions (
    native_name TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES ingestion_schedules(schedule_id),
    state_version INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL REFERENCES ingestion_policy_revisions(policy_sha256),
    application_version TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE ingestion_schedule_ticks (
    native_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES ingestion_schedules(schedule_id),
    application_version TEXT NOT NULL,
    observed_at REAL NOT NULL,
    run_id TEXT REFERENCES ingestion_runs(run_id),
    error_code TEXT
);
CREATE INDEX ingestion_runs_owner ON ingestion_runs(owner_id,created_at);
CREATE INDEX ingestion_run_status ON ingestion_run_state(status);
CREATE INDEX ingestion_failures_run ON ingestion_failures(run_id,observed_at);
CREATE TRIGGER ingestion_policy_no_update BEFORE UPDATE ON ingestion_policy_revisions
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_policy'); END;
CREATE TRIGGER ingestion_policy_no_delete BEFORE DELETE ON ingestion_policy_revisions
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_policy'); END;
CREATE TRIGGER ingestion_run_no_update BEFORE UPDATE ON ingestion_runs
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_run'); END;
CREATE TRIGGER ingestion_run_no_delete BEFORE DELETE ON ingestion_runs
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_run'); END;
CREATE TRIGGER ingestion_input_no_update BEFORE UPDATE ON ingestion_inputs
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_input'); END;
CREATE TRIGGER ingestion_input_no_delete BEFORE DELETE ON ingestion_inputs
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_input'); END;
CREATE TRIGGER ingestion_page_no_update BEFORE UPDATE ON ingestion_pages
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_page'); END;
CREATE TRIGGER ingestion_page_no_delete BEFORE DELETE ON ingestion_pages
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_page'); END;
CREATE TRIGGER ingestion_attempt_no_update BEFORE UPDATE ON ingestion_page_attempts
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_page_attempt'); END;
CREATE TRIGGER ingestion_attempt_no_delete BEFORE DELETE ON ingestion_page_attempts
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_page_attempt'); END;
CREATE TRIGGER ingestion_schedule_version_no_update BEFORE UPDATE ON ingestion_schedule_versions
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_schedule_version'); END;
CREATE TRIGGER ingestion_schedule_version_no_delete BEFORE DELETE ON ingestion_schedule_versions
BEGIN SELECT RAISE(ABORT,'immutable_ingestion_schedule_version'); END;
