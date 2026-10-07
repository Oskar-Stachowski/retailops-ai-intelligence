"""Join independently published anomaly and stockout migration histories."""

from collections.abc import Sequence

revision: str = "0023_ai07_ai08"
down_revision: tuple[str, str] = ("0022_anomaly_evaluations", "0021_stockout_jobs")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
