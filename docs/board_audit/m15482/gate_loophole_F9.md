# maturity_gate 机器门漏洞（m15482 审计发现 F9）

审计日期：2026-11-12 · 审计单 m15482 · 波及：`/root/tp-agent/maturity_gate.py`（活体门，30min 周期）

## 现象

审计者按纪律在 `evidence.integration_callers` 如实写“无调用方”，形如：

```
"integration_callers": "none: grep 空，harness 未接入平台"
```

门函数

```python
def callers_verified(callers, repo):
    if not callers:
        return False
    c = str(callers[0]).split(":")[0]        # -> "none"
    r = subprocess.run(f"grep -rl --include='*.py' -F {json.dumps(c[:60])} {ECO}/{repo} ...")
    return bool(r.stdout.strip())
```

取第一段 `none` 去 `grep -F none`，在 TensorPlatform 里必然命中（例如 `agent_work/*.py`），于是返回 **True**。

## 复现（本单实测）

```
>>> mg.callers_verified(["none: 平台侧无调用方"], "TensorPlatform")
True
$ grep -rl --include='*.py' -F none /root/tensor-ecosystem/TensorPlatform | head -2
/root/tensor-ecosystem/TensorPlatform/agent_work/m15032/write_verdict.py
/root/tensor-ecosystem/TensorPlatform/agent_work/m15146_write_verdict.py
```

推论：**如实申报“无调用方”反而让门判 L3/L4 通过**；诚实审计被机制惩罚，虚标反被奖励。
这正是“测试绿≠问题解决”的门级同构问题：门检查的是“字符串能否 grep 到”，不是“是否有真实调用路径”。

## 建议修复（最小、可验证）

```python
NONE_PATS = ("none", "n/a", "na", "null", "-", "无", "空")
EXCLUDE_PAT = r"(?:^|/)(agent_work|docs|tests|artifacts|runs|scripts)/"

def callers_verified(callers, repo):
    if not callers:
        return False
    c = str(callers[0]).strip().split(":")[0].strip()
    if not c or c.lower() in NONE_PATS:          # 明确“无” → 直接 False
        return False
    r = subprocess.run(
        f"grep -rl --include='*.py' -F {json.dumps(c[:80])} {ECO}/{repo} 2>/dev/null "
        f"| grep -v -E {json.dumps(EXCLUDE_PAT)} | head -1",
        shell=True, capture_output=True, text=True)
    return bool(r.stdout.strip())
```

配套建议：

1. 门自身纳入版本管理（当前 `/root/tp-agent/maturity_gate.py` 不在 TensorPlatform 仓内，改不了、审不了、回滚不了）；
2. 加一条 pytest 契约测试：`callers_verified(["none: …"]) is False`、`callers_verified(["backend/api/x.py callers"])` 在真实引用存在时为 True；
3. 与 `semantic_gate` 三问对齐：门的判据应是“真实调用路径存在”，不是“字符串被 grep 到”。

## 影响面声明

本单**未**利用该漏洞：m15482 verdict 自报 **L2**（而非门会放行的 L3/L4），evidence 里 `integration_callers` 如实写“none”。
本漏洞可能已使历史上部分 verdict 被门高估一级——建议 owner/仲裁人按本复现脚本对 11-11 之后 confirmed 的 L3/L4 verdict 做一次抽检。