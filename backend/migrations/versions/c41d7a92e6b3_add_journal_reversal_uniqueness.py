"""Enforce one reversal per journal entry."""

from alembic import op

revision = "c41d7a92e6b3"
down_revision = "b80e21ada6c5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_journal_reversal_of",
        "journal_entries",
        ["reversal_of_id"],
    )


def downgrade():
    op.drop_constraint(
        "uq_journal_reversal_of",
        "journal_entries",
        type_="unique",
    )
