"""baseline: Alembic 도입 전 create_all 로 만든 스키마

users / web_sessions / reservations / push_subscriptions 를 그대로 둔다.
Alembic 도입 전부터 운영하던 DB는 시작 시 이 리비전으로 stamp 된 뒤 이후 리비전이 적용된다.

Revision ID: 0001
Revises:
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
