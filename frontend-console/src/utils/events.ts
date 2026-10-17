/** 智能体事件类型的展示元数据（颜色对齐设计令牌）与摘要提取。 */

export interface AgentEvent {
  id: number
  session_id: number
  seq: number
  kind: string
  agent_name?: string
  execution_id?: number | null
  execution_key?: string | null
  payload: Record<string, any>
  created_at?: string | null
}

export const EVENT_KIND_META: Record<string, { label: string; color: string }> = {
  plan:               { label: '任务规划',   color: '#7aa2f7' },
  execution_started:  { label: '开始执行',   color: '#9d7cd8' },
  run_registered:     { label: 'Run 注册',   color: '#2fae8b' },
  validation_seen:    { label: '校验结论',   color: '#16829c' },
  decision:           { label: '阶段判定',   color: '#b07a25' },
  approval_requested: { label: '提请人审',   color: '#e08e45' },
  approval_decided:   { label: '人审裁定',   color: '#5b8c5a' },
  note:               { label: '备注',       color: '#90a0b0' },
  error:              { label: '错误',       color: '#c24545' },
}

export function kindLabel(kind: string): string {
  return EVENT_KIND_META[kind]?.label || kind
}

export function kindColor(kind: string): string {
  return EVENT_KIND_META[kind]?.color || '#90a0b0'
}

/** 每类事件的一句话摘要（零手抄：全部取自 payload 实值）。 */
export function summarizeEvent(e: AgentEvent): string {
  const p = e.payload || {}
  switch (e.kind) {
    case 'plan':
      return `${p.stage || ''}${p.max_wall_seconds ? ` · 预算 ${p.max_wall_seconds}s` : ''}`.trim() || '—'
    case 'execution_started':
      return e.execution_key || '—'
    case 'run_registered':
      return p.run_id || '—'
    case 'validation_seen': {
      const checks = p.checks_total != null ? `检查 ${p.checks_passed}/${p.checks_total}` : ''
      return [checks, p.decision].filter(Boolean).join(' · ') || '—'
    }
    case 'decision': {
      const n = Array.isArray(p.registered_run_ids) ? p.registered_run_ids.length : null
      return [
        p.status,
        p.stop_condition ? `终止:${p.stop_condition}` : null,
        n != null ? `${n} run` : null,
      ].filter(Boolean).join(' · ')
    }
    default: {
      const s = JSON.stringify(p)
      return s && s !== '{}' ? s.slice(0, 90) : '—'
    }
  }
}
