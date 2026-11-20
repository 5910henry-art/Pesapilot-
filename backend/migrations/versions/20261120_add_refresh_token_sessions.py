"""Add session and device metadata to refresh tokens."""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "b7e4c2a91f60"
down_revision = "f3a9c2d7e610"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "refresh_tokens",
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("user_agent", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("ip_address", sa.String(length=45), nullable=True),
    )

    bind = op.get_bind()
    tokens = sa.table(
        "refresh_tokens",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("session_id", sa.Uuid(as_uuid=True)),
    )

    rows = bind.execute(
        sa.select(tokens.c.id).where(tokens.c.session_id.is_(None))
    ).fetchall()

    for row in rows:
        bind.execute(
            tokens.update()
            .where(tokens.c.id == row.id)
            .values(session_id=uuid.uuid4())
        )

    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.alter_column(
            "session_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )

    op.create_index(
        "ix_refresh_tokens_session_id",
        "refresh_tokens",
        ["session_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_refresh_tokens_session_id",
        table_name="refresh_tokens",
    )
    op.drop_column("refresh_tokens", "ip_address")
    op.drop_column("refresh_tokens", "user_agent")
    op.drop_column("refresh_tokens", "session_id")
