"""Recompute structured claims against bounded, digest-bound local evidence."""
import hashlib
import json
import math
import subprocess
import os
import sys
import shutil
import tempfile
from pathlib import Path

PLOT_PYTHON=os.environ.get('FACTORY_PLOT_PYTHON',sys.executable)

def attachment(folder, ref):
    name=ref['name']
    if not isinstance(name,str) or Path(name).name!=name or name in {'.','..'}:raise ValueError('invalid artifact name')
    path=folder/name
    if path.is_symlink() or path.resolve().parent!=folder.resolve():raise ValueError('artifact escape')
    if path.stat().st_size>10*1024*1024:raise ValueError('artifact exceeds limit')
    raw=path.read_bytes()
    if len(raw)!=ref['size_bytes'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('artifact digest mismatch')
    return raw

def reference(folder,name):
    raw=(folder/name).read_bytes()
    return {'name':name,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def pointer(data,path):
    if path=='':return data
    if not isinstance(path,str) or not path.startswith('/'):raise ValueError('invalid JSON pointer')
    for part in path[1:].split('/'):
        key=part.replace('~1','/').replace('~0','~')
        if isinstance(data,list):
            if not key.isdigit() or str(int(key))!=key:raise ValueError('invalid array index')
            data=data[int(key)]
        else:data=data[key]
    return data

def check_claim(folder,claim):
    value=pointer(json.loads(attachment(folder,claim['artifact'])),claim['pointer'])
    expected=claim['expected']
    if type(expected) not in (int,float,bool,str) or type(value)!=type(expected):raise ValueError('scalar type mismatch')
    if isinstance(expected,float) and (not math.isfinite(expected) or not math.isfinite(value)):raise ValueError('nonfinite measurement')
    if value!=expected:raise ValueError('claim differs from source')
    if not isinstance(claim.get('unit'),str) or not claim['unit']:raise ValueError('unit required')
    return value

def check_figure(folder,figure):
    raw=attachment(folder,figure['image'])
    attachment(folder,figure['data']);attachment(folder,figure['script'])
    if not figure.get('caption'):raise ValueError('figure caption required')
    # Fixed decoder only; model-authored plotting scripts are never executed here.
    script="import io,sys,json,PIL;from PIL import Image;Image.MAX_IMAGE_PIXELS=16000000;b=sys.stdin.buffer.read();im=Image.open(io.BytesIO(b));im.verify();im=Image.open(io.BytesIO(b));im.load();assert im.format=='PNG';print(json.dumps({'width':im.width,'height':im.height,'format':im.format,'pillow_version':PIL.__version__}))"
    run=subprocess.run([PLOT_PYTHON,'-I','-c',script],input=raw,capture_output=True,timeout=20)
    if run.returncode:raise ValueError('image decoding failed')
    dimensions=json.loads(run.stdout)
    if figure.get('rendering')=='fixed-matplotlib-test-chart-v1':
        trusted=Path(__file__).with_name('plot_test_results.py')
        if figure['script']['sha256']!=hashlib.sha256(trusted.read_bytes()).hexdigest():raise ValueError('unknown fixed renderer')
        measurements=json.loads(attachment(folder,figure['data']))
        source=measurements['source']
        if pointer(json.loads(attachment(folder,source['artifact'])),source['pointer'])!=measurements['counts']:raise ValueError('plot measurements differ from raw test counters')
        with tempfile.TemporaryDirectory(prefix='tensorplatform-figure-') as temp:
            target=Path(temp)/'rerender.png'
            subprocess.run([PLOT_PYTHON,'-I',str(trusted),str(folder/figure['data']['name']),str(target)],check=True,capture_output=True,timeout=30)
            if target.read_bytes()!=raw:raise ValueError('figure differs from fixed renderer output')
        dimensions['rerender_verified']=True
    return dimensions

def evaluate(folder,claims,figures):
    rows=[]
    for kind,items,checker in [('claim',claims,check_claim),('figure',figures,check_figure)]:
        for i,item in enumerate(items):
            try:observed=checker(folder,item);row={'kind':kind,'index':i,'passed':True,'observed':observed}
            except (ValueError,KeyError,TypeError,OSError,IndexError,subprocess.SubprocessError) as e:row={'kind':kind,'index':i,'passed':False,'reason':str(e)}
            rows.append(row)
    return {'schema_version':1,'passed':all(r['passed'] for r in rows),'checks':rows,
        'scope':'structured claims and image decoding/provenance only; narrative semantics and visual scientific correctness require independent review',
        'figure_status':'checked' if figures else 'not-applicable-no-registered-figure',
        'unchecked_narrative':True,'decoder_python':PLOT_PYTHON}

def collect_claims(folder,data):
    claims=[]
    def walk(value,path):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in {'log','log_excerpt','methods','subtests','files','deliverable','attempts'}:continue
                walk(v,path+'/'+k.replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            for i,v in enumerate(value):walk(v,path+'/'+str(i))
        elif type(value) in (int,float,bool):
            claims.append({'artifact':reference(folder,'result.json'),'pointer':path,'expected':value,'unit':'seconds' if path.endswith('latency_seconds') else 'source-defined-count-or-flag'})
    for key in ['baseline','candidate','verified','inference','usage','latency_seconds']:
        if key in data:walk(data[key],'/'+key)
    return claims


def make_test_figure(folder,data):
    tests=(data.get('candidate') or {}).get('tests',{})
    if tests.get('status')!='completed' or tests.get('truncated'):return []
    source=folder/'test-measurements.json'
    source.write_text(json.dumps({'counts':tests['counts'],'source':{'artifact':reference(folder,'result.json'),'pointer':'/candidate/tests/counts'}},indent=2))
    script=folder/'plot-test-results.py'
    shutil.copyfile(Path(__file__).with_name('plot_test_results.py'),script)
    subprocess.run([PLOT_PYTHON,'-I',str(script),str(source),str(folder/'test-results.png')],check=True,capture_output=True,timeout=30)
    return [{'image':reference(folder,'test-results.png'),'data':reference(folder,source.name),'script':reference(folder,script.name),'caption':'实测测试方法状态；子测试错误另列于原始结果，不换算成业务准确率。','rendering':'fixed-matplotlib-test-chart-v1'}]
