from sbam_api.schema_sql.validate import check
F = "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN %s; END $$"
S = "CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$ %s $$"
cases = {
 # expected BLOCKED (control)
 "ctl EXECUTE": F % "EXECUTE 'select 1'",
 "ctl pg_read_file": S % "select pg_read_file('/etc/passwd')",
 "ctl COPY": "COPY t TO '/tmp/x'",
 "ctl DROP": "DROP TABLE t",
 # candidate bypasses
 "query_to_xml concat": S % "select query_to_xml('select pg_read_'||'file(''/etc/passwd'')', true,false,'')::text",
 "unicode-escaped ident": S % 'select U&"pg\\005fread\\005ffile"(\'/etc/passwd\')',
 "set_config role": F % "PERFORM set_config('role','postgres',false)",
 "pg_ls_waldir": S % "select name from pg_ls_waldir() limit 1",
 "pg_ls_logdir": S % "select name from pg_ls_logdir() limit 1",
 "lo_get": S % "select lo_get(1)::text",
 "pg_sleep": F % "PERFORM pg_sleep(100000)",
 "pg_terminate_backend": F % "PERFORM pg_terminate_backend(1)",
 "pg_reload_conf": F % "PERFORM pg_reload_conf()",
 "current_setting": S % "select current_setting('data_directory')",
 "cursor_to_xml/table_to_xml": S % "select table_to_xml('pg_authid'::regclass,true,false,'')::text",
 "pg_authid read": S % "select rolpassword from pg_authid limit 1",
 "dollar-in-comment EXEC split": F % "EXECUT/**/E 'select 1'",
 "plpgsql EXECUTE w/ unicode kw": F % "PERFORM 1",
 "CREATE TABLE LIKE": "CREATE TABLE a (LIKE pg_authid)",
 "CREATE TABLE INHERITS": "CREATE TABLE a (x int) INHERITS (pg_class)",
 "CREATE TABLE PARTITION": "CREATE TABLE a (x int) PARTITION BY RANGE (x)",
 "CREATE TABLE TABLESPACE": "CREATE TABLE a (x int) TABLESPACE pg_global",
 "CREATE TABLE UNLOGGED": "CREATE UNLOGGED TABLE a (x int)",
 "CREATE TEMP TABLE": "CREATE TEMP TABLE a (x int)",
 "CREATE TABLE default sleep": "CREATE TABLE a (x int default pg_sleep(1)::text::int)",
 "CREATE INDEX expr": "CREATE INDEX i ON t ((pg_read_file('/etc/passwd')))",
 "CREATE TRIGGER": "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()",
 "CREATE TRIGGER EXECUTE PROCEDURE": "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW EXECUTE PROCEDURE f()",
 "extension case": "CREATE EXTENSION IF NOT EXISTS \"PGCRYPTO\"",
 "extension schema": "CREATE EXTENSION pgcrypto SCHEMA public CASCADE",
 "func LANGUAGE c": "CREATE FUNCTION f() RETURNS int AS '/lib/x.so','f' LANGUAGE c",
 "func LANGUAGE in body decoy": "CREATE FUNCTION f() RETURNS int AS $$ select 1 -- LANGUAGE sql\n$$ LANGUAGE c",
 "func plpythonu decoy": "CREATE FUNCTION f() RETURNS int AS $$ select 1 $$ LANGUAGE plpythonu -- LANGUAGE sql",
 "func LANGUAGE plpgsql in string decoy": "CREATE FUNCTION f() RETURNS int AS $$ import os $$ LANGUAGE plpython3u /* LANGUAGE plpgsql */",
 "func SET search_path": "CREATE FUNCTION f() RETURNS int LANGUAGE sql SET search_path = pg_catalog AS $$ select 1 $$",
 "BEGIN ATOMIC body": "CREATE FUNCTION f() RETURNS int LANGUAGE sql BEGIN ATOMIC select pg_read_file('x')::int; END",
 "multi-stmt in func tail": "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'select 1' ; DROP TABLE x",
 "psql meta": "\\! id",
 "nul byte": "CREATE TABLE a (x int);\x00DROP TABLE a",
 "bom": "\ufeffCREATE TABLE a (x int)",
}
for k, v in cases.items():
    try:
        s = check(v); r = "ALLOWED" if s.ok else "blocked: " + "; ".join(p.message for p in s.problems)[:70]
    except Exception as e:
        r = f"EXC {type(e).__name__}: {str(e)[:60]}"
    print(f"{k:38} {r}")
