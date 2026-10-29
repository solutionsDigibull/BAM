"""Non-exfiltration confirmation: do the allowlist-missed functions actually execute in the
schema-check sandbox? No file reads, no data exfiltration -- only innocuous arguments.

Run against a throwaway `schema-check`-image container, e.g.:
  docker run -d --rm -e POSTGRES_HOST_AUTH_METHOD=trust --tmpfs /var/lib/postgresql/data \
      -p 127.0.0.1:<port>:5432 postgres:16.15-alpine@sha256:721873c3...
  SCHEMA_CHECK_PORT=<port> python poc_safe.py
"""
import os

from sbam_api.schema_sql.validate import check
from sbam_workers.build.schema_sql.apply import apply
from sbam_workers.build.schema_sql.guard import guard_sql

DSN = f"postgresql://postgres@127.0.0.1:{os.environ.get('SCHEMA_CHECK_PORT', '5432')}/postgres"

def run(label, fn_body):
    sql = f"""
CREATE TABLE handoff_t (id serial PRIMARY KEY);
CREATE OR REPLACE FUNCTION pwn() RETURNS trigger AS $$ BEGIN {fn_body}; RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER pwn_t BEFORE INSERT ON handoff_t FOR EACH ROW EXECUTE FUNCTION pwn();
"""
    s = check(sql)
    print(f"== {label}")
    print("   check() ok=", s.ok, [p.message for p in s.problems])
    if not s.ok:
        return
    stmts = [x.text for x in s.statements] + guard_sql(["handoff_t"])
    probs = apply(DSN, stmts, ["handoff_t"], ["handoff_t"])
    print("   apply() findings:", [(p.code.value, p.message[:150]) for p in probs])

# Confirms the function body actually runs server-side (no real data touched):
run("pg_sleep(2) actually blocks (timing side channel)", "RAISE NOTICE 'before'; PERFORM pg_sleep(2); RAISE NOTICE 'after'")
run("current_setting reads scratch server's own config (no secrets)", "PERFORM current_setting('server_version')")
run("pg_terminate_backend targets a fake pid (expect false, not an error)", "PERFORM pg_terminate_backend(999999)")
