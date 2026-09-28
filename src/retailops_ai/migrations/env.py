"""Alembic receives the connection from the explicit, serialized runner."""

from alembic import context
from sqlalchemy.engine import Connection

if context.is_offline_mode():
    raise RuntimeError("Offline SQL migration is not supported")
connection: Connection = context.config.attributes["connection"]
context.configure(connection=connection, version_table_schema="ai")
with context.begin_transaction():
    context.run_migrations()
