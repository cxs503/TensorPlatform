# capability_evidence_chain — 跨仓验证工件 → 能力台账的零人工干预证据链

> 战役节点 `p474w1-n01-evidence-chain`（mission 13872）。本包把平台沿用的
> 「相对误差 < 15 % 即 PASS」点判据，替换为 **U 感知等价检验（TOST）+ 统计功效 +
> 最小可判效应（MDE）+ 历史 PASS 普查重分类**，并给出证据 → 证书 → 台账动作的
> 自动链路。

## 1. 解决什么问题

旧判据是**点判据**：一个数字小于阈值就 PASS。它有两个结构缺陷

1. **不携带不确定性**：单次运行与 20 次复算被同等对待；一个「恰好落在阈值内」的
   样本会永久 PASS，数据再多也无法推翻；
2. **不可判读灵敏度**：PASS 不说明该设计能否探测到阈值被违反——样本量 3 与样本量
   300 的 PASS 长得一样。

本包改用等价检验：H0 `|μ| ≥ margin` 对 H1 `|μ| < margin`，两个单侧检验同时拒绝
（= 双侧区间整体落入 margin）才判 EQUIVALENT；同时报告

* `power`（真实偏差取观测值时正确判等的概率）；
* `MDE`（在目标功效下**仍能被认证为等价的最大真实偏差**，等价检验语境的
  minimum detectable effect；MDE/margin → 1 表示证据足以支撑该 margin）。

## 2. 三段流水线

```
scanner.scan ──► binder.collect_samples / associate ──► binder.evaluate ──► pipeline.run
   跨仓工件            工件→能力条目关联率              TOST+power+MDE       工件+报告
```

| 环节 | 做什么 | 关键点 |
|---|---|---|
| `scanner` | 按 repo/glob 发现 JSON 工件，抽取**键名匹配声明式正则**的数值叶 | 记录每个文件的 SHA-256 与状态（`ok`/`invalid_json`/`read_error`/`too_large`）；不可读文件入账而非静默丢弃 |
| `binder` | 观测 → 能力条目样本（repo 作用域 + 值域过滤 + 确定性截断）；计算关联率 | 关联率 = 绑定文件 / 被识别文件；未绑定文件逐条给原因 |
| `equivalence` | 三种统计场景 | 均值（单/双样本/配对 t）、方差比（F 检验双区间）、分布（KS 距离自助区间包含） |
| `reclassify` | 历史 PASS/FAIL 普查 | 六类判定 + 可复算的标记率 + 摘要 digest |
| `pipeline` | 编排 + 判据自证 | `criteria` 块的布尔值全部由数字推导，不接受手填 |

## 3. 零领域语义（硬约束）

* 仓库名、glob、度量正则、margin、alpha、theta、seed、普查来源**全部来自 JSON 配置**；
* 包内与测试中不出现任何物理域词条；`pipeline.zero_domain_check` 用
  `tools/evidence_domain_lexicon.json` 做机械扫描（验收判据 f）；
* 测试夹具统一使用合成域名（`synth_solver_a` / `synth.*`）。

## 4. 用法

```bash
# 全链路（默认读 $TP_ECOSYSTEM_ROOT 下的求解器仓）
TP_ECOSYSTEM_ROOT=/path/to/ecosystem python3 -m tools.capability_evidence_chain.cli run \
    --config tools/capability_evidence_chain/configs/ecosystem_solvers.json \
    --out artifacts/p474w1_n01/evidence_chain.json \
    --package-dir tools/capability_evidence_chain \
    --lexicon tools/evidence_domain_lexicon.json

# 只读摘要
python3 -m tools.capability_evidence_chain.cli summary \
    --artifact artifacts/p474w1_n01/evidence_chain.json
```

节点验收脚本（生成工件、MDE 表、普查表、台账动作表、四张图与 acceptance.json）：

```bash
TP_ECOSYSTEM_ROOT=/path/to/ecosystem python3 scripts/run_p474w1_n01.py
```

## 5. 配置要点

```jsonc
{
  "root_env": "TP_ECOSYSTEM_ROOT",
  "repos": [{"name": "...", "path": "...", "include": ["benchmarks/**/*.json"],
             "exclude": ["**/.git/**"], "max_files": 5000}],
  "capabilities": [{
    "id": "...", "scenario": "mean|variance|distribution",
    "margin": 0.15, "alpha": 0.05, "theta": 2.0, "power_target": 0.8,
    "groups": {"a": {"repo": "...", "include": [...], "metric": "^(rel_error)$",
                     "value_range": [0.0, 10.0], "max_samples": 200}}
  }],
  "reclassification": {
    "sources": ["docs/**/*.json", "artifacts/**/*.json"],
    "base": ".",                              // 普查所在仓（证据仓之外）
    "exclude": ["artifacts/p474w1_n01/**"],   // 必须排除自身输出，否则自反馈膨胀
    "value_threshold_pairs": [["deviation_pct", "tolerance_pct"]]
  }
}
```

## 6. 复现性

* `seed` 固定（默认 20261111），分布场景的自助法可复现；
* 工件内记录配置 digest、扫描 digest、能力结果 digest、MDE 表 digest、普查 digest；
* 统计功效的解析式（均值/方差）在 `tests/test_equivalence.py` 中与蒙特卡洛对照，
  容差 4 个标准误。

## 7. 已知局限

1. 能力样本把不同 benchmark 案例合并，**不是**独立重复实验；区间是对「工件总体」
   的描述性陈述，不是对单次运行的推断。
2. 跨仓方差/分布条目是证据流之间的描述性比较，不是物理等价性声明。
3. `max_files` 与 `value_range` 限定总体，被排除数量逐条报告；文件上限一旦小于仓内
   JSON 数量会按字典序截断整段目录（已用「上限 > 全量」规避）。
4. 历史普查只能覆盖同时给出数值、阈值、样本量与离散度的声明；缺项者标
   `THRESHOLD_ONLY` / `UNVERIFIABLE`，不做插补。

## 8. 测试

```bash
python3 -m pytest tools/capability_evidence_chain/tests -q     # 71 例
```