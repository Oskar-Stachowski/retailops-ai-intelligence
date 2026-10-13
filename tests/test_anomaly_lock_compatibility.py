import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "anomaly_lock_compatibility", ROOT / "scripts/anomaly_lock_compatibility.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
BASELINE = ROOT / "environments/anomaly/qualification.uv.lock"
ORIGINAL = BASELINE.read_bytes()
ORIGINAL_SHA = "33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587"
CURRENT = (ROOT / "uv.lock").read_bytes()


def test_original_fit_lock_and_reviewed_ai12_extension_are_both_bound():
    assert hashlib.sha256(ORIGINAL).hexdigest() == ORIGINAL_SHA
    unchanged = module.lock_compatibility(ORIGINAL_SHA, ORIGINAL, None)
    assert unchanged["status"] == "unchanged"
    extended = module.lock_compatibility(ORIGINAL_SHA, CURRENT, BASELINE)
    assert extended["status"] == "additive_extension_verified"
    assert extended["training_lock_sha256"] == ORIGINAL_SHA
    assert extended["runtime_lock_sha256"] == hashlib.sha256(CURRENT).hexdigest()
    assert extended["original_packages_unchanged"] > 50
    assert "langgraph" in extended["added_packages"]


def test_changed_lock_requires_explicit_training_reference():
    with pytest.raises(ValueError, match="dependency_lock_changed"):
        module.lock_compatibility(ORIGINAL_SHA, CURRENT, None)


def test_training_reference_cannot_be_replaced_by_a_current_lock(tmp_path):
    path = tmp_path / "forged.lock"
    path.write_bytes(CURRENT)
    with pytest.raises(ValueError, match="training_lock_binding"):
        module.lock_compatibility(ORIGINAL_SHA, CURRENT, path)


@pytest.mark.parametrize(
    "package,old,new",
    [
        ("scikit-learn", 'version = "1.9.1"', 'version = "1.9.2"'),
        ("scikit-learn", 'name = "scipy"', 'name = "unreviewed-runtime"'),
        ("scikit-learn", 'hash = "sha256:', 'hash = "sha256:0'),
        ("scikit-learn", 'source = { registry = "', 'source = { registry = "other-'),
        ("pydantic", 'version = "2.13.5"', 'version = "2.13.4"'),
        ("retailops-ai-intelligence", 'name = "langgraph"', 'name = "unreviewed-agent"'),
        ("retailops-ai-intelligence", 'specifier = "==1.2.12"', 'specifier = "==1.2.13"'),
        ("retailops-ai-intelligence", "extra == 'forecast'", "extra == 'unreviewed'"),
        ("retailops-ai-intelligence", 'editable = "."', 'editable = "../other"'),
        ("langgraph", 'version = "1.2.12"', 'version = "1.2.13"'),
    ],
)
def test_extension_rejects_dependency_artifact_marker_and_root_changes(package, old, new):
    sections = CURRENT.decode().split("[[package]]")
    index = next(i for i, s in enumerate(sections) if s.startswith(f'\nname = "{package}"\n'))
    assert old in sections[index]
    sections[index] = sections[index].replace(old, new, 1)
    changed = "[[package]]".join(sections).encode()
    with pytest.raises(ValueError, match="anomaly_compatibility_"):
        module.lock_compatibility(ORIGINAL_SHA, changed, BASELINE)


@pytest.mark.parametrize("mutation", ["python", "removed", "duplicate"])
def test_extension_rejects_environment_missing_and_ambiguous_packages(mutation):
    sections = CURRENT.decode().split("[[package]]")
    index = next(i for i, s in enumerate(sections) if s.startswith('\nname = "scikit-learn"\n'))
    if mutation == "python":
        sections[0] = sections[0].replace(
            'requires-python = ">=3.11.15, <3.12"', 'requires-python = ">=3.12"'
        )
    elif mutation == "removed":
        sections.pop(index)
    else:
        sections.append(sections[index])
    with pytest.raises(ValueError, match="anomaly_compatibility_"):
        module.lock_compatibility(ORIGINAL_SHA, "[[package]]".join(sections).encode(), BASELINE)
