import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录' }
  },
  {
    path: '/',
    component: () => import('@/views/Landing.vue')
  },
  {
    path: '/notebook/:id',
    name: 'NotebookViewer',
    component: () => import('@/views/training/NotebookViewer.vue'),
    meta: { title: 'Notebook查看器' }
  },
  {
    path: '/main',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/data/dashboard',
    children: [
      // ==================== 科研工厂 ====================
      {
        path: '/research/factory',
        name: 'ResearchFactory',
        component: () => import('@/views/research/ResearchFactory.vue'),
        meta: { title: '科研工厂' }
      },
      // ==================== 数据中台 ====================
      {
        path: '/data/knowledge-graph',
        name: 'KnowledgeGraph',
        component: () => import('@/views/data/KnowledgeGraph.vue'),
        meta: { title: '知识图谱' }
      },
      {
        path: '/data/industrial-protocol',
        name: 'IndustrialProtocol',
        component: () => import('@/views/data/IndustrialProtocol.vue'),
        meta: { title: '工业协议' }
      },

      {
        path: '/data/dashboard',
        name: 'DataDashboard',
        component: () => import('@/views/legacy/data/DataDashboard.vue'),
        meta: { title: '数据大盘' }
      },
      {
        path: '/data/public-datasets',
        name: 'PublicDatasets',
        component: () => import('@/views/data/PublicDatasets.vue'),
        meta: { title: '公开数据集' }
      },
      {
        path: '/data/data-source',
        name: 'DataSource',
        component: () => import('@/views/data/DataSource.vue'),
        meta: { title: '数据接入' }
      },
      {
        path: '/data/new-datasets',
        name: 'Datasets',
        component: () => import('@/views/data/Datasets.vue'),
        meta: { title: '数据集管理' }
      },
      {
        path: '/data/cleaning',
        name: 'DataCleaning',
        component: () => import('@/views/data/DataCleaning.vue'),
        meta: { title: '数据清洗' }
      },
      {
        path: '/data/quality',
        name: 'DataQuality',
        component: () => import('@/views/data/DataQuality.vue'),
        meta: { title: '数据质量' }
      },
      {
        path: '/data/workspace',
        name: 'DataWorkspace',
        component: () => import('@/views/data/DataWorkspace.vue'),
        meta: { title: '数据工作台' }
      },
      {
        path: '/data/intelligent-analysis',
        name: 'IntelligentDataAnalysis',
        component: () => import('@/views/data/IntelligentDataAnalysis.vue'),
        meta: { title: '智能数据分析' }
      },
      {
        path: '/data/analysis',
        name: 'DataAnalysis',
        component: () => import('@/views/data/DataAnalysis.vue'),
        meta: { title: '数据分析' }
      },
      {
        path: '/data/anomaly-attribution',
        name: 'AnomalyAttribution',
        component: () => import('@/views/data/AnomalyAttribution.vue'),
        meta: { title: '过程影响分析' }
      },
      {
        path: '/data/realtime-sensor-monitor',
        name: 'RealtimeSensorMonitor',
        component: () => import('@/views/data/RealtimeSensorMonitor.vue'),
        meta: { title: '实时仪表监测' }
      },
      {
        path: '/data/feature-engineering',
        name: 'FeatureEngineering',
        component: () => import('@/views/legacy/data/FeatureEngineering.vue'),
        meta: { title: '特征工程' }
      },
      {
        path: '/data/catalog',
        name: 'DataHubCatalog', component: () => import('@/views/data/DataHubCatalog.vue'),
        meta: { title: '数据资产目录' }
      },
      {
        path: '/academy',
        name: 'Academy',
        component: () => import('@/views/Academy.vue'),
        meta: { title: 'AI学院' }
      },
      {
        path: '/data/ai-agent',
        name: 'AIAgent',
        component: () => import('@/views/data/AIAgent.vue'),
        meta: { title: 'AI智能体' }
      },

      {
        path: '/data/sandbox',
        redirect: to => ({ path: '/model/code-editor', query: to.query })
      },
      {
        path: '/model/code-editor',
        name: 'CodeEditor',
        component: () => import('@/views/data/SandboxLab.vue'),
        meta: { title: '代码编辑器' }
      },
      // ==================== 模型工厂 ====================
      {
        path: '/model/automl',
        name: 'AutoML',
        component: () => import('@/views/training/AutoML.vue'),
        meta: { title: 'AutoML对比' }
      },
      {
        path: '/model/algorithm-store',
        name: 'AlgorithmStore',
        component: () => import('@/views/training/AlgorithmStore.vue'),
        meta: { title: '算法商店' }
      },
      { path: '/model/new-models', redirect: '/model/versions' },
      {
        path: '/model/training-tasks',
        name: 'TrainingTasks',
        component: () => import('@/views/training/ModelTraining.vue'),
        meta: { title: '训练任务' }
      },
      {
        path: '/model/training-guide',
        name: 'TrainingGuide',
        component: () => import('@/views/training/TrainingGuide.vue'),
        meta: { title: '训练流程说明' }
      },
      {
        path: '/model/control-simulations',
        name: 'ControlSimulations',
        component: () => import('@/views/training/ControlSimulationTasks.vue'),
        meta: { title: '控制仿真任务' }
      },
      {
        path: '/model/distributed-training',
        name: 'DistributedTraining',
        component: () => import('@/views/training/DistributedTraining.vue'),
        meta: { title: '并行参数搜索' }
      },
      {
        path: '/model/notebook',
        name: 'Notebook',
        component: () => import('@/views/training/Notebook.vue'),
        meta: { title: 'Notebook' }
      },
      {
        path: '/model/hyperopt',
        name: 'HyperOpt',
        component: () => import('@/views/training/HyperOpt.vue'),
        meta: { title: '超参搜索' }
      },
      {
        path: '/model/pipeline',
        name: 'Pipeline',
        component: () => import('@/views/training/Pipeline.vue'),
        meta: { title: 'ML Pipeline' }
      },
      {
        path: '/model/mlflow',
        name: 'MLflow',
        component: () => import('@/views/training/MLflow.vue'),
        meta: { title: 'MLflow追踪' }
      },
      {
        path: '/model/optuna',
        name: 'OptunaDashboard',
        component: () => import('@/views/training/OptunaDashboard.vue'),
        meta: { title: 'Optuna优化' }
      },
      {
        path: '/model/model-evaluation',
        name: 'ModelEvaluation',
        component: () => import('@/views/training/ModelEvaluation.vue'),
        meta: { title: '模型评估' }
      },
      {
        path: '/model/model-explain',
        name: 'ModelExplain',
        component: () => import('@/views/training/ModelExplain.vue'),
        meta: { title: '模型解释' }
      },
      {
        path: '/model/model-monitoring',
        name: 'ModelMonitoring',
        component: () => import('@/views/training/ModelMonitoring.vue'),
        meta: { title: '模型监控' }
      },
      {
        path: '/model/model-governance',
        name: 'ModelGovernance',
        component: () => import('@/views/training/ModelGovernance.vue'),
        meta: { title: '模型治理' }
      },
      {
        path: '/model/abtest',
        name: 'ABTest',
        component: () => import('@/views/training/ABTest.vue'),
        meta: { title: '过渡测试' }
      },
      {
        path: '/model/model-comparison',
        name: 'ModelComparison',
        component: () => import('@/views/training/ModelComparison.vue'),
        meta: { title: '模型对比测试' }
      },
      {
        path: '/model/llmops',
        name: 'LLMOps', component: () => import('@/views/training/LLMOps.vue'),
        meta: { title: 'LLMOps' }
      },
      {
        path: '/deploy/artifacts',
        alias: '/model/onnx',
        name: 'ModelArtifacts',
        redirect: '/deploy/deployment',
        meta: { title: '部署管理' }
      },
      {
        path: '/model/inference-dashboard',
        name: 'InferenceDashboard',
        component: () => import('@/views/training/InferenceDashboard.vue'),
        meta: { title: '推理服务' }
      },
      {
        path: '/deploy/batch-inference',
        alias: '/model/batch-inference',
        name: 'BatchInference',
        component: () => import('@/views/training/BatchInference.vue'),
        meta: { title: '批量推理' }
      },
      {
        path: '/model/feature-store',
        name: 'FeatureStore',
        component: () => import('@/views/training/FeatureStore.vue'),
        meta: { title: '特征存储' }
      },


      {
        path: "/model/scheduler",
        name: "Scheduler",
        component: () => import("@/views/training/Scheduler.vue"),
        meta: { title: "定时调度" }
      },
      {
        path: "/model/notifications",
        name: "NotificationCenter",
        component: () => import("@/views/training/NotificationCenter.vue"),
        meta: { title: "告警通知" }
      },
      {
        path: "/model/reports",
        name: "ReportCenter",
        component: () => import("@/views/training/ReportCenter.vue"),
        meta: { title: "报表中心" }
      },
      {
        path: "/model/billing",
        name: "Billing",
        component: () => import("@/views/training/Billing.vue"),
        meta: { title: "成本计费" }
      },
      {
        path: "/data/labeling",
        name: "LabelingCenter",
        component: () => import("@/views/data/LabelingCenter.vue"),
        meta: { title: "数据标注" }
      },

            {
        path: '/model/versions',
        name: 'ModelVersion',
        component: () => import('@/views/training/ModelVersion.vue'),
        meta: { title: '模型版本' }
      },
      {
        path: '/model/drift',
        name: 'DriftDetection',
        component: () => import('@/views/training/DriftDetection.vue'),
        meta: { title: '漂移检测' }
      },
      {
        path: '/model/code-generator',
        name: 'CodeGenerator',
        component: () => import('@/views/training/CodeGenerator.vue'),
        meta: { title: '代码生成器' }
      },
      {
        path: '/data/masking',
        name: 'DataMasking',
        component: () => import('@/views/data/DataMasking.vue'),
        meta: { title: '数据脱敏' }
      },
      {
        path: "/data/data-governance",
        name: "DataGovernance",
        component: () => import("@/views/data/DataGovernance.vue"),
        meta: { title: "数据治理" }
      },
      {
        path: "/data/lineage",
        name: "DataLineage",
        component: () => import("@/views/data/DataLineage.vue"),
        meta: { title: "数据血缘" }
      },
      {
        path: "/data/data-catalog",
        name: "DataCatalog",
        component: () => import("@/views/data/DataCatalog.vue"),
        meta: { title: "数据目录" }
      },

            {
        path: '/model/experiments',
        name: 'ExperimentCompare',
        component: () => import('@/views/training/ExperimentCompare.vue'),
        meta: { title: '实验对比' }
      },
      {
        path: '/model/evaluation-center',
        name: 'EvaluationCenter',
        component: () => import('@/views/training/EvaluationCenter.vue'),
        meta: { title: '评估中心' }
      },

      {
        path: "/model/cv-training",
        name: "CVTraining",
        component: () => import("@/views/training/CVTraining.vue"),
        meta: { title: "CV训练" }
      },
      {
        path: "/model/multimodal-training",
        name: "MultimodalTraining",
        component: () => import("@/views/training/MultimodalTraining.vue"),
        meta: { title: "工业多模态融合训练" }
      },
      {
        path: "/model/nlp-training",
        name: "NLPTraining",
        component: () => import("@/views/training/NLPTraining.vue"),
        meta: { title: "NLP训练" }
      },
      {
        path: "/model/audio-training",
        name: "AudioTraining",
        component: () => import("@/views/training/AudioTraining.vue"),
        meta: { title: "音频训练" }
      },
      {
        path: "/model/gan-training",
        name: "GANTraining",
        component: () => import("@/views/training/GANTraining.vue"),
        meta: { title: "GAN训练" }
      },
      {
        path: "/model/rl-training",
        name: "RLTraining",
        component: () => import("@/views/training/RLTraining.vue"),
        meta: { title: "强化学习训练" }
      },
      {
        path: "/model/gnn-training",
        name: "GNNTraining",
        component: () => import("@/views/training/GNNTraining.vue"),
        meta: { title: "图神经网络训练" }
      },
      {
        path: "/model/recsys-training",
        name: "RecSysTraining",
        component: () => import("@/views/training/RecSysTraining.vue"),
        meta: { title: "推荐系统训练" }
      },
      {
        path: "/model/causal-analysis",
        name: "CausalAnalysis",
        component: () => import("@/views/training/CausalAnalysis.vue"),
        meta: { title: "因果分析" }
      },
      {
        path: "/model/model-serving",
        name: "ModelServing",
        component: () => import("@/views/training/ModelServing.vue"),
        meta: { title: "模型服务" }
      },
      {
        path: "/model/ray-dashboard",
        name: "RayDashboard",
        component: () => import("@/views/training/RayDashboard.vue"),
        meta: { title: "Ray仪表盘" }
      },

      // ==================== 部署 ====================
      {
        path: '/deploy/deployment',
        name: 'Deployment',
        component: () => import('@/views/deploy/Deployment.vue'),
        meta: { title: '部署管理' }
      },
      {
        path: "/deploy/edge-devices",
        name: "EdgeDevices",
        component: () => import("@/views/deploy/EdgeDevices.vue"),
        meta: { title: "边缘设备" }
      },

      {
        path: "/system/api-keys",
        name: "ApiKey",
        component: () => import("@/views/system/ApiKey.vue"),
        meta: { title: "API密钥" }
      },

            {
        path: '/system/tenant',
        name: 'Tenant',
        component: () => import('@/views/system/Tenant.vue'),
        meta: { title: '多租户' }
      },
      {
        path: '/system/kms',
        name: 'KMS',
        component: () => import('@/views/system/KMS.vue'),
        meta: { title: '密钥管理' }
      },
      {
        path: '/system/config-center',
        name: 'ConfigCenter',
        component: () => import('@/views/system/ConfigCenter.vue'),
        meta: { title: '配置中心' }
      },
      {
        path: '/system/compliance',
        name: 'Compliance',
        component: () => import('@/views/system/Compliance.vue'),
        meta: { title: '合规管理' }
      },
      {
        path: '/system/gpu-scheduler',
        name: 'GPUScheduler',
        component: () => import('@/views/system/GPUScheduler.vue'),
        meta: { title: 'GPU调度' }
      },
      {
        path: '/system/cicd',
        name: 'CICD',
        component: () => import('@/views/system/CICD.vue'),
        meta: { title: 'CI/CD' }
      },

            {
        path: '/system/remote-ops',
        name: 'RemoteOps',
        component: () => import('@/views/system/RemoteOps.vue'),
        meta: { title: '远程运维' }
      },
      {
        path: '/system/work-order',
        name: 'WorkOrder',
        component: () => import('@/views/system/WorkOrder.vue'),
        meta: { title: '工单管理' }
      },

      // ==================== 系统 ====================
      {
        path: '/system/monitoring',
        name: 'Monitoring',
        component: () => import('@/views/system/Monitoring.vue'),
        meta: { title: '监控告警' }
      },
      {
        path: '/system/rbac',
        name: 'RBAC',
        component: () => import('@/views/system/RBAC.vue'),
        meta: { title: '权限管理' }
      },
      {
        path: '/system/audit-logs',
        name: 'AuditLogs',
        component: () => import('@/views/system/AuditLogs.vue'),
        meta: { title: '审计日志' }
      },
      {
        path: '/system/users',
        name: 'UserManagement',
        component: () => import('@/views/system/UserManagement.vue'),
        meta: { title: '用户管理', adminOnly: true }
      },
    ]
  },
  // 兼容旧路径 → 重定向到新路径
  { path: '/model/models', redirect: '/model/versions' },
  { path: '/data/datasets', redirect: '/data/new-datasets' },
  // 404 catch-all
  { path: "/:pathMatch(.*)*", name: "NotFound", component: () => import("@/views/NotFound.vue"), meta: { title: "404" } },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// ============ 认证守卫 — 未登录跳 /login ============
router.beforeEach(async (to, _from, next) => {
  if (to.meta?.title) {
    document.title = `${to.meta.title} - 开放数字化底座`
  }
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  if (to.path !== '/login' && !token) {
    next({ path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined })
    return
  }
  if (to.path === '/login' && token) {
    // 已登录访问登录页 → 直接回首页
    next('/')
    return
  }

  // 功能权限门控(仅前端强校验)。首页/登录等公开页放行。
  const publicPaths = ['/', '/login']
  if (publicPaths.includes(to.path)) {
    next()
    return
  }
  const userStore = useUserStore()
  await userStore.fetchUserInfo()
  // adminOnly 路由(如用户管理)仅管理员可进
  if (to.meta?.adminOnly && !userStore.isAdmin) {
    next('/')
    return
  }
  // RBAC 读取权限和 features 白名单统一门控；无权限时直接回首页，不渲染目标页面。
  if (!userStore.canAccessPath(to.path)) {
    next('/')
    return
  }
  next()
})

export default router
