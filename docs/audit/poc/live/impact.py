import sys, time, threading, subprocess, httpx
sys.path.insert(0,"/tmp/audit-poc/live"); import lib
def sql(q): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",q],capture_output=True,text=True).stdout.strip()
TA=sql("select id from sbam.tenants where name='TenantA'"); f=sql("select id from sbam.users where email='fdeA1@audit.test'")
H={"Authorization":"Bearer "+lib.token(f,TA,["sbam-fde"],name="fdeA1")}; B="http://127.0.0.1:55997/api/v1"
c=httpx.Client(timeout=60); c.get(B+"/me",headers=H)
def probe(label,secs):
    lat=[]; end=time.time()+secs
    while time.time()<end:
        t=time.time(); c.get(B+"/me",headers=H); lat.append((time.time()-t)*1000); time.sleep(0.05)
    lat.sort(); print(f"{label}: n={len(lat)} p50={lat[len(lat)//2]:.0f}ms p95={lat[int(len(lat)*.95)-1]:.0f}ms max={lat[-1]:.0f}ms")
probe("baseline", 4)
t=threading.Thread(target=lambda: subprocess.run([".venv/bin/python","-I","/tmp/audit-poc/live/xlsx_dos.py","200000"],capture_output=True)); t.start()
time.sleep(1.5); probe("during ONE 14MB import", 12)
ts=[threading.Thread(target=lambda: subprocess.run([".venv/bin/python","-I","/tmp/audit-poc/live/xlsx_dos.py","200000"],capture_output=True)) for _ in range(3)]
[x.start() for x in ts]; time.sleep(2); probe("during THREE parallel imports", 15); [x.join() for x in ts]; t.join()
