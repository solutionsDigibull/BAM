import logging, sqlglot
from sbam_api.schema_sql.validate import check
T = "CREATE OR REPLACE FUNCTION touch() RETURNS trigger AS $$ BEGIN %s; RETURN NEW; END; $$ LANGUAGE plpgsql"
cases = {
 "benign trigger fn": "NEW.updated_at = now()",
 "ctl EXECUTE": "EXECUTE 'select 1'",
 "ctl pg_read_file": "PERFORM pg_read_file('/etc/passwd')",
 "query_to_xml concat": "PERFORM query_to_xml('select pg_read_'||'file(''/etc/passwd'')', true,false,'')",
 "unicode-escaped ident": 'PERFORM U&"pg\\005fread\\005ffile"(\'/etc/passwd\')',
 "set_config role": "PERFORM set_config('role','postgres',false)",
 "pg_ls_waldir": "PERFORM name FROM pg_ls_waldir() LIMIT 1",
 "pg_ls_logdir": "PERFORM name FROM pg_ls_logdir() LIMIT 1",
 "lo_get": "PERFORM lo_get(1)",
 "pg_sleep": "PERFORM pg_sleep(100000)",
 "pg_terminate_backend": "PERFORM pg_terminate_backend(1)",
 "pg_reload_conf": "PERFORM pg_reload_conf()",
 "pg_authid": "PERFORM rolpassword FROM pg_authid",
 "table_to_xml": "PERFORM table_to_xml('pg_authid'::regclass,true,false,'')",
 "EXEC split by comment": "EXECUT/**/E 'select 1'",
 "SECURITY DEFINER": "NULL",
}
for k, body in cases.items():
    q = T % body
    if k == "SECURITY DEFINER": q = q + " SECURITY DEFINER"
    s = check(q); print(f"{k:28} {'ALLOWED' if s.ok else 'blocked: '+'; '.join(p.message for p in s.problems)[:60]}")
# CREATE FUNCTION language / decoys through Command path
for k,q in {
 "plpythonu decoy comment": "CREATE OR REPLACE FUNCTION touch() RETURNS trigger AS $$ import os $$ LANGUAGE plpython3u -- LANGUAGE plpgsql",
 "plperl decoy in body": "CREATE OR REPLACE FUNCTION touch() RETURNS trigger AS $$ BEGIN RETURN NEW; END; -- LANGUAGE plpgsql\n$$ LANGUAGE plperlu",
 "language quoted c": "CREATE OR REPLACE FUNCTION touch() RETURNS trigger AS '/lib/x.so','f' LANGUAGE 'c'",
}.items():
    s = check(q); print(f"{k:28} {'ALLOWED' if s.ok else 'blocked: '+'; '.join(p.message for p in s.problems)[:60]}")
