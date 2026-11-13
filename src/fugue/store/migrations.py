"""Ordered, transactional SQLite schema migrations."""

import sqlite3

from fugue.store.records import StoreError

MIGRATIONS: list[str] = [
    """
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY, ticket_number INT NOT NULL, repo TEXT NOT NULL,
        state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE stage_attempts (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL, attempt INT NOT NULL, status TEXT NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT, detail TEXT,
        UNIQUE(run_id, stage, attempt)
    );
    CREATE TABLE operations (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        kind TEXT NOT NULL, key TEXT NOT NULL, intent_json TEXT NOT NULL,
        result_json TEXT, status TEXT NOT NULL
            CHECK(status IN ('intent', 'done', 'failed', 'reconciled')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id, kind, key)
    );
    CREATE TABLE approvals (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        kind TEXT NOT NULL, subject TEXT NOT NULL, head_sha TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected')),
        decided_by TEXT NOT NULL, reason TEXT NOT NULL,
        created_at TEXT NOT NULL, invalidated_at TEXT
    );
    CREATE TABLE agent_sessions (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL, provider TEXT NOT NULL, authentication_mode TEXT NOT NULL,
        session_id TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE usage_records (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL, provider TEXT NOT NULL, authentication_mode TEXT NOT NULL,
        input_tokens INT, cached_input_tokens INT, output_tokens INT, reasoning_tokens INT,
        cost_estimate_usd REAL, duration_ms INT, created_at TEXT NOT NULL
    );
    CREATE TABLE artifacts (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        name TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
        size_bytes INT NOT NULL, created_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE usage_records ADD COLUMN model TEXT;
    ALTER TABLE usage_records ADD COLUMN tier TEXT;
    """,
]


def apply(connection: sqlite3.Connection) -> None:
    """Apply all missing migrations atomically, including their version updates."""
    # executescript commits pending transactions, so execute individual statements instead.
    connection.execute("BEGIN IMMEDIATE")
    try:
        version: int = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise StoreError(f"Database version {version} is newer than {len(MIGRATIONS)}")
        for index in range(version, len(MIGRATIONS)):
            for statement in MIGRATIONS[index].split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {index + 1}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
