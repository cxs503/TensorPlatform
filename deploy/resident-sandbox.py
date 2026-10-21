#!/usr/bin/python3
"""Root-owned fixed Docker runner. No caller paths, commands or Docker arguments."""
import json
import hashlib
import resource
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
IMAGE = subprocess.check_output(['/usr/bin/docker','image','inspect','tensorplatform-factory-builder:local','--format','{{.Id}}']).decode().strip()
if len(sys.argv) != 1:
    raise SystemExit('arguments are not supported')
source = sys.stdin.buffer.read(65537)
if not source or len(source) > 65536:
    raise SystemExit('source size outside limits')
source.decode('utf-8')
name = 'tensorplatform-resident-check-' + uuid.uuid4().hex
with tempfile.TemporaryDirectory(prefix='tensorplatform-resident-check-') as temp:
    path = Path(temp)
    path.chmod(0o755)
    (path/'candidate.py').write_bytes(source)
    (path/'candidate.py').chmod(0o444)
    command = ['/usr/bin/docker','run','--pull','never','--rm','--name',name,'--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
        '--memory','1g','--cpus','1','--user','65534:65534',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','-e','PYTHONDONTWRITEBYTECODE=1',
        '-e','OPENBLAS_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',
        '--mount',f'type=bind,src={temp},dst=/work,readonly',
        '--mount','type=bind,src=/usr/local/lib/tensorplatform-resident/check_quality.py,dst=/runner.py,readonly',
        '--entrypoint','python',IMAGE,'/runner.py']
    try:
        with tempfile.TemporaryFile() as out:
            resource.setrlimit(resource.RLIMIT_FSIZE, (131072,131072))
            proc = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, env={'PATH':'/usr/bin:/bin'})
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
                raise RuntimeError('sandbox timed out') from None
            out.seek(0)
            payload = out.read(131073)
            if len(payload) > 131072 or proc.returncode:
                print(json.dumps({'passed':False,'error':'sandbox execution failed','exit_code':proc.returncode}))
            else:
                # Do not stream arbitrary candidate stdout through the privileged helper.
                result = json.loads(payload)
                if not isinstance(result, dict) or not isinstance(result.get('checks'),list):
                    raise ValueError('invalid sandbox result')
                result["test_suite_sha256"] = hashlib.sha256(Path("/usr/local/lib/tensorplatform-resident/check_quality.py").read_bytes()).hexdigest()
                result["image"] = IMAGE
                print(json.dumps(result))
    finally:
        subprocess.run(['/usr/bin/docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
