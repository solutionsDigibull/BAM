"""Masters import: memory/time cost of a <=15MB workbook (admin role). Uses fake clamd so the scan step passes."""
import sys, io, os, time, subprocess, threading, random
sys.path.insert(0, "/tmp/audit-poc/live"); sys.path.insert(0, "/home/Fool/Arch - Work/BAM-audit/server/tests")
import httpx, lib, openpyxl
from clamd_fake import FakeClamd
def sql(q): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",q],capture_output=True,text=True).stdout.strip()
TA = sql("select id from sbam.tenants where name='TenantA'"); adm = sql("select id from sbam.users where email='adminA@audit.test'")
H = {"Authorization":"Bearer "+lib.token(adm, TA, ["sbam-admin"], name="adminA")}
pid = open('/tmp/audit-poc/live/api.pid').read().strip()
rss = lambda: int(open(f"/proc/{pid}/status").read().split("VmRSS:")[1].split()[0])//1024
rows = int(sys.argv[1]); cols = 6
wb = openpyxl.Workbook(write_only=True)
for name in ("Sheet A", "Sheet B"):
    ws = wb.create_sheet(name)
    for i in range(rows): ws.append([i, i*7+1, random.randint(0, 10**9), "r%d" % random.randint(0, 10**6), None, i % 97])
buf = io.BytesIO(); wb.save(buf); data = buf.getvalue()
print(f"workbook: {len(data)/1e6:.1f} MB, {rows} rows x2 sheets; API RSS before: {rss()} MB")
os.environ["X"] = "1"
with FakeClamd() as clam:
    print("fake clamd on", clam.port, "(API must be started with CLAMAV_HOST/PORT pointing at it)")
    peak = [0]; stop = [False]
    def watch():
        while not stop[0]: peak[0] = max(peak[0], rss()); time.sleep(0.05)
    threading.Thread(target=watch, daemon=True).start()
    t0 = time.time()
    try:
        r = httpx.post("http://127.0.0.1:55997/api/v1/admin/masters/import", headers=H, files={"file": ("m.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}, data={"mode":"validate-only"}, timeout=300)
        print("status", r.status_code, r.text[:160])
    except Exception as e: print("request died:", type(e).__name__)
    stop[0] = True
    print(f"elapsed {time.time()-t0:.1f}s, API RSS peak {peak[0]} MB, after {rss()} MB")
