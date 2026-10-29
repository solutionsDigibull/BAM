import json, http.server, sys
sys.path.insert(0, "/tmp/audit-poc/live")
import lib
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps(lib.jwks()).encode(); self.send_response(200); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
http.server.HTTPServer(("127.0.0.1", 55998), H).serve_forever()
