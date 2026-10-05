import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_ci_tests", ROOT / "scripts/run_ci_tests.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_shards_preserve_all_current_and_new_files_without_overlap():
    files = {f"tests/test_{name}.py" for name in ("slow", "medium", "fast", "new", "extra")}
    weights = {"tests/test_slow.py": 60, "tests/test_medium.py": 30, "removed.py": 900}
    groups = module.assign_files(files, weights, 4)
    flattened = [name for group in groups for name in group]
    assert set(flattened) == files
    assert len(flattened) == len(set(flattened)) == len(files)
    assert all(groups)
    assert groups == module.assign_files(set(reversed(sorted(files))), weights, 4)


@pytest.mark.parametrize("count", [0, -1, 3])
def test_invalid_shard_count_cannot_silently_drop_tests(count):
    with pytest.raises(ValueError):
        module.assign_files({"a.py", "b.py"}, {}, count)


def test_invalid_file_weight_is_rejected():
    with pytest.raises(ValueError):
        module.assign_files({"a.py"}, {"a.py": -1}, 1)
