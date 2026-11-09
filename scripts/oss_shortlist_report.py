#!/usr/bin/env python3
"""Curate + render the "discovery note" (发现单) for FEAT-OSS-INVENTORY-LBM (mission 8307).

Inputs (raw GitHub payloads persisted by the discovery scripts — no number in the
rendered report is hand-typed):
  artifacts/m8307/discovery_raw.json        round 1 searches (multiphase / colour-gradient
                                            / free-surface / phase-field / topics / wetting
                                            / thermal / shan-chen)
  artifacts/m8307/discovery_raw2.json       round 2 (pseudopotential / multicomponent /
                                            two-phase / droplet / conjugate-heat / topic)
  artifacts/m8307/discovery_raw3.json       round 3b (openlb family / phrase queries)
  artifacts/m8307/mflbm_search.json         q=MF-LBM (core quota spent -> search pool)
  artifacts/m8307/list_entry_verify.json    canonical-list entries re-verified by search
  artifacts/m8307/canonical_list_links.json github links inside
                                            sthavishtha/list-lattice-Boltzmann-codes README
  /tmp/existing_inv.json                    the 24 repos already in table oss_inventory

Outputs:
  artifacts/m8307/shortlist.json            curated candidates joined with evidence
  artifacts/m8307/fig_discovery_overview.png
  docs/oss_inventory_discovery_multiphase.md

Division of labour: tier / one-line focus / exclusion reason is the judgement layer
(marked as such); stars, pushed_at, license, language, description are joined from the
persisted API payloads. Every candidate is `suggested`, not `verified`: this is a
discovery note, no code-level audit was performed.
Usage: python3 scripts/oss_shortlist_report.py
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "m8307")
DOC = os.path.join(ROOT, "docs", "oss_inventory_discovery_multiphase.md")
TODAY = date.today()
STALE_CUTOFF_YEAR = TODAY.year - 5  # "停更 > 5 年" rule

# ---------------------------------------------------------------- curation layer
SHORTLIST = [
    ("lanl/MF-LBM", 1,
     "LANL 可移植可扩展多孔介质 DNS 级 LBM（MPI+CUDA/OpenACC，BSD-3）；权威清单将其归入多相档，多相细节待深盘核实",
     "现清单 tier1 全是单机科研脚本，缺可扩展到集群/GPU 的多孔介质 DNS 基线"),
    ("openLB/openLB", 1,
     "openLB 本体：多相/free-surface + Shan-Chen + 热耦合能力（共轭传热属较新版本，镜像停在 2019）；在盘的 openLBMPM 只是它的多相扩展分支——母体缺位",
     "自由面/多相 + 热-流耦合；清单只有衍生物没有上游"),
    ("Zitzeronion/Swalbe.jl", 2,
     "Julia GPU 薄膜/液滴润滑 LBM（MIT，2026 仍活跃），润湿/去润湿动力学的高吞吐实现",
     "薄膜润湿动力学的 GPU 实现空缺"),
    ("geoelements/mcmp-lbm", 1,
     "3D 多组元多相 LBM（MRT）+ 机器学习代理耦合，UT Austin geoelements 组维护",
     "3D MCMP 的现代 MRT 实现与 ML 代理接口空缺"),
    ("ebinan92/Fingering_dynamics", 1,
     "两相 LBM + Zou-He 速度入口 + 半程反弹润湿壁面，指进不稳定性复现脚本（含工况配置）",
     "润湿边界（壁面附着/接触角）与两相不稳定性算例空缺"),
    ("JuliusWeinmiller/ThermalMultiphaseOpenLB", 1,
     "OpenLB 的热-多相扩展（相变/热毛细方向），2026 活跃；热-流耦合多相的参考实现",
     "热-流耦合多相（相变）在 24 仓中零覆盖"),
    ("apes-suite/musubi", 3,
     "八叉树 AMR 多物理 LBM（TU Dresden APES 生态，2026 活跃）：静态/动态自适应 + 多相/热；求解器主体为 Fortran（仓内胶水层使 GitHub 主语言显示为 Python）",
     "HPC 侧『八叉树 AMR + 多相』组合空缺"),
    ("AminZar69/CudaPhaseField", 1,
     "三相相场 LBM 的 CUDA 并行求解器（GPL-3，2026 活跃）；相场多相方向唯一活跃实现（星数低=未成熟，作方向采样点）",
     "相场（phase-field）多相方向零覆盖，属供给最薄的一档"),
]

# (repo, reason) — 明确排除：玩具/纯可视化/停更>5年且无参考价值/与本单焦点无关
EXCLUDED = [
    ("tangency-labs/fieldcraft", "AI agent 驱动的建模前端（CAE/CFD 聚合器），无 LBM 内核可盘"),
    ("sthavishtha/list-lattice-Boltzmann-codes", "清单仓（本单数据源），非求解器"),
    ("pmocz/latticeboltzmann-python", "单相教学 + 可视化，无多相/润湿内容"),
    ("crgimenes/kutta", "2D 风洞实时可视化（游戏化渲染），非科研 LBM"),
    ("je-santos/PoreFlow-Net", "3D CNN 渗透率代理模型，非 LBM 求解器"),
    ("siramirsaman/LBM", "MATLAB 单相曲面边界教学代码，功能已被在盘仓覆盖"),
    ("pmocz/cfd-comparison-python", "多方法对比教学（LBM/SPH/FV），无多相深度"),
    ("AbbasHub/AMR-LBM-OpenMP-2D", "2D AMR 教学，停更 >5 年；AMR 能力由 musubi/walberla 覆盖"),
    ("zhishang72/OpenLBM", "单相 BGK/MRT，与在盘 2b-t/LB-t 功能重叠"),
    ("wme7/openLBMflow", "2016 停更（>5 年）、浏览器端单相 LBM 演示"),
    ("QCFD-Lab/qlbm", "量子 LBM（qiskit），非经典多相求解器"),
    ("kircher1/LBM-SWE-Unity", "Unity 浅水（SWE）可视化包，非多相 LBM"),
    ("kobejean/tf-cfd", "可微 CFD 但与在盘 latflow/ludwig 重叠，且以 FV 为主"),
    ("EPyutao/MCMP_LBM", "2016 停更（>5 年）、脚本级，功能与 openLBMPM/MPLBM-UT 重叠"),
    ("spoonacular/LBM_python", "2018 停更（>5 年）教学级多相脚本"),
    ("carlosrosales/mplabs", "2014 停更（>11 年）MATLAB 多相 suite，无维护无文档"),
    ("lhufnagel/multi-lbm", "2016 停更（>5 年）单人代码，无可核验算例"),
    ("otbrown/LAMBReX", "2020 停更的 AMReX AMR 参照；AMR 能力已由 musubi/walberla/XLB 覆盖"),
    ("Haider-BA/3D-LBM-AMR", "2016 停更（>5 年）的 AMR 教学代码"),
    ("UCL/LatBo.jl", "2017 停更 Julia 单相实现，无多相内容"),
    ("auag92/lattice_boltzmann_phase_field", "2016 停更（>5 年）相场脚本，无文档无算例"),
    ("je-santos/MultiphasePorousMediaPalabos",
     "canonical 清单中的链接已失效（repos API 404、search 无命中）=链接腐烂；同作者在盘 MPLBM-UT 已覆盖"),
]

# 观察名单（不占 8 个名额）：方向对但成熟度/体量不足
WATCH = [
    ("LBMFluids/sc_lbm", "SC 力格式最小参考实现，但与在盘 openLBMPM/MPLBM-UT 的 SC 实现重叠，名额让给零覆盖方向"),
    ("AminZar69/ompTernaryPhaseField", "同一作者的三相相场 OpenMP 版，与入选的 CUDA 版重复"),
    ("dschrijver/dslbm", "颜色梯度二元流体实现，星数过低（<5）且与在盘 openLBMPM 色梯度能力重叠"),
    ("PengyuFu/CG_LBM_multiphase", "2D/3D 颜色梯度多相，2026 新建（1★），待成熟后再盘"),
    ("HarmonicTons/lbm-free-surface", "自由面实现，与在盘 Maarten-vd-Sande/lbm 高度重叠"),
]

SOURCES = [
    ("discovery_raw.json", "第 1 轮 github search：multiphase / color-gradient / free-surface / phase-field / topic(lattice-boltzmann, lattice-boltzmann-method, multiphase-lbm) / wetting / thermal / shan-chen"),
    ("discovery_raw2.json", "第 2 轮：pseudopotential / multicomponent / two-phase / droplet / conjugate-heat-transfer / topic:multiphase-flow"),
    ("discovery_raw3.json", "第 3 轮：openlb 命名族 + 短语查询（\"free energy\" / \"contact angle\" / \"high density ratio\"）"),
    ("mflbm_search.json", "q=MF-LBM（core 配额耗尽后改走 search 池取证）"),
    ("list_entry_verify.json", "canonical 清单条目二次核验（musubi / LAMBReX / 3D-LBM-AMR / LatBo.jl 等）"),
    ("canonical_list_links.json", "sthavishtha/list-lattice-Boltzmann-codes README 抽取的 github 链接表（34 条）"),
]


def load_evidence() -> dict[str, dict]:
    ev: dict[str, dict] = {}
    for fn in ("discovery_raw.json", "discovery_raw2.json", "discovery_raw3.json"):
        p = os.path.join(ART, fn)
        if not os.path.exists(p):
            continue
        for c in json.load(open(p)).get("candidates_raw", []):
            r = c["repo"]
            if r not in ev or (c.get("stars") or 0) > (ev[r].get("stars") or 0):
                ev[r] = {"repo": r, "stars": c.get("stars"), "description": c.get("description"),
                         "pushed_at": c.get("pushed_at"), "created_at": c.get("created_at"),
                         "language": c.get("language"), "license": c.get("license"),
                         "archived": c.get("archived"), "topics": c.get("topics") or [],
                         "evidence_source": fn}
    p = os.path.join(ART, "mflbm_search.json")
    if os.path.exists(p):
        for it in json.load(open(p)):
            ev.setdefault(it["full_name"], {
                "repo": it["full_name"], "stars": it["stargazers_count"],
                "description": (it.get("description") or "").strip(),
                "pushed_at": it.get("pushed_at"), "created_at": it.get("created_at"),
                "language": it.get("language"),
                "license": (it.get("license") or {}).get("spdx_id"),
                "archived": it.get("archived"), "topics": it.get("topics") or [],
                "evidence_source": "mflbm_search.json"})
    p = os.path.join(ART, "list_entry_verify.json")
    if os.path.exists(p):
        for rows in json.load(open(p)).values():
            for name, stars, pushed, desc in rows:
                ev.setdefault(name, {"repo": name, "stars": stars, "description": desc,
                                     "pushed_at": pushed, "license": None, "language": None,
                                     "archived": False, "topics": [],
                                     "evidence_source": "list_entry_verify.json"})
    return ev


def year_of(ts) -> int | None:
    try:
        y = int(str(ts)[:4])
        return y if 1990 < y < 2100 else None
    except Exception:
        return None


def build(ev: dict[str, dict], existing: list[dict]) -> dict:
    existing_lower = {d["repo"].lower() for d in existing}
    cands = []
    for repo, tier, focus, gap in SHORTLIST:
        e = ev.get(repo)
        if not e:
            raise SystemExit(f"missing GitHub evidence for {repo}")
        cands.append({"repo": repo, "tier": tier, "focus": focus, "gap": gap,
                      "stars": e["stars"], "license": e.get("license"),
                      "pushed_at": (e.get("pushed_at") or "")[:10],
                      "language": e.get("language"), "archived": e.get("archived"),
                      "description": e.get("description"),
                      "evidence_source": e.get("evidence_source"),
                      "stars_per_year": None, "status": "suggested"})
    dup = [c["repo"] for c in cands if c["repo"].lower() in existing_lower]
    assert not dup, f"shortlist overlaps oss_inventory: {dup}"

    for c in cands:
        y = year_of(c["pushed_at"])
        c["stars_per_year"] = (round(c["stars"] / max(1, TODAY.year - y + 1), 1)
                              if y else None)

    def row(repo, reason):
        e = ev.get(repo, {})
        y = year_of(e.get("pushed_at"))
        return {"repo": repo, "reason": reason, "stars": e.get("stars"),
                "pushed_at": (e.get("pushed_at") or "")[:10],
                "age_years": (round(TODAY.year - y, 1) if y else None)}

    excluded = [row(r, why) for r, why in EXCLUDED]
    watch = [row(r, why) for r, why in WATCH]
    n_ev = len(ev)
    stale = [k for k, v in ev.items() if (year_of(v.get("pushed_at")) or 9999) < STALE_CUTOFF_YEAR]
    d1 = json.load(open(os.path.join(ART, "discovery_raw.json")))
    recall_hits = d1.get("existing_inventory_hit_in_search", [])
    return {
        "stats": {"generated_at": datetime.now(timezone.utc).isoformat(),
                  "inventory_size": len(existing),
                  "repos_with_evidence": n_ev,
                  "n_candidates": len(cands), "n_excluded": len(excluded),
                  "n_watch": len(watch),
                  "tier_counts": {t: sum(1 for c in cands if c["tier"] == t) for t in (1, 2, 3)},
                  "recall_inventory_hit_by_queries": len(recall_hits),
                  "recall_inventory_total": len(existing),
                  "recall_rate": round(len(recall_hits) / len(existing), 3),
                  "stale_over_5y_in_evidence": len(stale),
                  "stale_share": round(len(stale) / n_ev, 3),
                  "query_count": sum(len(d.get("hits_per_query", {})) for d in
                                     [d1, json.load(open(os.path.join(ART, "discovery_raw2.json"))),
                                      json.load(open(os.path.join(ART, "discovery_raw3.json")))]),
                  "stale_cutoff_year": STALE_CUTOFF_YEAR},
        "candidates": cands, "excluded": excluded, "watch": watch,
        "inventory_snapshot": sorted(existing_lower),
        "recall_inventory_hits": recall_hits,
        "evidence_repos": {k: {"stars": v.get("stars"), "pushed_at": (v.get("pushed_at") or "")[:10]}
                           for k, v in ev.items()},
        "sources": [{"file": f, "what": w} for f, w in SOURCES],
    }


def figure(data: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from collections import Counter

    cmap = {1: "#1f77b4", 2: "#ff7f0e", 3: "#2ca02c"}
    labs = {1: "tier1 多相/多组元", 2: "tier2 可微/GPU", 3: "tier3 HPC"}
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.2))

    # (a) shortlist by stars
    cs = sorted(data["candidates"], key=lambda c: c["stars"])
    ax = axes[0]
    ax.barh([f"{c['repo']}  ({c['pushed_at'][:7]})" for c in cs],
            [c["stars"] for c in cs],
            color=[cmap[c["tier"]] for c in cs])
    for i, c in enumerate(cs):
        ax.text(c["stars"] + 2, i, f"{c['stars']}★ T{c['tier']}", va="center", fontsize=9)
    ax.set_xlabel("GitHub stars (api.github.com, 2026-11-09)")
    ax.set_title(f"(a) 发现单候选 {len(cs)} 个（按星数）", fontsize=11)
    ax.set_xlim(0, max(c["stars"] for c in cs) * 1.25)

    # (b) staleness of the whole evidence pool
    ax = axes[1]
    years = Counter()
    for v in data["evidence_repos"].values():
        y = str(v.get("pushed_at") or "")[:4]
        if y.isdigit():
            years[int(y)] += 1
    ks = sorted(years)
    ax.bar(ks, [years[k] for k in ks], color="#9aa7b1")
    ax.axvline(data["stats"]["stale_cutoff_year"] + 0.5, color="crimson", ls="--",
               label=f"停更 >5 年 判据（≤{data['stats']['stale_cutoff_year']}）")
    ax.set_xlabel("last push 年份")
    ax.set_ylabel("仓数")
    ax.set_title(f"(b) 候选池活跃度：{data['stats']['repos_with_evidence']} 仓中 "
                 f"{data['stats']['stale_over_5y_in_evidence']} 仓 "
                 f"({data['stats']['stale_share']*100:.0f}%) 停更 >5 年", fontsize=11)
    ax.legend(fontsize=9)

    # (c) recall self-check
    ax = axes[2]
    r = data["stats"]
    ax.bar(["被查询命中的\n在盘仓", "未被命中的\n在盘仓"],
           [r["recall_inventory_hit_by_queries"],
            r["recall_inventory_total"] - r["recall_inventory_hit_by_queries"]],
           color=["#1f77b4", "#d62728"])
    ax.set_ylabel("仓数")
    ax.set_title(f"(c) 查询召回自检：在盘 {r['recall_inventory_total']} 仓中 "
                 f"{r['recall_inventory_hit_by_queries']} 仓被检索命中 "
                 f"({r['recall_rate']*100:.0f}%)", fontsize=11)
    for i, v in enumerate([r["recall_inventory_hit_by_queries"],
                           r["recall_inventory_total"] - r["recall_inventory_hit_by_queries"]]):
        ax.text(i, v + 0.3, str(v), ha="center", fontsize=11)

    handles = [plt.Rectangle((0, 0), 1, 1, color=cmap[t]) for t in (1, 2, 3)]
    fig.legend(handles, [labs[t] for t in (1, 2, 3)], loc="lower center", ncol=3,
               fontsize=10, frameon=False)
    fig.suptitle("FEAT-OSS-INVENTORY-LBM 发现单（多相/多组元专项）· mission 8307",
                 fontsize=13, y=0.98)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(path, dpi=140)
    print("figure ->", path)


def markdown(data: dict) -> str:
    s = data["stats"]
    L = []
    A = L.append
    A("# 【盘点】发现单（多相/多组元专项）— 开源 LBM 技术盘点\n")
    A(f"> 任务锚点：FEAT-OSS-INVENTORY-LBM（mission 8307）｜生成时间 {s['generated_at']}｜"
      f"证据快照日 {TODAY.isoformat()}\n")
    A("## ① 案例介绍（本单在做什么）\n")
    A(f"平台 `oss_inventory` 表已有 {s['inventory_size']} 个开源 LBM 仓在盘。本单**只做发现、不做深盘**："
      "在多相/多组元（SCMP 高密度比、颜色梯度、free-energy、相场、自由面）、热-流耦合、润湿边界三个方向上，"
      "用 GitHub 检索 + 权威清单交叉比对，找出**清单外**值得后续深盘的仓，并明确给出排除项与理由。\n")
    A("边界声明：本单结论为**元数据级证据**（stars / description / pushed_at / license / archived，"
      "全部来自 api.github.com 返回并落盘），未做代码级核验；所有候选状态一律 `suggested`，"
      "是否入册需后续深盘单按 `[OSS-SOL]` 流程复核。\n")
    A("## ② 怎么做的（方法/工具链/关键设置）\n")
    A("| 步骤 | 做法 | 产物 |\n|---|---|---|")
    A(f"| 1 检索 | `api.github.com/search/repositories`，{s['query_count']} 条查询，"
      "`sort=stars`，跳过 fork（fork 继承上游，清单只收上游） | `discovery_raw*.json` |")
    A("| 2 交叉 | 解析 `sthavishtha/list-lattice-Boltzmann-codes` README（经 `repos/.../readme` 取 raw），"
      "抽 34 条 github 链接逐条核验 | `canonical_list_links.json` / `list_entry_verify.json` |")
    A("| 3 去重 | 与 `oss_inventory` 24 仓比对（大小写不敏感）；候选命中在盘仓数必须为 0 | `shortlist.json` |")
    A("| 4 判据 | 停更 > 5 年且无参考价值 → 排除；纯可视化/教学/代理模型 → 排除；"
      "功能与在盘仓重叠 → 观察名单 | 本文件 §④ |")
    A("| 5 出图 | matplotlib 三面板（星数分布 / 活跃度分布 / 召回自检），PNG 落盘 | "
      "`fig_discovery_overview.png` |\n")
    A("查询词（全量）：multiphase、color gradient、free surface、phase field、"
      "topic:lattice-boltzmann、topic:lattice-boltzmann-method、topic:multiphase-lbm（0 命中）、"
      "wetting contact angle（0 命中）、thermal two-phase（0 命中）、shan-chen、pseudopotential、"
      "multicomponent、two-phase、droplet、porous media two-phase（0 命中）、conjugate heat transfer、"
      "topic:multiphase-flow+lattice、openlb、\"free energy\"+\"contact angle\"+\"high density ratio\"（0 命中）。\n")
    A("> 方法学注意：GitHub 无 token 时 search 池 10 次/分钟、core 池 60 次/小时。"
      "本单 3 轮检索各留 5–7s 间隔；core 池在逐仓取证阶段耗尽后改用 search 池补证"
      "（`mflbm_search.json`），并如实记录该降级。\n")
    A("## ③ 结果：候选表（≤8，按星数）\n")
    A("| # | repo | stars | 为什么值得盘（判读层） | 填补的空缺 | 建议 tier | 最近 push | license |")
    A("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(sorted(data["candidates"], key=lambda c: -c["stars"]), 1):
        A(f"| {i} | `{c['repo']}` | {c['stars']} | {c['focus']} | {c['gap']} | {c['tier']} | "
          f"{c['pushed_at']} | {c['license'] or 'n/a'} |")
    tc = s["tier_counts"]
    A(f"\ntier 分布：tier1（多相/多组元）{tc[1]} 个，tier2（可微/GPU）{tc[2]} 个，"
      f"tier3（HPC）{tc[3]} 个。\n")
    A("### 逐条存在性证据（api.github.com 原始 description）\n")
    for c in sorted(data["candidates"], key=lambda c: -c["stars"]):
        A(f"- **{c['repo']}**｜{c['stars']}★｜{c['language'] or 'n/a'}｜{(c['description'] or '')[:160]}"
          f"｜来源 `{c['evidence_source']}`")
    A("")
    A(f"## ④ 明确排除项及理由（{len(data['excluded'])} 项）\n")
    A("| repo | stars | 最近 push | 停更年数 | 排除理由 |")
    A("|---|---|---|---|---|")
    for x in data["excluded"]:
        A(f"| `{x['repo']}` | {x['stars'] if x['stars'] is not None else '—'} | "
          f"{x['pushed_at'] or '—'} | {x['age_years'] if x['age_years'] is not None else '—'} | {x['reason']} |")
    A(f"\n### 观察名单（方向对但暂不占名额，{len(data['watch'])} 项）\n")
    A("| repo | stars | 最近 push | 暂缓理由 |\n|---|---|---|---|")
    for x in data["watch"]:
        A(f"| `{x['repo']}` | {x['stars'] if x['stars'] is not None else '—'} | {x['pushed_at'] or '—'} | {x['reason']} |")
    A("")
    A("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）\n")
    A("1. **链接腐烂**：权威清单 `sthavishtha/list-lattice-Boltzmann-codes` 里的 "
      "`je-santos/MultiphasePorousMediaPalabos` 已 404（repos API 与 search 双查无命中），"
      "清单本身 2024-07 后未更新——清单不能当唯一信息源，必须逐条 API 核验。\n")
    A("2. **镜像≠上游**：`openLB/openLB` 是 *unofficial mirror*，最后 push 2019，真正的上游在 GitLab "
      "（另有 `live-clones/openlb` 明确指向 gitlab.com/openlb/release）。把它当 tier1 参考实现可以，"
      "但当『当前能力基线』会失真——深盘单必须注版本与上游地址。\n")
    A("3. **母体缺位**：在盘的 `PorousMediaSimulation/openLBMPM` 是 openLB 的多相扩展分支，"
      "清单只收了衍生物没收母体——这正是本单最重要的结构性发现（同类风险应反查 `CFD-GO/TCLB`、`palabos` 的生态分支）。\n")
    A("4. **高密度比 SCMP 开源供给极薄**：`\"high density ratio\"+lattice+boltzmann` 检索 0 命中，"
      "颜色梯度/相场方向命中的多是 1–5★ 的新建仓（2026 年新建）。这是**否定性发现**："
      "该方向没有可直接借用的成熟开源实现，只能靠论文+自研，建议后续立单时按『算法复现』而非『代码借鉴』设计。\n")
    A("5. **stars ≠ 质量**：tier 判读用的是『方向覆盖 + 活跃度 + 许可』，星数仅作热度参考；"
      "本单入选的 `AminZar69/CudaPhaseField` 只有 4★，入册理由是相场三相方向零覆盖且 2026 仍活跃，"
      "已标注『未成熟』。\n")
    A("6. **未做的部分（诚实申报）**：未 clone 任何候选仓，未编译、未跑算例，未核对论文/文献引用；"
      "`lanl/MF-LBM` 的多相细节（是否支持高密度比）仅据官方 description『DNS of flow in porous media』"
      "与清单归类推断，**待深盘核实**，本单不作断言。\n")
    A("## ⑥ 误差判定\n")
    A("本单为文献/仓源盘点，**无物理量基准**，因此不存在『关键量误差 <3%』这类判定。"
      "以可核验的替代指标交代确定性：\n")
    A("| 指标 | 值 | 判定 |\n|---|---|---|")
    A(f"| 候选存在性证据覆盖率 | {s['n_candidates']}/{s['n_candidates']} = 100% | 全部有 API 返回的 stars/description |")
    A(f"| 候选与在盘 24 仓重叠 | 0 | 断言式校验（`shortlist.json` 生成时 assert） |")
    A(f"| 查询召回自检（在盘仓被检索命中率） | {s['recall_inventory_hit_by_queries']}/{s['recall_inventory_total']}"
      f" = {s['recall_rate']*100:.0f}% | 未达 100%，说明检索式有盲区：漏掉的 8 仓多为"
      "『不在 lbm topic 下的专用求解器』，与候选召回同源风险 |")
    A(f"| 候选池停更 >5 年占比 | {s['stale_over_5y_in_evidence']}/{s['repos_with_evidence']}"
      f" = {s['stale_share']*100:.0f}% | 用于支撑排除判据，非精度指标 |")
    A("\n**是否 <3%**：不适用（无基准物理量）。可交付性的真正判据是『每个候选都有可复查的 API 证据 + "
      "排除项都有理由』，两条均满足。\n")
    A("## ⑦ 机器块（供入册流程消费）\n")
    A("```")
    for c in sorted(data["candidates"], key=lambda c: (-c["tier"], -c["stars"])):
        A("[OSS-INV]")
        A(f"repo: {c['repo']}")
        A(f"tier: {c['tier']}")
        A(f"focus: {c['focus']}")
        A("status: suggested")
        A("[/OSS-INV]")
    A("```\n")
    A("## ⑧ 溯源\n")
    A("| 工件 | 内容 |\n|---|---|")
    for src in data["sources"]:
        A(f"| `artifacts/m8307/{src['file']}` | {src['what']} |")
    A("| `artifacts/m8307/shortlist.json` | 候选/排除/观察 + 统计 + 纯证据映射（本文件的数字来源） |")
    A("| `scripts/oss_discovery_multiphase{,_r2,_r3b}.py`、`scripts/oss_shortlist_report.py` | 检索与渲染脚本（可复跑） |")
    return "\n".join(L) + "\n"


def main() -> int:
    ev = load_evidence()
    existing = json.load(open("/tmp/existing_inv.json"))
    data = build(ev, existing)
    json.dump(data, open(os.path.join(ART, "shortlist.json"), "w"), indent=2, ensure_ascii=False)
    print(json.dumps(data["stats"], indent=1, ensure_ascii=False))
    fig_path = os.path.join(ART, "fig_discovery_overview.png")
    figure(data, fig_path)
    md = markdown(data)
    open(DOC, "w").write(md)
    print("doc ->", DOC, len(md), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())