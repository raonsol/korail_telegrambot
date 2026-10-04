"""reservations.allow_waitlist: 예약 생성 시 고른 예약대기 사용 여부

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.add_column(
            sa.Column(
                "allow_waitlist",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.drop_column("allow_waitlist")
