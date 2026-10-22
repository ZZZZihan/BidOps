CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL,
    question_sha256 TEXT NOT NULL,
    reserved_cny REAL NOT NULL,
    estimated_cny REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    model_requests INTEGER NOT NULL DEFAULT 0,
    error_code TEXT
);
