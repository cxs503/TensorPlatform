#!/usr/bin/env python3
"""Exercise technical development gates in a disposable clone, never live integration.

Synthetic session IDs validate mechanics, not independent human/model semantic review.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/factory'))
from platform_workspace import Workspace,git,review

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='tensorplatform-self-build-validation-') as temp:
  root=Path(temp)/'platform'
  subprocess.run(['git','clone','--shared','--quiet',str(ROOT),str(root)],check=True)
  author=str(uuid.uuid4());peer=str(uuid.uuid4());workspace=Workspace(root,author)
  workspace.call('workspace.write',{'path':'docs/reports/workspace-validation-only.md','content':'Synthetic development gate validation. This file remains in a disposable clone.\n','expected_sha256':None})
  candidate=workspace.check()
  if not candidate['passed']:raise RuntimeError(candidate['log'][-5000:])
  commit=workspace.call('workspace.commit',{'message':'Synthetic check of platform development workflow'})
  missing_review_rejected=False
  try:workspace.call('workspace.integrate',{})
  except ValueError:missing_review_rejected=True
  if not missing_review_rejected:raise AssertionError('integration accepted without peer approval')
  checked=review(root,peer,author,{'expected_commit':commit['commit'],'decision':'approve','reason':'Synthetic technical gate exercise only; this is not independent semantic acceptance.'})
  if not checked['tests_passed']:raise AssertionError('peer rerun failed')
  merged=workspace.call('workspace.integrate',{})
  if git(root,'rev-parse','HEAD').strip()!=commit['commit']:raise AssertionError('disposable source did not integrate')
  result={'scope':'disposable clone technical gate validation; synthetic sessions; no live platform integration or model semantic acceptance','candidate':candidate,'peer_check':checked,'missing_review_rejected':missing_review_rejected,'integration':merged,'live_source_modified':False}
  Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
  print(json.dumps({'candidate_passed':candidate['passed'],'peer_rerun_passed':checked['tests_passed'],'missing_review_rejected':missing_review_rejected,'disposable_integration_completed':True,'live_source_modified':False}))
if __name__=='__main__':main()
