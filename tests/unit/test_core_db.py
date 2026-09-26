"""SQLite 설정"""

from sqlalchemy import text

from core.db import Database


def test_file_sqlite_uses_wal(tmp_path):
    """파일 SQLite는 WAL + synchronous=NORMAL (커밋 비용 감소, 쓰기 중 읽기 허용)"""
    db = Database(f"sqlite:///{tmp_path}/app.db")
    try:
        with db.engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            # NORMAL == 1
            assert conn.execute(text("PRAGMA synchronous")).scalar() == 1
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        db.dispose()


def test_memory_sqlite_keeps_default_journal():
    db = Database("sqlite://")
    try:
        with db.engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "memory"
    finally:
        db.dispose()
