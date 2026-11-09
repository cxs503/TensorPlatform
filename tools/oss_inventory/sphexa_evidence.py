#!/usr/bin/env python3
"""SPH-EXA (sphexa-org/sphexa) 代码级证据表生成器 —— 零手抄。

用法:
    python tools/oss_inventory/sphexa_evidence.py --checkout /path/to/sphexa --out artifacts/oss-sph-sphexa/sphexa_evidence.json

设计（对应 docs/oss-to-tk-playbook.md 步②机器判据）:
  * 每条「能力声明」= 一条正/负断言 + 检索式；
  * 否定声明（"没有 X"）与肯定声明同等举证：grep 命中数一并落盘，命中 0 即为否定证据；
  * 所有指针由 **本次** 检出实时计算，报告里的 file:line 全部来自本文件，不手敲。

commit 由 --commit 传入（浮动分支的指针三个月后会失效，报告必须钉 commit）。
"""
import argparse
import json
import os
import re
import subprocess
import sys

# (claim_id, 断言, 检索正则, 限定路径, 期望: "present"|"absent")
SPEC = [
    # --- 方法族：SPH 变体 ---
    ("M1", "标准 WCSPH 密度求和 + 动量/能量核（Monaghan 型人工黏性）",
     r"MomentumAndEnergy|artificial_viscosity", "sph/include/sph/hydro_std", "present"),
    ("M2", "体积元（Volume Element, Hopkins 2015 风格）公式：xmass/gradh/dV/disc1 三阶矩阵",
     r"veDefinition|dV11|gradh", "sph/include/sph/hydro_ve", "present"),
    ("M3", "人工黏性开关（逐粒子 alpha，div(v) 驱动，SPHYNX 血统）",
     r"AVswitches|alpha", "sph/include/sph/hydro_ve/av_switches_kern.hpp", "present"),
    ("M4", "IAD（积分近似导数）正则化：矩阵质量判据 + ridge 修正",
     r"iadMomentQuality|needRegularization|iadRidgeQuality", "sph/include/sph/iad_regularization.hpp", "present"),
    ("M5", "ISPH/PCISPH/DFSPH/IISPH/PBD 任一不可压缩/投影/PBD 变体",
     r"\b(ISPH|PCISPH|DFSPH|IISPH|PBD)\b", ".", "absent"),
    ("M6", "delta-SPH（密度扩散项 / delta 项）",
     r"delta.?sph|deltasph|density diffusion", ".", "absent"),
    # --- 边界处理 ---
    ("B1", "固定边界修正 FBC（近壁粒子加速度/速度按因子衰减，非幽灵粒子法）",
     r"fbcAdjustFactors|BoundaryType::fixed", "sph/include/sph/positions.hpp", "present"),
    ("B2", "周期/开放/固定盒边界类型（cstone::BoundaryType）",
     r"enum class BoundaryType|BoundaryType::(periodic|open|fixed)", "domain/include/cstone", "present"),
    ("B3", "幽灵/镜像/动态边界粒子法（DualSPHysics 型 wall particle）",
     r"ghost particle|mirror particle|dynamic boundary|wall particle", ".", "absent"),
    # --- 时间步 ---
    ("T1", "CFL 型逐粒子时间步 dt = Kcour*h/v_signal",
     r"tsKCourant", "sph", "present"),
    ("T2", "块时间步 / 分级 rung（2 的幂，最多 4 级）",
     r"findRungRanges|maxNumRungs", "sph/include/sph", "present"),
    ("T3", "leapfrog / KDK 位置-速度更新",
     r"positionUpdate|energyUpdate", "sph/include/sph", "present"),
    ("T4", "重力/加速度限制步长 etaAcc*sqrt(h/|a|)",
     r"accelerationTimestep", "sph/include/sph/ts_global.hpp", "present"),
    # --- 多相 / 自由面 ---
    ("P1", "多相 / 表面张力 / 自由面检测",
     r"multiphase|multi-phase|surface tension|free.?surface", ".", "absent"),
    # --- 并行 ---
    ("G1", "MPI + OpenMP（ijloop 向量化）",
     r"omp parallel for simd|omp parallel", "domain/include/cstone/traversal/ijloop", "present"),
    ("G2", "CUDA/HIP 双后端（同一 .cu 源，GPU_SYMBOL 宏切换）",
     r"GPU_SYMBOL|HIP|hip", ".", "present"),
    ("G3", "GPU-aware 通信 / 设备端 halo 交换",
     r"cudaMemcpyAsync|P2pTags::haloExchange", "domain/include/cstone/halos/exchange_halos_gpu.cuh", "present"),
    ("G4", "SFC（Morton 键）八叉树域分解 + 全局交换",
     r"exchangeParticles|domainDecomp", "domain/include/cstone/domain", "present"),
    ("G5", "GPU 邻居搜索（Octree + ijloop）",
     r"findNeighborsGpu|ijloop", "domain/include/cstone/traversal/ijloop", "present"),
    # --- 重力（天体 SPH 特有） ---
    ("R1", "八叉树笛卡尔多极矩远场重力（Ryoanji）",
     r"multipole|Multipole", "ryoanji", "present"),
    ("R2", "周期重力 Ewald 求和",
     r"Ewald|ewald", "ryoanji", "present"),
    # --- 验证面 ---
    ("V1", "Sedov-Taylor 解析解求解器（自相似 ODE 数值积分）",
     r"sedov", "main/src/analytical_solutions", "present"),
    ("V2", "Evrard/Noh/Gresho-Chan 解析解/L1 误差比对脚本",
     r"computeL1Error", "main/src/analytical_solutions", "present"),
    ("V3", "CI 常数回归参考文件（8 个 IC × CPU/CUDA 后端）",
     r"", "ci/reference", "present"),
    ("V4", "dam-break / Poiseuille / Taylor-Green 水动力基准",
     r"dam.?break|Poiseuille|Taylor.?Green", ".", "absent"),
    ("V5", "Sod 激波管标准测试",
     r"\bsod\b", ".", "absent"),
    # --- 核函数与归一化（本轮独立复算的对象）---
    ("K1", "核函数族：sinc^n / 0.9*sinc^4+0.1*sinc^9 混合核 / Wendland C2/C4/C6",
     r"using KernelVariant|struct WendlandC|struct SincN", "sph/include/sph/sph_kernels.hpp", "present"),
    ("K2", "混合核硬编码归一化常数 K1/K2（注释标注来源 kernel_3D_k）",
     r"K1     = 0\.4589|K2     = 1\.3913", "sph/include/sph/sph_kernels.hpp", "present"),
    ("K3", "核归一化由 Simpson 积分 kernel_3D_k 现算（支撑域 kernelSupport=2）",
     r"kernel_3D_k|kernelSupport", "sph/include/sph/sph_kernels.hpp", "present"),
    ("K4", "sinc(PI/2*v) 谐波核（fastmath::sin + rcp）",
     r"wharmonic_std", "sph/include/sph/kernels.hpp", "present"),
    # --- 时间步与推进器驱动 ---
    ("T5", "rung 数 = min(floor(log2(dt_max/dt_min))+1, maxNumRungs)",
     r"numRungs    = std::min", "sph/include/sph/ts_rungs.hpp", "present"),
    ("T6", "块时间步驱动：逐 rung 循环 + drift-back 条件 + butterfly 子步",
     r"driftBack|useRung|butterfly", "main/src/propagator/ve_hydro_bdt.hpp", "present"),
    # --- EOS / IO / 现场可视化 ---
    ("E1", "理想气体 EOS（gamma + 平均分子量 mui，内能/温度双通路）",
     r"idealGasEOS_u|idealGasCv", "sph/include/sph/hydro_ve/eos.hpp", "present"),
    ("I1", "原地可视化（Ascent / Catalyst 适配器）",
     r"ascent_adaptor|catalyst_adaptor|ascent|catalyst", "main/src/viz", "present"),
    ("I2", "重启/续算：HDF5 step 属性 loadOrStore（dt_m1 / numRungs）",
     r"loadOrStore", "sph/include/sph/timestep.h", "present"),
    ("G6", "NVIDIA MPS 包装脚本（多 rank 共享 GPU）",
     r"CUDA_DEVICE_MAX_CONNECTIONS|num_gpus=", "mps_wrapper.sh", "present"),
    # --- 测试框架 ---
    ("X1", "gtest 单测 + ctest 注册",
     r"TEST\(|TEST_F\(", "sph/test", "present"),
]


def run_grep(pattern, path):
    """返回 [(relpath, lineno, text)]，最多 40 条。"""
    if not pattern:
        # 空检索式 = 目录清单断言（如 CI 参考文件集）：每个文件即一条指针
        hits = []
        for dirpath, _dirs, files in os.walk(path):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                hits.append({"file": os.path.relpath(full, ROOT), "line": 1, "text": fn})
        return hits
    try:
        out = subprocess.run(["grep", "-rnE", "-H", pattern, path], capture_output=True, text=True, timeout=300).stdout
    except subprocess.TimeoutExpired:
        return []
    hits = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel = os.path.relpath(parts[0], ROOT)
        if rel.split(os.sep)[0] == "extern":  # 第三方子模块不算本仓实现
            continue
        if os.path.basename(rel) == "CMakeLists.txt":  # 构建脚本不算实现证据
            continue
        if int(parts[1]) < 8:  # 跳过 MIT 许可证头主体
            continue
        hits.append({"file": rel, "line": int(parts[1]), "text": parts[2].strip()[:180]})
    return hits


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--commit", default="unknown")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ROOT = os.path.abspath(args.checkout)

    records, n_pos, n_neg = [], 0, 0
    for cid, claim, pattern, where, expect in SPEC:
        target = os.path.join(ROOT, where) if where != "." else ROOT
        hits = run_grep(pattern, target)
        if expect == "present":
            ok = len(hits) > 0
            n_pos += 1
        else:
            ok = len(hits) == 0
            n_neg += 1
        records.append({
            "id": cid, "claim": claim, "grep": {"pattern": pattern, "path": where},
            "expect": expect, "hit_count": len(hits), "assertion_holds": ok,
            "pointers": [f"{h['file']}:{h['line']}" for h in hits[:6]],
            "samples": hits[:3],
        })

    failures = [r["id"] for r in records if not r["assertion_holds"]]
    # --- 仓规模统计（报告里的数字全部出自这里，零手抄）---
    def count_ext(exts, sub=""):
        n = loc = 0
        base = os.path.join(ROOT, sub) if sub else ROOT
        for dirpath, _d, files in os.walk(base):
            if os.path.relpath(dirpath, ROOT).split(os.sep)[0] == "extern":
                continue
            for fn in files:
                if any(fn.endswith(e) for e in exts):
                    n += 1
                    try:
                        with open(os.path.join(dirpath, fn), errors="ignore") as fh:
                            loc += sum(1 for _ in fh)
                    except OSError:
                        pass
        return {"files": n, "lines": loc}

    stats = {
        "cpp_hpp_cu_all": count_ext([".cpp", ".hpp", ".cu", ".h", ".cuh"]),
        "sph_module": count_ext([".cpp", ".hpp", ".cu", ".h", ".cuh"], "sph"),
        "domain_cstone": count_ext([".cpp", ".hpp", ".cu", ".h", ".cuh"], "domain"),
        "ryoanji_gravity": count_ext([".cpp", ".hpp", ".cu", ".h", ".cuh"], "ryoanji"),
        "gpu_source_files": count_ext([".cu", ".cuh"]),
        "gtest_TESTS": sum(1 for _ in run_grep(r"TEST\(|TEST_F\(", os.path.join(ROOT, "sph"))
                           + run_grep(r"TEST\(|TEST_F\(", os.path.join(ROOT, "main"))
                           + run_grep(r"TEST\(|TEST_F\(", os.path.join(ROOT, "physics"))
                           + run_grep(r"TEST\(|TEST_F\(", os.path.join(ROOT, "ryoanji"))),
        "ci_reference_files": len([1 for _p in os.listdir(os.path.join(ROOT, "ci/reference"))]),
    }
    doc = {
        "repo": "sphexa-org/sphexa",
        "stats": stats,
        "commit": args.commit,
        "checkout": ROOT,
        "summary": {"claims": len(records), "positive": n_pos, "negative": n_neg,
                    "failed": failures},
        "claims": records,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print(f"wrote {args.out}: {len(records)} claims, failed={failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())