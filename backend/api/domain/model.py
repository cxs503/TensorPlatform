"""
Model domain — aggregates all model-related APIs.

Covers model management, training, evaluation, monitoring, deployment,
feature store, algorithm store, LLM, labeling, experiments and more.

Aggregates routers from ~22 model-related API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["模型中台"])

# ── Model management ──
from api.models import router as models_router                     # prefix="/models"
from api.model_version import router as model_version_router       # prefix="/model-version"

# ── Training ──
from api.training import router as training_router                # prefix="/training"
from api.model_training import router as model_training_router    # prefix="/model-training"
from api.distributed_training import router as distributed_training_router  # prefix="/distributed-training"
from api.hyperopt import router as hyperopt_router                # prefix="/hyperopt"
from api.automl import router as automl_router                    # prefix="/automl"
from api.automl_presets import router as automl_presets_router    # prefix="/automl/presets"
from api.enhanced_experiments import router as enhanced_experiments_router  # prefix="/experiments"
from api.real_training import router as real_training_router      # prefix="/real-training"
from api.multimodal_training import router as multimodal_training_router  # prefix="/multimodal-training"

# ── Evaluation & explainability ──
from api.model_evaluation import router as model_evaluation_router  # prefix="/model-evaluation"
from api.model_comparison import router as model_comparison_router  # prefix="/model-comparison"
from api.model_explain import router as model_explain_router        # prefix="/model-explain"
from api.llm_ops import router as llm_ops_router                  # prefix="/llm"
from api.model_governance import router as model_governance_router  # prefix="/governance"
from api.model_monitoring import router as model_monitoring_router  # prefix="/monitoring"
from api.drift_detection import router as drift_detection_router    # prefix="/drift"

# ── MLOps pipeline ──
from api.mlflow import router as mlflow_router                    # prefix="/mlflow"
from api.ml_pipeline import router as ml_pipeline_router          # prefix="/pipelines"

# ── Feature & algorithm store ──
from api.feature_store_v2 import router as feature_store_v2_router  # prefix="/feature-store"
from api.algorithm_store import router as algorithm_store_router    # prefix="/algorithm-store"

# ── AI capabilities ──
from api.model_lineage import router as model_lineage_router  # prefix="/model-lineage"
from api.model_security import router as model_security_router  # prefix="/model-security"
from api.rootcause import router as rootcause_router  # prefix="/rootcause"
from api.ocr_api import router as ocr_router  # prefix="/ocr"
from api.rag import router as rag_router  # prefix="/rag"
from api.causal_api import router as causal_router  # prefix="/causal"
from api.gnn_training import router as gnn_training_router  # prefix="/gnn"
from api.recsys_training import router as recsys_training_router  # prefix="/recsys"
from api.rl_training import router as rl_training_router  # prefix="/rl"
from api.gan_training import router as gan_training_router  # prefix="/gan"
from api.audio_training import router as audio_training_router  # prefix="/audio"
from api.nlp_training import router as nlp_training_router  # prefix="/nlp"
from api.cv_training import router as cv_training_router  # prefix="/cv"
from api.llm import router as llm_router                          # prefix="/llm"
from api.labeling import router as labeling_router                # prefix="/labeling"

# ── MLflow tracking API ──
from api.mlflow_api import router as mlflow_api_router            # prefix="/mlflow"

# ── Distributed training API (Ray) ──
from api.distributed_api import router as distributed_api_router  # prefix="/distributed"


# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(models_router)
router.include_router(model_version_router)
router.include_router(training_router)
router.include_router(model_training_router)
router.include_router(distributed_training_router)
router.include_router(hyperopt_router)
router.include_router(automl_router)
router.include_router(automl_presets_router)
router.include_router(enhanced_experiments_router)
router.include_router(real_training_router)
router.include_router(multimodal_training_router)
router.include_router(model_evaluation_router)
router.include_router(model_comparison_router)
router.include_router(model_explain_router)
router.include_router(llm_ops_router)
router.include_router(model_governance_router)
router.include_router(model_monitoring_router)
router.include_router(drift_detection_router)
router.include_router(mlflow_router)
router.include_router(ml_pipeline_router)
router.include_router(feature_store_v2_router)
router.include_router(algorithm_store_router)
router.include_router(llm_router)
router.include_router(labeling_router)
router.include_router(mlflow_api_router)
router.include_router(distributed_api_router)
router.include_router(model_lineage_router)
router.include_router(model_security_router)
router.include_router(rootcause_router)
router.include_router(ocr_router)
router.include_router(rag_router)
router.include_router(causal_router)
router.include_router(gnn_training_router)
router.include_router(recsys_training_router)
router.include_router(rl_training_router)
router.include_router(gan_training_router)
router.include_router(audio_training_router)
router.include_router(nlp_training_router)
router.include_router(cv_training_router)

# ── Optional: enhanced AutoML router (depends on numpy) ──
try:
    from api.automl import enhanced_router as automl_enhanced_router
    if automl_enhanced_router:
        router.include_router(automl_enhanced_router)
except (ImportError, AttributeError):
    pass
