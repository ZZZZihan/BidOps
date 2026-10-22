CREATE TABLE IF NOT EXISTS enterprise_logins (
    token_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    csrf TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS enterprise_login_expiry ON enterprise_logins(expires_at);
CREATE TABLE IF NOT EXISTS enterprise_oauth_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enterprise_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    lock_until REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS enterprise_session_owner ON enterprise_sessions(owner_id, updated_at);
CREATE TABLE IF NOT EXISTS enterprise_turns (
    owner_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES enterprise_sessions(session_id) ON DELETE CASCADE,
    request_sha256 TEXT NOT NULL,
    question TEXT NOT NULL,
    created_at REAL NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('started', 'completed', 'failed')),
    error_code TEXT,
    error_status INTEGER,
    response_json TEXT,
    messages_json TEXT,
    PRIMARY KEY(owner_id, request_id)
);
CREATE INDEX IF NOT EXISTS enterprise_turn_session ON enterprise_turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS enterprise_audit (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at REAL NOT NULL,
    owner_id TEXT,
    event TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS enterprise_audit_no_update BEFORE UPDATE ON enterprise_audit
BEGIN SELECT RAISE(ABORT, 'enterprise audit is append only'); END;
CREATE TRIGGER IF NOT EXISTS enterprise_audit_no_delete BEFORE DELETE ON enterprise_audit
BEGIN SELECT RAISE(ABORT, 'enterprise audit is append only'); END;
