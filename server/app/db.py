"""SQLite 연결과 스키마. 시각은 모두 밀리초 epoch(v1의 `at`과 같음)."""
import os
import sqlite3
import time

# 스키마를 바꿀 때는 MIGRATIONS 끝에 단계를 추가한다(앞 단계는 고치지 않는다).
MIGRATIONS = [
    """
    CREATE TABLE users (
      email TEXT PRIMARY KEY,
      name TEXT NOT NULL DEFAULT '',
      role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
      created_at INTEGER NOT NULL,
      last_login_at INTEGER
    );
    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY,
      email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
      created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL
    );
    CREATE INDEX sessions_email ON sessions(email);
    CREATE TABLE settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at INTEGER NOT NULL,
      updated_by TEXT
    );
    CREATE TABLE shorts (
      id INTEGER PRIMARY KEY,
      video_id TEXT NOT NULL,
      start_sec INTEGER NOT NULL DEFAULT 0,
      end_sec INTEGER NOT NULL DEFAULT 0,
      title TEXT NOT NULL DEFAULT '',
      sermon_title TEXT NOT NULL DEFAULT '',
      extra TEXT NOT NULL DEFAULT '',
      text TEXT NOT NULL,
      model TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      legacy_key TEXT UNIQUE
    );
    CREATE INDEX shorts_created ON shorts(created_at DESC);
    CREATE TABLE setlists (
      id INTEGER PRIMARY KEY,
      playlist_id TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      ok INTEGER NOT NULL,
      total INTEGER NOT NULL,
      songs_json TEXT NOT NULL,
      raw_message TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX setlists_created ON setlists(created_at DESC);
    CREATE TABLE jobs (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK (kind IN ('plan', 'detect')),
      status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'error')),
      input_json TEXT NOT NULL,
      result_json TEXT,
      error TEXT,
      shorts_id INTEGER,
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      finished_at INTEGER
    );
    CREATE INDEX jobs_user ON jobs(created_by, created_at DESC);
    """,
    # 설교 대본. 쇼츠 기획이 끝난 뒤 이어서 받아쓰므로 상태를 따로 둔다('', running, done, error).
    """
    ALTER TABLE shorts ADD COLUMN transcript TEXT NOT NULL DEFAULT '';
    ALTER TABLE shorts ADD COLUMN transcript_status TEXT NOT NULL DEFAULT '';
    ALTER TABLE shorts ADD COLUMN transcript_error TEXT;
    """,
]


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn


def init_db(path: str) -> None:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = connect(path)
    try:
        conn.execute('PRAGMA journal_mode = WAL')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            with conn:
                conn.executescript(sql)
                conn.execute(f'PRAGMA user_version = {i}')
    finally:
        conn.close()


def seed_admins(conn: sqlite3.Connection, emails: list[str]) -> None:
    """ADMIN_EMAILS에 있는 이메일은 항상 관리자로 둔다."""
    with conn:
        for email in emails:
            conn.execute(
                "INSERT INTO users (email, role, created_at) VALUES (?, 'admin', ?) "
                "ON CONFLICT(email) DO UPDATE SET role = 'admin'",
                (email, now_ms()),
            )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str, by: str) -> None:
    conn.execute(
        'INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, '
        'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
        (key, value, now_ms(), by),
    )
