CREATE TABLE IF NOT EXISTS "user" (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS note (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    title VARCHAR(160) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_note_user_id ON note(user_id);

CREATE TABLE IF NOT EXISTS task (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    title VARCHAR(160) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_date DATE NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_task_user_id ON task(user_id);

CREATE TABLE IF NOT EXISTS login_attempt_bucket (
    key VARCHAR(64) PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_login_attempt_bucket_window_started_at
    ON login_attempt_bucket(window_started_at);

CREATE TABLE IF NOT EXISTS flask_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255),
    data BYTEA,
    expiry TIMESTAMP WITHOUT TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_flask_sessions_session_id
    ON flask_sessions(session_id);
CREATE INDEX IF NOT EXISTS ix_flask_sessions_expiry
    ON flask_sessions(expiry);