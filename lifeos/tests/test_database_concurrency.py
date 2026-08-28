import sqlite3

from app.db import conn


def test_database_uses_wal_and_waits_for_short_write_contention(fresh_db):
    with conn() as database:
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert database.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000

    reader = sqlite3.connect(fresh_db, timeout=1)
    reader.execute("BEGIN")
    reader.execute("SELECT value FROM settings LIMIT 1").fetchone()
    try:
        with conn() as writer:
            writer.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                ("concurrency_probe", "ok"),
            )
    finally:
        reader.close()

    with conn() as database:
        value = database.execute(
            "SELECT value FROM settings WHERE key='concurrency_probe'"
        ).fetchone()
    assert value[0] == "ok"
