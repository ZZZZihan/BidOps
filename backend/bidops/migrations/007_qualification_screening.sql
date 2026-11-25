CREATE TABLE IF NOT EXISTS qualification_results (
    result_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS qualification_result_no_update BEFORE UPDATE ON qualification_results
BEGIN SELECT RAISE(ABORT, 'qualification history is immutable'); END;
CREATE TABLE IF NOT EXISTS qualification_result_bodies (
    result_id TEXT PRIMARY KEY REFERENCES qualification_results(result_id),
    body_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS qualification_body_no_update BEFORE UPDATE ON qualification_result_bodies
BEGIN SELECT RAISE(ABORT, 'qualification history is immutable'); END;
CREATE TABLE IF NOT EXISTS qualification_result_sources (
    result_id TEXT NOT NULL REFERENCES qualification_results(result_id),
    object_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(result_id,object_id,version_id)
);
CREATE TRIGGER IF NOT EXISTS qualification_source_no_update BEFORE UPDATE ON qualification_result_sources
BEGIN SELECT RAISE(ABORT, 'qualification history is immutable'); END;
CREATE TABLE IF NOT EXISTS qualification_result_lifecycle (
    result_id TEXT PRIMARY KEY REFERENCES qualification_results(result_id),
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    purge_state TEXT NOT NULL DEFAULT 'live' CHECK(purge_state IN ('live','purged')),
    purged_at REAL
);
CREATE INDEX IF NOT EXISTS qualification_history_owner ON qualification_results(owner_id,created_at,result_id);
CREATE INDEX IF NOT EXISTS qualification_source_object ON qualification_result_sources(object_id,result_id);
