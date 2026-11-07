"""One-shot live probe: fetch /entries?page=4&per_page=3 and print status+body."""
import threading
import urllib.request

from service import make_server

srv = make_server(0)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
with urllib.request.urlopen(f"http://127.0.0.1:{port}/entries?page=4&per_page=3") as r:
    print("http_code=", r.status)
    print(r.read().decode())
srv.shutdown()
