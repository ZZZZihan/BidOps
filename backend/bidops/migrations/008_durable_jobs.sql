CREATE TABLE IF NOT EXISTS durable_jobs (
    run_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    inputs_manifest_sha256 TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(owner_id,idempotency_key)
);
CREATE TRIGGER IF NOT EXISTS durable_job_no_update BEFORE UPDATE ON durable_jobs
BEGIN SELECT RAISE(ABORT, 'durable job inputs are immutable'); END;
CREATE TABLE IF NOT EXISTS durable_job_inputs (
    run_id TEXT PRIMARY KEY REFERENCES durable_jobs(run_id),
    body_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS durable_job_input_no_update BEFORE UPDATE ON durable_job_inputs
BEGIN SELECT RAISE(ABORT, 'durable job inputs are immutable'); END;
CREATE TABLE IF NOT EXISTS durable_job_sources (
    run_id TEXT NOT NULL REFERENCES durable_jobs(run_id),
    object_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(run_id,object_id,version_id)
);
CREATE TRIGGER IF NOT EXISTS durable_job_source_no_update BEFORE UPDATE ON durable_job_sources
BEGIN SELECT RAISE(ABORT, 'durable job sources are immutable'); END;
CREATE TABLE IF NOT EXISTS durable_job_state (
    run_id TEXT PRIMARY KEY REFERENCES durable_jobs(run_id),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN
      ('queued','running','waiting_review','cancel_requested','cancelled','failed','timed_out','interrupted','completed')),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK(state_version>=0),
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_id TEXT,
    result_revision TEXT,
    error_code TEXT,
    updated_at REAL NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    purge_state TEXT NOT NULL DEFAULT 'live' CHECK(purge_state IN ('live','purged')),
    purged_at REAL
);
CREATE TRIGGER IF NOT EXISTS durable_cancelled_terminal BEFORE UPDATE ON durable_job_state
WHEN OLD.status='cancelled' AND NEW.status!='cancelled'
BEGIN SELECT RAISE(ABORT, 'cancelled task cannot resume'); END;
CREATE TABLE IF NOT EXISTS durable_model_attempts (
    run_id TEXT NOT NULL REFERENCES durable_jobs(run_id),
    seq INTEGER NOT NULL CHECK(seq>=1),
    ledger_id TEXT,
    request_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started','response_received','unknown')),
    started_at REAL NOT NULL,
    completed_at REAL,
    response_sha256 TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(run_id,seq)
);
CREATE TABLE IF NOT EXISTS durable_attempt_bodies (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY(run_id,seq),
    FOREIGN KEY(run_id,seq) REFERENCES durable_model_attempts(run_id,seq)
);
CREATE TRIGGER IF NOT EXISTS durable_attempt_body_no_update BEFORE UPDATE ON durable_attempt_bodies
BEGIN SELECT RAISE(ABORT, 'model responses are immutable'); END;
CREATE TABLE IF NOT EXISTS durable_review_fixtures (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES durable_jobs(run_id),
    idempotency_key TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    UNIQUE(run_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS durable_review_fixture_bodies (
    event_id TEXT PRIMARY KEY REFERENCES durable_review_fixtures(event_id),
    event_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS durable_jobs_owner ON durable_jobs(owner_id,created_at,run_id);
CREATE INDEX IF NOT EXISTS durable_job_source_object ON durable_job_sources(object_id,run_id);
