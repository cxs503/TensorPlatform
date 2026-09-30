# 开放数字化底座测试覆盖率提升计划

## 目标：从 1% 提升到 100%

### 📊 当前状态
| 模块 | 文件数 | 行数 | 覆盖率 |
|------|--------|------|--------|
| tests/ | 3 | ~15,000行 | 25个测试 (100%通过) |
| backend/ | 181 | ~35,000行 | 1% |
| services/ | 88 | ~27,000行 | 0% |
| api/ | 93 | ~8,000行 | 0% |

---

## 阶段一：基础框架完善 (1-2周)

### ✅ 已完成
- [x] pytest 框架配置
- [x] 基础测试文件
- [x] 测试依赖安装

### 📋 待完成
- [ ] 覆盖率报告自动化
- [ ] CI/CD 整合
- [ ] 测试数据 fixture 定义

---

## 阶段二：核心服务层测试 (3-4周)

### 优先级 P0 (必须完成，2周)

| 服务 | 行数 | 测试重点 |
|------|------|----------|
| **data_query_service.py** (461行) | 461 | 数据库查询、API测试 |
| **auth_service.py** (347行) | 347 | 认证、授权测试 |
| **password_service.py** (假设) | - | 密码加密、验证 |
| **cache_service.py** (434行) | 434 | Redis 缓存操作 |

### 优先级 P1 (重要，1周)

| 服务 | 行数 | 测试重点 |
|------|------|----------|
| **photovoltaic_service.py** | - | 光伏数据查询 |
| **storage_service.py** | - | 储能数据查询 |
| **load_forecasting_service.py** | - | 负荷预测 |
| **carbon_service.py** | - | 碳排放计算 |

### 优先级 P2 (其他服务，1周)

- [ ] `abtest_service.py` (433行)
- [ ] `alert_service.py` (380行)
- [ ] `api_key_service.py` (225行)
- [ ] `audit_service.py` (279行)
- [ ] `backup_service.py` (422行)
- [ ] `cicd_service.py` (335行)
- [ ] `config_center_service.py` (424行)
- [ ] `data_catalog_service.py` (304行)
- [ ] `data_lineage_service.py` (1329行 - 大文件)
- [ ] `data_security_service.py` (585行)

---

## 阶段三：API 层测试 (2-3周)

### 优先级 P0
- [ ] `api/data_query.py` - 数据查询 API
- [ ] `api/auth.py` - 认证 API
- [ ] `api/photovoltaic.py` - 光伏 API
- [ ] `api/storage.py` - 储能 API

### 优先级 P1
- [ ] `api/projects.py` - 项目管理
- [ ] `api/models.py` - 模型管理
- [ ] `api/users.py` - 用户管理

### 优先级 P2
- [ ] 其他 85 个 API 文件

---

## 阶段四：集成测试 (2周)

- [ ] E2E 测试框架搭建
- [ ] 完整流程测试
- [ ] 异常场景测试
- [ ] 性能回归测试

---

## 阶段五：自动化与优化 (1周)

- [ ] CI/CD 整合
- [ ] 覆盖率 Badge
- [ ] 自动测试报告
- [ ] 监控告警

---

## 🎯 里程碑目标

| 时间 | 目标覆盖率 | 说明 |
|------|-----------|------|
| 第1周 | 10% | P0 服务测试完成 |
| 第3周 | 30% | P0+P1 服务测试完成 |
| 第5周 | 50% | 核心模块 100% |
| 第7周 | 70% | 完成 80%+
| 第9周 | 90% | 仅剩边缘场景
| 第10周 | 100% | 完成所有测试 |

---

## 📝 测试编写规范

### 1. 文件命名
```
test_<module_name>.py
```

### 2. 测试类型
- **单元测试**: `test_<function>`.
- **集成测试**: `test_<module>_integration`.
- **E2E测试**: `test_<flow>_end_to_end`.

### 3. 依赖注入
```python
@pytest.fixture
def db_session():
    # 数据库会话
    pass

@pytest.fixture
def auth_client():
    # 认证客户端
    pass
```

### 4. Mock 策略
- 外部服务: `@patch`
- 数据库: `monkeypatch`
- 时间: `freezegun`

---

## 🔧 自动化脚本

### 生成测试骨架
```bash
# 为指定服务生成测试文件
python scripts/generate_test_skeleton.py <service_name>
```

### 覆盖率检查
```bash
# 检查未覆盖代码
coverage report --show-missing
```

### CI 阶段
```yaml
# .github/workflows/ci.yml
- name: Run tests
  run: make test

- name: Check coverage
  run: make coverage-check
```

---

## 📈 当前测试覆盖明细

### 已测试 (1%)
- ✅ `backend/tests/test_api.py` (4个测试)
- ✅ `backend/tests/test_auth.py` (8个测试)
- ✅ `backend/tests/test_database.py` (13个测试)

### 未测试 (99%)
- ⚠️ `backend/services/*.py` (88个文件)
- ⚠️ `backend/api/*.py` (93个文件)
- ⚠️ `backend/utils/*.py` (部分)

---

## ⚡ 快速提升策略

### 方案 A: 聚焦核心模块 (推荐)
优先测试用户高频使用的模块：
1. 数据查询服务 (photovoltaic, storage, carbon)
2. 认证授权服务
3. API 端点

**预计2周可达 20% 覆盖率**

### 方案 B: 分模块逐步覆盖
按优先级 P0→P1→P2 顺序逐个模块完成

**预计5周可达 50% 覆盖率**

### 方案 C: 仅覆盖业务逻辑 (折中)
跳过数据库访问层，仅测试业务逻辑函数

**预计3周可达 40% 覆盖率**

---

## 💡 建议

由于 100% 覆盖率对大型项目不现实（测试维护成本过高），建议：

1. **核心业务 80%+** - 用户实际使用的功能
2. **边缘场景 50%+** - 不常用但需要的模块
3. **测试数据生成** - Mock 数据替代真实数据库
4. **E2E测试** - 替代部分单元测试

---

## 🚀 立即可执行

### 今天任务
```bash
# 1. 查看当前覆盖率详情
cd /root/open_digital_foundation/open-digital-foundation/backend
coverage report --show-missing | grep -v " 0%"

# 2. 生成 HTML 报告
coverage html

# 3. 查看报告
firefox htmlcov/index.html
```

### 本周任务
```bash
# 1. 编写 5 个核心测试
# 2. 查看覆盖率变化
make coverage

# 3. 写入内存记录
# 记录：2026-03-09 测试覆盖率提升计划制定完成
```

---

**计划制定时间**: 2026-03-09 21:16 UTC
**计划版本**: v1.0
**状态**: ⏳ 待执行
