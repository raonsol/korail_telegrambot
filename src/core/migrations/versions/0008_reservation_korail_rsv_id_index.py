"""reservations.korail_rsv_id 인덱스 (내 승차권: 코레일 예약번호로 예약 기록 찾기)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_reservations_korail_rsv_id", "reservations", ["korail_rsv_id"])


def downgrade() -> None:
    op.drop_index("ix_reservations_korail_rsv_id", table_name="reservations")
