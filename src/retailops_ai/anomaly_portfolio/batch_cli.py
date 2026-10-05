"""Private pipeline-only pinned scoring and atomic complete publication; no HTTP writes."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import Field
from sqlalchemy import create_engine

from retailops_ai.anomaly_detectors.protocol import Scope, Window
from retailops_ai.anomaly_portfolio.batch import batch
from retailops_ai.anomaly_portfolio.cli import prepared
from retailops_ai.anomaly_portfolio.journal import PostgresJournal
from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL
from retailops_ai.anomaly_portfolio.result_store import PostgresResults, authorized
from retailops_ai.anomaly_portfolio.serving_contract import Query, ReleaseID
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.common import Contract, Symbol, UtcTime
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import private_principal


class BatchRequest(Contract):
    request_id: Symbol
    release_id: ReleaseID
    scopes: tuple[Scope, ...] = Field(min_length=1, max_length=100)
    window: Window
    as_of: UtcTime
    maximum_days: Annotated[int, Field(ge=1, le=128)] = 128


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--prepared-receipt", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    engine = None
    try:
        actor = private_principal(args.policy_file, args.credentials_file)
        if "pipeline" not in actor.roles or "anomaly:run" not in actor.capabilities:
            raise ValueError("anomaly_pipeline_authorization_required")
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("anomaly_batch_request_budget")
        strict_json(raw)
        request = BatchRequest.model_validate_json(raw)
        if (request.window.end - request.window.start).days >= request.maximum_days:
            raise ValueError("anomaly_batch_window_budget")
        for scope in request.scopes:
            authorized(
                actor,
                Query(
                    product_id=scope.product_id,
                    selling_location_id=scope.selling_location_id,
                    channel=scope.channel,
                ),
                "anomaly:run",
            )
        settings = load_settings()
        if settings.database_url is None:
            raise ValueError("anomaly_batch_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
        )
        journal = PostgresJournal(engine)
        with journal.locked(MODEL):
            release = journal.release(request.release_id)
        # The enrolled, approved immutable release is the authority. Model
        # bytes are independently checked against its pin in batch(). Runtime
        # neither follows live aliases nor reopens private evaluation truth.
        frame = prepared(args.prepared_receipt)
        manifest, items = batch(
            release,
            args.model,
            frame,
            request.scopes,
            request.window,
            request.as_of,
            datetime.now(UTC),
        )
        result = PostgresResults(engine).publish(
            request.request_id, actor, release, manifest, items
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('{"error":"anomaly_batch_operation_failed"}', file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
