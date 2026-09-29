"""任务租约：谁在做、几点算死、第几次被领、被救几次

Revision ID: 9bcd272e0bb4
Revises: 5b2194852f4b
Create Date: 2026-09-29

已有的任务行没有这几列，所以两个整数列要带 server_default，否则 SQLite 的
ALTER TABLE ADD COLUMN NOT NULL 在有数据的表上会失败。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9bcd272e0bb4"
down_revision: str | Sequence[str] | None = "5b2194852f4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lease_owner", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("lease_token", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("recoveries", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_index(batch_op.f("ix_tasks_lease_until"), ["lease_until"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_lease_until"))
        batch_op.drop_column("recoveries")
        batch_op.drop_column("lease_token")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("lease_owner")
