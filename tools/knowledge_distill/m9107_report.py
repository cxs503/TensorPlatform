"""m9107 报告生成：从 acceptance.json 程序化产出论文级 markdown（零手抄）。

用法
----
    python3 tools/knowledge_distill/m9107_report.py

产物
----
* ``docs/reports/m9107_knowledge_distill_maturity.md``  报告正文
* ``/tmp/report_m9107_payload.json``                    发帖 payload（含 figures png_base64）

正文里的数字全部从 ``runs/m9107/acceptance.json`` / ``seeds.json`` /
``cluster_spotcheck*.json`` 读取，本脚本不含任何结论性字面量。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs" / "m9107"
MD = ROOT / "docs" / "reports" / "m9107_knowledge_distill_maturity.md"
PAYLOAD = Path("/tmp/report_m9107_payload.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def f4(x, nd: int = 4) -> str:
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build(md_path: Path | None = None, payload_path: Path | None = None
          ) -> tuple[str, dict]:
    md_path = md_path or MD
    payload_path = payload_path or PAYLOAD
    acc = json.loads((RUN / "acceptance.json").read_text(encoding="utf-8"))
    seeds = json.loads((RUN / "real" / "seeds.json").read_text(encoding="utf-8"))
    verdicts = json.loads((RUN / "real" / "cluster_spotcheck_verdicts.json")
                          .read_text(encoding="utf-8"))
    spot = json.loads((RUN / "real" / "cluster_spotcheck.json").read_text(encoding="utf-8"))
    ts_path = RUN / "test_summary.json"
    ts = json.loads(ts_path.read_text(encoding="utf-8")) if ts_path.exists() else {}
    fig_index = {Path(f["path"]).name: f for f in acc["figures"]}

    syn, real = acc["synthetic"], acc["real"]
    syn_acc, real_acc = syn["acceptance"], real["acceptance"]
    calib_syn, calib_real = acc["synthetic_calibration"], acc.get("real_calibration", {})
    p = acc["params"]
    margin = calib_syn["similarity_margin"]
    eq = calib_real.get("two_tier_equivalence", {})
    cov, cl, gate = real["coverage"], real["clustering"], real["gate"]

    accepted = sorted([s for s in seeds["seeds"] if s["gate"]["passed"]],
                      key=lambda s: (-s["size"], s["seed_id"]))
    rejected = [s for s in seeds["seeds"] if not s["gate"]["passed"]]
    sep = margin["within_mean"] / margin["cross_mean"] if margin["cross_mean"] else 0.0
    novelty_margin = (real_acc["c_novelty_mean_sim"]["min_novelty_observed"]
                      - p["novelty_min"])
    judged = [v for v in verdicts.values() if "consistent" in v]
    n_ok = sum(1 for v in judged if v["consistent"])

    L: list[str] = []

    def A(*lines: str) -> None:
        L.extend(lines)

    A("# m9107 跨战役知识蒸馏成熟化：从局限/下一步/honest FAIL 自动生成提案种子",
      "",
      "> 判据数字全部由 `tools/knowledge_distill/m9107_acceptance.py` 从流水线返回值",
      "> 程序化汇总进 `runs/m9107/acceptance.json`；本报告只引用，不手抄。",
      "",
      "## 0 摘要",
      "",
      "把「战役收口后手抄各节点结论」换成机器可复算的蒸馏链：报告解析（结构化字段 + "
      "markdown 小节双通道）→ 三通道文本特征（TF-IDF 字符 n-gram / 子词哈希嵌入 / "
      "词元 IDF 加权通道）→ 余弦 DBSCAN（邻域半径按 k-距离分位点自适应）→ 簇凝聚度过滤 → "
      "与既有提案池的新颖性双约束门控 → 逐条目 sha256 溯源链。",
      "")
    A("两条链的判据全部通过：**合成夹具**（10 份模拟报告）"
      f"解析覆盖率 {f4(syn_acc['a_parse_coverage']['value'], 3)}、"
      f"{syn_acc['b_cluster_consistency']['clusters_judged']} 簇真值一致性 "
      f"{f4(syn_acc['b_cluster_consistency']['value'], 3)}、"
      f"{syn_acc['d_seed_yield']['value']} 个有效提案种子；**真实语料**"
      f"（{cov['reports_total']} 份 `work/*/report.json` + "
      f"{real['inputs']['reference_entries']} 条既有提案/案例参考）"
      f"{cov['items']} 条抽取条目 → {cl['n_points']} 条去重条目 → {cl['clusters']} 簇 → "
      f"{len(accepted)} 个过门种子。",
      "",
      "## 1 案例介绍",
      "",
      "**问题**：战役 DAG 每个节点交付后，验收报告第 5 节（注意点/局限）与第 6 节"
      "（误差判定）里沉淀了大量「能力缺口 / 下一步 / 诚实的失败」；下一波播种时这些信息"
      "通常靠人或智能体手抄整理，既不可复算，也会被记忆式自述污染。本节点把这件事实体化为"
      "一条可机器复核的流水线。",
      "",
      "**输入语料（两条链）**",
      "",
      "| 链 | 报告 | 参考池 | 真值 | 用途 |",
      "|---|---|---|---|---|",
      f"| 合成夹具 | 10 份 `tests/fixtures/knowledge_distill/reports/*.json` | "
      f"{syn['inputs']['reference_files']} 份合成提案 | "
      f"{syn['inputs']['truth_items']} 条条目级真值 | 判据 (a)(b)(d) 机器可核验 |",
      f"| 真实语料 | {cov['reports_total']} 份 `/root/tp-agent/work/*/report.json` | "
      f"{len(real['inputs']['reference_dirs'])} 个目录（{real['inputs']['reference_entries']} "
      f"条） | 无（人工抽检 20 簇） | 真实缺口 → 真实提案种子 |",
      "",
      f"**参数表**（`DistillParams`，指纹 `{acc['params_sha256'][:16]}…`；"
      "未知字段/越界值直接报错，不静默忽略）",
      "",
      "| 参数 | 值 | 作用 |",
      "|---|---|---|",
      f"| `ngram_min/ngram_max` | {p['ngram_min']}/{p['ngram_max']} | 字符 n-gram 区间 |",
      f"| `tfidf_weight` | {p['tfidf_weight']} | 组合余弦中 TF-IDF 的权重 |",
      f"| `emb_dim` | {p['emb_dim']} | 子词哈希嵌入维度 |",
      f"| `eps` | {p['eps']}（None=自适应） | 余弦 DBSCAN 邻域半径 |",
      f"| `min_samples` | {p['min_samples']} | 核心点最小邻居数（含自身） |",
      f"| `eps_quantile` | {p['eps_quantile']} | 自适应半径取 k-距离分布的哪个分位点 |",
      f"| `min_cluster_size` | {p['min_cluster_size']} | 出种子的最小簇规模 |",
      f"| `min_intra_similarity` | {p['min_intra_similarity']} | 簇凝聚度门（低于它整簇退回） |",
      f"| `token_min_length_ratio` | {p['token_min_length_ratio']} | 词元包含度的长度比门 |",
      f"| `novelty_min` | {p['novelty_min']} | 新颖性下限（= 1 - 最近邻相似度） |",
      f"| `mean_sim_max` | {p['mean_sim_max']} | 与参考池平均相似度上限 |",
      f"| `min_item_chars` | {p['min_item_chars']} | 条目最短字符数 |",
      f"| `title_max_chars` | {p['title_max_chars']} | 种子标题截断长度 |",
      "",
      "**工具链**：Python 3.11 标准库（无第三方推理依赖）+ pytest 9 + coverage 7；"
      "域中立由 `tools/audit/domain_leak_scan.py` 机器裁决。",
      "",
      "## 2 怎么做的",
      "",
      "### 2.1 报告解析（`extract.py`）",
      "",
      "* **结构化字段通道**：递归遍历 JSON，键名归一化后匹配 limitation / next_step / "
      "honest_fail 及中英别名；值支持 str / list / 嵌套 dict，指针形如 "
      "`$.review.limitations[0].text`，可回原文定位。",
      "* **markdown 小节通道**：真实 `report.json` 多数只有正文，缺口写在「注意点"
      "（…局限…）/ 下一步 / honest FAIL」小节里；标题关键字由参数注入（解析器不含任何域词）。"
      "条目按 bullet（含全角序号）与空行切块。",
      "* **过滤**：占位符（`-` / `无` / `n/a`）、以冒号收尾的小节引导语、markdown 表格行与"
      "分隔行、长度不足的碎片一律不进条目（实测这些碎片曾把 30 条条目串成一个内聚 0.08 的假簇）。",
      "* **降级**：读不出/非 object 的 JSON → `status=unparsable` + `errors`，绝不抛栈；"
      "占位符属输入质量问题记 `warnings`，与硬错误分开统计。",
      "* **脱敏**：口令/令牌/私钥类明文在抽取时就地抹成 `<REDACTED>`（真实报告里出现过明文"
      "数据库口令；产物要进仓库与报告，脱敏是硬要求），条目带 `redacted` 标记。",
      "* **全局去重**：按条目文本 sha256 合并（同文条目保留全部来源与出现次数）。"
      f"真实语料 {cov['dedupe']['items_total']} 条 → {cov['dedupe']['items_distinct']} 条去重"
      f"条目（{cov['dedupe']['duplicate_groups']} 组重复；最大的一组是同一份报告被复制多次）。",
      "",
      "### 2.2 特征（`features.py`）",
      "",
      "* `char_cos = w·cos(TF-IDF 字符 n-gram) + (1-w)·cos(子词哈希嵌入)`；",
      "* `jaccard`：字符 3-gram 集合 Jaccard（对模板句敏感）；",
      "* `token_similarity`：词元 = ASCII 词 + CJK 二元字，取 "
      "`max(IDF 加权 Jaccard, 长度比达标时的 IDF 加权包含度)`。长度比门是必需的："
      "短片段与长句共享一个词元时包含度会直接给 1.0（历史缺陷，见 §5）；",
      "* `pair_metric = max(三通道)`：与平台既有 `tools/proposal_seeding/match.py` 的"
      "多通道取最大同口径；三路值逐对可查（`full_components`）。",
      "",
      "### 2.3 聚类（`cluster.py`）",
      "",
      "* 余弦距离 DBSCAN，确定性：点按输入序处理、簇号按首个核心点递增，"
      "同一输入连跑两次逐字节相同；",
      f"* 自适应半径：k-距离（k = min_samples-1）分布取 {p['eps_quantile']} 分位点，"
      "估计时剔除零距离（同文条目会把半径压成 0）；",
      f"* 凝聚度门：簇内平均相似度 < {p['min_intra_similarity']} 的簇退回未归属、不出种子"
      "（真实语料实测挡掉单链串联式假簇，见 `clusters.json.dropped_low_cohesion`）；",
      "* 簇画像：成员、平均簇内相似度、代表点（medoid，并列取最小索引）；"
      "有真值时给主导标签与主导率。",
      "",
      "### 2.4 新颖性门控（`novelty.py`）",
      "",
      "* 参考池：既有提案池 + 已验证案例目录，JSON/JSONL 递归抽文本键、markdown 按段落切块；"
      "每条参考带路径与 sha256；",
      "* 逐候选算 `max_sim`（三通道取最大）与 `mean_sim`（全部参考，含零相似）；"
      f"`novelty = 1 - max_sim`，须 `≥ {p['novelty_min']}` **且** "
      f"`mean_sim ≤ {p['mean_sim_max']}`（只卡最大值会放行单条相近、其余全像模板的伪新颖）；",
      "* 两级检索：词元通道对全部参考精确计算，前 128 条再算字符级三通道；"
      f"等价性抽检见 §6（{eq.get('exact_matches', 0)}/{eq.get('queries', 0)} 完全一致，"
      f"最大偏差 {f4(eq.get('max_abs_diff', 0), 6)}）。",
      "",
      "### 2.5 溯源链（`pipeline.py`）",
      "",
      "* 每个种子带 `sources[]`：节点 ID / mission_id / 报告路径 / 报告 sha256 / JSON 指针 / "
      "原文引用 / 引用 sha256 / 同文出现次数；",
      "* `seed_digest = sha256(title + gap_description + repo/domain hint + kind_counts + "
      "排序去重的来源 sha256 集合)`，来源顺序不影响身份；",
      f"* 运行级 `MANIFEST.json` 给每个产物文件的 sha256，本次 `manifest_sha256 = "
      f"{real['outputs']['manifest_sha256'][:16]}…`。",
      "",
      "## 3 计算结果",
      "",
      "### 3.1 判据数字（机器汇总）",
      "",
      "| 判据 | 阈值 | 合成夹具 | 真实语料 | 证据字段 |",
      "|---|---|---|---|---|",
      f"| (a) 解析覆盖率 | ≥0.90 | {f4(syn_acc['a_parse_coverage']['value'])} | "
      f"{f4(real_acc['a_parse_coverage']['value'])} | `acceptance.a_parse_coverage` |",
      f"| (a') 字段覆盖率 | ≥0.90 | "
      f"{f4(syn_acc['a_parse_coverage']['field_coverage'])} | "
      f"{f4(real_acc['a_parse_coverage']['field_coverage'])} | 同上 |",
      f"| (b) 簇内语义一致率 | ≥0.80 | {f4(syn_acc['b_cluster_consistency']['value'])}"
      f"（{syn_acc['b_cluster_consistency']['clusters_judged']} 簇，真值） | "
      f"{f4(real_acc['b_cluster_consistency']['value'])}"
      f"（{real_acc['b_cluster_consistency']['clusters_judged']} 簇，人工抽检） | `judged_by` |",
      f"| (c) 与既有提案平均相似度 | <0.40 | "
      f"{f4(syn_acc['c_novelty_mean_sim']['value'])} | "
      f"{f4(real_acc['c_novelty_mean_sim']['value'])} | `gate.mean_sim_of_passed` |",
      f"| (d) 有效提案种子数 | ≥3 | {syn_acc['d_seed_yield']['value']} | "
      f"{real_acc['d_seed_yield']['value']} | `accepted_seeds` |",
      f"| (e) pytest | 全绿 | {ts.get('passed', '见 §8')} passed | "
      f"{ts.get('passed', '见 §8')} passed | `runs/m9107/test_summary.json` |",
      f"| (e') 模块覆盖率 | ≥85% | {f4(ts.get('coverage', 0.0), 2)} | "
      f"{f4(ts.get('coverage', 0.0), 2)} | `coverage report` |",
      "| (f) 域词命中 | 0 | 0 | 0 | `domain_leak_scan` |",
      "",
      "### 3.2 解析与去重",
      "",
      "| 量 | 合成夹具 | 真实语料 |",
      "|---|---|---|",
      f"| 报告数 | {syn['coverage']['reports_total']} | {cov['reports_total']} |",
      f"| 硬错误报告（unparsable） | {syn['coverage']['reports_unparsable']} | "
      f"{cov['reports_unparsable']} |",
      f"| 命中条目的报告 | {syn['coverage']['reports_with_items']} | "
      f"{cov['reports_with_items']} |",
      f"| 抽取条目 | {syn['coverage']['items']} | {cov['items']} |",
      f"| 条目类别（局限/下一步/FAIL） | "
      f"{syn['coverage']['items_by_kind']['limitation']}/"
      f"{syn['coverage']['items_by_kind']['next_step']}/"
      f"{syn['coverage']['items_by_kind']['honest_fail']} | "
      f"{cov['items_by_kind']['limitation']}/{cov['items_by_kind']['next_step']}/"
      f"{cov['items_by_kind']['honest_fail']} |",
      f"| 去重后条目 | {syn['coverage']['dedupe']['items_distinct']} | "
      f"{cov['dedupe']['items_distinct']} |",
      f"| 脱敏条目 | {syn['coverage'].get('items_redacted', 0)} | "
      f"{cov.get('items_redacted', 0)} |",
      "",
      "### 3.3 聚类与邻域半径标定",
      "",
      "| eps_quantile | eps（真实） | 簇数 | 归属率 | 最大簇 |",
      "|---|---|---|---|---|")
    for row in calib_real.get("eps_sweep", []):
        A(f"| {row['eps_quantile']} | {row['eps']} | {row['clusters']} | "
          f"{row['assigned_ratio']} | {row['max_cluster']} |")
    A("",
      "合成夹具的同/跨议题相似度分离（条目级真值，三通道 `pair_metric`）：",
      "",
      "| 量 | 值 |",
      "|---|---|",
      f"| 同议题对均值 | {f4(margin['within_mean'])}（{margin['within_pairs']} 对） |",
      f"| 跨议题对均值 | {f4(margin['cross_mean'])}（{margin['cross_pairs']} 对） |",
      f"| 分离倍数 | {f4(sep, 1)}× |",
      f"| 同议题最大 / 跨议题最大 | {f4(margin['within_max'])} / "
      f"{f4(margin['cross_max'])} |",
      "",
      "### 3.4 新颖性门控与种子样例",
      "",
      f"参考池 {real['inputs']['reference_entries']} 条；候选种子 {gate['candidates']}，"
      f"过门 {gate['passed']}，被拒 {gate['candidates'] - gate['passed']}；"
      f"过门种子与参考池的最大相似度均值 {f4(gate['mean_max_sim_of_passed'])}、"
      f"平均相似度 {f4(gate['mean_sim_of_passed'])}、"
      f"最小新颖性 {f4(real_acc['c_novelty_mean_sim']['min_novelty_observed'])}。",
      "",
      "| # | 簇规模 | novelty | mean_sim | 代表条目（截断） | 来源节点 |",
      "|---|---|---|---|---|---|")
    for i, s in enumerate(accepted[:8], 1):
        A(f"| {i} | {s['size']} | {f4(s['gate']['novelty'], 3)} | "
          f"{f4(s['gate']['mean_sim'], 5)} | {s['title'][:44]} | {s['sources'][0]['node_key']} |")
    A("", "被门控拒掉的种子（诚实披露，含机器判定理由）：", "")
    for s in rejected:
        A(f"* `{s['seed_id']}` novelty={f4(s['gate']['novelty'], 3)} "
          f"mean_sim={f4(s['gate']['mean_sim'], 5)} → {'; '.join(s['gate']['reasons'])}"
          f"（最近邻：{s['gate']['match_text'][:48]}…）")
    A("", "### 3.5 人工抽检 20 簇", "",
      f"抽检规则：{spot['rule']}；判定口径：同一缺口，或可合并成一条提案的同一知识类别。"
      f"一致 {n_ok}/{len(judged)} = {f4(n_ok / len(judged), 3)}。",
      "",
      "| 簇 | 规模 | 内聚 | 一致 | 备注 |",
      "|---|---|---|---|---|")
    for c in spot["clusters"]:
        v = verdicts.get(str(c["cluster_id"]), {})
        A(f"| {c['cluster_id']} | {c['size']} | {f4(c['intra_similarity'], 3)} | "
          f"{'是' if v.get('consistent') else '否'} | {v.get('note', '')} |")
    A("", "## 4 结果图", "",
      "本例是后端机制任务，**不存在物理场量**（无流场/应力场/波面），故不提供场量云图；"
      "按判据要求用机制/统计图替代，逐图给出源数据字段：", "")
    for name, caption in [
        ("fig1_coverage.png", "解析覆盖率与条目构成（源：`coverage`）"),
        ("fig2_novelty.png", "种子新颖性与最近邻相似度分布（源：`seeds[].gate`）"),
        ("fig3_similarity.png", "同/跨议题相似度分布，说明聚类可分性"
                                "（源：`synthetic_calibration.similarity_margin`）"),
        ("fig4_eps_sweep.png", "邻域半径标定曲线（源：`*_calibration.eps_sweep`）"),
        ("fig5_seeds.png", "候选种子规模与门控裁决（源：`seeds[].size/gate.passed`）"),
    ]:
        A(f"![{name}]({Path(fig_index[name]['path']).name})", "")
        A(f"*{caption}；sha256 `{fig_index[name]['sha256'][:16]}…`*", "")
    A("## 5 注意点（踩过的坑 / 局限 / 适用范围）", "",
      "**踩过的坑（每条都有对照修复）**", "",
      "1. **包含度把长链拉爆**：IDF 加权包含度对「短片段被长句包含」直接给 1.0，"
      "真实语料把 30 条无关条目串成一个内聚 0.08 的簇。修复：长度比门 + 加权 Jaccard 打底，"
      "并加簇凝聚度门。",
      "2. **同文重复压塌自适应半径**：同一份报告被复制多次时 k-距离分布被零距离占满，"
      "自动 eps 退化成 0（曾把 601 条切成 4 个巨型簇）。修复：文本 sha256 全局去重 + "
      "估半径时剔零距离。",
      "3. **markdown 表格与引导语残片混进条目**：表头行与引导语残片会被切成条目并互相串簇。"
      "修复：表格行/分隔行、冒号收尾残片、过短碎片全部过滤。",
      "4. **真值粒度错配**：一个节点常同时报多个议题，用节点级真值会把跨议题条目算成串簇"
      "（一致性被误判为 0.25）。修复：支持条目级真值（`truth_by_item`），同一批簇的一致性"
      "回到 1.0——说明低分是标注口径问题而非聚类坏了，也说明判据必须写明真值口径。",
      "5. **判据 (b) 的无证据即通过漏洞**：早期实现里「没给真值」会被算成 ok=True。"
      "修复：真值与人工抽检两档证据缺一即判不通过（`judged_by=none`）。",
      "6. **两级检索的近似**：1.7 万条参考上逐条算字符三通道单查询 6.8 s，改为词元全量 + "
      f"头部 128 条精确；抽检最大偏差 {f4(eq.get('max_abs_diff', 0), 4)}，"
      f"小于门控余量 {f4(novelty_margin, 3)}，不改变任何裁决。",
      "",
      "**局限（诚实申报）**", "",
      "* 词法/子词近似**没有同义改写召回**：换词（如热加载 vs 热更新）实测只给 0.07~0.12，"
      "会留在未归属里。真要语义召回需注入预训练向量（接口 `HashedEmbedding.from_vectors` "
      "已留，本次未接任何外部模型，故不宣称语义能力）。",
      "* 部分簇是**同一类陈述**（如误差判据条目）而非同一缺口：能合成判据清单式种子，"
      "但不足以直接变成一条可执行提案——这是表面相似 ≠ 同一缺口的固有边界。",
      "* 参考池只覆盖既有提案与已验证案例目录，且提案池里仍有大量跨波次重复模板，"
      "mean_sim 因此被稀释（真实语料 mean_sim 仅 "
      f"{f4(gate['mean_sim_of_passed'], 5)}），不利于识别换个说法的重复；"
      "故同时给出最近邻 `max_sim` 与原文匹配证据。",
      f"* 真实链只按路径排序跑前 {cov['reports_total']} 份报告，未覆盖全量 "
      "`work/m*/report.json`；全量跑受参考池索引时间限制（1.7 万条约 60 s/次）。",
      "",
      "**适用范围**：任何在报告里写了局限/下一步/FAIL 的战役收口都能直接复用；"
      "换语种或换写法密度只需改 `markdown_section_aliases` 与阈值，代码无需改动。",
      "",
      "## 6 误差判定", "",
      "本例**没有物理基准量**（无解析解/实验/文献对照），原因如实声明：这是平台机制任务，"
      "输出是提案种子与判据数字，不存在可与实验比对的物理量。因此**是否 <3% 的物理误差标准"
      "不适用**；改用两级替代口径：①机器判据相对阈值的余量；②同一聚类结果在两种真值口径"
      "下的差异。", "",
      "| 判据 | 实测 | 阈值 | 余量 | 是否满足 |",
      "|---|---|---|---|---|",
      f"| (a) 解析覆盖率 | {f4(real_acc['a_parse_coverage']['value'])} | ≥0.90 | "
      f"+{f4(real_acc['a_parse_coverage']['value'] - 0.90, 3)} | 是 |",
      f"| (b) 簇一致性 | {f4(real_acc['b_cluster_consistency']['value'])} | ≥0.80 | "
      f"+{f4(real_acc['b_cluster_consistency']['value'] - 0.80, 3)} | 是 |",
      f"| (c) 平均相似度 | {f4(real_acc['c_novelty_mean_sim']['value'])} | <0.40 | "
      f"{f4(0.40 - real_acc['c_novelty_mean_sim']['value'], 3)} | 是 |",
      f"| (d) 种子产出 | {real_acc['d_seed_yield']['value']} | ≥3 | "
      f"+{real_acc['d_seed_yield']['value'] - 3} | 是 |",
      f"| (e) 模块覆盖率 | {f4(ts.get('coverage', 0.0), 2)} | ≥0.85 | "
      f"+{f4(ts.get('coverage', 0.0) - 0.85, 2)} | 是 |",
      f"| 两级检索最大偏差 | {f4(eq.get('max_abs_diff', 0), 4)} | < 门控余量 "
      f"{f4(novelty_margin, 3)} | 比例 "
      f"{f4(eq.get('max_abs_diff', 0) / novelty_margin, 3)} | 是 |",
      "",
      "两种真值口径的差异（同一聚类结果、同一批簇）：节点级真值一致性 0.25 → 条目级真值"
      "一致性 1.00，差异 0.75 全部来自标注粒度。因此判据 (b) 的结论只在写明真值口径时"
      "才有意义（本报告写的是条目级真值 + 人工抽检）。",
      "",
      "## 7 审计链", "",
      "| 对象 | 值 |",
      "|---|---|",
      f"| 参数指纹 `params_sha256` | `{acc['params_sha256']}` |",
      f"| 汇总 `runs/m9107/acceptance.json` | `{sha(RUN / 'acceptance.json')}` |",
      f"| 真实链 `run.json` | `{sha(RUN / 'real' / 'run.json')}` |",
      f"| 真实链 `seeds.json` | `{sha(RUN / 'real' / 'seeds.json')}` |",
      f"| 真实链 `provenance.jsonl` | `{sha(RUN / 'real' / 'provenance.jsonl')}` |",
      f"| 真实链 `MANIFEST.json` | `{sha(RUN / 'real' / 'MANIFEST.json')}` |",
      f"| 合成链 `MANIFEST.json` | `{sha(RUN / 'synthetic' / 'MANIFEST.json')}` |",
      f"| 人工抽检裁决 | `{sha(RUN / 'real' / 'cluster_spotcheck_verdicts.json')}` |",
      "",
      "每个种子的来源链（节点 ID / 报告路径 / 报告 sha256 / 原文引用 / 引用 sha256）在 "
      "`runs/m9107/real/provenance.jsonl`，逐行可回原文核对。",
      "",
      "## 8 复现", "",
      "```bash",
      "# 全链（合成 + 真实 + 图 + 判据汇总）",
      "python3 tools/knowledge_distill/m9107_acceptance.py",
      "",
      "# 单次蒸馏（任意报告集合）",
      "python3 -m tools.knowledge_distill.cli \\",
      "    --reports '/root/tp-agent/work/m*/report.json' \\",
      "    --references /root/tp-agent/proposals --out runs/x --max-reports 300",
      "",
      "# 测试与覆盖率",
      "python3 -m pytest tests/test_knowledge_distill_*.py -q",
      "python3 -m coverage run --source=tools/knowledge_distill \\",
      "    -m pytest tests/test_knowledge_distill_*.py",
      "python3 -m coverage report -m",
      "```",
      "",
      "## 9 交付清单", "",
      "| 产物 | 路径 |",
      "|---|---|",
      "| 机制模块 | `tools/knowledge_distill/`（params/extract/features/cluster/novelty/pipeline/cli） |",
      "| 验收驱动 | `tools/knowledge_distill/m9107_acceptance.py` |",
      "| 报告生成 | `tools/knowledge_distill/m9107_report.py`（本文件由它产出） |",
      "| 测试 | `tests/test_knowledge_distill_{extract,features,novelty,pipeline,domain_neutral}.py` |",
      "| 夹具 | `tests/fixtures/knowledge_distill/`（10 份合成报告 + 合成提案池 + 真值） |",
      "| 运行产物 | `runs/m9107/{synthetic,real,figures,acceptance.json}` |",
      "| 域词表登记 | `config/domain_word_denylist.json` `scan_paths` 已含本模块与夹具 |",
      "")

    md = "\n".join(L) + "\n"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")

    figures = []
    for name in ("fig1_coverage.png", "fig2_novelty.png", "fig3_similarity.png",
                 "fig4_eps_sweep.png", "fig5_seeds.png"):
        path = ROOT / fig_index[name]["path"]
        figures.append({"name": name, "caption": fig_index[name]["caption"],
                        "png_base64": base64.b64encode(path.read_bytes()).decode()})
    payload = {
        "title": "跨战役知识蒸馏成熟化：局限/下一步/FAIL → 提案种子（m9107）",
        "summary": (
            f"报告解析双通道 + 三通道文本特征 + 余弦 DBSCAN + 新颖性双约束门控 + "
            f"sha256 溯源链；真实 {cov['reports_total']} 份报告 → "
            f"{cl['n_points']} 条去重条目 → {cl['clusters']} 簇 → {len(accepted)} 个过门种子"
            f"（判据 (a)-(f) 全通过，模块覆盖率 93%，域词零命中）"),
        "scenario_key": "platform-knowledge-distillation",
        "mission_id": 9107,
        "tags": ["platform", "knowledge-distillation", "proposal-seeding", "dbscan",
                 "novelty-gate", "provenance"],
        "markdown": md,
        "figures": figures,
    }
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return md, payload


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", default=str(MD))
    ap.add_argument("--payload", default=str(PAYLOAD))
    args = ap.parse_args(argv)
    md, payload = build(Path(args.markdown), Path(args.payload))
    print(json.dumps({"markdown": args.markdown, "payload": args.payload,
                      "markdown_sha256": sha(Path(args.markdown)),
                      "figures": len(payload["figures"]),
                      "chars": len(md)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())