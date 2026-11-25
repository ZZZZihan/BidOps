CREATE TABLE IF NOT EXISTS review_cases (
    case_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    candidate_batch_id TEXT NOT NULL REFERENCES qualification_results(result_id),
    create_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(owner_id,create_key)
);
CREATE TABLE IF NOT EXISTS review_case_state (
    case_id TEXT PRIMARY KEY REFERENCES review_cases(case_id),
    head_revision_id TEXT NOT NULL,
    review_version INTEGER NOT NULL DEFAULT 0 CHECK(review_version>=0),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','awaiting_materials','recomputing','closed')),
    review_epoch TEXT NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    purge_state TEXT NOT NULL DEFAULT 'live' CHECK(purge_state IN ('live','purged')),
    purged_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_revisions (
    revision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES review_cases(case_id),
    parent_revision_id TEXT REFERENCES review_revisions(revision_id),
    run_id TEXT NOT NULL UNIQUE REFERENCES durable_jobs(run_id),
    result_id TEXT NOT NULL REFERENCES qualification_results(result_id),
    result_revision TEXT NOT NULL,
    inputs_manifest_sha256 TEXT NOT NULL,
    trigger_event_id TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_assignments (
    case_id TEXT NOT NULL REFERENCES review_cases(case_id),
    subject_id TEXT NOT NULL,
    PRIMARY KEY(case_id,subject_id)
);
CREATE TABLE IF NOT EXISTS review_events (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES review_cases(case_id),
    revision_id TEXT NOT NULL REFERENCES review_revisions(revision_id),
    assessment_id TEXT,
    condition_id TEXT,
    event_kind TEXT NOT NULL CHECK(event_kind IN
      ('decision','assignment','close','successor_linked','revision_attached','reauthorized')),
    actor_id TEXT NOT NULL,
    authorization_context_id TEXT NOT NULL,
    occurred_at REAL NOT NULL,
    review_version INTEGER NOT NULL CHECK(review_version>0),
    request_sha256 TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    supersedes_event_id TEXT REFERENCES review_events(event_id),
    review_epoch TEXT NOT NULL,
    UNIQUE(case_id,actor_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS review_event_bodies (
    event_id TEXT PRIMARY KEY REFERENCES review_events(event_id),
    body_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_outbox (
    event_id TEXT PRIMARY KEY REFERENCES review_events(event_id),
    run_id TEXT NOT NULL REFERENCES durable_jobs(run_id),
    kind TEXT NOT NULL CHECK(kind IN ('decision','closed','superseded')),
    review_epoch TEXT NOT NULL,
    frozen INTEGER NOT NULL DEFAULT 0 CHECK(frozen IN (0,1)),
    delivered_at REAL,
    consumed_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
    last_error_code TEXT
);
CREATE TABLE IF NOT EXISTS review_sources (
    case_id TEXT NOT NULL REFERENCES review_cases(case_id),
    object_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(case_id,object_id,version_id)
);
CREATE TABLE IF NOT EXISTS review_successors (
    run_id TEXT PRIMARY KEY REFERENCES durable_jobs(run_id),
    case_id TEXT NOT NULL REFERENCES review_cases(case_id),
    parent_revision_id TEXT NOT NULL REFERENCES review_revisions(revision_id),
    trigger_event_id TEXT NOT NULL REFERENCES review_events(event_id),
    link_event_id TEXT NOT NULL UNIQUE REFERENCES review_events(event_id),
    inputs_manifest_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS review_case_no_update BEFORE UPDATE ON review_cases
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_revision_no_update BEFORE UPDATE ON review_revisions
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_event_no_update BEFORE UPDATE ON review_events
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_event_body_no_update BEFORE UPDATE ON review_event_bodies
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_source_no_update BEFORE UPDATE ON review_sources
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_successor_no_update BEFORE UPDATE ON review_successors
BEGIN SELECT RAISE(ABORT,'review history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_case_no_delete BEFORE DELETE ON review_cases
BEGIN SELECT RAISE(ABORT,'review audit metadata cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS review_revision_no_delete BEFORE DELETE ON review_revisions
BEGIN SELECT RAISE(ABORT,'review audit metadata cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS review_event_no_delete BEFORE DELETE ON review_events
BEGIN SELECT RAISE(ABORT,'review audit metadata cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS review_successor_no_delete BEFORE DELETE ON review_successors
BEGIN SELECT RAISE(ABORT,'review audit metadata cannot be deleted'); END;
CREATE INDEX IF NOT EXISTS review_case_owner ON review_cases(owner_id,created_at,case_id);
CREATE INDEX IF NOT EXISTS review_source_object ON review_sources(object_id,case_id);
CREATE INDEX IF NOT EXISTS review_event_target ON review_events(case_id,revision_id,assessment_id,condition_id,review_version);
