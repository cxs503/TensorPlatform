# tools/capability_audit — 能力审计自动化 + 防造假锚链

把平台“某个能力是 REAL / MOCK / STUB”这类**自述**结论，升级为可复跑、可证伪、
不可事后篡改的机器审计。四个子命令：

| 子命令 | 作用 |
|---|---|
| `audit` | 对 :8001 的能力端点跑四族探针（输入扰动不变性 / 因果敏感性 / 解析解对拍 / 单调性三刀），产出 `audit_results.json` |
| `report` | 由审计结果程序化生成 Markdown 报告、5 张图与 `criteria.json`（验收数字 + 产物路径） |
| `anchor` | 对机器证据构 Merkle 树 → Ed25519 签名 → 附 git commit 时间锚，产出 `anchor.json` |
| `verify` | 重新哈希工件、重算 Merkle 根、验签、核对 commit 内容与锚链，产出 `verification.json` |

## 为什么四族探针

单一“输出看起来合理”不足以证明算法真实存在。四族探针覆盖四种不同的造假方式：

1. **输入扰动不变性**——语义等价输入（重复调用、JSON 键序、单位等价、Froude 相似、
   批/单请求切片、方法声明的速度无关性）必须给出同一答案。抓“随机/不稳定/口径不一致”。
2. **因果敏感性**——物理上必然影响结果的参数（舵角、螺距比、KG、航速）必须把输出推过阈值。
   抓“硬编码常数”（`/maneuvering/turning` 就是这样被击穿的）。
3. **解析解对拍**——每条闭式解独立转写在 `oracle.py`（ITTC-1957、Fr/Re/P_E 恒等式、
   壁面式 KB/BM/GM、作动盘理想效率、深水色散），并注明出处与容差。
4. **单调性三刀**——沿三条独立参数轴扫描，符号由物理锁定，并带最小步长门限，
   防止“几乎不变”被算作单调。

## 用法

```bash
# 1) 审计（需要 :8001 在线；不可达时能力一律 WITHHELD，不静默判过）
python3 -m tools.capability_audit.cli audit  --out artifacts/capability_audit
# 2) 报告（图 + criteria.json）
python3 -m tools.capability_audit.cli report --out artifacts/capability_audit
# 3) 先把机器证据提交，再锚定该提交（外部时间锚）
git add artifacts/capability_audit && git commit -m "audit artifacts"
python3 -m tools.capability_audit.cli anchor --out artifacts/capability_audit
# 4) 独立复核
python3 -m tools.capability_audit.cli verify --out artifacts/capability_audit
# 5) 把锚信息写回报告（复用已锚定的图，避免改动 Merkle 根）
python3 -m tools.capability_audit.cli report --out artifacts/capability_audit --reuse-figures
```

只审计部分能力：`audit --only calc.resistance,calc.stability`。
CI 里需要“失败即红”：`audit --strict`（存在 fatal 探针失败时退出码 1）。

## 判定规则

* 任一 fatal 探针失败 ⇒ `observed_level = MOCK`；若原声称 REAL ⇒ `overclaim = true`。
* 端点不可达 / 无不 comparable 字段 ⇒ `WITHHELD`（**不是**通过）。
* 非 fatal 探针（标 `information-only`）失败只记录，不改变判定。

## 锚链设计

```
工件(audit_results.json + figures/*.png)
  → leaf = SHA256(0x00‖canonical_json{path,sha256,bytes})
  → 内部节点 = SHA256(0x01‖left‖right)            (RFC 6962 域分隔)
  → Merkle root
  → Ed25519 签名(payload = root + 工件清单 + git commit + prev_anchor)
  → 外部时间锚 = 构建时 HEAD 提交（SHA + committer date，内容逐字节比对）
  → 链式 prev_anchor.anchor_sha256（append-only）
```

注意：`report.md` 与 `criteria.json` 是被锚定证据的**派生视图**，故不进入 Merkle 根
（否则形成“报告描述锚、锚覆盖报告”的不动点）；`verify` 用 `criteria_consistency`
复核派生计数与锚定结果一致。完整性边界见报告第 6 节。

## 测试

```bash
python3 -m pytest tools/capability_audit/tests/test_capability_audit.py -q
```

35 个用例覆盖：RFC 8032 三组签名的字节级一致 + 篡改负控、Merkle 各种树高的存在性证明、
探针引擎在一套“诚实 API”上判 REAL 与在一套“硬编码 API”上判 MOCK、
审计→报告→锚定→复核全链路、以及三类篡改（工件、签名、派生计数）都必须被检出。