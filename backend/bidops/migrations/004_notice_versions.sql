CREATE TABLE nd_projects (
    project_id TEXT PRIMARY KEY,
    authority TEXT NOT NULL,
    project_code TEXT NOT NULL,
    buyer_name TEXT NOT NULL,
    UNIQUE(authority, project_code, buyer_name)
);
CREATE TABLE nd_logical_notices (
    logical_notice_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE
);
CREATE TABLE nd_versions (
    version_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    logical_notice_id TEXT NOT NULL REFERENCES nd_logical_notices(logical_notice_id),
    project_id TEXT REFERENCES nd_projects(project_id),
    association_state TEXT NOT NULL CHECK(association_state IN ('matched','pending_review')),
    parser_version TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    available_at REAL NOT NULL,
    raw_sha256 TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    legacy_payload_json TEXT NOT NULL
);
CREATE INDEX nd_versions_snapshot ON nd_versions(snapshot_id);
CREATE INDEX nd_versions_project ON nd_versions(project_id);
CREATE TABLE nd_package_versions (
    package_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES nd_versions(version_id),
    label TEXT NOT NULL,
    normalized_label TEXT NOT NULL,
    evidence_start INTEGER NOT NULL,
    evidence_end INTEGER NOT NULL,
    PRIMARY KEY(package_id,version_id)
);
CREATE TABLE nd_observations (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    observed_at REAL NOT NULL,
    source_complete INTEGER NOT NULL CHECK(source_complete IN (0,1)),
    PRIMARY KEY(snapshot_id,observed_at)
);
CREATE INDEX nd_observations_time ON nd_observations(observed_at,snapshot_id);
CREATE TABLE nd_attachments (
    record_id TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL,
    parent_version_id TEXT NOT NULL REFERENCES nd_versions(version_id),
    observed_at REAL NOT NULL,
    acquisition_status TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    raw_sha256 TEXT,
    text_sha256 TEXT,
    normalized_text TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX nd_attachments_parent ON nd_attachments(parent_version_id,observed_at,record_id);
CREATE TRIGGER nd_versions_no_update BEFORE UPDATE ON nd_versions BEGIN SELECT RAISE(ABORT,'nd_immutable_version'); END;
CREATE TRIGGER nd_versions_no_delete BEFORE DELETE ON nd_versions BEGIN SELECT RAISE(ABORT,'nd_immutable_version'); END;
CREATE TRIGGER nd_packages_no_update BEFORE UPDATE ON nd_package_versions BEGIN SELECT RAISE(ABORT,'nd_immutable_package'); END;
CREATE TRIGGER nd_packages_no_delete BEFORE DELETE ON nd_package_versions BEGIN SELECT RAISE(ABORT,'nd_immutable_package'); END;
CREATE TRIGGER nd_observations_no_update BEFORE UPDATE ON nd_observations BEGIN SELECT RAISE(ABORT,'nd_immutable_observation'); END;
CREATE TRIGGER nd_observations_no_delete BEFORE DELETE ON nd_observations BEGIN SELECT RAISE(ABORT,'nd_immutable_observation'); END;
CREATE TRIGGER nd_attachments_no_update BEFORE UPDATE ON nd_attachments BEGIN SELECT RAISE(ABORT,'nd_immutable_attachment'); END;
CREATE TRIGGER nd_attachments_no_delete BEFORE DELETE ON nd_attachments BEGIN SELECT RAISE(ABORT,'nd_immutable_attachment'); END;
