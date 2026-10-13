"""Join the published ML history and the preserved assistant history."""

from collections.abc import Sequence

revision: str = "0026_ai12"
down_revision: tuple[str, str] = ("0023_ai07_ai08", "0010_assistant_token_budget")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
