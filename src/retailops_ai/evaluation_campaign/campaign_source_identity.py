"""Check both original producer and exporter pins without loading controllers."""

from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.source_replay import PrivateSourceReplay
from retailops_ai.source_snapshot.files import SnapshotError


def _producer(replay: PrivateSourceReplay, source: CampaignSourceRecipe) -> None:
    # Inventory sources pin API/generator dependencies separately from Arrow.
    # New generation plans freeze the exporter lock explicitly before execution.
    exporter_lock = source.exporter_lock_sha256 or replay.declared_exporter_lock_sha256
    _producer_values(replay, source.producer_commit, source.producer_lock_sha256, exporter_lock)


def _producer_values(
    replay: PrivateSourceReplay, commit: str, producer_lock: str, exporter_lock: str | None
) -> None:
    if (
        replay.producer_commit != commit
        or replay.producer_code_state != "clean"
        or replay.producer_lock_sha256 != producer_lock
        or replay.exporter_commit != commit
        or replay.exporter_lock_sha256 is None
        or replay.exporter_lock_sha256 != exporter_lock
    ):
        raise SnapshotError("campaign_export_verified_producer_or_lock_mismatch")
