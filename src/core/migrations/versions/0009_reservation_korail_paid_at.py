"""reservations: 결제 완료 확인 시각 (결제한 예약에는 결제 기한 알림을 보내지 않음)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.add_column(sa.Column("korail_paid_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.drop_column("korail_paid_at")
