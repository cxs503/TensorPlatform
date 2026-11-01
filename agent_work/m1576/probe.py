"""Read-only probe of the ship-performance API (:8001) geometry endpoints."""
import json
import sys
import urllib.request

API = "http://127.0.0.1:8001/api/v1"


def get(path, timeout=60):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def brief(obj, n=800):
    s = json.dumps(obj, ensure_ascii=False)
    return s[:n]


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print("=== " + path)
        try:
            d = get(path)
            if isinstance(d, dict):
                print("keys:", list(d)[:20])
            print(brief(d))
        except Exception as exc:  # noqa: BLE001
            print("ERR", type(exc).__name__, exc)
        print()