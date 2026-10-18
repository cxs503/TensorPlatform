"""Persistent research-task API with a real local CPU spring-chain benchmark."""
from __future__ import annotations
import csv, hashlib, json, math, os, sqlite3, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/research-factory", tags=["科研工厂"])
DB_PATH = Path(os.getenv("RESEARCH_FACTORY_DB", "uploads/research_factory.sqlite3")).resolve()
ARTIFACT_DIR = Path(os.getenv("RESEARCH_FACTORY_ARTIFACTS", "uploads/research-artifacts")).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def connect():
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.row_factory = sqlite3.Row
    return db

with connect() as db:
    db.execute("""CREATE TABLE IF NOT EXISTS research_runs (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, solver TEXT NOT NULL,
      backend TEXT NOT NULL, status TEXT NOT NULL, verification TEXT NOT NULL, created_at TEXT NOT NULL,
      started_at TEXT, finished_at TEXT, parameters_json TEXT NOT NULL, result_json TEXT, error TEXT, artifact_dir TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_created ON research_runs(created_at DESC)")

class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    goal: str = Field(default="", max_length=4000)
    solver: Literal["SpringChainBenchmark"] = "SpringChainBenchmark"
    backend: Literal["local-cpu"] = "local-cpu"
    parameters: dict[str, Any] = Field(default_factory=dict)

def serialize(row):
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json"))
    raw = item.pop("result_json")
    item["result"] = json.loads(raw) if raw else None
    return item

@router.get("/health")
def health():
    return {"status":"ready","service":"research-factory","database":"sqlite-persistent",
            "runners":[{"id":"spring-chain","kind":"real-numerical-computation","backend":"local-cpu"}],
            "hpc":{"connected":False,"reason":"No HPC scheduler adapter configured"}}

@router.get("/tasks")
def list_tasks(limit: int = 50):
    limit = min(max(limit,1),200)
    with connect() as db:
        rows = db.execute("SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
    return {"items":[serialize(row) for row in rows],"total":len(rows)}

@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate):
    defaults = {"particles":24,"steps":2400,"dt":0.001,"spring_stiffness":180.0,"damping":0.8,"initial_impulse":2.0}
    bounds = {"particles":(8,64),"steps":(100,10000),"dt":(0.0001,0.003),"spring_stiffness":(10,1000),"damping":(0,10),"initial_impulse":(0.1,10)}
    for key,value in payload.parameters.items():
        if key not in bounds: raise HTTPException(422, f"Unknown parameter: {key}")
        lo,hi=bounds[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not lo <= value <= hi:
            raise HTTPException(422, f"Parameter {key} must be in [{lo}, {hi}]")
        defaults[key] = int(value) if key in ("particles","steps") else float(value)
    if int(defaults["particles"]) != defaults["particles"] or int(defaults["steps"]) != defaults["steps"]:
        raise HTTPException(422,"particles and steps must be integers")
    run_id="RF-"+datetime.now(timezone.utc).strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:8].upper()
    with connect() as db:
        db.execute("""INSERT INTO research_runs(id,title,goal,solver,backend,status,verification,created_at,parameters_json)
        VALUES(?,?,?,?,?,?,?,?,?)""",(run_id,payload.title.strip(),payload.goal.strip(),payload.solver,payload.backend,"queued","not-validated",datetime.now(timezone.utc).isoformat(),json.dumps(defaults)))
        return serialize(db.execute("SELECT * FROM research_runs WHERE id=?",(run_id,)).fetchone())

@router.post("/tasks/{run_id}/run")
def run_task(run_id: str):
    with connect() as db:
        row=db.execute("SELECT * FROM research_runs WHERE id=?",(run_id,)).fetchone()
        if row is None: raise HTTPException(404,"Research task not found")
        if row["status"]=="completed": return serialize(row)
        if row["status"]=="running": raise HTTPException(409,"Task already running")
        db.execute("UPDATE research_runs SET status='running',started_at=?,error=NULL WHERE id=?",(datetime.now(timezone.utc).isoformat(),run_id))
        task=serialize(db.execute("SELECT * FROM research_runs WHERE id=?",(run_id,)).fetchone())
    p=task["parameters"]; n=int(p["particles"]); steps=int(p["steps"]); dt=float(p["dt"])
    k=float(p["spring_stiffness"]); damping=float(p["damping"]); impulse=float(p["initial_impulse"])
    x=[0.0]*n; v=[0.0]*n; v[n//2]=impulse; samples=[]; peak_disp=0.0; peak_energy=0.5*impulse*impulse
    started=time.perf_counter()
    try:
        for step in range(steps):
            forces=[0.0]*n
            for i in range(1,n-1): forces[i]=k*(x[i-1]+x[i+1]-2*x[i])-damping*v[i]
            nv=v[:]
            for i in range(1,n-1): nv[i]=v[i]+forces[i]*dt
            nx=x[:]
            for i in range(1,n-1): nx[i]=x[i]+nv[i]*dt
            x,v=nx,nv
            if not all(math.isfinite(a) for a in x+v): raise ArithmeticError("Non-finite state; reduce dt or stiffness")
            kinetic=0.5*sum(a*a for a in v)
            elastic=0.5*k*sum((x[i+1]-x[i])**2 for i in range(n-1))
            energy=kinetic+elastic; peak_energy=max(peak_energy,energy); peak_disp=max(peak_disp,max(abs(a) for a in x))
            if step % max(1,steps//120)==0 or step==steps-1:
                samples.append({"time":round((step+1)*dt,8),"max_abs_displacement":max(abs(a) for a in x),"kinetic_energy":kinetic,"elastic_energy":elastic,"total_energy":energy})
        elapsed=round(time.perf_counter()-started,6)
        artifact_dir=ARTIFACT_DIR/run_id; artifact_dir.mkdir(parents=True,exist_ok=True)
        timeseries_path=artifact_dir/"timeseries.csv"
        with timeseries_path.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=list(samples[0].keys())); writer.writeheader(); writer.writerows(samples)
        timeseries_bytes=timeseries_path.read_bytes()
        timeseries_artifact={"name":"timeseries.csv","url":f"/api/v1/research-factory/tasks/{run_id}/artifacts/timeseries.csv",
          "media_type":"text/csv","sha256":hashlib.sha256(timeseries_bytes).hexdigest(),"size_bytes":len(timeseries_bytes)}
        result={"benchmark":"damped-mass-spring-chain-v1","description":"Real local CPU integration of a 1D damped spring chain; not an ice DEM simulation.",
          "particles":n,"steps":steps,"dt":dt,"simulated_time":steps*dt,"wall_time_seconds":elapsed,"peak_displacement":peak_disp,
          "initial_kinetic_energy":0.5*impulse*impulse,"peak_total_energy":peak_energy,"final_total_energy":samples[-1]["total_energy"],
          "sample_count":len(samples),"samples":samples,"artifacts":[timeseries_artifact],
          "validation":{"finite_state":True,"method":"finite-value check","scientific_validation":"not-established"}}
        manifest={"schema_version":1,"run_id":run_id,"status":"succeeded","validation_status":"not_run",
          "title":task["title"],"goal":{"objective":task["goal"] or task["title"]},"solver":task["solver"],"backend":"local-cpu",
          "created_at":task["created_at"],"parameters":p,
          "configuration_sha256":hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest(),
          "producer":{"name":task["solver"],"revision":os.getenv("GIT_COMMIT_SHA","unknown")},
          "environment":{"backend":"local-cpu"},"resources":{"accelerator":"none"},
          "metrics":{"peak_displacement":peak_disp,"final_total_energy":samples[-1]["total_energy"],"wall_time_seconds":elapsed},
          "outputs":[{"uri":timeseries_artifact["url"],"media_type":timeseries_artifact["media_type"],
            "checksum_sha256":timeseries_artifact["sha256"],"size_bytes":timeseries_artifact["size_bytes"],
            "role":"timeseries","producer_revision":os.getenv("GIT_COMMIT_SHA","unknown")}],
          "validation":{"status":"not_run","validator":"finite-value-check",
            "checks":[{"name":"finite_state","status":"passed","description":"All recorded state values were finite."}],
            "limitations":["No independent scientific benchmark, conservation, or convergence validation was run."]},
          "limitations":["Successful execution is not equivalent to scientific validation."],"result":result}
        manifest_path=artifact_dir/"run-manifest.json"
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        manifest_bytes=manifest_path.read_bytes()
        result["artifacts"].append({"name":"run-manifest.json","url":f"/api/v1/research-factory/tasks/{run_id}/artifacts/run-manifest.json",
          "media_type":"application/json","sha256":hashlib.sha256(manifest_bytes).hexdigest(),"size_bytes":len(manifest_bytes)})
        with connect() as db:
            db.execute("""UPDATE research_runs SET status='completed',verification='computed-not-scientifically-validated',
              finished_at=?,result_json=?,artifact_dir=? WHERE id=?""",(datetime.now(timezone.utc).isoformat(),json.dumps(result),str(artifact_dir),run_id))
            return serialize(db.execute("SELECT * FROM research_runs WHERE id=?",(run_id,)).fetchone())
    except Exception as exc:
        with connect() as db: db.execute("UPDATE research_runs SET status='failed',error=?,finished_at=? WHERE id=?",(str(exc),datetime.now(timezone.utc).isoformat(),run_id))
        raise HTTPException(422,f"Numerical run failed: {exc}")

@router.get("/tasks/{run_id}")
def get_task(run_id: str):
    with connect() as db: row=db.execute("SELECT * FROM research_runs WHERE id=?",(run_id,)).fetchone()
    if row is None: raise HTTPException(404,"Research task not found")
    return serialize(row)

@router.get("/tasks/{run_id}/artifacts/{name}")
def artifact(run_id: str,name: str):
    if name not in ("timeseries.csv","run-manifest.json"): raise HTTPException(404,"Artifact not found")
    with connect() as db: row=db.execute("SELECT artifact_dir FROM research_runs WHERE id=?",(run_id,)).fetchone()
    if row is None or not row["artifact_dir"]: raise HTTPException(404,"Artifact not available")
    root=Path(row["artifact_dir"]).resolve(); path=(root/name).resolve()
    if path.parent != root or not path.is_file(): raise HTTPException(404,"Artifact not found")
    return FileResponse(path,filename=name)
