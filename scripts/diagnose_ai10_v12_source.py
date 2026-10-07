"""Diagnostic only: replay existing native bytes through a reconstructed SQL fixture."""

import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as xml
from pathlib import Path

from sqlalchemy import create_engine, text

from check_ai10_v12_native import original_database_url
from check_stockout_final_acceptance import private_json, require
from check_v12_lifecycle import docker, port
from retailops_ai.intelligence_events.contracts import ForecastGenerated

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".local/ai10-native-v12-source"
ARTIFACT = ROOT / ".local/prior-artifact"


def main():
    require(os.environ.get("GITHUB_ACTIONS") == "true", "diagnostic_hosted_runner_required")
    output = ROOT / "source-diagnostic"
    output.mkdir(mode=0o700)
    owner = uuid.uuid4().hex
    name = "ai10-v12-diagnostic-" + owner
    report = dict(scope="diagnostic_reconstructed_SQL_only", original_publisher_accepted=False,
                  full_v12_accepted=False, status="failed")
    engine = None
    with tempfile.TemporaryDirectory(prefix="ai10-v12-diagnostic-") as temporary:
        work = Path(temporary)
        try:
            bundle = work / "bundle"
            shutil.copytree(ARTIFACT / "accepted-v12", bundle)
            old = json.loads((bundle / "acceptance.json").read_text())
            require(old["status"] == "failed" and old["rows"] == 56
                    and old["atomic_outbox_failure_rollback"] is True,
                    "diagnostic_exact_completed_AI_stage_required")
            for filename, digest in {
                "events.jsonl": "59ca9bec2e69374e2f140e3ef4673f8803eb469d8e1fe549f15f31efbc42d142",
                "native-publication.json": "a8fc83cc442543e76e3ea64f6b96d73b48a1bbfe02491aea69d4665bb3fbf68d",
            }.items():
                require(hashlib.sha256((bundle / filename).read_bytes()).hexdigest() == digest,
                        "diagnostic_exact_native_bytes_required")
            # Private diagnostic copy reproduces the controller's pre-consumer seal.
            # The immutable public failure evidence is neither altered nor accepted.
            old.update(status="passed")
            private_json(bundle / "acceptance.json", old)
            events = [ForecastGenerated.model_validate_json(line)
                      for line in (bundle / "events.jsonl").read_bytes().splitlines()]
            require(len(events) == 56, "diagnostic_complete_native_census")
            password = secrets.token_hex(24)
            docker("run", "-d", "--name", name, "--label", "retailops.ai10.v12.owner=" + owner,
                   "-e", "POSTGRES_USER=ai_app", "-e", "POSTGRES_PASSWORD=" + password,
                   "-e", "POSTGRES_DB=retailops_ai", "-p", "127.0.0.1::5432",
                   "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea")
            url = original_database_url(password, port(name, "5432/tcp"))
            engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
            deadline = time.monotonic() + 45
            while True:
                try:
                    with engine.connect() as connection:
                        connection.execute(text("SELECT 1"))
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise ValueError("diagnostic_database_not_ready") from None
                    time.sleep(.2)
            migration = ast.parse((ROOT / "src/retailops_ai/migrations/versions/0020_intelligence_outbox.py").read_text())
            upgrade = next(node for node in migration.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade")
            ddl = ast.literal_eval(upgrade.body[0].value.args[0])
            with engine.begin() as connection:
                connection.execute(text("CREATE SCHEMA ai"))
                connection.execute(text("CREATE TABLE ai.service_metadata(name text PRIMARY KEY,value jsonb NOT NULL)"))
                connection.execute(text("CREATE TABLE ai.v12_forecast_outputs(artifact_id text PRIMARY KEY)"))
                connection.execute(text(ddl))
                connection.execute(text("INSERT INTO ai.service_metadata VALUES ('ai10_v12_native_owner',CAST(:owner AS jsonb))"),dict(owner=json.dumps(owner)))
                connection.execute(text("INSERT INTO ai.v12_forecast_outputs VALUES (:id)"),dict(id=old["publication_id"]))
                for event in events:
                    connection.execute(text("INSERT INTO ai.intelligence_outbox(event_id,artifact_id,environment,topic,partition_key,document) VALUES (:id,:artifact,'test',:topic,:key,CAST(:document AS jsonb))"),
                                       dict(id=str(event.event_id),artifact=old["publication_id"],topic=event.topic,key=event.partition_key,document=event.model_dump_json()))
            control = work / "control.json"
            private_json(control, dict(owner=owner,database_url=url,publication_id=old["publication_id"],
                                      commit=os.environ["GITHUB_SHA"],workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
                                      event_sha256={str(event.event_id):hashlib.sha256(event.model_dump_json().encode()).hexdigest() for event in events}))
            # Add fixed exception categories only to a private diagnostic copy.
            delivery = work / "deliver.py"
            content = (ROOT / "scripts/deliver_ai10_v12_native.py").read_text()
            before = '    except Exception:\n        print(\'{"status":"failed","category":"ai10_v12_original_outbox_delivery"}\')'
            after = '    except Exception as error:\n        safe = str(error) if isinstance(error, ValueError) and re.fullmatch(r"ai10_v12_[a-z0-9_]{1,160}", str(error)) else None\n        print(json.dumps(dict(status="failed", exception_type=type(error).__name__, category=safe, sqlstate=getattr(error, "sqlstate", None))))'
            require(content.count(before) == 1, "diagnostic_delivery_binding")
            delivery.write_text(content.replace(before, after))
            env = {**os.environ,"PYTHONPATH":str(SOURCE)+os.pathsep+str(SOURCE / "services/api"),
                   "REQUIRE_BROKER_TESTS":"1","REQUIRE_AI10_NATIVE_FORECAST_READ":"1",
                   "AI10_NATIVE_FORECAST_OUTPUT":str(bundle),"AI10_NATIVE_PRODUCER_COMMIT":old["commit"],
                   "AI10_NATIVE_READ_REPORT":str(work / "source-read.json"),
                   "AI10_V12_ORIGINAL_DATABASE_CONTROL":str(control),
                   "AI10_NATIVE_DELIVERY_PYTHON":str(ROOT / "tools/intelligence-delivery/.venv/bin/python"),
                   "AI10_NATIVE_DELIVERY_SCRIPT":str(delivery),"GITHUB_RUN_ID":str(old["workflow_run_id"])}
            # The delivery control binds to this diagnostic runner; Source acceptance
            # binds to the immutable producer run. Restore only that control field.
            document = json.loads(control.read_text()); document["workflow_run_id"] = old["workflow_run_id"]
            private_json(control, document)
            with (work / "source.log").open("wb") as log:
                completed = subprocess.run([str(SOURCE / "services/api/.venv/bin/python"),"-m","pytest","-q","-x",
                                           "services/api/tests/test_native_forecast_output_durability.py","--junitxml="+str(work / "tests.xml")],
                                          cwd=SOURCE,env=env,stdout=log,stderr=log,timeout=600,check=False)
            log = (work / "source.log").read_text()
            report["returncode"] = completed.returncode
            report["failure_categories"] = sorted(set(re.findall(r"\bai10_v12_[a-z0-9_]{1,160}\b",log)))
            report["exception_types"] = sorted(set(re.findall(r'"exception_type": "([A-Za-z][A-Za-z0-9_]*)"',log)))
            if (work / "tests.xml").is_file():
                root = xml.parse(work / "tests.xml").getroot()
                report["test_counts"] = {key:sum(int(s.get(key,"0")) for s in root.findall("testsuite")) for key in ("tests","errors","failures","skipped")}
                report["failure_locations"] = sorted(set(re.findall(r"(?:services/api/)?(tests/[a-zA-Z0-9_/.]+\.py):([0-9]+)", (work / "tests.xml").read_text())))
                report["test_failure_types"] = [node.get("type") for node in root.iter() if node.tag in {"failure","error"}]
            report["status"] = "passed" if completed.returncode == 0 else "failed"
        except Exception as error:
            report["exception_type"] = type(error).__name__
            if isinstance(error,ValueError) and re.fullmatch(r"diagnostic_[a-z0-9_]{1,160}",str(error)):
                report["failure_category"] = str(error)
        finally:
            if engine is not None:
                engine.dispose()
            require(docker("inspect","--format",'{{ index .Config.Labels "retailops.ai10.v12.owner" }}',name,required=False) in {"",owner}, "diagnostic_cleanup_owner")
            docker("rm","-fv",name,required=False)
            report["owned_fixture_removed"] = True
    private_json(output / "report.json",report)
    print(json.dumps(report),flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
