"""add personal loan approval workflow

Revision ID: d1a6c9e42f70
Revises: 8d755ae75dba
"""

from alembic import op
import sqlalchemy as sa


revision = "d1a6c9e42f70"
down_revision = "8d755ae75dba"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("personal_loans") as batch_op:
        batch_op.drop_constraint(
            "ck_personal_loan_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_personal_loan_status",
            "status IN "
            "('draft', 'pending_approval', 'active', 'paid', 'overdue', "
            "'disputed', 'restructured', 'written_off', 'cancelled', 'rejected')",
        )
        batch_op.add_column(
            sa.Column("decision_reason", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("decided_by", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_personal_loans_decided_by_users",
            "users",
            ["decided_by"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("personal_loans") as batch_op:
        batch_op.drop_constraint(
            "fk_personal_loans_decided_by_users",
            type_="foreignkey",
        )
        batch_op.drop_column("decided_at")
        batch_op.drop_column("decided_by")
        batch_op.drop_column("decision_reason")
        batch_op.drop_constraint(
            "ck_personal_loan_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_personal_loan_status",
            "status IN "
            "('draft', 'active', 'paid', 'overdue', 'disputed', "
            "'restructured', 'written_off', 'cancelled')",
        )
