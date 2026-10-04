"""users: 코레일 기기 프로파일 / Android ID

사용자마다 고정된 기기 신원으로 코레일에 접속하기 위한 컬럼 (pykorail 0.2.1+).
여러 계정이 같은 Android ID를 쓰면 코레일이 차단하므로 Android ID는 유일해야 한다.
기존 사용자는 NULL 로 두고 다음 코레일 로그인 때 발급한다.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("korail_device_profile", sa.String(length=40), nullable=True)
        )
        batch.add_column(
            sa.Column("korail_android_id", sa.String(length=16), nullable=True)
        )
        batch.create_index(
            "ix_users_korail_android_id", ["korail_android_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_korail_android_id")
        batch.drop_column("korail_android_id")
        batch.drop_column("korail_device_profile")
