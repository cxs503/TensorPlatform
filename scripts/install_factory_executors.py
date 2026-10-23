#!/usr/bin/env python3
"""Install fixed privileged executors for an explicitly selected checkout; no model access."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:parser.error('run this installation as root')
    root=args.root.resolve()
    if not (root/'backend/factory_app.py').is_file():parser.error('root must be the reviewed TensorPlatform checkout')
    subprocess.run(['/usr/bin/docker','image','inspect','tensorplatform-factory-builder:local'],check=True,stdout=subprocess.DEVNULL)
    library=Path('/usr/local/lib/tensorplatform-resident');library.mkdir(parents=True,exist_ok=True)
    executors=Path('/usr/local/libexec');executors.mkdir(parents=True,exist_ok=True)
    for filename in ['run_unittest.py','check_quality.py']:
        dest=library/filename;shutil.copyfile(root/'tools/factory/resident'/filename,dest);dest.chmod(0o644)
    for source,name in [('development-sandbox.py','development-sandbox'),('resident-sandbox.py','resident-sandbox'),('platform-check.py','platform-check')]:
        content=(root/'deploy'/source).read_text()
        # Replace the complete quoted literal with repr, so unusual paths remain valid Python.
        content=content.replace("'__FACTORY_WORKSPACE_ROOT__'",repr(str(root)))
        dest=executors/('tensorplatform-'+name);dest.write_text(content);dest.chmod(0o755)
    print('Fixed executors installed; grant only these explicit commands to the service account if required.')
if __name__=='__main__':main()
