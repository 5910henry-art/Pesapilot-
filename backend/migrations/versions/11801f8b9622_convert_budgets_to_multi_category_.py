"""Convert budgets to multi-category allocations.

Revision ID: 11801f8b9622
Revises: c4d8a1f29b70
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "11801f8b9622"
down_revision = "c4d8a1f29b70"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Fail safely rather than silently losing or misrepresenting old budgets.
    null_categories = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM budgets WHERE category_id IS NULL"
        )
    ).scalar_one()

    duplicate_periods = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM (
                SELECT workspace_id, period_start, period_end
                FROM budgets
                GROUP BY workspace_id, period_start, period_end
                HAVING COUNT(*) > 1
            ) AS duplicates
        """)
    ).scalar_one()

    if null_categories:
        raise RuntimeError(
            "Cannot migrate budgets: existing budgets without a category "
            "need manual review before migration."
        )

    if duplicate_periods:
        raise RuntimeError(
            "Cannot migrate budgets: duplicate workspace periods exist. "
            "Resolve them before migration."
        )

    # Both allocation and budget category references use workspace-scoped keys.
    op.create_unique_constraint(
        "uq_expense_category_workspace_id",
        "expense_categories",
        ["workspace_id", "id"],
    )

    # The allocation table references this composite parent key.
    op.create_unique_constraint(
        "uq_budget_workspace_id",
        "budgets",
        ["workspace_id", "id"],
    )

    # Enforce one parent budget for each exact workspace period.
    op.create_unique_constraint(
        "uq_budget_workspace_period",
        "budgets",
        ["workspace_id", "period_start", "period_end"],
    )

    op.drop_constraint(
        "uq_budget_category_period",
        "budgets",
        type_="unique",
    )

    op.alter_column(
        "budgets",
        "category_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    op.create_foreign_key(
        "fk_budget_category_workspace",
        "budgets",
        "expense_categories",
        ["workspace_id", "category_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )

    # Create category-level allocations.
    op.create_table(
        "budget_category_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_budget_allocation_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "budget_id"],
            ["budgets.workspace_id", "budgets.id"],
            name="fk_budget_allocation_budget_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "category_id"],
            ["expense_categories.workspace_id", "expense_categories.id"],
            name="fk_budget_allocation_category_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "budget_id",
            "category_id",
            name="uq_budget_allocation_category",
        ),
    )

    op.create_index(
        "ix_budget_category_allocations_workspace_budget",
        "budget_category_allocations",
        ["workspace_id", "budget_id"],
        unique=False,
    )

    # Preserve every existing budget as one initial category allocation.
    existing_budgets = bind.execute(
        sa.text("""
            SELECT id, workspace_id, category_id, amount_minor, created_at
            FROM budgets
            ORDER BY created_at, id
        """)
    ).mappings().all()

    if existing_budgets:
        allocation_table = sa.table(
            "budget_category_allocations",
            sa.column("id", sa.Uuid()),
            sa.column("workspace_id", sa.Uuid()),
            sa.column("budget_id", sa.Uuid()),
            sa.column("category_id", sa.Uuid()),
            sa.column("amount_minor", sa.BigInteger()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )

        rows = [
            {
                "id": uuid.uuid4(),
                "workspace_id": row["workspace_id"],
                "budget_id": row["id"],
                "category_id": row["category_id"],
                "amount_minor": row["amount_minor"],
                "created_at": row["created_at"],
            }
            for row in existing_budgets
        ]

        op.bulk_insert(allocation_table, rows)

    # Verify that every existing budget received exactly one allocation.
    missing_allocations = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM budgets b
            LEFT JOIN budget_category_allocations a
              ON a.workspace_id = b.workspace_id
             AND a.budget_id = b.id
            WHERE a.id IS NULL
        """)
    ).scalar_one()

    if missing_allocations:
        raise RuntimeError(
            "Budget migration backfill incomplete; transaction must roll back."
        )


def downgrade():
    bind = op.get_bind()

    # A multi-category budget cannot be represented by the old schema.
    incompatible_budgets = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM (
                SELECT b.id
                FROM budgets b
                LEFT JOIN budget_category_allocations a
                  ON a.workspace_id = b.workspace_id
                 AND a.budget_id = b.id
                GROUP BY b.id
                HAVING COUNT(a.id) <> 1
            ) AS incompatible
        """)
    ).scalar_one()

    if incompatible_budgets:
        raise RuntimeError(
            "Cannot downgrade: one or more budgets do not have exactly "
            "one category allocation. Downgrade would lose budget data."
        )

    # Restore the old parent category and amount from its sole allocation.
    bind.execute(
        sa.text("""
            UPDATE budgets b
            SET category_id = a.category_id,
                amount_minor = a.amount_minor
            FROM budget_category_allocations a
            WHERE a.workspace_id = b.workspace_id
              AND a.budget_id = b.id
        """)
    )

    op.drop_index(
        "ix_budget_category_allocations_workspace_budget",
        table_name="budget_category_allocations",
    )
    op.drop_table("budget_category_allocations")

    op.drop_constraint(
        "fk_budget_category_workspace",
        "budgets",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_expense_category_workspace_id",
        "expense_categories",
        type_="unique",
    )

    op.drop_constraint(
        "uq_budget_workspace_period",
        "budgets",
        type_="unique",
    )
    op.drop_constraint(
        "uq_budget_workspace_id",
        "budgets",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_budget_category_period",
        "budgets",
        ["category_id", "period_start", "period_end"],
    )

    op.alter_column(
        "budgets",
        "category_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
