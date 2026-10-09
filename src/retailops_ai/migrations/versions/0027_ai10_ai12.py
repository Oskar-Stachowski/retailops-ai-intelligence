"""Join published native intelligence delivery and assistant histories."""

from collections.abc import Sequence

revision: str = "0027_ai10_ai12"
down_revision: tuple[str, str] = ("0025_model_intelligence_outbox", "0026_ai12")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
