"""Add audit fields for rejected platform loan repayments."""

from alembic import op
import sqlalchemy as sa


revision = "d8a4c71f2b90"
down_revision = "c6d8f2a91b40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "platform_loan_payments",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column("rejected_by", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "rejected_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "rejection_reason",
                sa.String(length=500),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_platform_payment_rejected_by",
            "users",
            ["rejected_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_constraint(
            "ck_platform_payment_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_platform_payment_status",
            "status IN ('pending', 'confirmed', 'failed', 'reversed', 'rejected')",
        )
        batch_op.create_check_constraint(
            "ck_platform_payment_rejection",
            "status != 'rejected' OR "
            "(rejected_by IS NOT NULL AND rejected_at IS NOT NULL "
            "AND rejection_reason IS NOT NULL "
            "AND length(trim(rejection_reason)) > 0)",
        )


def downgrade():
    # Preserve rejection audit history; downgrade is intentionally unsupported.
    pass
