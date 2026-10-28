"""BUG-A repro: parallel requests carrying a brand-new session id (= right after login) -> intermittent 500."""
import sys, time, subprocess, concurrent.futures as cf
sys.path.insert(0, "/tmp/audit-poc/live")
import httpx, lib
B = "http://127.0.0.1:55997/api/v1"
def sql(q): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",q],capture_output=True,text=True).stdout.strip()
TA = sql("select id from sbam.tenants where name='TenantA'"); u = sql("select id from sbam.users where email='fdeA1@audit.test'")
tot = bad = 0
for trial in range(15):
    tok = lib.token(u, TA, ["sbam-fde"], name="fdeA1")            # new sid per trial = a new login
    h = {"Authorization": "Bearer " + tok}; cl = httpx.Client(timeout=30)
    with cf.ThreadPoolExecutor(8) as ex: codes = list(ex.map(lambda _: cl.get(B + "/projects", headers=h).status_code, range(8)))
    tot += len(codes); bad += sum(c != 200 for c in codes)
print(f"{bad} of {tot} requests failed (HTTP 500) when 8 calls followed each of 15 simulated logins")
