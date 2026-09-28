"""Serialize explicit migrations; never called from API startup."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from retailops_ai.config import Settings

MIGRATION_LOCK_ID = 384790011


def migrate(settings: Settings) -> None:
    if settings.database_url is None:
        raise ValueError("AI database URL is required")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})")
            connection.commit()
            try:
                config = Config()
                config.set_main_option("script_location", str(Path(__file__).resolve().parent))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            finally:
                connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})")
                connection.commit()
    finally:
        engine.dispose()
