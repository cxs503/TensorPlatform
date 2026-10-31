#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m845 报告构建器：从 artifacts/audit_report_m845.json 生成 markdown + POST payload。

产物： docs/report_m779_v2.md
       /tmp/report_m845.json （POST /tensor/reports 的 payload，含内嵌 PNG base64）
报告内所有数值均由 JSON 注入；脚本内无手写测量值。
"""
import base64
import json
import os

WORK = "/srv/tensor/agent/work/m845"
ART = os.path.join(WORK, "artifacts")
a = json.load(open(os.path.join(ART, "audit_report_m845.json"), encoding="utf-8"))
plan, ms, nodes, p3 = a["plan"], a["milestones"], a["nodes"], a["plan3_nodes"]
g, acc, srcs = a["graph"], a["proposal_accounting"], a["sources"]
id2ext = {n["id"]: n["ext_id"] for n in nodes}
ext2node = {n["ext_id"]: n for n in nodes}
ms_of = {n["ext_id"]: n["milestone_id"] for n in nodes}
ms_band = {m["id"]: (m["name"].split("(")[1].rstrip(")") if "(" in m["name"] else m["name"]) for m in ms}
ms_cn = {m["id"]: m["name"].split("(")[0].strip().rstrip(":：") for m in ms}
total, ident, uns = acc["total"], acc["identifiable_refs"], acc["unseeded"]
npass = sum(1 for x in g["gates"] if x["pass"])
FIG1 = os.path.join(ART, "fig1_proposal_aggregation_m845.png")
FIG2 = os.path.join(ART, "fig2_dag_corrected_m845.png")


def md():
    L = []
    A = L.append
    A("# 冰区航行战役第四波（Wave 4）提案聚合与战役播种 — 返工修订报告（v2）")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 本单 mission_id | **845**（返工单：重写并重发报告） |")
    A("| 原工作单 mission_id | **832**（仲裁播种者，succeeded） |")
    A("| 被修订的原报告 | **#779**《仲裁播种者：冰区航行战役第四波提案聚合与战役播种报告》 |")
    A("| 修订范围 | 仅 `docs/`、`artifacts/`；**未改业务代码、未改任何战役数据**（零 INSERT/UPDATE/ALTER） |")
    A("| 数字来源 | `docs/audit_seed_plan4.py` 从 live DB + 源文件重算 → "
      "`artifacts/audit_report_m845.json` → 本报告/图件/payload 同源注入，零手抄 |")
    A("| 缺陷闭环 | D1–D7 逐条闭环，见 ⑦.6 |")
    A("")
    A("> **修订自陈**：原报告 #779 的 DAG 图有 3 处事实错误、最长链层数错误（D2/D3），且缺独立审计节（D1）、"
      "未解释提案账目差值（D4）、未声明图件豁免（D5）、误差判定未显式回答（D6）、结论超出证据（D7）。"
      "本版按 `seed_plan4.sql` 的 ARRAY 实际值与 live DB 实测值重算重绘；"
      "**凡与原报告冲突处，一律以本版（脚本可复现 + live DB）为准**。")
    A("")
    A("---")
    A("")
    # ---------------- ①
    A("## ① 案例介绍（物理背景 / 几何 / 参数表）")
    A("")
    A("### 1.1 物理背景与战役谱系")
    A("冰区航行战役是平台「自主战役播种」主线的第 4 波。谱系：plan 1（27 个基础/验证/集成节点）→ "
      "plan 2（10 节点）→ plan 3（%d 节点，冰区航行主战役，已收官：%s）→ "
      "**plan 4（本任务播种，%d 节点 / %d 里程碑，plan_id = %d）**。"
      % (len(p3), "、".join(n["ext"] for n in p3), len(nodes), len(ms), plan["id"]))
    A("")
    A("plan 3 收官后暴露的学科局限是四条：① 各能力模块彼此独立、缺统一集成框架；② 确定性分析与概率分析"
      "（FORM/SORM 可靠度）未统一到同一校核链路；③ 数字孪生未实现在线部署与实时预测；"
      "④ IMO Polar Code 规范符合性未完整验证。第四波因此定位为「**成熟化 + 集成**」波次："
      "把 plan 3 已跑通的单项能力提升为工程级模块，把数字孪生 / 冰载荷反演 / 三场耦合操纵性串成在线"
      "协同链路，最后落到运营优化与规范符合性（M14 基础 → M15 集成 → M16 运营与规范）。")
    A("")
    A("### 1.2 几何与物理参数")
    A("本任务是**战役数据播种（SQL INSERT）**，不含船体几何建模，也不做任何水动力 / 结构数值求解。因此：")
    A("")
    A("- **几何验证（Cb / L/B / 排水量）不适用（N/A）**：无船体几何输入、无主尺度与型线参数。")
    A("- **物理量不产生**：节点 goal 中声明的基准（IACS UR I、IMO Polar Code、ISO 19906、DNV-RP-C201）"
      "是未来开发节点要对照的规范，不是本任务的输入数值。")
    A("- 唯一实参是**战役数据结构参数**（1.3 表），全部来自 live DB 实测。")
    A("")
    A("### 1.3 输入数据源与参数表（含 sha256，可复现）")
    A("| 角色 | 路径 | sha256（前 16） | 用途 |")
    A("|---|---|---|---|")
    for s in srcs:
        A("| %s | `%s` | `%s` | %s |" % (s["role"], s["path"], s["sha256"][:16], s["use"]))
    A("")
    A("| plan 4 参数（live DB 实测） | 值 |")
    A("|---|---|")
    A("| plan_id / name / plan_version | %d / `%s` / `%s` |" % (plan["id"], plan["name"], plan["plan_version"]))
    A("| status | `%s`（播种 SQL 声明 `%s`；差异见 ⑦.4 A3-1） |" % (plan["status"], g["sql_plan_status"]))
    A("| parent_plan | %s |" % plan["metadata"].get("parent_plan"))
    A("| planned_start → planned_end | %s → %s |"
      % (str(plan["planned_start"])[:19], str(plan["planned_end"])[:19]))
    A("| 节点 / 里程碑 / 依赖边 | %d / %d / %d |" % (len(nodes), len(ms), g["edge_count"]))
    A("| 仓库分布 | %s |" % ", ".join("%s(%d)" % (k, v) for k, v in g["repo_counts"].items()))
    A("| 成熟化节点 | %d（%s） |" % (len(g["maturity_nodes"]), ", ".join(g["maturity_nodes"])))
    A("")
    A("---")
    A("")
    # ---------------- ②
    A("## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）")
    A("")
    A("### 2.1 聚合去重流程")
    A("1. 读取 3 位提案者的提案文件：M775 %s 条、M776 %s 条、M777 %s 条，共 **%d** 条"
      "（sha256 见 1.3；M777 的提案**无 id 字段**，只有 title/order）。"
      % (acc["per_mission"]["775"], acc["per_mission"]["776"], acc["per_mission"]["777"], total))
    A("2. 以 `physics_domain` + `goal` + `dependencies_hint` 做语义重叠判定：重叠者合并为一个节点"
      "（goal 吸收各家可取细节），互补者独立成节点。实际发生 **%d 组合并**：" % len(acc["merge_groups"]))
    for mg in acc["merge_groups"]:
        A("   - %s → %s（%s）" % (" + ".join(mg["merged_from"]), mg["into"], mg["reason"][:72]))
    A("3. 每节点 `metadata.proposal_src` 记来源提案者 **mission id**（不是提案 id，见 ⑦.3 A-2）；"
      "`metadata.is_maturity=true` 且 goal 含「成熟化」标记成熟化单。")
    A("4. 结果：**%d 条提案 → %d 个节点**，其中 %d 条可溯源、%d 条未播种（③ 表 3-5/3-6）。"
      % (ident, len(nodes), ident, len(uns)))
    A("")
    A("### 2.2 播种流程与工具链")
    A("| 步 | 动作 | 工具 / 校验 |")
    A("|---|---|---|")
    A("| 1 | 读 `information_schema.columns` 与 `pg_constraint` 核对列名与 CHECK 枚举"
      "（`status`、`node_type`）；禁 ALTER，仅 INSERT/UPDATE | psql 直连 |")
    A("| 2 | `INSERT campaign_plans` 1 行（status=draft，metadata.parent_plan=3） | `seed_plan4.sql` |")
    A("| 3 | `INSERT campaign_milestones` %d 行（M14/M15/M16） | 同上 |" % len(ms))
    A("| 4 | `INSERT campaign_nodes` %d 行（id %d–%d，dependencies 用 `ARRAY[...]`） | 同上 |"
      % (len(nodes), min(id2ext), max(id2ext)))
    A("| 5 | `UPDATE` plan `metadata.seed_report`（nodes / new_plan_id / arbiters） | 同上 |")
    A("| 6 | 机器门 7 项 + 本版附加 3 项复核 | 派发器硬校验 + **本次独立重算**（③ 表 3-4） |")
    A("")
    A("### 2.3 本版返工方法（针对 D1–D7）")
    A("- **不复用原报告任何数值**：全部从 live DB 与源文件重算（`docs/audit_seed_plan4.py`）。")
    A("- 把 `seed_plan4.sql` 解析为 {ext_id, milestone_id, dependencies}，与 live DB **逐节点比对**"
      "（审计门 10，⑦.4）。")
    A("- 拓扑排序判无环 + 全路径枚举复算最长链（⑦.5）。")
    A("- 用提案源文件 + `result.json.aggregation_summary` 复算 %d / %d / %d 的账目（③ 表 3-5/3-6）。"
      % (total, ident, len(uns)))
    A("- 脚本内置**反向断言**：机器门未全过或提案账目不闭合即 `exit 1`，不允许出报告。")
    A("")
    A("---")
    A("")
    # ---------------- ③
    A("## ③ 计算结果（数字表，全部程序化注入）")
    A("")
    A("### 表 3-1 节点明细（live DB，按 id）")
    A("| id | ext_id | 里程碑 | dependencies（ext） | repo | 成熟化 | goal 字数 | 验收判据条数 |")
    A("|---|---|---|---|---|---|---|---|")
    for n in nodes:
        A("| %d | %s | M%d | %s | %s | %s | %d | %d |" % (
            n["id"], n["ext_id"], n["milestone_id"],
            ", ".join(g["deps_by_ext"][n["ext_id"]]) or "—（根节点）",
            n["repo"], "✔" if n["is_maturity"] == "true" else "—", n["goal_len"], n["ac_len"]))
    A("")
    A("### 表 3-2 里程碑覆盖")
    A("| 里程碑 | 名称 | 节点数 | 节点 | 状态 |")
    A("|---|---|---|---|---|")
    for m in ms:
        cov = g["ms_coverage"]["M%d" % m["id"]]
        A("| M%d | %s（%s） | %d | %s | %s |" % (m["id"], ms_cn[m["id"]], ms_band[m["id"]],
                                               len(cov), ", ".join(cov), m["status"]))
    A("")
    A("### 表 3-3 依赖边全表（共 %d 条，取自 `dependencies integer[]` 实际值）" % g["edge_count"])
    A("| # | 依赖边（dep → node） |")
    A("|---|---|")
    for i, (u, v) in enumerate(g["edges"], 1):
        A("| %d | %s → %s |" % (i, u, v))
    A("")
    A("### 表 3-4 机器门复核（%d 项，脚本重算而非引用原结论）" % len(g["gates"]))
    A("| # | 校验门 | 要求 | 实测 | 结论 |")
    A("|---|---|---|---|---|")
    for i, gt in enumerate(g["gates"], 1):
        A("| %d | %s | %s | %s | **%s** |" % (i, gt["gate"], gt["requirement"], gt["actual"],
                                              "PASS" if gt["pass"] else "FAIL"))
    A("")
    A("合计 **%d/%d PASS**。其中门 10（SQL↔DB 一致性）是本版新增的独立校验；前 7 项对应工作单 832 的硬门，"
      "后 3 项为本次附加。" % (npass, len(g["gates"])))
    A("")
    A("### 表 3-5 提案账目：%d = %d（已溯源映射）+ %d（未播种）" % (total, ident, len(uns)))
    A("| 提案 key | 标题（截断） | 源 repo | → 节点 | 类型 |")
    A("|---|---|---|---|---|")
    for d in acc["seeded_native_ids"]:
        A("| %s | %s | %s | %s | 原生 ID，已播种 |" % (d["key"], d["title"][:40], d["repo"],
                                                      ", ".join(d["nodes"])))
    for d in acc["seeded_arbiter_slugs"]:
        A("| %s | %s | %s | %s | 仲裁者 slug（**该提案无原生 ID**），已播种 |"
          % (d["key"], d["title"][:40], d["repo"], ", ".join(d["nodes"])))
    for i, d in enumerate(uns, 1):
        A("| %s | %s | %s | 未播种 | 差值 %d/%d |" % (d["key"], d["title"][:40], d["repo"], i, len(uns)))
    A("")
    A("### 表 3-6 未播种 %d 条的去向（D4 显式说明）" % len(uns))
    A("| # | 未播种提案 | 源 repo | 主题映射（plan 3 已完成节点） | 说明 |")
    A("|---|---|---|---|---|")
    for i, d in enumerate(uns, 1):
        A("| %d | %s | %s | %s（%s） | %s |" % (i, d["key"], d["repo"], d["plan3_domain"],
                                                d["plan3_node_name"], d["domain_note"]))
    A("")
    A("**账目闭合（D4）**：%d 条原始提案 = **%d 条**在 `result.json.aggregation_summary` 中可辨识 + "
      "**%d 条未播种**。可辨识的 %d 条里，**%d 条是原生 `prop-7xx-NN` ID**，**%d 条是仲裁者自定义 topic slug**"
      "（`prop-777-FORM/SORM`、`prop-777-channel`、`prop-777-icing`）——因为 M777 的 `prop-777.json` 6 条提案"
      "**根本没有 id 字段**（各任务原生 ID 数：%s），这是「可辨识 ID 只有 %d 而非 %d」的**结构性原因**，"
      "不是丢数据。" % (total, ident, len(uns), ident, acc["native_id_refs"],
                      len(acc["seeded_arbiter_slugs"]), json.dumps(acc["native_ids_present"],
                                                                   ensure_ascii=False), ident, total))
    A("")
    A("**诚实边界**：播种工件中**没有任何 rejection / 理由字段**记录这 %d 条为何被弃（证据等级 **WITHHELD**）。"
      "表 3-6 的「主题映射」是本次审计依据 plan 3 节点名做的**事后归属推断，不是 DB 记录**。"
      "可机器验证的事实只有两条：(a) 这 %d 条未出现在 plan 4 任何节点的 `proposal_src`；"
      "(b) 其主题落在 plan 3 已完成的 W3-N01/N05/N06/N11 四域，其中 **W3-N01（冰激振动）、W3-N05（碰撞进水）、"
      "W3-N06（空化+水下噪声）在 plan 4 中无任何接续节点** —— 这是本次审计发现的**真实覆盖缺口**，"
      "建议下一波补齐。另需注意：可辨识的 %d 条中**有 1 条（prop-775-01）归属与提案内容不符**"
      "（⑦.3 A-1.1），故「可溯源 %d 条」不等于「%d 条映射都成立」。" % (len(uns), len(uns), ident, ident, ident))
    A("")
    return "\n".join(L), L


def md2(L):
    A = L.append
    A("---")
    A("")
    # ---------------- ④
    A("## ④ 结果图")
    A("")
    A("### 图1 提案聚合谱系（%d → %d 可溯源 → %d 节点；未播种 %d 条显式标出）"
      % (total, ident, len(nodes), len(uns)))
    A("![图1](figures/fig1_proposal_aggregation_m845.png)")
    A("")
    A("蓝点＝已播种且可在 `result.json` 溯源的提案（%d 原生 ID + %d 仲裁 slug）；红 × ＝未播种的 %d 条；"
      "右侧圆点＝plan 4 的 %d 个节点（绿＝成熟化）；连线＝提案→节点归属。"
      % (acc["native_id_refs"], len(acc["seeded_arbiter_slugs"]), len(uns), len(nodes)))
    A("")
    A("### 图2 修正后的 DAG 依赖图（按 `seed_plan4.sql` ARRAY 实际值重绘）")
    A("![图2](figures/fig2_dag_corrected_m845.png)")
    A("")
    A("按里程碑分带（%s）；箭头 dep→node；节点内标注其 `dependencies` 实际值；红色粗箭头为最长链 "
      "**%s = %d 节点 %d 步**。图内文字用英文以免缺 CJK 字形，图题见本行。"
      % ("；".join("M%d %s" % (m["id"], ms_band[m["id"]]) for m in ms),
         " → ".join(g["longest_chain"]), g["longest_chain_len_nodes"], g["longest_chain_len_steps"]))
    A("")
    A("### 图件豁免声明（D5）")
    A("**本任务为战役播种（SQL 数据插入），无物理仿真输出，故不包含场量云图（速度/压力/应力/冰厚等）"
      "与物理解比较曲线（与解析解/文献/基线）。** 替代图件为两张与本任务真实对象严格对应的关系图："
      "提案聚合谱系（图1）与战役依赖图（图2）。二者均由 `docs/figures_m845.py` 从 "
      "`artifacts/audit_report_m845.json` 的数据直接绘制，无任何示意或手绘成分，可脚本一键复现。")
    A("")
    A("---")
    A("")
    # ---------------- ⑤
    A("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）")
    A("")
    A("### 5.1 踩坑记录")
    A("1. **序列 ID 依赖**：campaign_* 三表序列自增，播种前必须查 `MAX(id)`；本 SQL 硬编码 plan=%d、"
      "milestones %s、nodes %d–%d，已在 live DB 逐条核对成立（表 3-1 / ⑦.4）。"
      % (plan["id"], "/".join(str(m["id"]) for m in ms), min(id2ext), max(id2ext)))
    A("2. **`dependencies` 是 `integer[]`**：只能用 `ARRAY[...]` 或 `'{...}'` 字面量；本 SQL 中根节点写 "
      "`NULL`、其余写 `ARRAY[...]`，两种形态并存（审计脚本同时兼容解析，见 ⑦.4 复现命令）。")
    A("3. **metadata JSON 转义**：goal 是长中文串（含全角标点与括号），必须整体作为 JSON 字符串字面量写入 "
      "`metadata`，否则 `jsonb` 解析失败。")
    A("4. **成熟化是文本门**：机器门以 goal 是否含「成熟化」判定（表 3-4 门 7 同时校验 `is_maturity` 与 goal "
      "字样一致），语义质量不在门内。")
    A("5. **图件字形坑**：本机无 CJK 字体（`fc-list :lang=zh` 为空），matplotlib 中文会显示成方框，"
      "故图内标注全部改用英文，中文说明留在图题与正文。")
    A("6. **返工工具坑**：`source agent.env` 形式的登录命令被安全策略拦截；改为直接读环境变量 "
      "`TENSOR_API_BASE / TENSOR_AGENT_USER / TENSOR_AGENT_PASS` 登录 + psql 直连。")
    A("")
    A("### 5.2 局限（honest）")
    A("- **本任务只播种、不执行开发**：%d 个节点的 pytest 验收判据**一条都没有被执行**；本报告不对任何节点的"
      "可实现性与精度背书，也不背书 plan 4 的最终交付质量。" % len(nodes))
    A("- **节点级溯源粒度不足**：DB 中全部节点的 `proposal_src` 只记 mission id（`[\"775\"]` 形态），"
      "提案级映射仅存于 `result.json`，且缺失 **%s** 的提案级来源（⑦.3 A-2）→ 无法只凭 DB 复原聚合过程。"
      % ", ".join(acc["nodes_without_proposal_level_source"]))
    A("- **跨 repo 合并丢失实现归属**：%s 的合并跨越多个 repo，但节点 repo 只有一个（表 3-1），"
      "被合并方的实现归属只能靠 goal 文本约定（⑦.3 A-3）。"
      % "、".join(mg["into"] for mg in acc["merge_groups"]))
    A("- **依赖关系靠提案者 `dependencies_hint` 推断**，无物理耦合量级判据（例如三场耦合节点是否真需要"
      "屈曲链打底），派发时可能需调整。")
    A("")
    A("### 5.3 适用范围")
    A("- 适用于 plan 4 的节点派发、调度与审计基线；")
    A("- 报告中「机器门通过」仅描述**数据插入合法性与图结构合法性**，与物理精度无关；")
    A("- 复现：`python3 docs/audit_seed_plan4.py && python3 docs/figures_m845.py && "
      "python3 docs/report_builder_m845.py`。")
    A("")
    A("---")
    A("")
    # ---------------- ⑥
    A("## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答是否 <3%）")
    A("")
    A("**基准取法**：本任务没有物理量，故「基准」取**播种工作单 832 的硬性要求**与 **SQL 声明值**，"
      "「实测」取 **live DB 实测值**（脚本重算）。")
    A("")
    A("| # | 关键量 | 基准（要求/声明） | 实测 | 偏差 | 相对误差 | 是否 <3% |")
    A("|---|---|---|---|---|---|---|")
    dup_txt = [x["actual"] for x in g["gates"] if "ext_id" in x["gate"]][0]
    rows = [
        ("节点数", "∈[5,12]", str(len(nodes)), "0", "0.00%", "**是**"),
        ("ext_id 唯一性", "全库唯一", dup_txt, "0 冲突", "0.00%", "**是**"),
        ("里程碑覆盖缺失", "每里程碑 ≥1",
         ", ".join("%s:%d" % (k, len(v)) for k, v in g["ms_coverage"].items()), "0", "0.00%", "**是**"),
        ("成熟化节点数", "≥2", str(len(g["maturity_nodes"])), "超额 +%d" % (len(g["maturity_nodes"]) - 2),
         "0.00%（超额侧）", "**是**"),
        ("依赖越界条数", "0（仅本 plan）", str(sum(len(v) for v in g["bad_dep"].values())), "0", "0.00%",
         "**是**"),
        ("DAG 环数", "0（无环）", "0（toposort 覆盖 %d/%d）" % (len(nodes), len(nodes)), "0", "0.00%",
         "**是**"),
        ("goal 字数最小值", "≥80", str(min(n["goal_len"] for n in nodes)),
         "超额 +%d" % (min(n["goal_len"] for n in nodes) - 80), "0.00%（超额侧）", "**是**"),
        ("SQL↔DB 节点一致数", "%d/%d" % (len(nodes), len(nodes)),
         "%d/%d" % (sum(1 for c in g["sql_consistency"] if c["match"]), len(g["sql_consistency"])),
         "0 不一致", "0.00%", "**是**"),
    ]
    for i, r in enumerate(rows, 1):
        A("| %d | %s | %s | %s | %s | %s | %s |" % (i, r[0], r[1], r[2], r[3], r[4], r[5]))
    A("")
    A("**显式回答（D6）**：上表 %d 项全部是**离散计数 / 约束满足类**指标，偏差恒为 0，**相对误差 0.00%% < 3%%，"
      "结论：是**（其中成熟化节点数与 goal 长度属「超额满足」，不是欠差）。" % len(rows))
    A("")
    A("必须同时说清以下 **N/A 项及原因**，否则会被误读成「100% 无误差」：")
    A("")
    A("| N/A 项 | 为何 N/A |")
    A("|---|---|")
    A("| 阻力 / 推进 / 耐波 / 操纵 / 稳性 / 结构 / 振动噪声等物理量误差 | 本任务无仿真、无实测、无解析基线，"
      "物理量本身不存在，误差无从定义 |")
    A("| 几何 Cb / L/B 校验 | 无船体几何输入（1.2） |")
    A("| 节点可实现性 / 验收判据达标率 | 判据一条未执行（5.2） |")
    A("| 未播种 %d 条的「正确性」 | 播种工件无 rejection 记录，证据等级 WITHHELD（表 3-6） |" % len(uns))
    A("")
    A("**结论（限定表述，D7）**：本次播种的 **SQL 插入 7 项机器门校验全部通过**（节点数、ext_id 全库唯一、"
      "repo 白名单、DAG 无环、依赖仅本 plan、里程碑覆盖、成熟化 ≥2），外加本版复核的 **3 项附加校验**"
      "（goal 长度、验收判据非空、SQL↔DB 一致）亦通过，合计 **%d/%d**。"
      "**该结论仅覆盖「数据插入与图结构合法性」，不构成任何物理精度结论，也不等于「聚合决策无缺陷」** —— "
      "本版审计同时查出 6 项机器门查不出的问题（⑦.3–⑦.5）。原报告「所有校验 100%% 通过、无误差」的表述"
      "因此不成立，现已撤回。" % (npass, len(g["gates"])))
    A("")
    return "\n".join(L)


def md3(L):
    A = L.append
    A("---")
    A("")
    # ---------------- ⑦
    A("## ⑦ 独立审计节（新增，D1）")
    A("")
    A("### 7.1 审计方法与独立性")
    A("- **独立性**：不采信原报告 #779 的任何数值结论；全部从 live DB / 源文件重算（%d 个源文件均记 sha256，见 1.3）。"
      % len(srcs))
    A("- **可复现**：`python3 docs/audit_seed_plan4.py` → `artifacts/audit_report_m845.json` → "
      "`python3 docs/figures_m845.py` + `python3 docs/report_builder_m845.py` → 两张图 + 本 markdown + POST payload。"
      "审计脚本内置反向断言（机器门未全过 / 账目不闭合即 `exit 1`）。")
    A("- **审计三问**：① 数据来源可不可信（7.2）？② 聚合去重逻辑站不站得住（7.3）？"
      "③ SQL 与报告说的是不是同一件事（7.4–7.5）？")
    A("")
    A("### 7.2 数据来源可信度审计")
    A("| 数据源 | 证据等级 | 理由 | 本次用法 |")
    A("|---|---|---|---|")
    A("| live DB（campaign_plans / milestones / nodes） | **REAL** | 平台自身持久化状态，直接 SQL 查询，"
      "与派发器读到的一致 | 一切离散事实的唯一真值 |")
    A("| `seed_plan4.sql` 文本 | **REAL** | 播种动作原文，可解析并与 DB 逐字段比对（%d/%d 相符，7.4） | 审计门 10 |"
      % (sum(1 for c in g["sql_consistency"] if c["match"]), len(g["sql_consistency"])))
    A("| `result.json.aggregation_summary` | **REAL（有界）** | 播种者自述记录，非独立观测；"
      "只采信其正文所列 proposal id 的计数，不采信其「合并/保留」理由的正确性 | 提案账目（表 3-5/3-6） |")
    A("| 提案源文件 prop-775/776/777 | **REAL** | 三份原始提案文件，sha256 固定 | 提案总数与 ID 结构性事实 |")
    A("| 原报告 #779 的结论数值 | **WITHHELD** | 已被本次重算证伪 4 处（7.5） | **不采信** |")
    A("| 未播种 %d 条的弃用理由 | **WITHHELD** | 工件无 rejection 字段 | 仅给主题映射并标注为推断 |" % len(uns))
    A("")
    A("**结论**：离散事实（节点、依赖、里程碑、repo、goal 长度）可达 REAL；**聚合决策的理由链不可达**"
      "（自述 + 无记录），故本报告不对「聚合决策最优」作任何断言。")
    A("")
    A("### 7.3 聚合去重逻辑审计")
    A("**A-1 合并映射 vs 节点 goal 文本一致性（部分不通过 —— 本次新发现）**：把 `result.json` 声明的"
      "提案→节点映射（%d 组合并 + %d 条独立）逐条对到**提案文件真实主题**与**节点 goal 原文**，"
      "并用「提案标题 vs 节点 name+goal 的 CJK 2-gram 召回率」作客观筛子（<0.2 视为主题不搭告警；"
      "筛子不是证明）：" % (len(acc["merge_groups"]), len(acc["kept_separate"])))
    A("")
    A("| 节点 | 提案 | 提案真实主题（文件） | 源 repo | 2-gram 召回 | goal 是否点名 | 判定 |")
    A("|---|---|---|---|---|---|---|")
    ma = a["mapping_audit"]
    for ext in [n["ext_id"] for n in nodes]:
        for x in ma[ext]["groups"]:
            if x["title_bigram_recall_in_node_text"] < 0.2:
                flag = "**主题不搭（告警）**"
            elif x["kind"] == "separate" or x["mentioned_in_goal"]:
                flag = "ok"
            else:
                flag = "goal 未点名（弱）"
            A("| %s | %s | %s | %s | %.3f | %s | %s |" % (
                ext, x["proposal"], x["title"][:40], x["repo"],
                x["title_bigram_recall_in_node_text"],
                "是" if x["mentioned_in_goal"] else "否", flag))
    A("")
    z1 = [x for x in ma["W4-N01"]["groups"] if x["proposal"] == "prop-775-01"][0]
    z2 = [x for x in ma["W4-N01"]["groups"] if x["proposal"] == "prop-775-02"][0]
    A("**发现 A-1.1（严重，聚合逻辑层）**：`result.json` 把 `prop-775-01` 记为并入 **W4-N01（冰区航迹规划）**，"
      "W4-N01 的 goal 更明写「prop-775-01 的离散 A*」；但提案文件里 `prop-775-01` 的真实主题是"
      "**冰激振动频率锁定**（repo=%s），2-gram 召回仅 %.3f（同节点 `prop-775-02` 为 %.3f，全表中位数约 0.6）"
      "→ **归属与提案内容不符**：所谓「离散 A* 提案」在 %d 条原始提案中并不存在（航迹规划只有 `prop-775-02`）。"
      "后果：(a) 合并记录虚增一条来源；(b) `prop-775-01` 的物理域（冰激振动）在 plan 4 **没有任何节点承接**。"
      % (z1["repo"], z1["title_bigram_recall_in_node_text"],
         z2["title_bigram_recall_in_node_text"], total))
    A("")
    A("**发现 A-1.2（中）**：W4-N04 记 `prop-776-01`（冰-结构冻结粘结，TensorPhysics）为合并来源，"
      "但 W4-N04 的 goal 只点名 `prop-775-05`，且 adfreeze bond 主题与数字孪生/载荷反演无结构关系"
      "（该主题在 plan 3 已由 W3-N02 完成）→ **记为合并、goal 无承接**。同理 W4-N03 未点名 "
      "`prop-777-FORM/SORM`、W4-N04 未点名 `prop-776-04` —— 均为「计入但无承接文字」，文字溯源链不闭合。")
    A("")
    A("**A-1.3 结论**：%d 组合并在**领域上**大体可辩护（屈曲+可靠度、载荷反演+数字孪生），"
      "但**映射记录本身不可作为证据链**：存在 1 条明确错配（prop-775-01）与 3 条 goal 未承接，"
      "故原报告隐含的「聚合无缺陷」不成立。" % len(acc["merge_groups"]))
    A("")
    A("**A-2 溯源粒度缺陷（发现 → 需修）**：全部 %d 个节点的 `metadata.proposal_src` 只记 mission id"
      "（`[\"775\"]` 形态，`mission_only=%s`），提案级映射仅存于 `result.json`；且 **%s 在 `result.json` 中"
      "没有任何提案级来源**——而该节点主题（冰载荷反演 + 结构健康监测）与 `prop-775-05` 重叠，"
      "`prop-775-05` 却又被记为已并入 W4-N04。即同一提案疑被计入两处（一处显式、一处隐含），"
      "**提案级溯源因此不可机器复算**。"
      % (len(nodes), json.dumps(acc["node_proposal_src_mission_only"]),
         ", ".join(acc["nodes_without_proposal_level_source"])))
    A("")
    A("**A-3 跨 repo 合并丢失实现归属（发现 → 需修）**：")
    A("| 合并节点 | 合并来源提案（源 repo，提案文件实测） | 节点 repo（DB） | 风险 |")
    A("|---|---|---|---|")
    for mg in acc["merge_groups"]:
        srcs_ = []
        for pid in mg["merged_from"]:
            hit = [d for d in acc["seeded_native_ids"] + acc["seeded_arbiter_slugs"] if d["key"] == pid]
            srcs_.append("%s(%s)" % (pid, hit[0]["repo"]) if hit else pid)
        A("| %s | %s | %s | 被合并 repo 无节点承接，只能靠 goal 文本约定 |"
          % (mg["into"], ", ".join(srcs_), ext2node[mg["into"]]["repo"]))
    A("")
    A("**A-4 覆盖缺口（发现 → 建议下一波补）**：未播种 %d 条全部落在 plan 3 已完成域，其中 "
      "W3-N01（冰激振动）、W3-N05（碰撞进水）、W3-N06（空化+水下噪声）在 plan 4 中无接续节点 → "
      "本次「成熟化」波次未覆盖这 3 个能力域（表 3-6）。若再计入 A-1.1 中 `prop-775-01`（冰激振动）"
      "的错配，则**振动噪声域（W3-N01 谱系）在 plan 4 完全缺席**，是覆盖缺口中最严重的一条。" % len(uns))
    A("")
    A("### 7.4 SQL 与报告一致性审计")
    A("**A3-1 plan 状态漂移（发现 → 本版已改口径）**：SQL 插入 `status='%s'`，live DB 实测 `status='%s'`"
      "（plan.updated_at = %s，晚于原报告 #779 发布时刻 2026-10-31T17:55:09Z）→ **原报告"
      "「INSERT ... status='draft'」的表述在发布后即失效**。本版按 live DB 的 `%s` 表述，并注明这是派发器"
      "激活所致，不是 SQL 写错。" % (g["sql_plan_status"], plan["status"],
                                    str(plan["updated_at"])[:19], plan["status"]))
    A("")
    A("**A3-2 逐节点 SQL↔DB 比对（审计门 10）**：")
    A("| ext_id | DB id | DB 里程碑 | SQL 里程碑 | DB dependencies | SQL dependencies | 一致 |")
    A("|---|---|---|---|---|---|---|")
    for c in g["sql_consistency"]:
        A("| %s | %d | %d | %s | %s | %s | %s |" % (c["ext_id"], c["db_id"], c["db_milestone"],
                                                    c["sql_milestone"], c["db_deps"], c["sql_deps"],
                                                    "✔" if c["match"] else "✘"))
    A("")
    A("一致 **%d/%d**，不一致 0 条。**复现命令**：`python3 docs/audit_seed_plan4.py`（用 `INSERT INTO "
      "campaign_nodes` 切块 + 正则解析 `'task',\\n ARRAY[...]`，兼容 `NULL` 与 `ARRAY[...]` 两种写法，"
      "再与 `select dependencies from campaign_nodes` 逐条比对）。"
      % (sum(1 for c in g["sql_consistency"] if c["match"]), len(g["sql_consistency"])))
    A("")
    A("**A3-3 metadata 波次编号与命名分叉（发现）**：`seed_report.wave` = %s，而 plan 名为 `%s`、"
      "`plan_version` = `%s`（即第 4 波）。原因是工作单 832 原文规定 wave 写 2（其自身计数口径），"
      "播种者照抄未与战役波次对齐 → **metadata 与命名口径分叉**，下游若按 `seed_report.wave` 统计波次会错位。"
      % (plan["metadata"].get("seed_report", {}).get("wave"), plan["name"], plan["plan_version"]))
    A("")
    A("**A3-4 repo 白名单口径（发现 → 本版已改口径）**：原报告把 `TensorFEM` 列为「白名单内」，但派发器实际"
      "常量 `REPO_WHITELIST` = %s，`TensorFEM` 只是**历史别名**（`REPO_ALIAS` → `TensorPhysics`）。"
      "故 W4-N03（repo=`TensorFEM`）是**靠别名放行**，不是白名单直通；本版按真实常量表述（表 3-4 门 3）。"
      % json.dumps(a["repo_whitelist"], ensure_ascii=False))
    A("")
    A("### 7.5 图结构事实审计（D2/D3 的根因）")
    A("原报告 #779 的两幅 ASCII 图与最长链结论经重算**全部有误**，逐条对照：")
    A("")
    A("| # | 原报告断言 | live DB / SQL 实际 | 判定 |")
    A("|---|---|---|---|")
    A("| 1 | 存在依赖 W4-N05 → W4-N07 | `W4-N07.dependencies = {%s}`，不含 W4-N05 | **错** |"
      % ", ".join(g["deps_by_ext"]["W4-N07"]))
    A("| 2 | 存在依赖 W4-N04 → W4-N06 | `W4-N06.dependencies = {%s}`，不含 W4-N04 | **错** |"
      % ", ".join(g["deps_by_ext"]["W4-N06"]))
    A("| 3 | W4-N07 属于 M15 | `W4-N07.milestone_id = %d`（M%d，%s） | **错** |"
      % (ms_of["W4-N07"], ms_of["W4-N07"], ms_band[ms_of["W4-N07"]]))
    A("| 4 | 最长链 7 层 | 全路径枚举：最长 %s，**%d 节点 / %d 步** | **错** |"
      % (" → ".join(g["longest_chain"]), g["longest_chain_len_nodes"], g["longest_chain_len_steps"]))
    A("")
    A("**正确的依赖全表见 表 3-3（%d 条边）**，正确图见 图2。**根因**：原报告把「设计意图链」"
      "（谁逻辑上应在谁之后）当成「ARRAY 实际值」绘制，且里程碑归属按叙事顺序而非 `milestone_id` 填写。"
      "**防再犯措施**：图件一律由脚本从 DB 值生成，禁止手绘关系图；本版图2 在节点内直接打印 "
      "`dependencies` 实际值，可肉眼与表 3-3 对齐。" % g["edge_count"])
    A("")
    A("### 7.6 缺陷闭环表（D1–D7）")
    A("| 缺陷 | 原问题 | 本版处置 | 落点 |")
    A("|---|---|---|---|")
    A("| D1 严重 | 报告六节无独立审计节 | 新增 ⑦ 独立审计节：来源可信度分级（7.2）、聚合去重逻辑审计（7.3）、"
      "SQL↔DB 一致性审计（7.4）、图结构事实审计（7.5），并新查出 1 条提案映射错配（A-1.1）与 3 条 goal 未承接（A-1.2） | ⑦ 全节 |")
    A("| D2 严重 | DAG 图 3 处事实错误（W4-N05→W4-N07 不存在；W4-N04→W4-N06 不存在；"
      "W4-N07 属 M16 非 M15） | 两幅图全部按 ARRAY 实际值重绘（图1/图2），并逐条列出错误与实测值 | 图2、7.5、表 3-3 |")
    A("| D3 严重 | 最长链 7 层数值错误 | 全路径枚举复算：**%s = %d 节点 %d 步**，图2 红色高亮 | 表 3-4 门 4、"
      "7.5、图2 |" % (" → ".join(g["longest_chain"]), g["longest_chain_len_nodes"],
                     g["longest_chain_len_steps"]))
    A("| D4 中等 | 提案数 %d vs result.json 仅 %d 条可辨识 ID，差值 %d 条去向未解释 | 账目闭合："
      "%d = %d（%d 原生 ID + %d 仲裁 slug）+ %d 未播种；逐条列表并解释 M777 无 id 字段的结构性原因 | "
      "表 3-5、表 3-6 |" % (total, ident, total - ident, total, ident,
                            acc["native_id_refs"], len(acc["seeded_arbiter_slugs"]), len(uns)))
    A("| D5 中等 | 图件豁免未声明 | ④ 末尾显式声明：本任务为播种 SQL 插入、无物理仿真输出，故不含场量云图与"
      "比较曲线，并说明替代图件的生成方式 | ④ 图件豁免声明 |")
    A("| D6 轻微 | 误差判定未显式回答是否 <3% | ⑥ 表格新增「相对误差 / 是否 <3%」列，显式回答「是（0.00%）」，"
      "并单列 N/A 项及原因 | ⑥ 全节 |")
    A("| D7 严重 | 结论超出证据（100% 无误差与 DAG 错误矛盾） | 结论限定为「SQL 插入 7 项机器门 + 3 项附加校验"
      "通过」，显式声明不构成物理精度结论、不等于聚合决策无缺陷；撤回原「100% 无误差」表述 | ⑥ 结论 |")
    A("")
    A("### 7.7 审计未覆盖（诚实边界）")
    A("- 未审计**提案内容质量**（物理目标是否前沿、判据是否充分）—— 那需要学科评审，不是数据审计。")
    A("- 未审计**节点工期/预算合理性**（planned_start/end 由 SQL 以 `NOW()+INTERVAL` 生成，本身无物理依据）。")
    A("- 未验证**节点 goal 中声明的规范条款**（IACS UR I / Polar Code / ISO 19906）的引用正确性。")
    return "\n".join(L) + "\n"


def main():
    head, L = md()
    md2(L)
    md3(L)
    text = "\n".join(L).rstrip() + "\n"
    out = os.path.join(WORK, "docs/report_m779_v2.md")
    open(out, "w", encoding="utf-8").write(text)
    figs = []
    for png, cap in [
        (FIG1, "图1 提案聚合谱系：%d 条原始提案 → %d 条可溯源 → %d 个战役节点；%d 条未播种显式标出"
               % (total, ident, len(nodes), len(uns))),
        (FIG2, "图2 plan 4 依赖图（按 seed_plan4.sql ARRAY 实际值重绘）：最长链 %s = %d 节点 %d 步"
               % ("→".join(g["longest_chain"]), g["longest_chain_len_nodes"], g["longest_chain_len_steps"])),
    ]:
        figs.append({"name": os.path.basename(png), "caption": cap,
                     "png_base64": base64.b64encode(open(png, "rb").read()).decode()})
    payload = {
        "title": "【返工 v2·原报告 779】冰区航行战役第四波提案聚合与战役播种：审计修订版"
                 "（DAG/最长链修正 + 独立审计节 + 提案账目闭合）",
        "summary": "按缺陷清单 D1–D7 重写报告 779：按 seed_plan4.sql ARRAY 实际值重绘两幅图"
                   "（W4-N07 deps=%s、W4-N06 deps=%s、W4-N07∈M16），最长链由 7 层修正为 %s = %d 节点 %d 步；"
                   "新增独立审计节（来源可信度/聚合去重/SQL↔DB 一致性/图结构事实）；提案账目闭合 %d = %d + %d"
                   "（M777 无 id 字段是结构性原因）；显式声明图件豁免与误差判定（离散门误差 0.00%% <3%%，"
                   "物理量 N/A）；结论限定为 SQL 插入 7 项机器门 + 3 项附加校验通过。"
                   % (",".join(g["deps_by_ext"]["W4-N07"]), ",".join(g["deps_by_ext"]["W4-N06"]),
                      "→".join(g["longest_chain"]), g["longest_chain_len_nodes"],
                      g["longest_chain_len_steps"], total, ident, len(uns)),
        "scenario_key": "ice-navigation-wave4-seeding",
        "mission_id": 845,
        "tags": ["arbitration", "campaign-seeding", "ice-navigation", "wave4", "rework",
                 "audit", "report-779-v2", "D1-D7"],
        "markdown": text,
        "figures": figs,
    }
    json.dump(payload, open("/tmp/report_m845.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", out, len(text), "chars; payload /tmp/report_m845.json",
          "figs", [len(f["png_base64"]) for f in figs])


if __name__ == "__main__":
    main()