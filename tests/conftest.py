import pytest

from retailops_ai.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for field in Settings.model_fields.values():
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)


@pytest.fixture(scope="session")
def completed_forecast_advancement(tmp_path_factory):
    # Register the expensive immutable setup once for both related test modules.
    # The builder closes all worker patches before any dependent test runs.
    from test_development_advancement import build_completed_advancement

    return build_completed_advancement(tmp_path_factory)
