# 示例组织AI赋能研发平台 - Vue 3 前端

基于 Vue 3 + TypeScript + Element Plus 构建的现代化前端应用。

## 技术栈

- **Vue 3** - 渐进式 JavaScript 框架
- **TypeScript** - 类型安全的 JavaScript 超集
- **Vite** - 下一代前端构建工具
- **Pinia** - Vue 状态管理
- **Vue Router** - 官方路由管理器
- **Element Plus** - Vue 3 组件库
- **ECharts** - 数据可视化图表库
- **Axios** - HTTP 客户端
- **Sass** - CSS 预处理器

## 项目结构

```
frontend-vue/
├── public/                 # 静态资源
├── src/
│   ├── api/               # API 接口
│   │   ├── index.ts      # API 模块导出
│   │   └── request.ts    # Axios 封装
│   ├── assets/           # 资源文件
│   │   └── styles/       # 样式文件
│   │       ├── main.scss
│   │       └── variables.scss
│   ├── components/       # 公共组件
│   ├── layouts/          # 布局组件
│   │   └── MainLayout.vue
│   ├── router/           # 路由配置
│   │   └── index.ts
│   ├── stores/           # 状态管理
│   │   ├── app.ts
│   │   ├── data.ts
│   │   └── user.ts
│   ├── types/            # 类型定义
│   │   └── index.ts
│   ├── views/            # 页面组件
│   │   ├── data/         # 数据管理
│   │   ├── training/     # 模型训练
│   │   ├── deploy/       # 模型部署
│   │   ├── apps/         # 应用场景
│   │   ├── system/       # 系统管理
│   │   ├── Dashboard.vue
│   │   └── Login.vue
│   ├── App.vue
│   ├── main.ts
│   └── vite-env.d.ts
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.node.json
└── vite.config.ts
```

## 功能模块

### 1. 数据管理
- **虚拟数据生成** - 生成模拟数据用于模型训练
- **生产数据接入** - 连接生产环境数据源
- **数据治理** - 元数据管理、数据血缘
- **数据集管理** - 数据集版本控制和标注

### 2. 模型训练
- **Jupyter Lab** - 在线 Python 开发环境
- **训练任务** - 管理模型训练任务
- **预设模型** - 预置模型模板
- **超参优化** - 自动化超参数调优
- **模型管理** - 模型版本管理和部署

### 3. 模型部署
- **部署管理** - 模型部署和版本控制
- **推理服务** - 在线推理测试

### 4. 应用场景
- **光伏预测** - 光伏发电功率预测
- **储能优化** - 储能系统调度优化
- **故障预测** - 设备故障预测预警
- **能耗分析** - 企业能耗监控分析
- **碳排放** - 碳排放管理和交易
- **负荷预测** - 电力负荷预测
- **智能控制** - AI 智能控制系统
- **数字孪生** - 3D 可视化数字孪生

### 5. 系统管理
- **监控告警** - 系统监控和告警管理
- **工单管理** - 工单创建和处理
- **报表管理** - 业务报表生成
- **系统设置** - 系统配置和个人设置

## 快速开始

### 安装依赖

```bash
cd frontend-vue
npm install
```

### 开发模式

```bash
npm run dev
```

访问 http://localhost:3000

### 构建生产版本

```bash
npm run build
```

### 预览生产构建

```bash
npm run preview
```

## 环境变量

创建 `.env` 文件配置环境变量：

```env
VITE_API_BASE_URL=/api/v1
VITE_APP_TITLE=示例组织AI赋能研发平台
```

## API 代理配置

开发环境代理配置（vite.config.ts）：

```typescript
server: {
  port: 3000,
  proxy: {
    '/api': {
      target: 'http://localhost:8001',
      changeOrigin: true
    },
    '/jupyter': {
      target: 'http://localhost:8888',
      changeOrigin: true
    }
  }
}
```

## 主题定制

修改 `src/assets/styles/variables.scss` 自定义主题：

```scss
$primary-color: #1e40af;
$primary-light: #3b82f6;
$bg-dark: #0f172a;
$bg-card: #1e293b;
// ...
```

## 浏览器支持

- Chrome >= 87
- Firefox >= 78
- Safari >= 14
- Edge >= 88

## 许可证

Copyright © 2024 示例组织科技有限责任公司
