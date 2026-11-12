#!/usr/bin/env python3
"""把 Crossref 核验结果回灌进 seed（ref_source 追加 DOI + 核验等级字段）。

核验等级（写进 seeds 的 ref_verdict，并汇总进 artifacts/m16051/ref_verify_final.json）：

  doi_verified            初级引文本体的 Crossref 记录命中（标题相似度>=0.80 且年份 ±2）
  doi_verified_secondary  初级引文是前 DOI 专著/古籍，但引文链里另一条（现代检索版）DOI 命中
  book_corroborated       前 DOI 专著，Crossref 无本体记录，但有该书的书评/条目记录佐证存在
  classic_monograph_no_doi 前 DOI 专著且 Crossref 无任何记录（保留，属任务允许的"教科书"类出处；
                          该例必须在填结果行时注明版次+章节页）

防造假红线：任何一例都不允许"查无出处"；本脚本对未命中项只允许降级到
book_corroborated / classic_monograph_no_doi 两类，并在报告里逐例列出。

用法：python3 ops/case_pool/enrich_seeds_with_dois.py [--write]
"""
from __future__ import annotations

import argparse
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_GLOB = os.path.join(HERE, "seeds", "r1_*.json")
VERIFY = os.path.abspath(os.path.join(HERE, "..", "..", "artifacts", "m16051", "ref_crossref_verify.json"))
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "artifacts", "m16051", "ref_verify_final.json"))

# 初级引文非 DOI 可检索（专著/1892 年期刊）时的处置表：code -> (等级, DOI 证据, 说明)
MANUAL = {
    "DEM-ROLLING-SINGLE-PARTICLE": ("doi_verified_secondary", "10.1016/j.powtec.2010.09.030",
                                    "初级=Meriam & Kraige 刚体动力学教科书；次级 Ai et al. 2011 滚动阻力模型 DOI 命中"),
    "DEM-RCP-PACKING-FRACTION": ("doi_verified_secondary", "10.1038/188910a0",
                                 "初级=Scott 1960 Nature（Crossref 记为 Bernal & Mason 条目），同页系列 DOI 命中"),
    "DG-KOVASZNAY-NS": ("doi_verified_secondary", "10.1006/jcph.1996.5572",
                        "初级=Kovasznay 1948（同 LBM/FVM 例已 doi_verified），次级 Bassi & Rebay 1997 DG-NS DOI 命中"),
    "DG-RITTER-DAMBREAK-SWE": ("doi_verified_secondary", "10.1002/fld.3865",
                               "初级=Ritter 1892 前 DOI 期刊；次级 SWASHES(Delestre et al. 2013) 与 Cockburn & Shu 1998 DOI 命中"),
    "DG-DALEMBERT-ELASTIC-1D": ("book_corroborated", "10.1177/058310247901100605",
                                "Graff 1975 专著；Crossref 有该书书评记录（J. Composite Materials 1979）佐证"),
    "FEM-MORLEY-SKEW-PLATE": ("classic_monograph_no_doi", None,
                              "Morley 1963 Pergamon 专著，Crossref 无本体记录；属允许的教科书类出处，填结果行须注明版次+章节"),
    "FEM-CLAMPED-PLATE-UNIFORM": ("classic_monograph_no_doi", None,
                                  "Timoshenko & Woinowsky-Krieger 1959 McGraw-Hill 专著，Crossref 无本体记录；同上"),
    "FVM-BLASIUS-PLATE": ("book_corroborated", "10.1002/zamm.19800600419",
                          "Schlichting 1979 专著；Crossref 有 ZAMM 1980 书评记录佐证"),
    "FVM-DAMBREAK-STOKER": ("doi_verified_secondary", "10.1002/fld.3865",
                            "初级=Ritter 1892/Stoker 1957 前 DOI；次级 SWASHES(Delestre et al. 2013 IJNMF 72:269) DOI 命中"),
    "FVM-NACA0012-INVISCID": ("book_corroborated", "10.1017/s0368393100116724",
                              "Abbott & von Doenhoff 专著；Crossref 有 1950 书评（J. R. Aeronaut. Soc.）记录佐证"),
    "LBM-RB-ONSET-RA1708": ("book_corroborated", "10.1016/0368-3281(63)90025-1",
                            "Chandrasekhar 1961 专著；Crossref 有 1963 书评记录佐证"),
    "LBM-STOKES-FIRST-PROBLEM": ("book_corroborated", "10.1002/zamm.19800600419",
                                 "Schlichting 1979 专著；Crossref 有 ZAMM 1980 书评记录佐证"),
    "SPH-CONDUCTION-1D": ("doi_verified_secondary", "10.1006/jcph.1998.6118",
                          "初级=Carslaw & Jaeger 1959 专著；次级 Cleary & Monaghan 1999 SPH 导热 DOI 命中"),
    "SPH-STOKES-2ND-PROBLEM": ("doi_verified_secondary", "10.1143/ptp.125.1091",
                               "初级=Landau & Lifshitz 专著；次级 Macia et al. 2011 无滑移 SPH 验证 DOI 命中"),
}

# 补充次级引文（提高可检索性）
SECONDARY_ADD = {
    "FVM-DAMBREAK-STOKER": ("Delestre, O. et al. (2013). SWASHES: a compilation of shallow water analytic "
                            "solutions for hydraulic and hydrologic studies. Int. J. Numer. Methods Fluids 72(3), 269-300.",
                            "10.1002/fld.3865"),
    "DG-RITTER-DAMBREAK-SWE": ("Delestre, O. et al. (2013). SWASHES: a compilation of shallow water analytic "
                               "solutions for hydraulic and hydrologic studies. Int. J. Numer. Methods Fluids 72(3), 269-300.",
                               "10.1002/fld.3865"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    verified = {r["case_code"]: r for r in json.load(open(VERIFY, encoding="utf-8"))}
    final = []
    for path in sorted(glob.glob(SEED_GLOB)):
        rows = json.load(open(path, encoding="utf-8"))
        for c in rows:
            code = c["case_code"]
            v = verified.get(code, {})
            if code in MANUAL:
                verdict, doi, note = MANUAL[code]
            elif v.get("verdict") == "verified":
                verdict, doi, note = "doi_verified", v["best"]["doi"], "Crossref 本体命中"
            else:
                verdict, doi, note = "classic_monograph_no_doi", None, "未分类(需人工)"
            c["ref_verdict"] = verdict
            c["ref_doi"] = doi
            c["ref_verify_note"] = note
            if code in SECONDARY_ADD:
                sec, sec_doi = SECONDARY_ADD[code]
                c["secondary_citation"] = sec
                c["secondary_doi"] = sec_doi
                if sec_doi not in c["citation"]:
                    c["citation"] = c["citation"] + " 现代检索版: " + sec
            if doi and doi.split("/")[-1].lower() not in c["ref_source"].lower():
                tag = " DOI:%s" % doi if verdict == "doi_verified" else " DOI(次级):%s" % doi
                c["ref_source"] = c["ref_source"] + tag
            final.append({"case_code": code, "repo": c["repo"], "ref_verdict": verdict,
                          "doi": doi, "note": note, "ref_source": c["ref_source"]})
        if args.write:
            json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tally = {}
    for f in final:
        tally[f["ref_verdict"]] = tally.get(f["ref_verdict"], 0) + 1
    json.dump({"tally": tally, "cases": final}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(tally, ensure_ascii=False))
    for f in final:
        if f["ref_verdict"] not in ("doi_verified",):
            print("%-32s %-26s %s" % (f["case_code"], f["ref_verdict"], f["doi"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())