"""reservations.waitlisted: 좌석 대신 예약대기를 신청한 예약

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.add_column(
            sa.Column(
                "waitlisted", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("reservations") as batch:
        batch.drop_column("waitlisted")
