CREATE TABLE IF NOT EXISTS company_objects (
    object_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('profile','material')),
    owner_id TEXT NOT NULL,
    head_version TEXT NOT NULL,
    retention_until REAL NOT NULL,
    deleted_at REAL,
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    purge_state TEXT NOT NULL DEFAULT 'live' CHECK(purge_state IN ('live','pending','purged')),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS company_grants (
    object_id TEXT NOT NULL REFERENCES company_objects(object_id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('read','write','grant','delete')),
    PRIMARY KEY(object_id,subject_id,action)
);
CREATE INDEX IF NOT EXISTS company_grant_subject ON company_grants(subject_id,action,object_id);
CREATE TABLE IF NOT EXISTS company_profile_revisions (
    version_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL REFERENCES company_objects(object_id),
    sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS company_profile_object ON company_profile_revisions(object_id,created_at);
CREATE TRIGGER IF NOT EXISTS company_profile_revision_no_update BEFORE UPDATE ON company_profile_revisions
BEGIN SELECT RAISE(ABORT, 'company revisions are immutable'); END;
CREATE TABLE IF NOT EXISTS company_material_revisions (
    version_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL REFERENCES company_objects(object_id),
    sha256 TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    blob_path TEXT NOT NULL UNIQUE,
    text_path TEXT NOT NULL UNIQUE,
    provenance_kind TEXT NOT NULL,
    source_attestation TEXT,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS company_material_object ON company_material_revisions(object_id,created_at);
CREATE TRIGGER IF NOT EXISTS company_material_revision_no_update BEFORE UPDATE ON company_material_revisions
BEGIN SELECT RAISE(ABORT, 'company revisions are immutable'); END;
CREATE TABLE IF NOT EXISTS company_profile_sources (
    profile_version TEXT NOT NULL,
    material_id TEXT NOT NULL,
    material_version TEXT NOT NULL,
    PRIMARY KEY(profile_version,material_id,material_version)
);
CREATE TABLE IF NOT EXISTS company_revision_tombstones (
    version_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text_sha256 TEXT,
    purged_at REAL NOT NULL
);
