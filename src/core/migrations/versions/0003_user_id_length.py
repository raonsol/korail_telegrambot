"""users.id 길이 20 → 50

관리자 코레일 계정(ADMIN_KORAIL_ID)도 사용자 행으로 두어 기기 신원을 저장한다.
이 ID는 이메일·회원번호일 수 있으므로 web_sessions.korail_id 등과 같은 50자로 늘린다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(length=20),
            type_=sa.String(length=50),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
