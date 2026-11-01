import json, urllib.request

tok = json.load(open('/srv/tensor/agent/work/m1667/agent_work/tok.json'))['token']
req = urllib.request.Request('http://127.0.0.1:8010/api/v1/tensor/missions?limit=60',
                             headers={'Authorization': 'Bearer ' + tok})
d = json.load(urllib.request.urlopen(req))
items = d['items']
print('total', d['total'])
for m in items:
    print(m.get('id'), m.get('status'), (m.get('created_at') or '')[:16], (m.get('goal') or '').split('\n')[0][:100])