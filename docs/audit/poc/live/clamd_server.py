import sys, time; sys.path.insert(0, "/home/Fool/Arch - Work/BAM-audit/server/tests")
from clamd_fake import FakeClamd
with FakeClamd() as c:
    print(c.port, flush=True); time.sleep(7200)
