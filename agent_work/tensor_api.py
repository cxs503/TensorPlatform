import os, json, urllib.request

env = {}
for line in open('/root/tp-agent/agent.env'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    env[k] = v

BASE = env['TENSOR_API_BASE']


def call(path, body=None, token=None, method=None):
    url = BASE + '/' + path.lstrip('/')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 method=method or ('POST' if body else 'GET'))
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {'status': e.code, 'error': e.read().decode()[:400]}


def get_token():
    tok = call('auth/login', {'username': env['TENSOR_AGENT_USER'],
                              'password': env['TENSOR_AGENT_PASS']})
    return tok.get('access_token') or tok.get('token')


if __name__ == '__main__':
    token = get_token()
    open('/tmp/tok.txt', 'w').write(token or '')
    reps = call('tensor/reports?limit=20', token=token)
    items = reps.get('items') or reps.get('reports') or reps
    if isinstance(items, dict):
        items = items.get('items', [])
    for r in items:
        print('---', r.get('id'), r.get('mission_id'), (r.get('title') or '')[:100])