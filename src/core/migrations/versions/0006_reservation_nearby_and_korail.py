"""reservations: 인근역 포함 여부, 코레일 예약번호·결제 기한 (예약 후 관리)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.add_column(
            sa.Column(
                "include_nearby",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("korail_rsv_id", sa.String(length=20)))
        batch.add_column(sa.Column("pay_deadline", sa.DateTime()))
        batch.add_column(sa.Column("pay_reminded_at", sa.DateTime()))
        batch.add_column(sa.Column("korail_cancelled_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.drop_column("korail_cancelled_at")
        batch.drop_column("pay_reminded_at")
        batch.drop_column("pay_deadline")
        batch.drop_column("korail_rsv_id")
        batch.drop_column("include_nearby")
