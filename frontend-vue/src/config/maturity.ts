export type MaturityLevel = 'production' | 'demo'

export interface FeatureMaturity {
  level: MaturityLevel
  label: string
  shortLabel: string
  tagType: 'success' | 'warning' | 'info'
  description: string
}

const demoPaths = new Set([
  '/model/code-generator',
  '/model/audio-training',
  '/model/gan-training',
  '/model/rl-training',
  '/model/recsys-training',
  '/model/causal-analysis',
  '/model/gnn-training',
  '/model/llmops',
  '/model/billing',
  '/system/tenant',
  '/system/kms',
  '/system/cicd',
  '/system/remote-ops',
])

const definitions: Record<MaturityLevel, FeatureMaturity> = {
  production: {
    level: 'production',
    label: 'Production',
    shortLabel: 'P',
    tagType: 'success',
    description: '已接入真实数据与服务，并纳入核心回归门禁。',
  },
  demo: {
    level: 'demo',
    label: 'Demo',
    shortLabel: 'D',
    tagType: 'info',
    description: '演示或教学能力，可能包含占位逻辑，不应用于生产决策。',
  },
}

export function getFeatureMaturity(path: string): FeatureMaturity {
  const normalized = path.length > 1 ? path.replace(/\/$/, '') : path
  const matches = (candidate: string) =>
    normalized === candidate || normalized.startsWith(`${candidate}/`)
  if ([...demoPaths].some(matches)) return definitions.demo
  return definitions.production
}
