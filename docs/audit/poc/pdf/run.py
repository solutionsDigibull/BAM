import sys, time, resource
from pypdf import PdfReader, __version__
f=sys.argv[1]; t=time.time()
r=PdfReader(f); pages=list(r.pages)
txt=pages[0].extract_text()
print(f"pypdf {__version__} {f}: {time.time()-t:.1f}s peakRSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB chars={len(txt)}")
