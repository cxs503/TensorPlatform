#!/usr/bin/env python3
"""Generate synthetic knowledge entries to seed the knowledge base."""
from __future__ import annotations
import argparse, hashlib, json, random, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.modules.knowledge_engine import KnowledgeStore, KnowledgeEntry

DOMAINS = ["resistance","propulsion","seakeeping","maneuvering","structural","vibration_noise","ice","green_navigation","stability","platform"]
CATEGORIES = ["new_capability","capability_extension","defect_fix","reusable_knowledge","falsified_hypothesis","no_substantive_delta"]
OUTCOMES = ["success","partial","fail","honest_fail"]
CONFIDENCES = ["high","medium","low","unknown"]
TOOLS = ["python","numpy","scipy","pytest","docker","git","sqlite","fastapi"]
REPOS = ["TensorPlatform","TensorLBM","TensorFEM","TensorFVM","TensorBEM","TensorPhysics"]
METHODS = ["ITTC-1957","Lindqvist","IACS","FEM Mindlin","LBM MRT","BEM panel","Vortex lattice","RANS k-eps"]
LIMITATIONS = ["Limited to regular waves","Neglects viscosity","Valid for slender bodies","Requires fine mesh","Computationally expensive"]
NEXT_STEPS = ["Extend to irregular waves","Add viscous correction","Validate more data","Optimize performance","Couple solvers"]

def gen_entry(idx):
    domain = random.choice(DOMAINS)
    cat = random.choice(CATEGORIES)
    outcome = random.choice(OUTCOMES)
    conf = random.choice(CONFIDENCES)
    repo = random.choice(REPOS)
    method = random.choice(METHODS)
    goal = f"Implement {domain} calculation with {method}"
    approach = f"Used {method} for {domain} analysis"
    tools = random.sample(TOOLS, random.randint(2,4))
    lims = random.sample(LIMITATIONS, random.randint(0,2))
    nxts = random.sample(NEXT_STEPS, random.randint(0,2))
    metrics = {"error_pct": round(random.uniform(0.5,15.0),2)} if random.random()>0.3 else {}
    tr = {}
    if random.random()>0.2:
        t = random.randint(5,50); p = t - random.randint(0,2)
        tr = {"total":t,"passed":p,"failed":t-p,"coverage_pct":round(random.uniform(70,98),1)}
    e = KnowledgeEntry(
        entry_id="",
        problem_statement={"goal":goal,"context":f"{domain} development","domain":domain,"tags":[domain,cat.replace("_","-")]},
        methodology={"approach":approach,"tools":tools,"parameters":{"method":method},"assumptions":[]},
        evidence_chain={"artifacts":[{"path":f"tests/test_{domain}.py","type":"test_log"}],"metrics":metrics,"test_results":tr},
        conclusion={"summary":f"{goal}. Outcome: {outcome}.","outcome":outcome,"key_findings":[goal],"limitations":lims,"next_steps":nxts},
        uncertainty={"confidence":conf,"confidence_score":{"high":0.9,"medium":0.6,"low":0.3,"unknown":0.5}[conf],"sources_of_uncertainty":["synthetic"],"validation_status":"unvalidated"},
        provenance={"source_type":"backfill","source_id":f"synthetic_{idx:06d}","repo":repo,"branch":"main","commit":hashlib.sha256(f"syn_{idx}".encode()).hexdigest()[:40],"agent_id":"synthetic_gen","extractor_version":"1.0.0","extraction_timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())},
    )
    e.entry_id = e.compute_id()
    return e

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store",required=True)
    ap.add_argument("--count",type=int,default=100)
    ap.add_argument("--seed",type=int,default=42)
    ap.add_argument("--verbose","-v",action="store_true")
    args = ap.parse_args(argv)
    random.seed(args.seed)
    store = KnowledgeStore(args.store)
    init = store.count()
    if args.verbose: print(f"Initial: {init}, generating {args.count}...")
    t0 = time.time()
    ok = fail = 0
    for i in range(args.count):
        try:
            store.upsert(gen_entry(init+i))
            ok += 1
            if args.verbose and (i+1)%100==0: print(f"  {i+1}/{args.count}")
        except Exception as e:
            fail += 1
            if args.verbose: print(f"  FAIL {i}: {e}")
    elapsed = time.time()-t0
    stats = {"initial":init,"generated":ok,"failed":fail,"final":store.count(),"elapsed_s":round(elapsed,2),"avg_ms":round(elapsed*1000/max(ok,1),2)}
    print(json.dumps(stats,indent=2))
    return 0 if fail==0 else 1

if __name__=="__main__": sys.exit(main())
