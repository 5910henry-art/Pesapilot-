"""scope platform loan applications to workspaces

Revision ID: e48a25f491cc
Revises: 359d69562793
Create Date: 2026-11-23 13:07:14.128205

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e48a25f491cc'
down_revision = '359d69562793'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("platform_loan_applications") as batch_op:
        batch_op.add_column(
            sa.Column("workspace_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_platform_application_workspace",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_index(
            "uq_platform_application_one_pending_per_customer"
        )
        batch_op.create_index(
            "uq_platform_application_one_pending_per_customer_workspace",
            ["customer_id", "workspace_id"],
            unique=True,
            postgresql_where=sa.text(
                "status = 'pending' AND workspace_id IS NOT NULL"
            ),
            sqlite_where=sa.text(
                "status = 'pending' AND workspace_id IS NOT NULL"
            ),
        )


def downgrade():
    bind = op.get_bind()
    duplicate = bind.execute(sa.text("""
        SELECT customer_id, COUNT(*)
        FROM platform_loan_applications
        WHERE status = 'pending'
        GROUP BY customer_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).first()

    if duplicate:
        raise RuntimeError(
            "Cannot downgrade: a customer has multiple pending "
            "applications across workspaces."
        )

    with op.batch_alter_table("platform_loan_applications") as batch_op:
        batch_op.drop_index(
            "uq_platform_application_one_pending_per_customer_workspace"
        )
        batch_op.create_index(
            "uq_platform_application_one_pending_per_customer",
            ["customer_id"],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
            sqlite_where=sa.text("status = 'pending'"),
        )
        batch_op.drop_constraint(
            "fk_platform_application_workspace",
            type_="foreignkey",
        )
        batch_op.drop_column("workspace_id")
