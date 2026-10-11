"""
dbt 数据转换服务
ELT 管道的 T 层 — SQL/模型驱动的数据转换
"""
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime


class DBTTransformer:
    """dbt 数据转换引擎"""

    DBT_DIR = "/app/dbt_project"

    def __init__(self):
        self.project_dir = Path(self.DBT_DIR)

    def init_project(self, project_name="foundation_data"):
        """初始化 dbt 项目"""
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Create dbt_project.yml
        project_yaml = f"""
name: '{project_name}'
version: '1.0.0'
config-version: 2
profile: 'foundation'

model-paths: ["models"]
analysis-paths: ["analyses"]
test-paths: ["tests"]
seed-paths: ["seeds"]
macro-paths: ["macros"]
snapshot-paths: ["snapshots"]

target-path: "target"
clean-targets:
  - "target"
  - "dbt_packages"

models:
  {project_name}:
    staging:
      +materialized: view
    marts:
      +materialized: table
"""
        (self.project_dir / "dbt_project.yml").write_text(project_yaml)
        (self.project_dir / "models").mkdir(exist_ok=True)
        (self.project_dir / "models" / "staging").mkdir(exist_ok=True)
        (self.project_dir / "models" / "marts").mkdir(exist_ok=True)

        return {
            "project": project_name,
            "directory": str(self.project_dir),
            "status": "initialized",
        }

    def create_model(self, name: str, sql: str, schema: str = "staging"):
        """创建 dbt 模型 (SQL)"""
        model_dir = self.project_dir / "models" / schema
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / f"{name}.sql"
        model_file.write_text(sql)

        return {
            "name": name,
            "schema": schema,
            "file": str(model_file),
            "sql_preview": sql[:200],
        }

    def run(self, models: str = None, full_refresh: bool = False):
        """执行 dbt run"""
        cmd = ["dbt", "run"]
        if models:
            cmd += ["--select", models]
        if full_refresh:
            cmd.append("--full-refresh")

        result = subprocess.run(
            cmd, cwd=self.project_dir, capture_output=True, text=True
        )

        return {
            "command": " ".join(cmd),
            "status": "success" if result.returncode == 0 else "error",
            "output": result.stdout[:500],
            "stderr": result.stderr[:200],
            "timestamp": datetime.utcnow().isoformat(),
        }

    def test(self, models: str = None):
        """执行 dbt test"""
        cmd = ["dbt", "test"]
        if models:
            cmd += ["--select", models]

        result = subprocess.run(
            cmd, cwd=self.project_dir, capture_output=True, text=True
        )
        return {"status": "success" if result.returncode == 0 else "error",
                "output": result.stdout[:500]}

    def list_models(self):
        """列出所有模型"""
        models_dir = self.project_dir / "models"
        models = []
        if models_dir.exists():
            for sql_file in models_dir.rglob("*.sql"):
                models.append({
                    "name": sql_file.stem,
                    "schema": sql_file.parent.name,
                    "path": str(sql_file),
                })
        return models
