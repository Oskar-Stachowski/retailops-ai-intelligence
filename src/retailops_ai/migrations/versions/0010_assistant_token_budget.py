"""Align durable admission with the evaluated 16k input + 3k output graph bound."""

from alembic import op

revision = "0010_assistant_token_budget"
down_revision = "0009_assistant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai.assistant_runs DROP CONSTRAINT assistant_runs_reserved_tokens_check")
    op.execute(
        "ALTER TABLE ai.assistant_runs ADD CONSTRAINT assistant_runs_reserved_tokens_check CHECK (reserved_tokens BETWEEN 1 AND 19000)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai.assistant_runs DROP CONSTRAINT assistant_runs_reserved_tokens_check")
    op.execute(
        "ALTER TABLE ai.assistant_runs ADD CONSTRAINT assistant_runs_reserved_tokens_check CHECK (reserved_tokens BETWEEN 1 AND 13500)"
    )
