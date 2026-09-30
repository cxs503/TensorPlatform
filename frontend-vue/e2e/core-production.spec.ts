import { expect, test, type Page } from '@playwright/test'

const username = process.env.E2E_USERNAME || 'admin'
const password = process.env.E2E_PASSWORD || 'CHANGE_ME_ADMIN'

async function login(page: Page) {
  await page.goto('/login')
  await page.locator('input').nth(0).fill(username)
  await page.locator('input').nth(1).fill(password)
  await Promise.all([
    page.waitForURL(url => !url.pathname.endsWith('/login')),
    page.locator('button[type="submit"]').click(),
  ])
  await expect.poll(() => page.evaluate(() => Boolean(localStorage.getItem('foundation_token')))).toBe(true)
}

test.describe('核心生产门禁', () => {
  test.describe.configure({ mode: 'serial' })

  test('未登录不能读取业务统计', async ({ page }) => {
    await page.goto('/login')
    const status = await page.evaluate(async () => {
      const response = await fetch('/api/v1/dashboard/statistics')
      return response.status
    })
    expect(status).toBe(401)
  })

  test('错误密码被拒绝且页面保留在登录页', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input').nth(0).fill(username)
    await page.locator('input').nth(1).fill('definitely-wrong-password')
    await page.locator('button[type="submit"]').click()
    await expect(page).toHaveURL(/\/login/)
    await expect(page.locator('.login-error')).toContainText('用户名或密码错误', { timeout: 15000 })
  })

  test('登录、用户信息与首页统计形成闭环', async ({ page }) => {
    await login(page)
    const result = await page.evaluate(async () => {
      const token = localStorage.getItem('foundation_token')
      const headers = { Authorization: `Bearer ${token}` }
      const [me, dashboard] = await Promise.all([
        fetch('/api/v1/auth/me', { headers }),
        fetch('/api/v1/dashboard/statistics', { headers }),
      ])
      return {
        meStatus: me.status,
        dashboardStatus: dashboard.status,
        me: await me.json(),
        dashboard: await dashboard.json(),
      }
    })
    expect(result.meStatus).toBe(200)
    expect(result.dashboardStatus).toBe(200)
    expect(result.me.username).toBe(username)
    expect(result.dashboard.datasets.total).toBeGreaterThanOrEqual(0)
  })

  test('关键页面无4xx/5xx和浏览器运行时错误', async ({ page }) => {
    test.setTimeout(120000)
    await login(page)
    const routes = [
      '/',
      '/main',
      '/data/new-datasets',
      '/data/knowledge-graph',
      '/data/industrial-protocol',
      '/data/data-governance',
      '/data/lineage',
      '/model/new-models',
      '/model/mlflow',
      '/model/gnn-training',
      '/model/model-evaluation',
      '/model/model-explain',
      '/model/inference-dashboard',
      '/model/online-inference',
      '/deploy/deployment',
      '/system/monitoring',
      '/system/rbac',
    ]
    const browserErrors: string[] = []
    const badResponses: string[] = []
    page.on('pageerror', error => browserErrors.push(error.message))
    page.on('console', message => {
      if (message.type() === 'error') browserErrors.push(message.text())
    })
    page.on('response', response => {
      if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`)
    })

    for (const route of routes) {
      const response = await page.goto(route, { waitUntil: 'domcontentloaded' })
      expect(response?.status(), route).toBe(200)
      await expect(page.locator('#app'), route).toBeVisible()
      await expect.poll(
        () => page.locator('body').innerText().then(text => text.trim().length),
        { message: route },
      ).toBeGreaterThan(50)
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {})
    }

    expect(badResponses).toEqual([])
    expect(browserErrors).toEqual([])
  })

  test('移动端核心工作台不空白且不横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await login(page)
    for (const route of ['/main', '/data/new-datasets', '/model/pipeline', '/deploy/deployment']) {
      await page.goto(route, { waitUntil: 'domcontentloaded' })
      await expect.poll(
        () => page.locator('body').innerText().then(text => text.trim().length),
        { message: route },
      ).toBeGreaterThan(50)
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }))
      expect(layout.overflow, route).toBe(false)
    }
  })

  test('生产与演示功能显示明确成熟度', async ({ page }) => {
    await login(page)
    await page.goto('/data/new-datasets')
    await expect(page.locator('.header-left .el-tag')).toContainText('Production')
    await expect(page.locator('.maturity-alert')).toHaveCount(0)

    await page.goto('/model/gnn-training')
    await expect(page.locator('.header-left .el-tag')).toContainText('Demo')
    await expect(page.locator('.maturity-alert')).toContainText('不应用于生产决策')
  })
})
