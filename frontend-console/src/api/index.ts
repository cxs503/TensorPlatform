import http from './request'

// ── auth ──
export const authApi = {
  login: (username: string, password: string) =>
    http.post<any>('/auth/login', { username, password }),
}

// ── scenarios ──
export const scenarioApi = {
  list: () => http.get<any>('/tensor/scenarios'),
  get: (key: string) => http.get<any>(`/tensor/scenarios/${key}`),
}

// ── executions ──
export const executionApi = {
  list: (params?: any) => http.get<any>('/tensor/executions', params),
  get: (id: number) => http.get<any>(`/tensor/executions/${id}`),
  trigger: (body: { scenario_key: string; stage: string; max_wall_seconds: number }) =>
    http.post<any>('/tensor/executions', body),
  cancel: (id: number, reason?: string) =>
    http.post<any>(`/tensor/executions/${id}/cancel`, { reason }),
}

// ── runs (TensorPhysics registry) ──
export const runApi = {
  list: (params?: any) => http.get<any>('/tensorphysics/runs', params),
  get: (runId: string, organizationId?: number) =>
    http.get<any>(`/tensorphysics/runs/${runId}`, { organization_id: organizationId }),
  artifactUrl: (runId: string, index: number) => `/tensorphysics/runs/${runId}/artifacts/${index}`,
}

// ── approvals ──
export const approvalApi = {
  list: (params?: any) => http.get<any>('/tensor/approvals', params),
  decide: (id: number, decision: 'approved' | 'rejected', note?: string) =>
    http.post<any>(`/tensor/approvals/${id}/decide`, { decision, note }),
}

// ── sessions ──
export const sessionApi = {
  list: (params?: any) => http.get<any>('/tensor/sessions', params),
  get: (id: number, afterSeq = 0) =>
    http.get<any>(`/tensor/sessions/${id}`, { after_seq: afterSeq }),
}

// ── system ──
export const systemApi = {
  health: () => http.get<any>('/tensor/system/health'),
}

// ── activity (overview board) ──
export const activityApi = {
  overview: (eventsLimit = 60) => http.get<any>('/tensor/activity', { events_limit: eventsLimit }),
}

// ── workspace (six-repo Tensor family snapshot) ──
export const workspaceApi = {
  latest: () => http.get<any>('/tensor/workspace'),
}

// ── LLM analysis (self-deployed DeepSeek via internal relay) ──
export const llmApi = {
  status: () => http.get<any>('/tensor/llm/status'),
  analyzeExecution: (id: number) => http.post<any>(`/tensor/llm/analyze/execution/${id}`, {}),
}

// ── dev missions (autonomous agent development tasks) ──
export const missionApi = {
  list: (params?: any) => http.get<any>('/tensor/missions', params),
  get: (id: number) => http.get<any>(`/tensor/missions/${id}`),
  create: (body: {
    goal: string
    repo: string
    base_ref?: string
    co_repos?: string[]
    budget?: Record<string, number>
    model?: string
    acceptance_criteria?: Array<{
      statement?: string
      metric?: string
      target?: string | number
      tolerance?: string | number
      required?: boolean
      requires_code?: boolean
      requires_test?: boolean
    }>
  }) => http.post<any>('/tensor/missions', body),
  children: (id: number) => http.get<any>(`/tensor/missions/${id}/children`),
  stop: (id: number, reason?: string) =>
    http.post<any>(`/tensor/missions/${id}/stop`, { reason }),
}

// ── progress reports (published per completed mission/stage) ──
export const reportApi = {
  list: (params?: any) => http.get<any>("/tensor/reports", params),
  get: (id: number) => http.get<any>(`/tensor/reports/${id}`),
  qaPending: () => http.get<any>('/tensor/reports/qa/pending'),
}

// ── audit ledger (append-only organization trail, PR-8 #594) ──
export const auditApi = {
  stats: () => http.get<any>('/audit/stats'),
  logs: (params?: any) => http.get<any>('/audit/logs', params),
  security: (days = 7) => http.get<any>('/audit/security', { days }),
}

// ── capability ledger (org capability deltas, PR-10) ──
export const capabilityApi = {
  deltas: (params?: any) => http.get<any>('/tensor/capability/deltas', params),
  summary: () => http.get<any>('/tensor/capability/summary'),
}

// ── four centers (PR-10 首期最小视图：单一真值载荷，metrics[] 每项带 SQL 口径) ──
export const centersApi = {
  organization: () => http.get<any>('/tensor/centers/organization'),
  quality: () => http.get<any>('/tensor/centers/quality'),
  capability: () => http.get<any>('/tensor/centers/capability'),
  innovation: () => http.get<any>('/tensor/centers/innovation'),
}

// ── paper production line (TensorPapers registry + paper-* missions) ──
export const paperApi = {
  line: () => http.get<any>('/tensor/papers/line'),
}

// ── knowledge column (TensorKnowledge KE API, read-only via platform proxy) ──
// 数据一律经 KE API（platform 代理），前端不直连 TK 库。
export const knowledgeApi = {
  status: () => http.get<any>('/tensor/knowledge/status'),
  overview: () => http.get<any>('/tensor/knowledge/overview'),
  map: () => http.get<any>('/tensor/knowledge/map'),
  chain: (claimId: string) => http.get<any>('/tensor/knowledge/chain', { claim_id: claimId }),
  search: (params: { q: string; kind?: string; domain?: string; limit?: number }) =>
    http.get<any>('/tensor/knowledge/search', params),
  assets: () => http.get<any>('/tensor/knowledge/assets'),
  // 证据直读（免鉴权 GET，白名单在 middleware/auth_guard）
  source: (path: string, line?: number) =>
    http.get<any>(`/tensor/knowledge/source/${path}`, line ? { line, context: 12 } : { context: 12 }),
}
