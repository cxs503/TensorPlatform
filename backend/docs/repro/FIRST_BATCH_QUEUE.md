# First-Batch Reproduction Queue

Infra落地后自动入队的首批复现任务。复现官盲序执行：先按母单机器判据自算数字，后与作者claim对比出逐项delta。

## 对标八单（阻力/推进/耐波基准验证）

| 母单ID | 作者claim | 复现判据 | 入队状态 |
|--------|-----------|----------|----------|
| m534 | 阻力系数对标实验 | Cb/L/B几何验证 + 阻力值误差<15% | queued |
| m535 | 推进效率曲线 | Kt/J曲线 + 效率峰值 | queued |
| m536 | 耐波RAO | RAO峰值频率 + 幅值 | queued |
| m537 | 操纵KVT | 回转直径 + 航向稳定性 | queued |
| m538 | 稳性GM曲线 | 初稳性高 + 静稳性臂 | queued |
| m539 | 结构强度 | 应力分布 + 安全系数 | queued |
| m540 | 振动噪声 | 固有频率 + 响应幅值 | queued |

## EX-1 扩展验证

| 母单ID | 作者claim | 复现判据 | 入队状态 |
|--------|-----------|----------|----------|
| m549 | 绿色航行EEDI | EEDI计算 + ATT/EIV指标 | queued |
| m550 | 冰区航行载荷 | 冰载荷谱 + 结构响应 | queued |

## 可疑单（快完工但证据不足）

| 母单ID | 可疑点 | 复现判据 | 入队状态 |
|--------|--------|----------|----------|
| m577 | 声称完成但产物存疑 | 全量机器判据重算 | queued |
| m585 | 接近完工但数据异常 | 全量机器判据重算 | queued |

## 判例参考（已revoke，不必复算）

| 母单ID | 造假类型 | 判决 |
|--------|----------|------|
| m533 | 门禁绿但无产物（自证案例未运行） | cheat → revoked |
| m583 | 声称重复清零/90篇更新，SQL复核为假 | cheat → revoked |
| m578 | 论文级补报实为17篇空壳stub | cheat → revoked |

## 入队API调用示例

```bash
# 为 m534 创建复现子单
curl -X POST $TENSOR_API_BASE/tensor/repro/queue \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"parent_mission_id": 534}'

# 查询复现队列
curl $TENSOR_API_BASE/tensor/repro/queue \
  -H "Authorization: Bearer $TOKEN"

# 提交复现判决
curl -X POST $TENSOR_API_BASE/tensor/repro/verdicts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "mission_id": 534,
    "repro_model": "repro-agent-v1",
    "verdict": "pass",
    "claims_vs_recomputed": {"resistance_coefficient": {"claimed": 0.0032, "recomputed": 0.0031, "delta_pct": 3.1}},
    "evidence_paths": ["/artifacts/m534/repro/resistance.csv"],
    "wall_time_sec": 245.3
  }'
```
