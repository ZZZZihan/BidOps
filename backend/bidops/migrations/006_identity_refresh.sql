-- 005 company objects precede this migration; neither migration requires the other's tables.
-- Existing application cookies are deliberately not backfilled into trusted identity snapshots.
CREATE TABLE IF NOT EXISTS enterprise_subjects (
    owner_id TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    roles_json TEXT NOT NULL,
    roles_checked_at REAL NOT NULL, -- Signed ID Token iat, not local receipt time.
    fresh_until REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(issuer, subject)
);
CREATE TABLE IF NOT EXISTS enterprise_login_identities (
    token_hash TEXT PRIMARY KEY REFERENCES enterprise_logins(token_hash) ON DELETE CASCADE,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    sid TEXT,
    authenticated_at REAL NOT NULL,
    fresh_until REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS enterprise_login_identity_sid
    ON enterprise_login_identities(issuer, sid);
CREATE INDEX IF NOT EXISTS enterprise_login_identity_subject
    ON enterprise_login_identities(issuer, subject);
CREATE TABLE IF NOT EXISTS enterprise_logout_receipts (
    issuer TEXT NOT NULL,
    jti_hash TEXT NOT NULL,
    issued_at REAL NOT NULL,
    received_at REAL NOT NULL,
    PRIMARY KEY(issuer, jti_hash)
);
CREATE TABLE IF NOT EXISTS enterprise_identity_revocations (
    issuer TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('sub', 'sid')),
    target_value TEXT NOT NULL,
    target_subject TEXT NOT NULL DEFAULT '',
    revoked_at REAL NOT NULL,
    received_at REAL NOT NULL,
    PRIMARY KEY(issuer, target_type, target_value, target_subject)
);
