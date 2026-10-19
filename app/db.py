"""SQLite index shared by the web and updater containers."""
import os
import sqlite3

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "fixtures.db")
FILES_DIR = os.path.join(DATA_DIR, "files")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    id          INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    company     TEXT NOT NULL,
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '',
    revised     TEXT,
    modes       TEXT NOT NULL DEFAULT '[]',
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    first_seen  TEXT NOT NULL,
    new_at      TEXT,
    updated_at  TEXT,
    removed     INTEGER NOT NULL DEFAULT 0,
    removed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_fixtures_company ON fixtures(company COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    status        TEXT NOT NULL,
    message       TEXT NOT NULL DEFAULT '',
    source_label  TEXT,
    n_new         INTEGER NOT NULL DEFAULT 0,
    n_updated     INTEGER NOT NULL DEFAULT 0,
    n_removed     INTEGER NOT NULL DEFAULT 0,
    n_unchanged   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS changes (
    id        INTEGER PRIMARY KEY,
    run_id    INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    filename  TEXT NOT NULL,
    company   TEXT NOT NULL,
    name      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_run ON changes(run_id);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init():
    os.makedirs(FILES_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
