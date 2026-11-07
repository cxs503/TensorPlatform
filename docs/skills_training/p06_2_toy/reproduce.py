"""Reproducer for the planted pagination off-by-one.

Starts the toy service on an ephemeral port, walks ``/entries`` page by
page (per_page=3) until the service says it is done, then checks that the
union of all pages equals the full item set.

Exit code: 0 = full coverage, 1 = items missing or duplicated.
"""
import json
import sys
import threading
import urllib.error
import urllib.request

from service import ITEMS, make_server


def main():
    srv = make_server(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    seen, total_pages, walked = [], None, 0
    page = 1
    while True:
        url = f"{base}/entries?page={page}&per_page=3"
        try:
            with urllib.request.urlopen(url) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                break
            raise
        seen.extend(item["id"] for item in data["items"])
        total_pages = data["total_pages"]
        walked += 1
        if page >= total_pages:
            break
        page += 1
        if walked > 100:
            break

    expected = [item["id"] for item in ITEMS]
    missing = [i for i in expected if i not in seen]
    dupes = len(seen) - len(set(seen))
    print(f"pages walked: {walked} (service reports total_pages={total_pages})")
    print(f"items seen: {len(seen)}/{len(expected)}  "
          f"missing={missing}  duplicates={dupes}")
    ok = not missing and dupes == 0
    print("REPRODUCE-OK" if ok else "REPRODUCE-FAIL: last partial page unreachable")
    srv.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
