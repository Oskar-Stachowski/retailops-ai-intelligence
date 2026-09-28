#!/bin/bash
set -eu
for value in "$AI_DB_PASSWORD" "$MLFLOW_DB_PASSWORD"; do
  [[ "$value" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || exit 1
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ai_app LOGIN PASSWORD '$AI_DB_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE mlflow_app LOGIN PASSWORD '$MLFLOW_DB_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE retailops_ai OWNER ai_app;
CREATE DATABASE retailops_mlflow OWNER mlflow_app;
REVOKE ALL ON DATABASE retailops_ai FROM PUBLIC;
REVOKE ALL ON DATABASE retailops_mlflow FROM PUBLIC;
GRANT CONNECT ON DATABASE retailops_ai TO ai_app;
GRANT CONNECT ON DATABASE retailops_mlflow TO mlflow_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname retailops_ai <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA ai AUTHORIZATION ai_app;
CREATE EXTENSION vector;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname retailops_mlflow <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO mlflow_app;
SQL
