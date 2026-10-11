import { expect, test, type Page } from '@playwright/test'

async function openPipeline(page: Page) {
  await page.addInitScript(() => localStorage.setItem('foundation_token', 'offline-contract-token'))
  await page.route('**/api/v1/datasets**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ items: [{ id: 41, name: '光伏确定性测试集' }], total: 1 }),
  }))
  await page.goto('/model/pipeline')
  await expect(page.getByRole('heading', { name: /低代码ML流水线/ })).toBeVisible()
}

async function prepareSingleConfiguredDataNode(page: Page) {
  for (const selector of ['.node-process', '.node-train', '.node-eval']) {
    await page.locator(`.custom-node${selector} button`).evaluate(
      element => (element as HTMLButtonElement).click(),
    )
  }
  const node = page.locator('.custom-node.node-data').first()
  await expect(node).toBeVisible()
  // Vue Flow consumes pointerdown for dragging before Element Plus can open.
  // DOM click bypasses only that gesture layer; the real select still owns state.
  await node.locator('.el-select__wrapper').evaluate(element => (element as HTMLElement).click())
  await page.getByRole('option', { name: '光伏确定性测试集' }).last().click()
  return node
}

test.describe('Pipeline 离线契约', () => {
  test('必填配置缺失时在浏览器端失败且不请求执行接口', async ({ page }) => {
    let pipelineRequests = 0
    await page.route('**/api/v1/pipelines**', route => {
      pipelineRequests += 1
      return route.fulfill({ status: 500, body: '{}' })
    })
    await openPipeline(page)
    await page.getByRole('button', { name: '▶ 运行', exact: true }).click()
    await expect(page.locator('.el-message')).toContainText('请选择数据集')
    expect(pipelineRequests).toBe(0)
  })

  test('页面提交持久化 Run 并以后端任务终态为准', async ({ page }) => {
    const calls: string[] = []
    let polls = 0
    await page.route('**/api/v1/pipelines', async route => {
      calls.push('persist')
      const definition = route.request().postDataJSON()
      expect(definition.tasks[0].config.dataset_id).toBe(41)
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 21 }) })
    })
    await page.route('**/api/v1/pipelines/21/run', async route => {
      calls.push('run')
      expect(route.request().postDataJSON()).toEqual({ validation_context: {} })
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ run_id: 81, status: 'PENDING' }) })
    })
    await page.route('**/api/v1/pipelines/runs/81', async route => {
      polls += 1
      const status = polls === 1 ? 'PENDING' : polls === 2 ? 'RUNNING' : 'SUCCESS'
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          id: 81, status,
          task_runs: [{ task_name: 'node_1', status, logs: status === 'SUCCESS' ? '真实数据加载完成' : '' }],
        }),
      })
    })
    await page.route('**/api/v1/pipelines/21/runs', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: [{ id: 81, status: 'RUNNING', triggered_by: 'offline-user', created_at: '2026-07-18T02:00:00Z' }] }),
    }))
    await page.route('**/api/v1/pipelines/runs/81/resume', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ run_id: 81, accepted: true, coordinator_task_id: 'resume-81' }),
    }))
    await openPipeline(page)
    const node = await prepareSingleConfiguredDataNode(page)
    await page.getByRole('button', { name: '▶ 运行', exact: true }).click()
    await expect(node).toContainText('成功')
    await expect(page.locator('.el-message')).toContainText('流水线执行成功')
    expect(calls).toEqual(['persist', 'run'])
    expect(polls).toBe(3)
    await page.getByRole('button', { name: /运行记录/ }).click()
    await expect(page.getByText('Pipeline 运行中心')).toBeVisible()
    await expect(page.getByText('offline-user')).toBeVisible()
    await page.getByRole('button', { name: '恢复' }).click()
    await expect(page.locator('.el-message').filter({ hasText: '已重新进入协调队列' })).toBeVisible()
  })

  test('导出必须先持久化，保存失败时不调用导出接口', async ({ page }) => {
    let exportRequests = 0
    await page.route('**/api/v1/pipelines', route => route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'offline persistence unavailable' }),
    }))
    await page.route('**/api/v1/pipelines/*/export', route => {
      exportRequests += 1
      return route.fulfill({ status: 200, body: JSON.stringify({ code: 'must not happen' }) })
    })
    await openPipeline(page)
    await prepareSingleConfiguredDataNode(page)
    await page.getByRole('button', { name: /导出/ }).click()
    await page.getByText('导出为 API', { exact: true }).click()
    await expect(page.locator('.el-message')).toContainText('导出失败')
    await expect(page.getByRole('dialog', { name: /导出流水线/ })).toHaveCount(0)
    expect(exportRequests).toBe(0)
  })

  test('持久化成功后只使用真实 pipeline id 导出', async ({ page }) => {
    const calls: string[] = []
    await page.route('**/api/v1/pipelines', async route => {
      calls.push('persist')
      const definition = route.request().postDataJSON()
      expect(definition.tasks).toHaveLength(1)
      expect(definition.tasks[0].task_type).toBe('data_load')
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 17 }) })
    })
    await page.route('**/api/v1/pipelines/17/export', async route => {
      calls.push('export')
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ format: 'api', code: '# token-required generated API' }),
      })
    })
    await openPipeline(page)
    await prepareSingleConfiguredDataNode(page)
    await page.getByRole('button', { name: /导出/ }).click()
    await page.getByText('导出为 API', { exact: true }).click()
    await expect(page.getByRole('dialog', { name: /导出流水线/ })).toBeVisible()
    await expect(page.locator('.code-block:visible')).toContainText('token-required generated API')
    expect(calls).toEqual(['persist', 'export'])
  })
})
