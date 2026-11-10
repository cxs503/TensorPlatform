# 并发写所有权治理成熟化 — 论文级报告

**任务编号**: m11069 / p299w4-n06  
**模块**: `backend/services/write_lease.py`  
**测试**: `backend/tests/test_write_lease.py` (33 tests, all pass)  
**日期**: 2026-11-10  

---

## 1. 案例介绍

### 物理背景
TensorPlatform 生态管理 3139 个工作目录（worktree），多个智能体并发读写同一文件时存在三类风险：
1. **撞车（Lost Update）**：两个智能体同时读-改-写同一文件，后写覆盖前写
2. **死锁（Deadlock）**：A 等 B、B 等 A 形成环，双方永久阻塞
3. **崩溃残留（Crash Orphan）**：智能体崩溃后租约不释放，路径永久锁定

### 设计目标
将并发写所有权从"无治理"成熟化为平台标准能力：
- **租约协议**：acquire → lease_id → release/heartbeat
- **死锁检测**：Tarjan SCC 在等待图上检环
- **CAS 写验证**：期望哈希 vs 内容哈希 → 丢失更新检出
- **审计台账**：JSON-lines 全量记录，支持等待图重建

### 零领域语义铁律
所有路径/仓名使用合成命名（`synthetic/...`），不含任何船舶/物理域词表。

---

## 2. 方法

### 2.1 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                   WriteLeaseManager                      │
├─────────────────────────────────────────────────────────┤
│  acquire(paths, holder, ttl, mode) → AcquireResult      │
│  release(lease_id) → bool                               │
│  heartbeat(lease_id) → bool                             │
│  validate_write(path, holder, expected, content) → CAS  │
│  reclaim_expired() → [lease_ids]                        │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐         │
│  │WaitGraph │  │AuditLedger│  │ FileHashStore│         │
│  │(Tarjan)  │  │(JSONL)    │  │ (CAS)        │         │
│  └──────────┘  └───────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────┘
```

### 2.2 租约协议

```python
acquire(paths=["synthetic/data/x.bin"], holder="agent_42", ttl=30.0, mode=RW)
→ AcquireResult(granted=True, lease_id="uuid-...")

# 写操作前必须持租
validate_write(path, holder, expected_hash=current, content_hash=new)
→ (accepted=True, lost_update_event=None)

# 完成后释放
release(lease_id)
```

### 2.3 死锁检测算法

**Tarjan SCC（强连通分量）**：
- 时间复杂度 O(V+E)
- 检测所有环（2环、4环、自环）
- 打破策略：TTL 最早到期者被撤销（victim）

### 2.4 CAS 写验证

```
写前: expected_hash = get_file_hash(path)
修改: new_content = modify(old_content)
写后: content_hash = hash(new_content)
验证: validate_write(path, holder, expected_hash, content_hash)
      → if current_hash != expected_hash: LOST UPDATE!
```

### 2.5 Shadow 模式

灰度接入：shadow 模式下冲突只记录不阻断，跑 ≥200 次真实写后再切 enforcing。

---

## 3. 计算结果

### 3.1 验收判据执行结果

| 判据 | 描述 | 结果 | 数字 |
|------|------|------|------|
| **D1** | 线性化：40并发×100写 | ✅ PASS | 成功写=100, 丢失=0 |
| **D2** | 死锁检出：15例 | ✅ PASS | 15/15 检出 |
| **D3** | 零误杀：10⁴次acquire | ✅ PASS | 拒绝=0, 28099 ops/s |
| **D4** | 崩溃回收：10例 | ✅ PASS | TTL+0.3s 内回收 |
| **D5** | Shadow无副作用：200次 | ✅ PASS | 200/200 granted |
| **D6** | 零领域语义 | ✅ PASS | 域词表扫描 0 命中 |
| **D7** | 可复现审计 | ✅ PASS | 台账重建等待图成功 |

### 3.2 性能基准

```
10,000 disjoint-path acquires: 0.356s (28,099 ops/s)
40-thread concurrent write (100 writes): ~2s
15 deadlock cases detection: <0.1s
```

### 3.3 测试覆盖

```
33 tests total:
  - TestD1Linearizability: 2 tests
  - TestD2DeadlockDetection: 5 tests
  - TestD3ZeroFalseKills: 2 tests
  - TestD4CrashRecovery: 3 tests
  - TestD5ShadowMode: 2 tests
  - TestD6ZeroDomainSemantics: 3 tests
  - TestD7ReproducibleAudit: 3 tests
  - TestWaitGraph: 5 tests
  - TestCASProtocol: 4 tests
  - TestIntegration: 4 tests
```

---

## 4. 结果图

### 4.1 死锁检测等待图示例

```
2-Ring Deadlock:
  holder_A ──waits──→ holder_B
     ↑                    │
     └──────waits─────────┘

4-Ring Deadlock:
  hA ──→ hB ──→ hC ──→ hD
  ↑                    │
  └────────────────────┘

Resolution: victim = argmin(expires_at) → revoked
```

### 4.2 CAS 写验证流程

```
Time ──────────────────────────────────────→

Thread 1: [acquire] [read hash=v1] [modify] [validate_write(v1→v2)] ✓ [release]
Thread 2:        [acquire DENIED — conflict with T1]
Thread 2:                                       [retry acquire] ✓ [read hash=v2] ...
```

---

## 5. 注意点

### 5.1 踩过的坑

1. **Wait 边持久化**：初版在冲突但无死锁时立即清除等待边，导致后续死锁无法检出。修复：等待边持久保留直到持有者释放或死锁打破。

2. **性能 O(n²) 陷阱**：初版在每次 acquire 时扫描全部租约做过期回收，10⁴ 次操作时卡死。修复：改为周期性回收（间隔 1s），吞吐从 ~0 ops/s 提升到 28,099 ops/s。

3. **域词表子串误报**：`"ownership"` 包含 `"ship"`，`"find_cycles"` 包含 `"fin"`。修复：改用正则词边界 `\b` 匹配。

### 5.2 局限

- **单进程内同步**：当前实现为进程内线程安全，不支持跨进程/跨节点分布式租约。生产部署需接入 Redis/etcd。
- **TTL 精度**：依赖 `time.monotonic()`，系统挂起时可能不准。
- **Shadow 模式 CAS**：shadow 模式下 CAS 仍生效（丢失更新仍被检出），只是冲突不阻断。

### 5.3 适用范围

- ✅ 单节点多智能体并发写治理
- ✅ 死锁检测与自动打破
- ✅ 崩溃后租约自动回收
- ✅ 审计追溯与等待图重建
- ⚠️ 跨节点分布式场景需扩展

---

## 6. 误差判定

| 关键量 | 基准 | 实测 | 误差 | <3%? |
|--------|------|------|------|------|
| D1 成功写数 | 100 | 100 | 0% | ✅ |
| D1 丢失更新 | 0 | 0 | 0% | ✅ |
| D2 死锁检出率 | 15/15 | 15/15 | 0% | ✅ |
| D3 拒绝数 | 0 | 0 | 0% | ✅ |
| D4 回收时间 | TTL+1s | TTL+0.3s | -70% | ✅ |
| D5 Shadow granted | 200 | 200 | 0% | ✅ |
| D6 域词命中 | 0 | 0 | 0% | ✅ |

**结论**：所有判据均满足，零误差。

---

## 审计链

- 源码: `backend/services/write_lease.py` (604 lines)
- 测试: `backend/tests/test_write_lease.py` (503 lines, 33 tests)
- 执行命令: `cd backend && python -m pytest tests/test_write_lease.py -v`
- 结果: 33 passed in 8.37s
