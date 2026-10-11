import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test'

const username = process.env.E2E_USERNAME || 'admin'
const password = process.env.E2E_PASSWORD || 'CHANGE_ME_ADMIN'

async function jsonOrText(response: APIResponse): Promise<unknown> {
  const text = await response.text()
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function expectOk(response: APIResponse, operation: string): Promise<any> {
  const body = await jsonOrText(response)
  expect(response.ok(), `${operation}: HTTP ${response.status()} ${JSON.stringify(body)}`).toBe(true)
  return body
}

async function authenticate(request: APIRequestContext) {
  const response = await request.post('/api/v1/auth/login', {
    data: { username, password },
  })
  const body = await expectOk(response, '登录')
  expect(body.access_token).toBeTruthy()
  return { Authorization: `Bearer ${body.access_token}` }
}

test.describe('完整模型生命周期生产门禁', () => {
  test.describe.configure({ mode: 'serial' })
  test.skip(
    process.env.RUN_LIFECYCLE_GATE !== '1',
    '仅通过 npm run test:e2e:lifecycle 显式运行，避免常规页面回归创建生产临时资产',
  )

  test('训练 → MLflow → 模型版本 → 部署 → 推理 → 回滚/提升 → 全量清理', async ({ request }) => {
    test.setTimeout(240_000)

    const headers = await authenticate(request)
    const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
    const name = `e2e-lifecycle-${suffix}`
    const X = Array.from({ length: 48 }, (_, index) => {
      const x1 = index / 10
      const x2 = (index % 7) - 3
      return [x1, x2, Math.sin(index / 4)]
    })
    const y = X.map(row => 3 * row[0] - 1.5 * row[1] + 0.25 * row[2])

    let jobId: string | undefined
    let modelId: number | undefined
    let modelVersionId: number | undefined
    let mlflowRunId: string | undefined
    let deploymentId: number | undefined
    const cleanupErrors: string[] = []

    try {
      const created = await expectOk(
        await request.post('/api/v1/real-training/jobs', {
          headers,
          data: {
            name,
            task_type: 'regression',
            model_type: 'random_forest',
            X,
            y,
            n_estimators: 8,
            max_depth: 3,
            normalize: true,
          },
        }),
        '创建训练任务',
      )
      jobId = created.job_id
      expect(jobId).toMatch(/^train_[a-f0-9]+$/)

      await expect.poll(
        async () => {
          const response = await request.get(`/api/v1/real-training/jobs/${jobId}`, { headers })
          const job = await expectOk(response, '轮询训练任务')
          if (job.status === 'failed') throw new Error(`训练失败: ${job.error || 'unknown error'}`)
          modelId = job.registered_model_id || job.config?.registered_model_id
          modelVersionId = job.config?.registered_model_version_id
          mlflowRunId = job.mlflow_run_id || job.config?.mlflow_run_id
          return job.status === 'completed' && Boolean(modelId && modelVersionId && mlflowRunId)
        },
        { message: '训练完成并写入模型、版本和 MLflow Run 关联', timeout: 150_000, intervals: [500, 1000, 2000] },
      ).toBe(true)

      const trainingPrediction = await expectOk(
        await request.post(`/api/v1/real-training/jobs/${jobId}/predict`, {
          headers,
          data: { X: [X[0], X[10]] },
        }),
        '训练模型推理',
      )
      expect(trainingPrediction.predictions).toHaveLength(2)

      await expect.poll(
        async () => {
          const response = await request.get(`/api/v1/models/${modelId}/lineage`, { headers })
          if (!response.ok()) return false
          const lineage = await response.json()
          modelVersionId = lineage.model_version?.id || modelVersionId
          return (
            lineage.training_job_id === jobId
            && lineage.mlflow_run_id === mlflowRunId
            && lineage.model_version?.mlflow_run_id === mlflowRunId
            && Boolean(lineage.mlflow_registry?.latest_version)
          )
        },
        { message: '模型血缘与 MLflow Registry 一致', timeout: 90_000, intervals: [1000, 2000] },
      ).toBe(true)

      const deployment = await expectOk(
        await request.post('/api/v1/deployments', {
          headers,
          data: {
            name: `${name}-deployment`,
            model_id: modelId,
            model_version_id: modelVersionId,
            strategy: 'rolling',
          },
        }),
        '创建部署',
      )
      deploymentId = deployment.id
      expect(deployment.model_id).toBe(modelId)
      expect(deployment.model_version_id).toBe(modelVersionId)

      const serving = await expectOk(
        await request.post(`/api/v1/deployments/${deploymentId}/serve`, { headers }),
        '加载部署模型',
      )
      expect(serving.model_id).toBe(modelId)
      expect(serving.model_version_id).toBe(modelVersionId)

      const inference = await expectOk(
        await request.post(`/api/v1/deployments/${deploymentId}/predict`, {
          headers,
          data: { features: X[5] },
        }),
        '部署推理',
      )
      expect(Number.isFinite(inference.prediction)).toBe(true)
      expect(inference.latency_ms).toBeGreaterThanOrEqual(0)

      const detail = await expectOk(
        await request.get(`/api/v1/deployments/${deploymentId}`, { headers }),
        '读取部署血缘',
      )
      expect(detail.model_id).toBe(modelId)
      expect(detail.model_version_id).toBe(modelVersionId)
      expect(detail.mlflow_run_id).toBe(mlflowRunId)

      const rolledBack = await expectOk(
        await request.post(`/api/v1/deployments/${deploymentId}/rollback`, {
          headers,
          data: { version: detail.version },
        }),
        '部署回滚',
      )
      expect(rolledBack.status).toBe('rolled_back')

      const promoted = await expectOk(
        await request.post(`/api/v1/deployments/${deploymentId}/promote`, { headers }),
        '部署提升',
      )
      expect(promoted.status).toBe('promoted')
    } finally {
      if (deploymentId) {
        const response = await request.delete(`/api/v1/deployments/${deploymentId}`, { headers })
        if (![200, 404].includes(response.status())) {
          cleanupErrors.push(`deployment ${deploymentId}: ${response.status()} ${JSON.stringify(await jsonOrText(response))}`)
        }
      }

      if (jobId) {
        const response = await request.delete(
          `/api/v1/real-training/jobs/${jobId}?purge_generated=true`,
          { headers },
        )
        if (![200, 404].includes(response.status())) {
          cleanupErrors.push(`job ${jobId}: ${response.status()} ${JSON.stringify(await jsonOrText(response))}`)
        } else if (response.status() === 200) {
          const result = await response.json()
          cleanupErrors.push(...(result.cleanup?.errors || []))
        }
      }

      if (deploymentId) {
        const response = await request.get(`/api/v1/deployments/${deploymentId}`, { headers })
        if (response.status() !== 404) cleanupErrors.push(`deployment ${deploymentId} cleanup verification: ${response.status()}`)
      }
      if (modelId) {
        const response = await request.get(`/api/v1/models/${modelId}`, { headers })
        if (response.status() !== 404) cleanupErrors.push(`model ${modelId} cleanup verification: ${response.status()}`)
      }
      if (jobId) {
        const response = await request.get(`/api/v1/real-training/jobs/${jobId}`, { headers })
        if (response.status() !== 404) cleanupErrors.push(`job ${jobId} cleanup verification: ${response.status()}`)
      }

      expect(cleanupErrors, '生命周期门禁必须完整回收临时资产').toEqual([])
    }
  })
})
