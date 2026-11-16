"""Allow shared savings accounts and track goal allocations."""

from alembic import op
import sqlalchemy as sa
import json
from uuid import UUID, uuid4
from datetime import datetime, timezone

revision = "b7c4e19a2d60"
down_revision = "8515b66844be"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "savings_goal_movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("journal_entry_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_savings_goal_movement_amount_positive",
        ),
        sa.CheckConstraint(
            "operation IN ('contribution', 'withdrawal')",
            name="ck_savings_goal_movement_operation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["savings_goals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entries.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "journal_entry_id",
            name="uq_savings_goal_movement_journal",
        ),
    )
    op.create_index(
        "ix_savings_goal_movements_workspace_id",
        "savings_goal_movements",
        ["workspace_id"],
    )
    op.create_index(
        "ix_savings_goal_movements_goal_id",
        "savings_goal_movements",
        ["goal_id"],
    )

    # Backfill existing goal transfers from their audit records.
    bind = op.get_bind()
    audit = sa.Table("audit_logs", sa.MetaData(), autoload_with=bind)
    goals = sa.Table("savings_goals", sa.MetaData(), autoload_with=bind)
    journals = sa.Table("journal_entries", sa.MetaData(), autoload_with=bind)
    movements = sa.Table(
        "savings_goal_movements",
        sa.MetaData(),
        autoload_with=bind,
    )

    goal_rows = bind.execute(
        sa.select(goals.c.id, goals.c.workspace_id)
    ).all()
    goal_workspace = {row.id: row.workspace_id for row in goal_rows}

    journal_rows = bind.execute(
        sa.select(journals.c.id, journals.c.workspace_id)
    ).all()
    journal_workspace = {
        row.id: row.workspace_id for row in journal_rows
    }

    logs = bind.execute(
        sa.select(audit).where(
            audit.c.resource_type == "savings_goal",
            audit.c.action.in_(
                ["savings_contribution", "savings_withdrawal"]
            ),
        )
    ).mappings().all()

    inserted_journals = set()

    for log in logs:
        details = log.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (TypeError, ValueError):
                continue

        if not isinstance(details, dict):
            continue

        try:
            goal_id = UUID(str(log["resource_id"]))
            journal_id = UUID(str(details["journal_entry_id"]))
            amount = int(details["amount_minor"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue

        workspace_id = log.get("workspace_id")
        if (
            amount <= 0
            or goal_id not in goal_workspace
            or journal_id not in journal_workspace
            or goal_workspace[goal_id] != workspace_id
            or journal_workspace[journal_id] != workspace_id
            or journal_id in inserted_journals
        ):
            continue

        operation = (
            "contribution"
            if log["action"] == "savings_contribution"
            else "withdrawal"
        )

        bind.execute(
            movements.insert().values(
                id=uuid4(),
                workspace_id=workspace_id,
                goal_id=goal_id,
                journal_entry_id=journal_id,
                amount_minor=amount,
                operation=operation,
                created_at=(
                    log.get("created_at")
                    or datetime.now(timezone.utc)
                ),
            )
        )
        inserted_journals.add(journal_id)

    # Remove the old one-goal-per-account unique constraint.
    inspector = sa.inspect(bind)
    unique_constraints = inspector.get_unique_constraints("savings_goals")

    for constraint in unique_constraints:
        if constraint.get("column_names") == ["financial_account_id"]:
            with op.batch_alter_table("savings_goals") as batch:
                batch.drop_constraint(
                    constraint["name"],
                    type_="unique",
                )
            break


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = inspector.get_unique_constraints("savings_goals")

    if not any(
        item.get("column_names") == ["financial_account_id"]
        for item in unique_constraints
    ):
        with op.batch_alter_table("savings_goals") as batch:
            batch.create_unique_constraint(
                "uq_savings_goals_financial_account_id",
                ["financial_account_id"],
            )

    op.drop_index(
        "ix_savings_goal_movements_goal_id",
        table_name="savings_goal_movements",
    )
    op.drop_index(
        "ix_savings_goal_movements_workspace_id",
        table_name="savings_goal_movements",
    )
    op.drop_table("savings_goal_movements")
