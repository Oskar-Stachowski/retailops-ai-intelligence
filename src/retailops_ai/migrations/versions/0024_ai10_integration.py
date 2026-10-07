"""Join accepted anomaly/stockout history with the AI10 outbox and observation replay."""

from collections.abc import Sequence

revision: str = "0024_ai10_integration"
down_revision: tuple[str, str] = ("0023_ai07_ai08", "0021_observation_replay")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
