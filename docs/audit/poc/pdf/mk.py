import sys
def pdf(extra_page="", content=b""):
    objs=[b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
          b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> "+extra_page.encode()+b" >>",
          b"<< /Length %d >>\nstream\n"%len(content)+content+b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out=b"%PDF-1.4\n"; offs=[]
    for i,o in enumerate(objs,1): offs.append(len(out)); out+=b"%d 0 obj\n"%i+o+b"\nendobj\n"
    x=len(out); out+=b"xref\n0 %d\n0000000000 65535 f \n"%(len(objs)+1)+b"".join(b"%010d 00000 n \n"%o for o in offs)
    out+=b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"%(len(objs)+1,x); return out
open("array.pdf","wb").write(pdf("/Foo ["+"0 "*7_000_000+"]"))
open("ops.pdf","wb").write(pdf(content=b"BT /F1 1 Tf "+b"0 0 Td (a) Tj "*500_000+b"ET"))
open("normal.pdf","wb").write(pdf(content=b"BT /F1 12 Tf 10 50 Td (hello) Tj ET"))
