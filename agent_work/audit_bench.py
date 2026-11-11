import json, os, urllib.request, urllib.error

base = os.environ["TENSOR_API_BASE"]
tok = open("/tmp/tok.txt").read().strip()

def get(path):
    req = urllib.request.Request(base + path, method="GET")
    req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]

# try candidate endpoints for benchmark cases
for p in [
    "/tensor/benchmark-cases?anchor_slug=KPI-QC-RECHECK",
    "/tensor/benchmark_cases?anchor_slug=KPI-QC-RECHECK",
    "/tensor/benchmarks/cases?anchor_slug=KPI-QC-RECHECK",
    "/tensor/benchmark-cases",
]:
    st, body = get(p)
    print(p, "->", st)
    if st == 200:
        s = json.dumps(body, ensure_ascii=False)
        print(s[:3000])
        break
