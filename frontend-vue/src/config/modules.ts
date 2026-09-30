/**
 * 三大板块导航配置
 *
 * 数据中台: 数据采集 → 治理 → 特征工程 → 案例
 * 模型工厂: 模型训练 → 模型管理 → 推理服务
 * 应用中心: AI + 能效 · 钢铁行业案例
 */
export interface MenuItem { path: string; title: string; icon: string; unavailable?: boolean; hidden?: boolean; adminOnly?: boolean }
export interface MenuGroup { title: string; items: MenuItem[] }
export type ModuleKey = 'data' | 'academy' | 'model' | 'app' | 'system'
export interface Module {
  key: ModuleKey; title: string; icon: string; theme: string; entry: string; desc: string
  items?: MenuItem[]; groups?: MenuGroup[]
}

export const MODULES: Module[] = [
  {
    key: 'data', title: '数据中台', icon: 'Coin', theme: 'indigo', entry: '/data/dashboard',
    desc: '数据采集、治理、特征工程',
    groups: [
      { title: '数据资产', items: [
        { path: '/data/dashboard', title: '数据大盘', icon: 'Odometer' },
        { path: '/data/public-datasets', title: '公开数据集', icon: 'Collection' },
        { path: '/data/data-source', title: '数据接入', icon: 'Connection' },
        { path: '/data/new-datasets', title: '数据集管理', icon: 'Folder' },
      ]},
      { title: '数据分析', items: [
        { path: '/data/analysis', title: '算法数据分析', icon: 'Histogram' },
        { path: '/data/anomaly-attribution', title: '过程影响分析', icon: 'TrendCharts' },
        { path: '/data/realtime-sensor-monitor', title: '实时仪表监测', icon: 'Monitor' },
      ]},
      { title: '数据治理', items: [
        { path: '/data/cleaning', title: '数据清洗', icon: 'Brush' },
        { path: '/data/quality', title: '数据质量', icon: 'Check', hidden: true },
        { path: '/data/catalog', title: '数据目录', icon: 'Collection', hidden: true },
        { path: '/data/lineage', title: '数据血缘', icon: 'Share' },
        { path: '/data/feature-engineering', title: '特征工程', icon: 'MagicStick' },
        { path: '/data/labeling', title: '数据标注', icon: 'Edit' },
        { path: '/data/masking', title: '数据脱敏', icon: 'Lock' },
        { path: '/data/workspace', title: '数据工作台', icon: 'DataAnalysis' },
        { path: '/data/virtual', title: '虚拟数据', icon: 'MagicStick', hidden: true },
        { path: '/data/knowledge-graph', title: '知识图谱', icon: 'Share', hidden: true },
      ]},
    ]
  },
  {
    key: 'academy', title: 'AI学院', icon: 'School', theme: 'purple', entry: '/academy',
    desc: '10周系统课程 · 工业数据实战',
    groups: [
      { title: '课程', items: [
        { path: '/academy', title: '全部课程', icon: 'Reading' },
      ]},
    ]
  },
  {
    key: 'model', title: '模型工厂', icon: 'Cpu', theme: 'green', entry: '/model/versions',
    desc: 'MLOps 全生命周期：实验 → 训练 → 资产 → 评估 → 部署 → 治理',
    groups: [
      { title: '🔬 实验开发', items: [
        { path: '/model/code-editor', title: '代码编辑器', icon: 'EditPen' },
        { path: '/model/notebook', title: 'Notebook', icon: 'Notebook' },
        { path: '/model/automl', title: 'AutoML对比', icon: 'DataAnalysis' },
        { path: '/model/pipeline', title: 'ML Pipeline', icon: 'Share' },
        { path: '/model/code-generator', title: '代码生成器', icon: 'EditPen' },
        { path: '/model/mlflow', title: 'MLflow追踪', icon: 'Monitor' },
      ]},
      { title: '🏋️ 训练引擎', items: [
        { path: '/model/training-guide', title: '训练流程说明', icon: 'Reading' },
        { path: '/model/training-tasks', title: '训练任务', icon: 'List' },
        { path: '/model/control-simulations', title: '控制仿真任务', icon: 'DataAnalysis' },
        { path: '/model/distributed-training', title: '并行参数搜索', icon: 'Connection' },
        { path: '/model/scheduler', title: '定时调度', icon: 'Clock' },
        { path: '/model/cv-training', title: 'CV训练', icon: 'Picture', unavailable: true },
        { path: '/model/multimodal-training', title: '工业多模态训练', icon: 'Connection' },
        { path: '/model/nlp-training', title: 'NLP训练', icon: 'EditPen', unavailable: true },
        { path: '/model/audio-training', title: '音频AI', icon: 'Headset', unavailable: true },
        { path: '/model/gan-training', title: '生成式AI', icon: 'PictureFilled', unavailable: true },
        { path: '/model/rl-training', title: '强化学习', icon: 'Aim', unavailable: true },
        { path: '/model/recsys-training', title: '推荐系统', icon: 'Star', unavailable: true },
        { path: '/model/causal-analysis', title: '因果推断', icon: 'Connection', unavailable: true },
        { path: '/model/gnn-training', title: '图神经网络', icon: 'Share', unavailable: true },
      ]},
      { title: '📦 模型资产', items: [
        { path: '/model/versions', title: '模型管理', icon: 'Tickets' },
        { path: '/model/algorithm-store', title: '算法商店', icon: 'Shop' },
        { path: '/model/feature-store', title: '特征存储', icon: 'FolderOpened', hidden: true },
        { path: '/model/llmops', title: 'LLMOps', icon: 'ChatDotRound' },
      ]},
      { title: '📊 评估测试', items: [
        { path: '/model/model-evaluation', title: '模型评估', icon: 'DataAnalysis' },
        { path: '/model/model-comparison', title: '模型对比测试', icon: 'Histogram' },
        { path: '/model/model-explain', title: '模型解释', icon: 'MagicStick' },
        { path: '/model/abtest', title: '过渡测试', icon: 'Switch', hidden: true },
      ]},
      { title: '🚀 推理部署', items: [
        { path: '/deploy/deployment', title: '部署管理', icon: 'Position' },
        { path: '/deploy/edge-devices', title: '边缘设备', icon: 'Connection' },
        { path: '/model/inference-dashboard', title: '在线推理', icon: 'Connection' },
        { path: '/deploy/batch-inference', title: '批量推理', icon: 'Files' },
      ]},
      { title: '📈 运维治理', items: [
        { path: '/model/model-monitoring', title: '模型监控', icon: 'View' },
        { path: '/model/drift', title: '漂移检测', icon: 'TrendCharts' },
        { path: '/model/model-governance', title: '模型治理', icon: 'Checked' },
        { path: '/model/reports', title: '报表中心', icon: 'Document' },
        { path: '/model/billing', title: '成本计费', icon: 'Money' },
        { path: '/model/notifications', title: '告警通知', icon: 'Bell' },
      ]},
    ]
  },
  {
    desc: 'AI + 能源服务 · 行业案例',
    items: [
      { path: '/data/ai-agent', title: 'AI智能体', icon: 'ChatDotRound' },
    ],
    groups: [
      { title: '能效优化', items: [
        { path: '/data/load-forecast', title: '负荷预测', icon: 'TrendCharts' },
        { path: '/data/intelligent-control', title: '智能控制', icon: 'SetUp' },
        { path: '/data/carbon', title: '碳排放', icon: 'Sunny' },
      ]},
      { title: '能源管控', items: [
        { path: '/data/fault-prediction', title: '故障预测', icon: 'Warning' },
        { path: '/data/digital-twin', title: '数字孪生', icon: 'Connection' },
        { path: '/data/photovoltaic', title: '光伏管理', icon: 'Sunny' },
        { path: '/data/industrial-protocol', title: '工业协议', icon: 'Plug' },
      ]},
    ]
  }
]

export const SYSTEM_ITEMS: MenuItem[] = [
  { path: '/system/monitoring', title: '监控告警', icon: 'View' },
  { path: '/system/users', title: '用户管理', icon: 'User', adminOnly: true },
  { path: '/system/rbac', title: '权限管理', icon: 'Lock' },
  { path: '/system/audit-logs', title: '审计日志', icon: 'Document' },
  { path: '/system/api-keys', title: 'API密钥', icon: 'Key' },
  { path: '/system/tenant', title: '多租户', icon: 'OfficeBuilding' },
  { path: '/system/kms', title: '密钥管理', icon: 'Lock' },
  { path: '/system/config-center', title: '配置中心', icon: 'Setting' },
  { path: '/system/compliance', title: '合规管理', icon: 'Checked' },
  { path: '/system/gpu-scheduler', title: 'GPU调度', icon: 'Cpu' },
  { path: '/system/cicd', title: 'CI/CD', icon: 'Connection' },
  { path: '/system/remote-ops', title: '远程运维', icon: 'Monitor' },
  { path: '/system/work-order', title: '工单管理', icon: 'Document' },
]
export const SYSTEM_ENTRY = '/system/monitoring'

export function flattenItems(module: Module): MenuItem[] {
  if (module.groups) return module.groups.flatMap(g => g.items)
  return module.items || []
}
export function resolveModuleKey(path: string): ModuleKey | null {
  for (const m of MODULES) {
    if (flattenItems(m).some((it) => path === it.path || path.startsWith(it.path + '/'))) return m.key
  }
  if (SYSTEM_ITEMS.some((it) => path === it.path || path.startsWith(it.path + '/'))) return 'system'
  return null
}
export function getModule(key: ModuleKey): Module | undefined { return MODULES.find((m) => m.key === key) }

const TRAINING_PATHS = [
  '/model/code-editor', '/model/notebook', '/model/automl', '/model/pipeline',
  '/model/code-generator', '/model/mlflow', '/model/training-tasks',
  '/model/distributed-training', '/model/scheduler', '/model/cv-training',
  '/model/multimodal-training', '/model/nlp-training', '/model/audio-training',
  '/model/gan-training', '/model/rl-training', '/model/recsys-training',
  '/model/causal-analysis', '/model/gnn-training'
]

const EXPERIMENT_PATHS = [
  '/model/model-evaluation', '/model/model-comparison', '/model/model-explain',
  '/model/abtest'
]

const DEPLOYMENT_PATHS = ['/deploy', '/model/inference-dashboard']

/** 返回访问页面所需的读取权限；null 表示该页面不属于 RBAC 资源域。 */
export function requiredPermissionForPath(path: string): string | null {
  const matches = (prefix: string) => path === prefix || path.startsWith(prefix + '/')
  if (path.startsWith('/system')) return null
  if (path.startsWith('/notebook')) return 'training:read'
  if (DEPLOYMENT_PATHS.some(matches)) return 'deployment:read'
  if (EXPERIMENT_PATHS.some(matches)) return 'experiments:read'
  if (TRAINING_PATHS.some(matches)) return 'training:read'
  if (path.startsWith('/model')) return 'models:read'
  if (path.startsWith('/data')) return 'datasets:read'
  return null
}
