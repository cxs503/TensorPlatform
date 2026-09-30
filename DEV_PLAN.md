# 开放数字化底座 — 补充开发实施计划

## 总体目标
将平台从原型阶段推进到可用的 MVP 阶段，实现核心业务闭环。

## Phase 0: 数据库基础 (P0)
- [ ] 创建所有 ORM 模型 (用户/项目/模型/训练/设备/碳排放/光伏等)
- [ ] 配置 Alembic 迁移
- [ ] 创建数据库表并验证

## Phase 1: 注册核心 API (P0)
- [ ] 按业务模块分组注册 82 个未注册的 API 路由
- [ ] 修复导入错误
- [ ] 验证所有路由可访问

## Phase 2: 服务层数据库化 (P1)
- [ ] carbon_service → SQLAlchemy
- [ ] photovoltaic_service → SQLAlchemy
- [ ] energy_efficiency_service → SQLAlchemy
- [ ] energy_analysis_service → SQLAlchemy

## Phase 3: 前端增强 (P1)
- [ ] 添加 API 调用层
- [ ] 添加加载/错误状态
- [ ] 连接真实数据端点
