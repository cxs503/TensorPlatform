import { expect, test, type Page, type Route } from '@playwright/test'

type JsonResponse = { status?: number; body: unknown }
type ApiHandler = (request: {
  method: string
  path: string
  search: URLSearchParams
  body: any
}) => JsonResponse | Promise<JsonResponse>

const json = (body: unknown, status = 200): JsonResponse => ({ status, body })

async function openOffline(page: Page, path: string, handler: ApiHandler) {
  await page.addInitScript(() => localStorage.setItem('foundation_token', 'model-factory-offline-token'))
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    expect(request.headers().authorization).toBe('Bearer model-factory-offline-token')
    const url = new URL(request.url())
    const response = await handler({
      method: request.method(),
      path: url.pathname.replace('/api/v1', ''),
      search: url.searchParams,
      body: request.postData() ? request.postDataJSON() : undefined,
    })
    await route.fulfill({
      status: response.status ?? 200,
      contentType: 'application/json',
      body: JSON.stringify(response.body),
    })
  })
  await page.goto(path)
  await expect(page.locator('#app')).toBeVisible()
}

test.describe('模型工厂持久化实验离线契约', () => {
  test('因果分析显式提交 fixture，轮询真实指标且刷新后保留历史', async ({ page }) => {
    let submitted = false
    let polls = 0
    let submittedBody: any

    await openOffline(page, '/model/causal-analysis', ({ method, path, body }) => {
      if (method === 'GET' && path === '/datasets') return json({ items: [], total: 0 })
      if (method === 'POST' && path === '/causal/analyze') {
        submitted = true
        submittedBody = body
        return json({ id: 6101, status: 'PENDING' })
      }
      if (method === 'GET' && path === '/causal/status') {
        if (!submitted) return json({ results: [] })
        polls += 1
        const completed = polls >= 2
        return json({
          results: [{
            id: 6101,
            status: completed ? 'COMPLETED' : 'RUNNING',
            config: { fixture_mode: true, treatment: 'X', outcome: 'Y', confounders: ['Z'] },
            data_source: 'fixture',
            effect: completed ? 1.23456 : null,
            ci_lower: completed ? 1.10234 : null,
            ci_upper: completed ? 1.36678 : null,
          }],
        })
      }
      return json({ detail: `unexpected offline request: ${method} ${path}` }, 404)
    })

    await expect(page.getByRole('heading', { name: '因果分析' })).toBeVisible()
    await page.getByRole('button', { name: '新建分析' }).click()
    await page.getByRole('button', { name: '提交任务' }).click()

    await expect(page.getByText('运行中')).toBeVisible()
    await expect(page.getByText('1.2346')).toBeVisible({ timeout: 6000 })
    await expect(page.getByText('1.1023 ~ 1.3668')).toBeVisible()
    expect(submittedBody).toMatchObject({
      fixture_mode: true,
      dataset_id: null,
      treatment: 'X',
      outcome: 'Y',
      confounders: ['Z'],
      method: 'linear_regression',
    })
    expect(polls).toBeGreaterThanOrEqual(2)

    await page.reload()
    await expect(page.getByText('1.2346')).toBeVisible()
    await expect(page.getByText('已完成')).toBeVisible()
  })

  test('推荐训练轮询到真实 RMSE，推荐请求使用已完成数据库 job_id', async ({ page }) => {
    let submitted = false
    let polls = 0
    let trainBody: any
    let inferenceJobId: string | null = null

    await openOffline(page, '/model/recsys-training', ({ method, path, search, body }) => {
      if (method === 'GET' && path === '/datasets') return json({ items: [], total: 0 })
      if (method === 'POST' && path === '/recsys/train') {
        submitted = true
        trainBody = body
        return json({ id: 6201, status: 'PENDING' })
      }
      if (method === 'GET' && path === '/recsys/jobs') {
        if (!submitted) return json({ jobs: [] })
        polls += 1
        const completed = polls >= 2
        return json({ jobs: [{
          id: 6201,
          status: completed ? 'COMPLETED' : 'RUNNING',
          progress: completed ? 100 : 55,
          config: { fixture_mode: true, factors: 16, epochs: 30 },
          metrics: completed ? { rmse: 0.31873 } : {},
        }] })
      }
      if (method === 'GET' && path === '/recsys/recommend') {
        inferenceJobId = search.get('job_id')
        return json({ recommendations: [{ item: 'asset-7', score: 4.78231 }] })
      }
      return json({ detail: `unexpected offline request: ${method} ${path}` }, 404)
    })

    await page.getByRole('button', { name: '新建训练' }).click()
    await page.getByRole('button', { name: '提交训练' }).click()
    await expect(page.getByText('运行中')).toBeVisible()
    await expect(page.getByText('0.3187')).toBeVisible({ timeout: 6000 })
    expect(trainBody).toMatchObject({ fixture_mode: true, dataset_id: null, algorithm: 'mf' })
    expect(polls).toBeGreaterThanOrEqual(2)

    await page.getByRole('button', { name: '真实推荐' }).click()
    await expect(page.getByText('asset-7')).toBeVisible()
    await expect(page.getByText('4.7823')).toBeVisible()
    expect(inferenceJobId).toBe('6201')
  })

  test('GNN 训练轮询到测试准确率，推理请求使用已完成数据库 job_id', async ({ page }) => {
    let submitted = false
    let polls = 0
    let trainBody: any
    let predictBody: any

    await openOffline(page, '/model/gnn-training', ({ method, path, body }) => {
      if (method === 'GET' && path === '/datasets') return json({ items: [], total: 0 })
      if (method === 'POST' && path === '/gnn/train') {
        submitted = true
        trainBody = body
        return json({ id: 6301, status: 'PENDING' })
      }
      if (method === 'GET' && path === '/gnn/jobs') {
        if (!submitted) return json({ jobs: [] })
        polls += 1
        const completed = polls >= 2
        return json({ jobs: [{
          id: 6301,
          status: completed ? 'COMPLETED' : 'RUNNING',
          progress: completed ? 100 : 50,
          config: { fixture_mode: true, epochs: 60, hidden_dim: 16 },
          metrics: completed ? { test_accuracy: 0.875 } : {},
        }] })
      }
      if (method === 'POST' && path === '/gnn/predict') {
        predictBody = body
        return json({ predictions: [{ node: 'node_0', class_id: 2, confidence: 0.91234 }] })
      }
      return json({ detail: `unexpected offline request: ${method} ${path}` }, 404)
    })

    await page.getByRole('button', { name: '新建训练' }).click()
    await page.getByRole('button', { name: '提交训练' }).click()
    await expect(page.getByText('运行中')).toBeVisible()
    await expect(page.getByText('87.5%')).toBeVisible({ timeout: 6000 })
    expect(trainBody).toMatchObject({
      fixture_mode: true,
      dataset_id: null,
      task_type: 'node_classification',
      model: 'gcn',
    })

    await page.getByRole('button', { name: '真实推理' }).click()
    await expect(page.getByText('node_0')).toBeVisible()
    await expect(page.getByText('0.9123')).toBeVisible()
    expect(predictBody).toMatchObject({ job_id: 6301, nodes: ['node_0', 'node_1'] })
  })

  test('GAN 训练轮询到真实损失，采样请求使用已完成数据库 job_id', async ({ page }) => {
    let submitted = false
    let polls = 0
    let trainBody: any
    let generateBody: any

    await openOffline(page, '/model/gan-training', ({ method, path, body }) => {
      if (method === 'GET' && path === '/datasets') return json({ items: [], total: 0 })
      if (method === 'POST' && path === '/gan/train') {
        submitted = true
        trainBody = body
        return json({ id: 6401, status: 'PENDING' })
      }
      if (method === 'GET' && path === '/gan/jobs') {
        if (!submitted) return json({ jobs: [] })
        polls += 1
        const completed = polls >= 2
        return json({ jobs: [{
          id: 6401,
          status: completed ? 'COMPLETED' : 'RUNNING',
          progress: completed ? 100 : 65,
          config: { fixture_mode: true, epochs: 40, latent_dim: 8, hidden_dim: 32 },
          metrics: completed ? { generator_loss: 0.72456, discriminator_loss: 0.58123 } : {},
        }] })
      }
      if (method === 'POST' && path === '/gan/generate') {
        generateBody = body
        return json({ samples: [[1.25, -0.75], [0.5, 0.125]] })
      }
      return json({ detail: `unexpected offline request: ${method} ${path}` }, 404)
    })

    await page.getByRole('button', { name: '新建训练' }).click()
    await page.getByRole('button', { name: '提交训练' }).click()
    await expect(page.getByText('运行中')).toBeVisible()
    await expect(page.getByText('0.7246')).toBeVisible({ timeout: 6000 })
    await expect(page.getByText('0.5812')).toBeVisible()
    expect(trainBody).toMatchObject({ fixture_mode: true, dataset_id: null, gan_type: 'gan' })

    await page.getByRole('button', { name: '从训练产物采样' }).click()
    await expect(page.getByText('1.25000, -0.75000')).toBeVisible()
    expect(generateBody).toMatchObject({ job_id: 6401, count: 8, seed: 42 })
  })

  test('失败任务没有产物时前端禁止发起推理请求', async ({ page }) => {
    let generateCalls = 0

    await openOffline(page, '/model/gan-training', ({ method, path }) => {
      if (method === 'GET' && path === '/datasets') return json({ items: [], total: 0 })
      if (method === 'GET' && path === '/gan/jobs') return json({ jobs: [{
        id: 6499,
        status: 'FAILED',
        progress: 35,
        config: { fixture_mode: true },
        metrics: {},
        error_message: 'artifact was not produced',
      }] })
      if (method === 'POST' && path === '/gan/generate') {
        generateCalls += 1
        return json({ detail: 'must not be called without a completed artifact' }, 409)
      }
      return json({ detail: `unexpected offline request: ${method} ${path}` }, 404)
    })

    await expect(page.getByText('失败')).toBeVisible()
    await page.getByRole('button', { name: '从训练产物采样' }).click()
    await expect(page.locator('.el-message')).toContainText('请选择已完成任务')
    expect(generateCalls).toBe(0)
  })
})
