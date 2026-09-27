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


def test_memory_sqlite_is_safe_across_threads():
    """메모리 SQLite는 연결 하나를 공유하므로 스레드별 세션이 겹치지 않아야 함

    (예약 시작은 asyncio.to_thread에서 DB를 쓰므로 동시 요청이 여러 스레드에서 실행됨)
    """
    import threading

    from core.db import utcnow
    from core.models import User

    db = Database("sqlite://")
    db.create_all()
    errors = []

    def worker(n):
        try:
            for i in range(30):
                user_id = f"010{n:02d}{i:04d}"
                with db.session() as s:
                    s.add(User(id=user_id, is_active=True, created_at=utcnow()))
                with db.session() as s:
                    assert s.get(User, user_id) is not None
        except Exception as e:  # 스레드 예외는 테스트로 전달되지 않으므로 수집
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        with db.session() as s:
            assert s.query(User).count() == 8 * 30
    finally:
        db.dispose()
