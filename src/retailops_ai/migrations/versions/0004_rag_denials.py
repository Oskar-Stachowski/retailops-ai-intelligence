"""Live document denials apply to all immutable index versions in an environment."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_rag_denials"
down_revision: str | None = "0003_rag_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE ai.rag_document_denials (
        environment text NOT NULL CHECK(environment IN ('local','test')),
        document_id text NOT NULL CHECK(document_id ~ '^document-sha256-[0-9a-f]{64}$'),
        denial jsonb NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(environment,document_id),
        CHECK ((denial->>'environment'=environment AND denial->>'document_id'=document_id
            AND denial->>'schema_version'='1.0') IS TRUE)
    );
    CREATE TRIGGER rag_denial_immutable BEFORE UPDATE OR DELETE ON ai.rag_document_denials
        FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();""")


def downgrade() -> None:
    op.execute("DROP TABLE ai.rag_document_denials")
