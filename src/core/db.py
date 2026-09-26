"""SQLAlchemy 데이터베이스 설정

- 로컬/subprocess 모드: SQLite (기본값 ``sqlite:///./korail_bot.db``)
- Celery(MQ) 모드(Docker): PostgreSQL
"""

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """DB 저장용 UTC 시각 (naive)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_database_url(url: str) -> str:
    """``postgresql://`` URL은 psycopg(v3) 드라이버를 사용하도록 변환"""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


class Database:
    def __init__(self, url: str):
        self.url = normalize_database_url(url)
        parsed = make_url(self.url)
        kwargs = {"pool_pre_ping": True}

        if parsed.get_backend_name() == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
            if parsed.database in (None, "", ":memory:"):
                kwargs["poolclass"] = StaticPool
            else:
                directory = os.path.dirname(os.path.abspath(parsed.database))
                os.makedirs(directory, exist_ok=True)

        self.engine = create_engine(self.url, **kwargs)

        if parsed.get_backend_name() == "sqlite":
            is_file_db = parsed.database not in (None, "", ":memory:")

            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                if is_file_db:
                    # WAL: 쓰기 중에도 읽기가 막히지 않고, 커밋마다 디스크 동기화를 하지 않아
                    # 동시 요청 시 쓰기 대기가 크게 줄어듦 (기본 DELETE 모드는 커밋당 ~20ms)
                    # synchronous=NORMAL은 WAL에서 권장값: 전원 장애 시 마지막 몇 트랜잭션만
                    # 유실될 수 있고 DB가 손상되지는 않음
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        self._sessionmaker = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def create_all(self, retries: int = 10, delay: float = 2.0) -> None:
        """테이블 생성 (DB 컨테이너가 늦게 뜨는 경우를 위해 재시도)"""
        from . import models  # noqa: F401  (모델 등록)

        for attempt in range(1, retries + 1):
            try:
                Base.metadata.create_all(self.engine)
                return
            except Exception as e:
                if attempt == retries:
                    raise
                logger.warning(
                    f"Database not ready ({e}), retrying {attempt}/{retries}..."
                )
                time.sleep(delay)

    def dispose(self) -> None:
        self.engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
