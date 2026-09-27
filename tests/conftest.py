import pytest

from retailops_ai.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for field in Settings.model_fields.values():
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)
