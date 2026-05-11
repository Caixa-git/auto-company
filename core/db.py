"""
SQLite connection and schema management.
"""

import sqlite3
import threading
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_local = threading.local()
_schema_initialized = set()  # track which db_paths have been initialized
_schema_lock = threading.Lock()


def _apply_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent  TEXT    NOT NULL,
            to_agent    TEXT    NOT NULL,
            msg_type    TEXT    NOT NULL,
            priority    INTEGER NOT NULL DEFAULT 5,
            payload     TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS agent_states (
            agent_name  TEXT    PRIMARY KEY,
            status      TEXT    NOT NULL DEFAULT 'idle',
            state_json  TEXT    NOT NULL DEFAULT '{}',
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hotl_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent  TEXT    NOT NULL,
            urgency     TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            body        TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS event_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name  TEXT    NOT NULL,
            event_type  TEXT    NOT NULL,
            detail      TEXT    NOT NULL DEFAULT '{}',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Single shared connection (main thread use)."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_thread_connection(db_path: str) -> sqlite3.Connection:
    """
    Per-thread independent SQLite connection with auto schema init.
    For :memory: each connection is its own DB — use a shared file path in production.
    """
    key = f"{threading.get_ident()}:{db_path}"
    if getattr(_local, "key", None) != key:
        _local.conn = sqlite3.connect(db_path)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _apply_schema(_local.conn)
        _local.key = key
    return _local.conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_schema(conn: sqlite3.Connection):
    """Explicit schema init (for main-thread connection)."""
    _apply_schema(conn)
    logger.info("DB schema initialized")
