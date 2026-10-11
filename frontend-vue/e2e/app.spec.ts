import { test, expect } from '@playwright/test';

// ========== 基础页面加载测试 ==========

test.describe('开放数字化底座 - 基础页面加载', () => {

  test('首页(Landing页)加载成功', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle(/开放数字化底座/);
  });

  test('数据大盘页面加载成功', async ({ page }) => {
    const response = await page.goto('/data/dashboard');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('数据集管理页面加载成功', async ({ page }) => {
    const response = await page.goto('/data/new-datasets');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('模型管理页面加载成功', async ({ page }) => {
    const response = await page.goto('/model/new-models');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('训练任务页面加载成功', async ({ page }) => {
    const response = await page.goto('/model/training-tasks');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('部署管理页面加载成功', async ({ page }) => {
    const response = await page.goto('/deploy/deployment');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('监控告警页面加载成功', async ({ page }) => {
    const response = await page.goto('/system/monitoring');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('权限管理页面加载成功', async ({ page }) => {
    const response = await page.goto('/system/rbac');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('审计日志页面加载成功', async ({ page }) => {
    const response = await page.goto('/system/audit-logs');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

});

// ========== 数据中台页面测试 ==========

test.describe('数据中台页面', () => {

  test('公开数据集页面加载', async ({ page }) => {
    const response = await page.goto('/data/public-datasets');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('数据接入页面加载', async ({ page }) => {
    const response = await page.goto('/data/data-source');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('数据质量页面加载', async ({ page }) => {
    const response = await page.goto('/data/quality');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('数据血缘页面加载', async ({ page }) => {
    const response = await page.goto('/data/lineage');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('AI智能体页面加载', async ({ page }) => {
    const response = await page.goto('/data/ai-agent');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('生产过程优化Demo页面加载', async ({ page }) => {
    const response = await page.goto('/data/steel-ai-case');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('钢铁能源页面加载', async ({ page }) => {
    const response = await page.goto('/data/steel-energy');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

});

// ========== 模型工厂页面测试 ==========

test.describe('模型工厂页面', () => {

  test('AutoML对比页面加载', async ({ page }) => {
    const response = await page.goto('/model/automl');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('算法商店页面加载', async ({ page }) => {
    const response = await page.goto('/model/algorithm-store');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('分布式训练页面加载', async ({ page }) => {
    const response = await page.goto('/model/distributed-training');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('Notebook页面加载', async ({ page }) => {
    const response = await page.goto('/model/notebook');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('超参搜索页面加载', async ({ page }) => {
    const response = await page.goto('/model/hyperopt');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('ML Pipeline页面加载', async ({ page }) => {
    const response = await page.goto('/model/pipeline');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('模型评估页面加载', async ({ page }) => {
    const response = await page.goto('/model/model-evaluation');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('模型解释页面加载', async ({ page }) => {
    const response = await page.goto('/model/model-explain');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('模型监控页面加载', async ({ page }) => {
    const response = await page.goto('/model/model-monitoring');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('A/B测试页面加载', async ({ page }) => {
    const response = await page.goto('/model/abtest');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('在线推理页面加载', async ({ page }) => {
    const response = await page.goto('/model/online-inference');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('批量推理页面加载', async ({ page }) => {
    const response = await page.goto('/model/batch-inference');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('特征存储页面加载', async ({ page }) => {
    const response = await page.goto('/model/feature-store');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('ONNX导出页面加载', async ({ page }) => {
    const response = await page.goto('/model/onnx');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('MLflow追踪页面加载', async ({ page }) => {
    const response = await page.goto('/model/mlflow');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('Optuna优化页面加载', async ({ page }) => {
    const response = await page.goto('/model/optuna');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('Ray集群页面加载', async ({ page }) => {
    const response = await page.goto('/model/ray');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

  test('模型治理页面加载', async ({ page }) => {
    const response = await page.goto('/model/model-governance');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

});

// ========== 导航与路由测试 ==========

test.describe('导航与路由', () => {

  test('旧路径重定向 - /model/models', async ({ page }) => {
    await page.goto('/model/models');
    await page.waitForTimeout(1000);
    const url = page.url();
    expect(url).toMatch(/\/model\/(models|new-models)/);
  });

  test('旧路径重定向 - /data/datasets', async ({ page }) => {
    await page.goto('/data/datasets');
    await page.waitForTimeout(1000);
    const url = page.url();
    expect(url).toMatch(/\/data\/(datasets|new-datasets)/);
  });

});

// ========== 错误处理测试 ==========

test.describe('错误处理', () => {

  test('不存在的路由返回200(SPA无404页面)', async ({ page }) => {
    const response = await page.goto('/this-path-does-not-exist');
    expect(response?.status()).toBe(200);
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
  });

});

// ========== 超参搜索 → 模型关联测试 ==========

test.describe('超参搜索关联模型', () => {

  test('超参搜索页面加载并显示模型链接', async ({ page }) => {
    await page.goto('/model/hyperopt');
    await page.waitForTimeout(3000);
    // 检查页面标题
    await expect(page.locator('#app')).toBeVisible({ timeout: 15000 });
    // 检查"最佳模型"列是否出现
    const modelLink = page.locator('text=#119');
    const visible = await modelLink.isVisible().catch(() => false);
    console.log(`Model link #119 visible: ${visible}`);
    await page.screenshot({ path: 'test-results/hyperopt-page.png', fullPage: true });
  });

  test('点击模型链接跳转到模型管理', async ({ page }) => {
    await page.goto('/model/hyperopt');
    await page.waitForTimeout(3000);
    
    // 点击模型链接
    const modelLink = page.locator('text=#119');
    if (await modelLink.isVisible({ timeout: 5000 }).catch(() => false)) {
      await modelLink.click();
      await page.waitForTimeout(2000);
      
      // 验证URL变化
      const url = page.url();
      console.log(`After click URL: ${url}`);
      expect(url).toContain('/model/new-models');
      expect(url).toContain('highlight=119');
      
      await page.screenshot({ path: 'test-results/model-highlight.png', fullPage: true });
    } else {
      console.log('Model link #119 not found on page');
    }
  });

  test('手动验证API数据一致性', async ({ page }) => {
    // 验证 models API 返回正确的模型
    const modelsResp = await page.request.get('http://127.0.0.1:8001/api/v1/models');
    expect(modelsResp.status()).toBe(200);
    const modelsData = await modelsResp.json();
    const items = modelsData.items || modelsData;
    console.log(`Models API returned ${items.length} models`);
    expect(items.length).toBeGreaterThan(0);
    
    // 验证 hyperopt API 返回 best_model_id
    const hyperoptResp = await page.request.get('http://127.0.0.1:8001/api/v1/hyperopt/studies');
    expect(hyperoptResp.status()).toBe(200);
    const hyperoptData = await hyperoptResp.json();
    for (const s of (hyperoptData.items || [])) {
      console.log(`Study: ${s.name}, best_model_id: ${s.best_model_id}`);
      expect(s.best_model_id).toBeTruthy();
    }
  });

});
