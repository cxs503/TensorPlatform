#!/usr/bin/env python3
"""Explicit, idempotent registration. Never assigns a persona or queues work."""
import argparse
import os
from run_resident_agent import Client

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',required=True)
    parser.add_argument('--role',required=True,choices=['platform','data','models','applications','infrastructure','verifier','strategy'])
    parser.add_argument('--model',required=True)
    parser.add_argument('--principal',default='agent')
    args=parser.parse_args()
    # Registration is authenticated as local admin; execution remains the selected AGENT principal.
    os.environ['FACTORY_AGENT_USER']='admin'
    client=Client()
    rows=client.call('GET','/tensor/factory/agents')['sessions']
    existing=next((r for r in rows if r['worker_id']==args.worker),None)
    if existing:
        if existing['role']!=args.role or existing['model']!=args.model:parser.error('existing session has different role/model; configure it explicitly through the API')
        print(existing['agent_session_id']);return
    # The private launcher provisions the execution user; look up its identity via login response.
    import json
    from urllib.request import Request
    from run_resident_agent import ROOT,read_response
    accounts=json.loads((ROOT/'.runtime/local-accounts.json').read_text())['accounts']
    login=read_response(Request(os.environ.get('FACTORY_API_URL','http://127.0.0.1:8011/api/v1').rstrip('/')+'/auth/login',data=json.dumps({'username':args.principal,'password':accounts[args.principal]}).encode(),headers={'Content-Type':'application/json'}))
    # Existing auth returns user_info with token identity; no tokens are printed.
    # JWT validation is owned by the API; parse only the returned token subject to locate that principal.
    import base64
    payload=login['access_token'].split('.')[1]
    claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
    uid=claims.get('user_id') or claims.get('sub')
    if uid is None:parser.error('login token has no principal identifier')
    result=client.call('POST','/tensor/factory/agents',{'worker_id':args.worker,'role':args.role,'model':args.model,'user_id':int(uid)})
    print(result['agent_session_id'])
if __name__=='__main__':main()
