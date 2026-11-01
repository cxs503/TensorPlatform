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
  }) => http.post<any>('/tensor/missions', body),
  children: (id: number) => http.get<any>(`/tensor/missions/${id}/children`),
  stop: (id: number, reason?: string) =>
    http.post<any>(`/tensor/missions/${id}/stop`, { reason }),
}

// ── progress reports (published per completed mission/stage) ──
export const reportApi = {
  list: (params?: any) => http.get<any>("/tensor/reports", params),
  get: (id: number) => http.get<any>(`/tensor/reports/${id}`),
}

// ── paper production line (TensorPapers registry + paper-* missions) ──
export const paperApi = {
  line: () => http.get<any>('/tensor/papers/line'),
}
