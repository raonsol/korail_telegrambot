"""Alembic 환경

- 앱 시작 시: ``Database.migrate()``가 연결을 ``config.attributes["connection"]``으로 넘김
- CLI (저장소 루트의 alembic.ini): ``DATABASE_URL``(기본 sqlite:///./korail_bot.db)에 연결
"""

import os

from alembic import context
from sqlalchemy import create_engine

from core import models  # noqa: F401  (모델 등록)
from core.db import Base, normalize_database_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return normalize_database_url(
        os.getenv("DATABASE_URL", "sqlite:///./korail_bot.db")
    )


def _configure(**kwargs) -> None:
    # SQLite는 ALTER TABLE 이 제한적이라 컬럼 변경/삭제를 테이블 재생성으로 처리
    context.configure(target_metadata=target_metadata, render_as_batch=True, **kwargs)


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(_url())
    try:
        with engine.connect() as connection:
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
