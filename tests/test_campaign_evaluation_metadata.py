"""Full-lineage evaluation requests retain bounded reads and strict JSON ownership."""

import pytest

from retailops_ai.evaluation_campaign.campaign_evaluation_metadata import MAX_PARENT_METADATA_BYTES
from retailops_ai.evaluation_campaign.campaign_evaluation_worker import read
from retailops_ai.evaluation_campaign.campaign_generation_worker import read as read_source
from retailops_ai.source_snapshot.files import SnapshotError


def test_large_evaluation_metadata_does_not_enlarge_source_requests(tmp_path):
    path = tmp_path / "request.json"
    path.write_bytes(b'{"declared_lineage":"' + b"x" * (3 * 1024**2) + b'"}')
    assert len(read(path)["declared_lineage"]) == 3 * 1024**2
    with pytest.raises(ValueError, match="campaign_generation_invalid_metadata"):
        read_source(path)
    with path.open("wb") as stream:
        stream.truncate(MAX_PARENT_METADATA_BYTES + 1)
    with pytest.raises(SnapshotError, match="metadata_size_limit"):
        read(path)


@pytest.mark.parametrize(
    "raw", [b'{"parent":1,"parent":2}', b'{"parent":NaN}', b"[]", b'{"parent":']
)
def test_evaluation_metadata_rejects_ambiguous_or_invalid_json(tmp_path, raw):
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    with pytest.raises(SnapshotError):
        read(path)


def test_evaluation_metadata_never_follows_symlink(tmp_path):
    target = tmp_path / "original.json"
    target.write_bytes(b'{"parent":"original"}')
    path = tmp_path / "request.json"
    path.symlink_to(target)
    with pytest.raises((OSError, SnapshotError)):
        read(path)
