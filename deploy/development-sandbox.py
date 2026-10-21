#!/usr/bin/python3
"""Fixed offline Python package executor; caller controls files, never Docker options."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
IMAGE='tensorplatform-factory-builder:local'

def validate(payload):
    if not isinstance(payload,dict) or set(payload)!={'files'}:raise ValueError('invalid package')
    files=payload['files']
    if not isinstance(files,dict) or not 2<=len(files)<=8:raise ValueError('file count outside limits')
    if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,70}\.py',n) or not isinstance(s,str) for n,s in files.items()):raise ValueError('invalid file')
    if not any(n.startswith('test_') for n in files) or not any(not n.startswith('test_') for n in files):raise ValueError('implementation and tests required')
    if sum(len(s.encode()) for s in files.values())>100000:raise ValueError('package too large')
    for source in files.values():compile(source,'<candidate>','exec')
    return files

def run():
    if len(sys.argv)!=1:raise ValueError('arguments not supported')
    raw=sys.stdin.buffer.read(150001)
    if len(raw)>150000:raise ValueError('payload too large')
    files=validate(json.loads(raw))
    image=subprocess.check_output(['/usr/bin/docker','image','inspect',IMAGE,'--format','{{.Id}}']).decode().strip()
    name='tensorplatform-development-'+uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='tensorplatform-development-') as temp:
        path=Path(temp);path.chmod(0o755)
        for filename,source in files.items():
            p=path/filename;p.write_text(source);p.chmod(0o444)
        args=['/usr/bin/docker','run','--pull','never','--rm','--name',name,'--network','none','--read-only',
              '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
              '--memory','1g','--cpus','1','--user','65534:65534',
              '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','-e','OPENBLAS_NUM_THREADS=1',
              '-e','OMP_NUM_THREADS=1','--mount',f'type=bind,src={temp},dst=/work,readonly',
              '--mount','type=bind,src=/usr/local/lib/tensorplatform-resident/run_unittest.py,dst=/runner.py,readonly',
              '--workdir','/work','--entrypoint','python',image,'-I','-B','/runner.py']
        # Bound collector output; malformed or inconsistent records fail closed.
        def limits():
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE,(131072,131072))
        timed_out=False
        try:
            with tempfile.TemporaryFile() as log:
                process=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,env={'PATH':'/usr/bin:/bin'},preexec_fn=limits)
                try:process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    timed_out=True;process.kill();process.wait()
                log.seek(0);output=log.read(131072).decode(errors='replace')
            try:
                tests=json.loads(output)
                if tests.get('schema_version')!=1 or tests.get('status')!='completed':raise ValueError('collector incomplete')
                methods=tests['methods'];counts=tests['counts']
                if type(tests['tests_run']) is not int or not 0<=tests['tests_run']<=512:raise ValueError('invalid method count')
                if len(methods)!=tests['tests_run'] or tests.get('truncated') or len({r['id'] for r in methods})!=len(methods):raise ValueError('ambiguous or truncated test identities')
                statuses={'passed','failure','error','skipped','expected_failure','unexpected_success'}
                if any(r.get('status') not in statuses for r in methods):raise ValueError('invalid status')
                if any(counts.get(k)!=sum(r['status']==k for r in methods) for k in statuses):raise ValueError('test counters disagree')
                subtests=tests['subtests']
                if len(subtests)>512 or any(r.get('parent_id') not in {m['id'] for m in methods} or r.get('status') not in {'passed','failure','error'} for r in subtests):raise ValueError('invalid subtest collection')
                if any(type(counts.get(k)) is not int for k in statuses):raise ValueError('invalid counter type')
                if any(counts.get('subtests_'+k)!=sum(r['status']==k for r in subtests) for k in ['passed','failure','error']):raise ValueError('subtest counters disagree')
                collection_ok=True
            except (ValueError,KeyError,TypeError):
                tests={'status':'unavailable','log':output[:24000]};collection_ok=False
            print(json.dumps({'exit_code':process.returncode,'timed_out':timed_out,
                'process_passed':process.returncode==0 and not timed_out and collection_ok and tests['counts']['passed']>0 and all(tests['counts'][k]==0 for k in ['failure','error','unexpected_success']),
                'image':image,'package_sha256':hashlib.sha256(raw).hexdigest(),'command':'python -I -B /runner.py (trusted unittest discovery)',
                'test_runner_sha256':hashlib.sha256(Path('/usr/local/lib/tensorplatform-resident/run_unittest.py').read_bytes()).hexdigest(),
                'tests':tests,'log':tests.get('log',''),'assurance':'collected software tests; not independent business or scientific acceptance'},ensure_ascii=False))
        finally:subprocess.run(['/usr/bin/docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)

if __name__=='__main__':run()
