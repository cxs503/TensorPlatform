import { reactive } from 'vue'

export interface GuideEntry {
  title: string
  desc: string
  steps: string[]
  tips: string[]
  api?: string[]
}

export const moduleGuides: Record<string, GuideEntry> = {
  '/data/dashboard': {
    title: '数据大盘',
    desc: '平台级数据资产总览, 展示数据集/模型/训练任务的实时统计',
    steps: ['进入数据中台→数据大盘', '查看顶部统计卡片(数据集/模型/训练)', '浏览近期模型列表和训练趋势'],
    tips: ['数据每30秒自动刷新', '点击统计卡片可跳转详情'],
    api: ['GET /dashboard/statistics', 'GET /dashboard/recent-activities'],
  },
  '/data/data-source': {
    title: '数据接入',
    desc: '配置外部数据源连接, 支持MySQL/PostgreSQL/InfluxDB/OPCUA等协议',
    steps: ['点击"新建数据源"', '选择数据源类型(MySQL/InfluxDB/OPCUA等)', '填写连接信息(主机/端口/数据库/用户名/密码)', '点击"测试连接"验证', '保存数据源'],
    tips: ['确保数据源服务已启动', 'OPCUA需要endpoint格式: opc.tcp://host:port', '支持HTTP文件上传作为备选方案'],
    api: ['POST /datasources/{type}', 'GET /datasources', 'POST /datasources/{type}/{id}/test'],
  },
  '/data/quality': {
    title: '数据质量',
    desc: '自动检测数据集质量问题: 完整性/一致性/准确性/唯一性',
    steps: ['选择要检查的数据集', '创建质量规则(非空/范围/唯一性等)', '点击"执行检查"', '查看检测结果和异常列表', '处理异常(修复/忽略)'],
    tips: ['质量评分100为满分', '规则可复用多个数据集', '历史检查结果自动保存'],
    api: ['POST /quality/rules', 'POST /quality/check', 'GET /quality/results'],
  },
  '/data/new-datasets': {
    title: '数据集管理',
    desc: '管理数据资产生命周期: 原始→清洗→治理→特征→标注',
    steps: ['浏览各阶段数据集卡片', '点击"上传"导入CSV/Excel数据', '选中原始数据→"一键治理"自动清洗', '治理后数据自动提升到governed阶段', '可继续特征工程→标注→用于训练'],
    tips: ['数据质量>80分可直接训练', '治理会保留原始副本', '支持CSV/Excel/JSON格式上传'],
    api: ['GET /datasets', 'POST /datasets', 'POST /datasets/{id}/upload', 'POST /datasets/{id}/govern'],
  },
  '/data/feature-engineering': {
    title: '特征工程',
    desc: '从原始数据构建AI特征: 滑动窗口/移动平均/傅里叶变换/交互特征',
    steps: ['选择源数据集', '添加转换步骤(可选模板)', '配置转换参数(窗口大小/周期等)', '预览转换结果', '执行特征工程生成新数据集'],
    tips: ['工业传感器数据推荐: 滑动窗口+移动平均', '时间数据推荐: 周期编码(hour_sin/cos)', '模板可一键套用: FeO/光伏/轴承'],
    api: ['GET /feature-engineering/types', 'POST /feature-engineering/preview', 'POST /feature-engineering/run'],
  },
  '/data/public-datasets': {
    title: '公开数据集',
    desc: '来自UCI ML Repository的国际公开数据集, 用于算法验证和学术研究',
    steps: ['浏览15个公开数据集卡片', '点击📥下载获取CSV原始文件', '点击🤖训练跳转模型工厂', '已训练数据集显示绿色边框和指标'],
    tips: ['全部数据来源真实可追溯', '已训练模型可对比基准性能', '网络受限, 部分数据需手动下载'],
  },
  '/model/new-models': {
    title: '模型管理',
    desc: '管理所有AI模型: 注册/查看/搜索/状态管理',
    steps: ['查看模型列表(名称/类型/状态)', '点击"新建模型"注册新模型', '设置模型类型(回归/分类)和描述', '模型状态: DRAFT→TRAINING→READY→DEPLOYED'],
    tips: ['READY状态的模型可用于部署', '每个模型可关联多个版本', 'DRAFT模型可编辑或删除'],
    api: ['GET /models', 'POST /models', 'PUT /models/{id}'],
  },
  '/model/model-training': {
    title: '模型训练',
    desc: '配置并启动AI模型训练: 选择数据集→选择算法→配置参数→训练',
    steps: ['选择训练数据集(来自数据中台)', '选择目标列(要预测的变量)', '勾选特征列', '选择算法: random_forest/mlp/lightgbm/xgboost', '设置task_type: regression(回归)/classification(分类)', '点击"开始训练"'],
    tips: ['回归任务输出R²/RMSE/MAE', '分类任务输出Accuracy/F1/Precision/Recall', '特征数建议5-20个', '大数据集(>10K行)训练需30-60秒'],
    api: ['POST /training', 'GET /training', 'GET /training/{id}'],
  },
  '/model/training-tasks': {
    title: '训练任务',
    desc: '查看所有训练任务的状态和结果',
    steps: ['浏览训练任务列表', '查看状态: PENDING→RUNNING→COMPLETED/FAILED', '点击任务查看详情(参数/指标/耗时)', 'COMPLETED任务可直接用于推理'],
    tips: ['Celery异步执行, 大任务等待10-60秒', '训练指标自动保存到MLflow', '失败任务查看error_message排查'],
    api: ['GET /training', 'GET /training/{id}'],
  },
  '/model/hyperopt': {
    title: '超参优化',
    desc: '自动搜索最优模型超参数: 贝叶斯优化/Hyperband/随机搜索/网格搜索',
    steps: ['创建新的优化Study', '选择算法: 贝叶斯优化(推荐) / Hyperband / 随机 / 网格', '选择数据集和目标列', '设置参数空间(n_estimators/max_depth等)', '设置试验次数(建议10-50)', '启动优化→等待完成→查看最优参数'],
    tips: ['贝叶斯优化效率最高, 适合低维连续参数', 'Hyperband适合大量参数组合', '优化结果自动保存可复用'],
    api: ['POST /hyperopt/studies', 'GET /hyperopt/algorithms'],
  },
  '/model/automl': {
    title: 'AutoML',
    desc: '零代码全自动机器学习: 自动选择算法+调参+评估',
    steps: ['创建AutoML任务', '选择数据集和目标列', '选择任务类型(回归/分类)', '设置时间限制(默认60秒)', '可选算法: lightgbm/xgboost/random_forest/linear/ridge', '启动→系统自动选择最佳模型'],
    tips: ['适合快速原型验证', '结果可与其他手动训练对比', '时间越长结果越好'],
    api: ['POST /automl/tasks', 'GET /automl/tasks'],
  },
  '/model/mlflow': {
    title: 'MLflow 实验追踪',
    desc: '记录和对比所有训练实验: 参数/指标/模型/代码版本',
    steps: ['查看实验列表(按项目分组)', '点击实验查看所有训练runs', '对比不同run的指标', '查看每个run的详细参数/指标/耗时'],
    tips: ['每次训练自动生成一个run', '支持按R²/Accuracy排序', '可导出实验数据到CSV'],
    api: ['GET /mlflow/experiments', 'GET /mlflow/runs'],
  },
  '/model/model-evaluation': {
    title: '模型评估',
    desc: '评估模型性能: ROC曲线/混淆矩阵/R²/RMSE/交叉验证',
    steps: ['选择模型和数据集', '选择任务类型(回归/分类)', '点击"开始评估"', '查看评估指标', '可选: 交叉验证评估稳定性'],
    tips: ['回归: R²>0.7为良好, R²>0.9为优秀', '分类: Accuracy>0.95为良好', '交叉验证cv_folds设5或10'],
    api: ['POST /model-evaluation/evaluate', 'GET /model-evaluation/results'],
  },
  '/model/model-explain': {
    title: '模型解释',
    desc: '理解模型决策: 特征重要性/SHAP/部分依赖/排列重要性',
    steps: ['选择已训练的模型', '选择评估数据集', '选择解释方法: 特征重要性/SHAP/LIME/部分依赖', '查看各特征对预测的贡献度'],
    tips: ['特征重要性显示全局影响', 'SHAP显示单个样本的决策', '部分依赖展示特征如何影响预测'],
    api: ['GET /model-explain/methods', 'POST /model-explain/feature-importance'],
  },
  '/model/drift-detection': {
    title: '漂移检测',
    desc: '监控数据分布变化, 触发模型重训练',
    steps: ['注册模型到漂移检测', '设置基线特征数据', '配置漂移阈值(KS/PSI等)', '每次推理自动检测漂移', '漂移超限触发告警和重训练'],
    tips: ['KS检测适合连续特征', 'PSI适合离散特征', '阈值建议0.1-0.2'],
    api: ['POST /drift/register', 'POST /drift/set-baseline'],
  },
  '/model/algorithm-store': {
    title: '算法商店',
    desc: '预置工业AI算法模板: 除尘优化/脱硫/储能/故障预测等15个模型',
    steps: ['浏览15个预置模型', '查看模型描述和适用场景', '点击模型查看详细说明', '可直接作为训练模板使用'],
    tips: ['覆盖能源/钢铁/故障预测场景', '每个模型含推荐算法和参数', '可用作AutoML的初始搜索点'],
    api: ['GET /algorithm-store/models'],
  },
  '/model/feature-store': {
    title: '特征仓库',
    desc: '管理可复用的AI特征: 特征组/在线服务/离线计算',
    steps: ['创建特征组(将相关特征归类)', '添加特征到特征组', '配置在线/离线特征服务', '训练时可引用特征组'],
    tips: ['特征组可实现跨项目复用', '在线特征用于实时推理', '离线特征用于批量训练'],
    api: ['GET /feature-store/feature-groups', 'POST /feature-store/feature-groups'],
  },
  '/model/labeling': {
    title: '数据标注',
    desc: '半自动数据标注工具: 标签管理/标注任务/审核流程',
    steps: ['创建标签类型(名称/颜色/分类)', '创建标注任务(关联数据集)', '执行标注(手动/半自动)', '审核标注结果', '导出标注数据用于训练'],
    tips: ['标签关联数据集后可训练分类模型', '支持多种标注类型(分类/区域/时序)', '审核流程确保标注质量'],
    api: ['POST /labeling/labels', 'POST /labeling/tasks'],
  },
  '/model/notebook': {
    title: 'Notebook',
    desc: '在线Jupyter Notebook: 创建/编辑/执行/分享',
    steps: ['点击"新建Notebook"', '输入名称→创建', '点击"打开"查看内容', '添加单元格(代码/Markdown)', '执行单元格→查看输出'],
    tips: ['当前支持API交互, JupyterHub交互待配置', 'Notebook可导出分享', '创建的Notebook持久化保存'],
    api: ['GET /notebooks', 'POST /notebooks', 'GET /notebooks/{id}'],
  },
  '/model/deployments': {
    title: '部署管理',
    desc: '模型上线部署: 创建部署→加载模型→在线推理→监控',
    steps: ['选择要部署的模型', '创建部署(填写版本号)', '调用/serve加载模型pickle文件', '使用/predict端点进行推理', '查看/metrics监控推理指标'],
    tips: ['模型需处于READY状态', '部署后将生成推理服务', '推理延迟通常<50ms', '可在部署管理中查看运行状态'],
    api: ['POST /deployments', 'POST /deployments/{id}/serve', 'POST /deployments/{id}/predict', 'GET /deployments/{id}/metrics'],
  },
  '/model/model-governance': {
    title: '模型治理',
    desc: '模型全生命周期合规管理: 审批/风险评估/审计/废弃',
    steps: ['模型上线前提交审批', '自动风险评估(数据隐私/公平性)', '审批通过后进入READY状态', '查看审计日志(所有操作记录)', '废弃过期模型'],
    tips: ['高风险模型需人工审批', '审计日志保留180天', '废弃模型自动归档'],
    api: ['GET /governance/health'],
  },
  '/model/model-monitoring': {
    title: '模型监控',
    desc: '在线模型实时监控: 准确率/QPS/延迟/漂移告警',
    steps: ['部署模型后自动开始监控', '查看准确率/QPS/延迟趋势图', '设置告警阈值', '漂移检测自动触发重训练', '查看告警历史'],
    tips: ['部署后至少运行100次推理获取稳定指标', 'QPS>1000需考虑扩容', '准确率下降>5%触发告警'],
    api: ['GET /monitoring/status'],
  },
  '/app/inference': {
    title: '在线推理',
    desc: '直接调用已部署模型进行实时预测',
    steps: ['选择已部署的模型', '输入特征值(或上传CSV)', '点击"推理"', '查看预测结果和置信度'],
    tips: ['支持单条和批量推理', '推理结果自动记录到日志', '可查看历史推理记录'],
    api: ['POST /inference/model/{id}', 'POST /inference/model/{id}/batch'],
  },
  // ── 缺失补充 ──

  '/model/model-versions': {
    title: '模型版本', desc: '管理模型版本: 版本对比/回滚/设为当前',
    steps: ['选择模型→查看版本列表', '添加新版本(关联训练任务)', '对比两个版本的指标差异', '回滚到历史版本'],
    tips: ['每次训练自动生成新版本', 'set-current标记使用中版本', '回滚前建议先备份'],
    api: ['POST /model-version/versions', 'GET /model-version/models/{id}/versions'],
  },
  '/model/distributed-training': {
    title: '并行参数搜索', desc: '多个 CPU worker 并行训练同一算法的不同参数组合，选择验证指标最优者',
    steps: ['选择可执行算法', '选择数据集与预测目标列', '设置并行 Worker 数并创建任务', '查看各参数组合的完成进度与最佳指标'],
    tips: ['需多GPU硬件环境', '数据并行适合大数据集', '模型并行适合大模型'],
  },
  '/model/enhanced-experiments': {
    title: '增强实验', desc: '多实验对比分析: 排行榜/消融实验/显著性检验',
    steps: ['选择要对比的实验', '查看排行榜(按R²/Accuracy排序)', '运行消融实验(逐个移除特征)', '显著性检验(确定最优模型)'],
    tips: ['至少3个实验做有效对比', '消融实验帮助理解特征贡献'],
  },
  '/model/model-serving': {
    title: '模型服务', desc: '高性能在线推理服务: REST API/负载均衡/扩缩容',
    steps: ['部署已训练模型', '获取推理API endpoint', '发送POST请求进行推理', '配置负载均衡和扩缩容'],
    tips: ['支持JSON和批量推理', '延迟通常<50ms', '高并发需配置多副本'],
    api: ['POST /deployments/{id}/serve', 'POST /deployments/{id}/predict'],
  },

  '/app/batch-inference': {
    title: '批量推理', desc: 'CSV上传→批量预测→结果下载',
    steps: ['上传CSV文件(含特征列)', '选择已部署模型', '点击"开始推理"', '批量完成后下载结果CSV'],
    tips: ['大文件分批上传(每批<1000行)', '推理结果含预测值和置信度'],
    api: ['POST /inference/model/{id}/batch'],
  },
  '/app/inference-history': {
    title: '推理历史', desc: '查询历史推理记录和日志',
    steps: ['按时间/模型筛选推理记录', '查看每条输入/输出/延迟', '导出历史数据到CSV'],
    tips: ['推理记录保留30天', '便于分析模型性能趋势'],
    api: ['GET /inference/history'],
  },

  '/app/industrial-protocol': {
    title: '工业协议', desc: '支持OPC UA/Modbus/MQTT等工业协议数据采集',
    steps: ['选择协议类型(OPC UA/Modbus/MQTT)', '配置连接参数(endpoint/端口/认证)', '测试连接', '浏览可用的数据点/标签'],
    tips: ['OPC UA是工业4.0标准', 'Modbus TCP适合PLC连接'],
  },
  '/app/edge-gateway': {
    title: '边缘网关', desc: '边缘计算: 本地推理+数据预处理+缓存转发',
    steps: ['注册边缘网关设备', '部署模型到边缘节点', '本地推理减少延迟', '定时同步云端'],
    tips: ['边缘推理延迟<10ms', '适合实时性要求高的场景', '断网时缓存→联网后同步'],
  },
  '/app/marketplace': {
    title: '应用市场', desc: 'AI应用模板市场: 一键部署行业解决方案',
    steps: ['浏览解决方案模板', '预览Demo效果', '一键部署到平台', '自定义配置'],
    tips: ['当前15个预置模型可一键使用'],
    api: ['GET /marketplace'],
  },

  '/system/monitoring': {
    title: '系统监控', desc: '平台基础设施监控: CPU/内存/磁盘/网络',
    steps: ['查看集群节点状态', '监控资源使用率', '设置告警阈值', '查看告警历史'],
  },
  '/system/rbac': {
    title: '权限管理', desc: '基于角色的访问控制: 用户/角色/权限',
    steps: ['创建角色(管理员/开发者/查看者)', '分配权限(读/写/执行)', '添加用户到角色', '测试权限生效'],
    tips: ['最小权限原则', '开发者可训练不可部署', '管理员可管理所有资源'],
  },
  '/system/file-upload': {
    title: '文件管理', desc: '上传和管理数据集/模型文件',
    steps: ['上传文件(CSV/Excel/JSON/pickle)', '查看文件列表', '预览内容', '删除过期文件'],
    tips: ['支持断点续传', '文件自动关联数据集'],
  },
  '/system/api-keys': {
    title: 'API密钥', desc: '管理API访问密钥: 创建/撤销/权限控制',
    steps: ['创建密钥(指定名称和权限)', '复制密钥(仅显示一次)', '撤销不需要的密钥'],
    tips: ['密钥等同于密码, 妥善保管', '定期轮换密钥', '每个应用单独密钥'],
  },
  '/system/kms': {
    title: '密钥管理', desc: '加密密钥全生命周期管理: 生成/存储/轮换',
    steps: ['生成加密密钥', '配置密钥别名', '设置自动轮换策略', '查看密钥使用日志'],
    tips: ['AES-256为默认加密算法'],
  },
  '/system/audit-logs': {
    title: '审计日志', desc: '操作审计: 谁/何时/做了什么',
    steps: ['按时间/用户/操作筛选日志', '查看操作详情', '导出审计报告'],
    tips: ['日志保留180天', '所有API调用自动记录'],
  },
  '/system/compliance': {
    title: '合规管理', desc: '合规检查: 数据隐私/模型公平性/法规遵从',
    steps: ['创建合规规则', '运行合规检查', '查看不合规项', '生成合规报告'],
    tips: ['GDPR/等保合规模板预置', '高风险模型需人工审批'],
  },
  '/system/tenant-management': {
    title: '租户管理', desc: '多租户隔离: 独立数据空间/资源配额',
    steps: ['创建租户', '分配资源配额(数据集/模型/API)', '设置租户管理员'],
    tips: ['每个租户数据完全隔离', '配额超出自动限流'],
  },
  '/system/settings': {
    title: '系统设置', desc: '平台配置: 主题/通知/默认参数',
    steps: ['修改平台名称和Logo', '配置邮件/短信通知', '设置默认训练参数'],
    tips: ['修改后立即生效', '通知模板支持变量替换'],
  },
  '/system/config-center': {
    title: '配置中心', desc: '集中管理应用配置: 环境变量/功能开关',
    steps: ['创建配置项', '设置不同环境的值(开发/测试/生产)', '启用/禁用功能开关'],
    tips: ['支持热更新', '配置变更历史可追溯'],
  },
  '/system/gateway': {
    title: 'API网关', desc: 'API路由/限流/认证统一管理',
    steps: ['配置路由规则', '设置限流策略(QPS限制)', '配置认证方式', '查看调用统计'],
    tips: ['默认QPS限制100', '认证支持JWT和API Key'],
  },
  '/system/service-mesh': {
    title: '服务网格', desc: '微服务治理: 负载均衡/熔断/限流/追踪',
    steps: ['查看服务拓扑', '配置熔断规则', '设置负载均衡策略', '追踪请求链路'],
    tips: ['熔断避免雪崩效应', '分布式追踪帮助排查'],
  },
  '/system/cicd': {
    title: 'CI/CD', desc: '持续集成与部署: 自动构建/测试/发布',
    steps: ['配置构建流水线', '设置触发条件(提交/PR)', '查看构建历史', '配置自动部署'],
    tips: ['master分支自动部署生产', 'PR自动运行测试'],
  },
  '/system/gpu-scheduler': {
    title: 'GPU调度', desc: 'GPU资源管理与任务调度',
    steps: ['查看GPU资源状态', '提交GPU训练任务', '设置优先级', '监控GPU使用率'],
    tips: ['显存不足时自动排队', '支持多任务分时共享'],
  },
  '/system/alert': {
    title: '告警管理', desc: '平台告警: 规则/通知/静默',
    steps: ['创建告警规则(阈值/事件)', '配置通知渠道(邮件/短信)', '查看告警历史', '设置静默时段'],
    tips: ['准确率下降>5%自动告警', '磁盘>90%自动告警'],
  },
  '/system/notifications': {
    title: '通知中心', desc: '系统通知: 训练完成/部署成功/异常告警',
    steps: ['查看未读通知', '标记已读', '按类型筛选'],
    tips: ['训练任务完成自动通知', '支持邮件/短信/站内推送'],
  },
  '/system/logging': {
    title: '日志管理', desc: '集中日志: API日志/训练日志/系统日志',
    steps: ['按时间/级别/来源筛选日志', '搜索关键词', '导出日志'],
    tips: ['ERROR级别自动高亮', '日志保留90天'],
  },
  '/system/remote-ops': {
    title: '远程运维', desc: '远程设备管理: SSH/命令行/文件传输',
    steps: ['添加远程设备', '建立SSH连接', '执行命令', '查看输出'],
    tips: ['支持批量操作', '操作记录全部留痕'],
  },
  '/system/work-orders': {
    title: '工单管理', desc: '运维工单: 创建/指派/处理/关闭',
    steps: ['创建工单(问题描述/优先级)', '指派工程师', '处理并记录步骤', '关闭工单'],
    tips: ['P0工单需1小时内响应', '关联告警可自动生成工单'],
  },
  '/system/reports': {
    title: '报表中心', desc: '自动生成日报/周报/月报',
    steps: ['选择报表类型(训练/推理/资源)', '设置生成周期', '配置收件人', '查看历史报表'],
    tips: ['报表支持PDF格式', '可自定义KPIs'],
  },
  '/system/backup': {
    title: '数据备份', desc: '数据库/模型/数据集定期备份',
    steps: ['配置备份策略(时间/频率)', '选择备份范围', '执行立即备份', '查看备份列表和恢复'],
    tips: ['每日自动全量备份', '保留最近7个备份'],
  },
  '/system/cache-management': {
    title: '缓存管理', desc: 'Redis缓存监控与清理',
    steps: ['查看缓存命中率', '查看各缓存Key占用', '手动清理过期缓存'],
    tips: ['高命中率说明缓存有效', '内存不足时自动淘汰旧key'],
  },
}
