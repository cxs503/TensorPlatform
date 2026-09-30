/**
 * 模型/算法分类枚举 —— 单一事实源
 *
 * 业务规则（ODF 2 大板块口径）:
 *   - 能效优化 (energy_optimization): 工业能效、能耗预测、能源调度
 *   - 故障预测 (fault_prediction):    设备故障诊断、健康度评估
 *   - 时序预测 (time_series_forecast): 时序回归、产线预测
 *   - 智能控制 (intelligent_control):  控制策略、参数寻优
 *   - 计算机视觉 (computer_vision):    视觉检测、缺陷识别
 *   - 自然语言处理 (nlp):              工业文本分析、知识抽取
 *
 * 命名规范: 前后端统一 snake_case value + 中文 label
 * 之前两套命名(energy vs energy_optimization)已收敛为长命名
 */

export interface ModelCategoryItem {
  value: string
  label: string
  /** Element Plus Tag type 颜色 */
  tagType: 'success' | 'danger' | 'primary' | 'warning' | 'info'
}

export const MODEL_CATEGORIES: ModelCategoryItem[] = [
  { value: 'energy_optimization',  label: '能效优化',   tagType: 'success' },
  { value: 'fault_prediction',     label: '故障预测',   tagType: 'danger' },
  { value: 'time_series_forecast', label: '时序预测',   tagType: 'warning' },
  { value: 'intelligent_control',  label: '智能控制',   tagType: 'primary' },
]

/** 算法组件分类(算法商店使用,范围比模型分类更宽,含视觉/NLP) */
export const COMPONENT_CATEGORIES: ModelCategoryItem[] = [
  { value: 'energy_optimization',  label: '能效优化',   tagType: 'success' },
  { value: 'fault_prediction',     label: '故障预测',   tagType: 'danger' },
  { value: 'time_series_forecast', label: '时序预测',   tagType: 'warning' },
  { value: 'computer_vision',      label: '计算机视觉', tagType: 'primary' },
  { value: 'nlp',                  label: '自然语言处理', tagType: 'info' },
]

/** value -> 完整定义 */
export const MODEL_CATEGORY_MAP: Record<string, ModelCategoryItem> =
  MODEL_CATEGORIES.reduce((acc, c) => {
    acc[c.value] = c
    return acc
  }, {} as Record<string, ModelCategoryItem>)

export function getCategoryByValue(v: string): ModelCategoryItem | undefined {
  return MODEL_CATEGORY_MAP[v] || COMPONENT_CATEGORIES.find(c => c.value === v)
}

export function getCategoryLabel(v: string): string {
  return getCategoryByValue(v)?.label ?? v
}

export function getCategoryTagType(v: string): ModelCategoryItem['tagType'] {
  return getCategoryByValue(v)?.tagType ?? 'info'
}