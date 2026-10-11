// 用户相关类型
export interface UserInfo {
  id: number
  username: string
  email: string
  full_name?: string
  role: string
  avatar?: string
  features?: string[] | null  // 允许的菜单 path 白名单(null/空=不限制)
  permissions?: string[]       // 当前角色的有效 RBAC 权限
  is_admin?: boolean          // 后端按 role 计算, 前端优先用它判断管理员
  created_at: string
}

// 项目
export interface Project {
  id: number
  name: string
  description?: string
  type: string
  status: 'active' | 'archived'
  models_count: number
  datasets_count: number
  created_at: string
  updated_at?: string
}

// 数据集
export interface Dataset {
  id: number
  name: string
  type: string
  samples: number
  size: number
  features?: string[]
  labels?: string[]
  created_at: string
  updated_at?: string
}

// 模型
export interface Model {
  id: number
  name: string
  type: string
  framework: string
  version: string
  status: 'training' | 'trained' | 'deployed' | 'failed'
  accuracy?: number
  metrics?: Record<string, number>
  created_at: string
  updated_at?: string
}

// 训练任务
export interface TrainingTask {
  id: number
  name: string
  model_name: string
  dataset_name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  start_time: string
  end_time?: string
  metrics?: Record<string, number>
}

// 部署
export interface Deployment {
  id: number
  name: string
  model_id: number
  model_name: string
  status: 'pending' | 'running' | 'stopped' | 'failed'
  endpoint: string
  version: string
  qps?: number
  latency?: number
  created_at: string
  updated_at?: string
}

// 告警
export interface Alert {
  id: number
  title: string
  level: 'info' | 'warning' | 'error' | 'critical'
  message: string
  source?: string
  status: 'active' | 'acknowledged' | 'resolved'
  created_at: string
  acknowledged_at?: string
  resolved_at?: string
}

// 工单
export interface WorkOrder {
  id: number
  title: string
  description: string
  priority: 'low' | 'medium' | 'high' | 'critical'
  status: 'pending' | 'assigned' | 'in_progress' | 'completed' | 'closed'
  assignee?: string
  created_at: string
  updated_at?: string
}

// 数据源
export interface DataSource {
  id: number
  name: string
  type: 'mysql' | 'postgresql' | 'mongodb' | 'api' | 'file'
  config: Record<string, any>
  status: 'connected' | 'disconnected' | 'error'
  last_sync?: string
  created_at: string
}

// 仪表盘统计
export interface DashboardStats {
  projects: {
    total: number
    active: number
  }
  datasets: {
    total: number
    size: number
  }
  models: {
    total: number
    deployed: number
  }
  inference: {
    total_calls: number
    today_calls: number
  }
}

// API 响应
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  message?: string
  detail?: string
}

// 分页
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}
