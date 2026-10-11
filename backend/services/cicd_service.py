"""
CI/CD 服务
支持流水线定义、触发、执行和Artifact管理
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class PipelineStatus(str, Enum):
    """流水线状态"""

    DRAFT = "draft"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    """阶段状态"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStage:
    """流水线阶段"""

    def __init__(self, name: str, action: str, config: Dict = None):
        self.name = name
        self.action = action  # build, test, deploy, notify
        self.config = config or {}
        self.status = StageStatus.PENDING
        self.started_at = None
        self.finished_at = None
        self.logs: List[str] = []


class PipelineRun:
    """流水线执行"""

    def __init__(self, pipeline_id: str):
        self.id = f"run_{uuid.uuid4().hex[:12]}"
        self.pipeline_id = pipeline_id
        self.status = PipelineStatus.PENDING
        self.stages: List[PipelineStage] = []
        self.started_at = None
        self.finished_at = None
        self.triggered_by = None
        self.commit_id = None
        self.artifacts: Dict[str, str] = {}  # name -> url


class Pipeline:
    """流水线"""

    def __init__(self, name: str, project_id: str = None):
        self.id = f"pipeline_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.project_id = project_id
        self.stages: List[PipelineStage] = []
        self.status = PipelineStatus.DRAFT
        self.webhook_url = None
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.last_run: Optional[PipelineRun] = None


class Webhook:
    """Webhook配置"""

    def __init__(self, pipeline_id: str, events: List[str]):
        self.id = f"webhook_{uuid.uuid4().hex[:12]}"
        self.pipeline_id = pipeline_id
        self.events = events  # push, pull_request, tag
        self.secret = uuid.uuid4().hex[:16]
        self.url = f"/api/v1/cicd/webhook/{self.id}"
        self.enabled = True


class Artifact:
    """构建产物"""

    def __init__(self, name: str, run_id: str, file_path: str, size: int):
        self.id = f"artifact_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.run_id = run_id
        self.file_path = file_path
        self.size = size
        self.created_at = datetime.now().isoformat()


class CICDService:
    """CI/CD服务"""

    def __init__(self):
        self.pipelines: Dict[str, Pipeline] = {}
        self.runs: Dict[str, PipelineRun] = {}
        self.webhooks: Dict[str, Webhook] = {}
        self.artifacts: Dict[str, Artifact] = {}

    def create_pipeline(
        self, name: str, project_id: str = None, stages: List[Dict] = None
    ) -> Pipeline:
        """创建流水线"""
        pipeline = Pipeline(name, project_id)

        # 添加默认阶段
        if stages:
            for stage in stages:
                pipeline.stages.append(
                    PipelineStage(
                        name=stage.get("name"),
                        action=stage.get("action"),
                        config=stage.get("config", {}),
                    )
                )
        else:
            # 默认阶段
            pipeline.stages = [
                PipelineStage("Build", "build", {"script": "make build"}),
                PipelineStage("Test", "test", {"script": "make test"}),
                PipelineStage("Deploy", "deploy", {"script": "make deploy"}),
            ]

        self.pipelines[pipeline.id] = pipeline
        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Optional[Pipeline]:
        """获取流水线"""
        return self.pipelines.get(pipeline_id)

    def update_pipeline(
        self, pipeline_id: str, stages: List[Dict]
    ) -> Optional[Pipeline]:
        """更新流水线阶段"""
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return None

        pipeline.stages = [
            PipelineStage(
                name=s.get("name"), action=s.get("action"), config=s.get("config", {})
            )
            for s in stages
        ]
        pipeline.updated_at = datetime.now().isoformat()
        return pipeline

    def delete_pipeline(self, pipeline_id: str) -> bool:
        """删除流水线"""
        if pipeline_id in self.pipelines:
            del self.pipelines[pipeline_id]
            return True
        return False

    def trigger_pipeline(
        self, pipeline_id: str, triggered_by: str = "manual", commit_id: str = None
    ) -> Optional[PipelineRun]:
        """触发流水线执行"""
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return None

        # 创建新的执行
        run = PipelineRun(pipeline_id)
        run.triggered_by = triggered_by
        run.commit_id = commit_id
        run.stages = [
            PipelineStage(s.name, s.action, s.config.copy()) for s in pipeline.stages
        ]
        run.status = PipelineStatus.RUNNING
        run.started_at = datetime.now().isoformat()

        self.runs[run.id] = run
        pipeline.last_run = run

        # 模拟执行各阶段
        self._execute_run(run)

        return run

    def _execute_run(self, run: PipelineRun):
        """执行流水线（模拟）"""
        for stage in run.stages:
            stage.status = StageStatus.RUNNING
            stage.started_at = datetime.now().isoformat()

            # 模拟阶段执行
            stage.logs.append(f"Executing stage: {stage.name}")
            stage.logs.append(f"Action: {stage.action}")

            # 根据动作类型生成日志
            if stage.action == "build":
                stage.logs.append("Compiling source code...")
                stage.logs.append("Build completed successfully")
            elif stage.action == "test":
                stage.logs.append("Running unit tests...")
                stage.logs.append("All tests passed")
            elif stage.action == "deploy":
                stage.logs.append("Deploying to target environment...")
                stage.logs.append("Deployment successful")

            stage.status = StageStatus.SUCCESS
            stage.finished_at = datetime.now().isoformat()

        run.status = PipelineStatus.SUCCESS
        run.finished_at = datetime.now().isoformat()

    def get_run(self, run_id: str) -> Optional[PipelineRun]:
        """获取执行详情"""
        return self.runs.get(run_id)

    def cancel_run(self, run_id: str) -> bool:
        """取消执行"""
        run = self.runs.get(run_id)
        if not run or run.status != PipelineStatus.RUNNING:
            return False

        run.status = PipelineStatus.CANCELLED
        run.finished_at = datetime.now().isoformat()

        # 将未完成的阶段标记为跳过
        for stage in run.stages:
            if (
                stage.status == StageStatus.PENDING
                or stage.status == StageStatus.RUNNING
            ):
                stage.status = StageStatus.SKIPPED

        return True

    def create_webhook(self, pipeline_id: str, events: List[str]) -> Optional[Webhook]:
        """创建Webhook"""
        if pipeline_id not in self.pipelines:
            return None

        webhook = Webhook(pipeline_id, events)
        self.webhooks[webhook.id] = webhook
        return webhook

    def trigger_by_webhook(
        self, webhook_id: str, event: str, payload: Dict
    ) -> Optional[PipelineRun]:
        """通过Webhook触发"""
        webhook = self.webhooks.get(webhook_id)
        if not webhook or not webhook.enabled:
            return None

        if event not in webhook.events:
            return None

        # 从payload提取信息
        commit_id = payload.get("commit_id", payload.get("after"))
        triggered_by = payload.get("user", "webhook")

        return self.trigger_pipeline(webhook.pipeline_id, triggered_by, commit_id)

    def upload_artifact(
        self, run_id: str, name: str, file_path: str, size: int
    ) -> Artifact:
        """上传构建产物"""
        artifact = Artifact(name, run_id, file_path, size)
        self.artifacts[artifact.id] = artifact
        return artifact

    def list_pipelines(self, project_id: str = None) -> List[Dict]:
        """列出流水线"""
        pipelines = self.pipelines.values()
        if project_id:
            pipelines = [p for p in pipelines if p.project_id == project_id]

        return [
            {
                "id": p.id,
                "name": p.name,
                "project_id": p.project_id,
                "status": p.status,
                "stages": [{"name": s.name, "action": s.action} for s in p.stages],
                "last_run": {
                    "id": p.last_run.id,
                    "status": p.last_run.status,
                    "started_at": p.last_run.started_at,
                }
                if p.last_run
                else None,
                "created_at": p.created_at,
            }
            for p in pipelines
        ]

    def list_runs(self, pipeline_id: str) -> List[Dict]:
        """列出流水线执行"""
        return [
            {
                "id": r.id,
                "pipeline_id": r.pipeline_id,
                "status": r.status,
                "triggered_by": r.triggered_by,
                "commit_id": r.commit_id,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "duration": self._calc_duration(r.started_at, r.finished_at),
            }
            for r in self.runs.values()
            if r.pipeline_id == pipeline_id
        ]

    def _calc_duration(self, start: str, end: str) -> int:
        """计算执行时长（秒）"""
        if not start or not end:
            return 0
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            return int((end_dt - start_dt).total_seconds())
        except:
            return 0


# 全局实例
_cicd_service: Optional[CICDService] = None


def get_cicd_service() -> CICDService:
    """获取CI/CD服务"""
    global _cicd_service
    if _cicd_service is None:
        _cicd_service = CICDService()
    return _cicd_service
