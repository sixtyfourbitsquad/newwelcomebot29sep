#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=runtime_password="$RUNTIME_DB_PASSWORD" --set=owner="$POSTGRES_USER" <<'SQL'
CREATE ROLE automation_runtime LOGIN PASSWORD :'runtime_password';
GRANT USAGE ON SCHEMA public TO automation_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO automation_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO automation_runtime;
SQL
