# 示例组织平台 - 代码规范指南

## Git 规范

### 分支命名
- `main` - 主分支（稳定版本）
- `develop` - 开发分支
- `feature/*` - 功能分支
- `fix/*` - Bug 修复分支
- `release/*` - 发布分支

### 提交规范
```
<type>: <subject>

<body>

<footer>
```

**Type:**
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档修改
- `style`: 代码格式（不影响代码运行）
- `refactor`: 重构
- `perf`: 性能优化
- `test`: 测试
- `chore`: 构建过程或辅助工具变动

**示例:**
```
feat: 添加用户认证模块

- 实现 JWT 认证
- 添加 RBAC 权限控制
- 支持多角色管理

Closes #123
```

---

## Python 代码规范

### 命名规则
- **类名**: `PascalCase` (如 `UserService`)
- **函数/变量**: `snake_case` (如 `get_user`)
- **常量**: `UPPER_CASE` (如 `MAX_RETRY`)
- **私有**: `_private_method`

### 导入顺序
```python
# 1. 标准库
import os
import sys

# 2. 第三方库
from fastapi import FastAPI
from sqlalchemy import create_engine

# 3. 本地模块
from database.config import get_db
from models.user import User
```

### 类型注解
```python
from typing import Optional, List

def get_user(user_id: int) -> Optional[User]:
    """获取用户信息"""
    pass

def list_users(skip: int = 0, limit: int = 100) -> List[User]:
    """列出用户"""
    pass
```

### 文档字符串
```python
def calculate_energy(
    power: float,
    duration_hours: float
) -> float:
    """
    计算能耗

    Args:
        power: 功率 (kW)
        duration_hours: 持续时间 (小时)

    Returns:
        能耗 (kWh)

    Raises:
        ValueError: 参数为负数时
    """
    if power < 0 or duration_hours < 0:
        raise ValueError("参数不能为负数")
    return power * duration_hours
```

---

## Vue 代码规范

### 组件命名
- **单文件组件**: `PascalCase.vue` (如 `UserList.vue`)
- **模板变量**: `camelCase` (如 `userName`)
- **事件处理**: `handleClick`

### Props 定义
```vue
<script setup lang="ts">
defineProps<{
  userId: number;
  userName?: string;
  isActive?: boolean;
}>()
</script>
```

### Emits 定义
```vue
<script setup lang="ts">
const emit = defineEmits<{
  (e: 'update', data: User): void;
  (e: 'delete', id: number): void;
}>()
</script>
```

---

## 数据库规范

### 表命名
- 使用蛇形命名: `user`, `user_profile`, `data_standard`

### 字段命名
- 使用蛇形命名: `user_id`, `created_at`, `is_active`

### 索引规范
- 主键自动创建索引
- 外键字段创建索引
- 高频查询字段添加索引

```sql
CREATE INDEX idx_user_email ON users(email);
CREATE INDEX idx_projects_status ON projects(status);
```

---

## API 设计规范

### 路径设计
```
GET    /api/v1/users              # 列出用户
POST   /api/v1/users              # 创建用户
GET    /api/v1/users/{id}         # 获取用户
PUT    /api/v1/users/{id}         # 更新用户
DELETE /api/v1/users/{id}         # 删除用户

GET    /api/v1/projects/{id}/models   # 项目下的模型列表
POST   /api/v1/models/{id}/train      # 训练模型
```

### 响应格式
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 1,
    "name": "example"
  }
}
```

### 错误码
| Code | Message | Description |
|------|---------|-------------|
| 200 | success | 成功 |
| 400 | bad_request | 请求参数错误 |
| 401 | unauthorized | 未认证 |
| 403 | forbidden | 禁止访问 |
| 404 | not_found | 资源不存在 |
| 500 | internal_error | 服务器错误 |

---

## 测试规范

### 测试结构
```
backend/tests/
├── __init__.py
├── test_database.py   # 数据库测试
├── test_auth.py       # 认证测试
├── test_api.py        # API 测试
└── test_services.py   # 服务层测试
```

### 测试原则
- **独立性**: 每个测试用例独立运行
- **可重复性**: 测试结果一致
- **全覆盖**: 关键业务逻辑全覆盖
- **易读性**: 测试名称清晰表达意图

### 测试命名
- 测试类: `Test + 类名` (如 `TestUserService`)
- 测试方法: `test_ + 功能描述` (如 `test_user_creation`)

### 覆盖率要求
- 代码覆盖率 ≥ 80%
- 关键业务逻辑 ≥ 90%
- 新增代码必须有测试

---

## 类型检查规范 (mypy)

### 类型注解要求
- 所有公共 API 必须有类型注解
- 函数参数和返回值必须标注类型
- 使用 `Optional` 表示可能为 None 的值

```python
from typing import Optional, List, Dict

def get_user(user_id: int) -> Optional[User]:
    """获取用户，不存在返回 None"""
    ...

def list_users(limit: int = 100) -> List[User]:
    """列出用户列表"""
    ...
```

### 配置文件
- 在 `backend/mypy.ini` 中配置类型检查

---

## 安全规范

### 敏感信息
- ❌ 不要硬编码密码/密钥
- ✅ 使用环境变量
- ✅ 使用 `.env` 文件（需加入 `.gitignore`）

### SQL 注入防护
```python
# ❌ 不安全
query = f"SELECT * FROM users WHERE id = {user_id}"

# ✅ 安全
from sqlalchemy import text
result = db.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})
```

### XSS 防护
- ✅ 使用 `v-html` 时先过滤
- ✅ 后端对输出进行转义

### 认证安全
- JWT 密钥必须使用强随机字符串
- 令牌设置合理的过期时间
- 敏感操作需要二次验证

---

## 性能优化

### 数据库
- 使用索引
- 避免 N+1 查询
- 分页查询大数据集
- 使用连接池

### API
- 使用连接池
- 实现缓存机制
- 异步处理耗时任务
- 压缩大响应

### 前端
- 懒加载组件
- 虚拟滚动大列表
- 图片懒加载
- 代码分割

---

## 文档规范

### API 文档
- FastAPI 自动生成 Swagger 文档 (`/docs`)
- 每个端点添加详细说明
- 添加参数描述和示例
- 标注已废弃的端点

### 代码注释
- 函数/类添加文档字符串
- 复杂逻辑添加行内注释
- TODO/FIXME 标记待办事项
- 注释保持更新

### README
- 项目简介
- 环境要求
- 快速开始
- API 文档链接
- 贡献指南

---

## 日志规范

### 日志级别
- `DEBUG`: 开发调试
- `INFO`: 一般信息
- `WARNING`: 警告信息
- `ERROR`: 错误信息
- `CRITICAL`: 严重错误

### 日志格式
```python
import logging

logger = logging.getLogger(__name__)

def example():
    logger.info("Processing request for user %s", user_id)
    try:
        # 处理逻辑
        pass
    except Exception as e:
        logger.error("Failed to process request: %s", str(e), exc_info=True)
```

---

## 代码提交规范

### 提交信息格式
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Type:**
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档修改
- `style`: 代码格式
- `refactor`: 重构
- `perf`: 性能优化
- `test`: 测试
- `chore`: 构建改动

**示例:**
```
feat(auth): 添加 JWT 认证支持

- 实现 access token 生成
- 添加 token 验证中间件
- 支持 refresh token 刷新

Closes #123
```
