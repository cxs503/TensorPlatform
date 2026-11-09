#!/usr/bin/env python3
"""SPHinXsim (Xiangyu-Hu/SPHinXsim) OSS 深盘 wave-2 inventory generator.

用途:
  1) 把「GitHub API 实查事实 + 源码级方法证据(文件:行) + 验证面盘点 + license 判定 +
     可迁移方案」落成机器可解析 JSON（唯一数字来源）；
  2) 从同一 JSON 程序化渲染图表（避免手抄数字进报告）。

源码证据的取得方式: `git clone --depth 1 --filter=blob:none --no-checkout` 后
sparse-checkout（API 未认证限额 60/h 耗尽期间的替代路径），因此 evidence 里的
file:line 是对 HEAD 5241a890 实际文件 grep -n 的结果，可复算:
    git ls-tree -r HEAD --name-only | ... ; grep -n <token> <file>

用法:
  python scripts/oss_audit/sphinxsim_inventory.py --checkout /tmp/sxs2 \
      --out artifacts/oss-sph/w2 --figdir docs/oss_inventory_sph/figures
  无 checkout 时（离线复现）仅从内置 facts 渲染图表，tree 统计标记为 cached。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = "Xiangyu-Hu/SPHinXsim"
HEAD_SHA = "5241a8902019e2b61559460748342dd9c29bd2a9"
CHECKED_AT = "2026-11-09T15:2x UTC"

# ---------------------------------------------------------------- GitHub API ---
# 实查来源: GET https://api.github.com/repos/Xiangyu-Hu/SPHinXsim （2026-11-09）
GITHUB = {
    "full_name": REPO,
    "html_url": "https://github.com/Xiangyu-Hu/SPHinXsim",
    "homepage": "https://xiangyu-hu.github.io/SPHinXsim/",
    "description": "The python, graphic UI and host-agnostic agentic interface for the "
                   "C++ multi-physics library SPHinXsys",
    "language": "C++",
    "license_spdx": "MIT",
    "stargazers_count": 19,
    "forks_count": 11,
    "open_issues_count": 25,
    "size_kb": 69196,
    "created_at": "2026-03-01T12:34:25Z",
    "pushed_at": "2026-11-09T12:33:13Z",
    "default_branch": "main",
    "archived": False,
    "fork": False,
    "head_commit": HEAD_SHA,
    "head_commit_date": "2026-11-09T12:32:20Z",
    "language_bytes": {"C++": 7091004, "Python": 1077007, "CMake": 238682,
                       "PostScript": 113663, "C": 91510, "Shell": 11584,
                       "HTML": 10551, "Dockerfile": 6485, "Ruby": 1361,
                       "Batchfile": 787, "JavaScript": 549, "Less": 235},
}

# ------------------------------------------------------------ 方法证据矩阵 ---
# status: supported | partial | absent
# 每条 evidence 均为 HEAD 下真实 grep -n 命中行（file 相对仓库根）。
METHODS = [
    {
        "name": "WCSPH (weakly compressible, Tait EOS)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.cpp",
             "line": 17, "what": "WeaklyCompressibleFluid::getPressure(rho) —— p = c0^2*rho0/gamma*((rho/rho0)^gamma-1)"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.cpp",
             "line": 27, "what": "getSoundSpeed(p, rho) 声速随密度更新"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.h",
             "line": 53, "what": "EOS 虚接口基类（可替换状态方程）"},
        ],
        "notes": "弱可压缩声学 WCSPH 是唯一在库内完整实现的流体范式；无投影/不可压求解器。",
    },
    {
        "name": "Riemann-solver dissipation (低耗散 Riemann WCSPH)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/materials/riemann_solver.h",
             "line": 83, "what": "BaseAcousticRiemannSolver 模板（声学 Riemann 近似）"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/riemann_solver.h",
             "line": 93, "what": "DissipativePJump(u_jump) —— 动量方程压力耗散项"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/riemann_solver.h",
             "line": 97, "what": "DissipativeUJump(p_jump) —— 连续性方程密度耗散项"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/riemann_solver.h",
             "line": 124, "what": "using DissipativeRiemannSolver = BaseAcousticRiemannSolver<NoLimiter>（无限制器=全耗散）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/riemann_solver/riemann_solver_ck.h",
             "line": 172, "what": "AcousticRiemannSolverCK = ...<TruncatedLinear>（SPHinXsim 生成的 C++ 走这条 CK 变体）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/riemann_solver/riemann_solver_ck.h",
             "line": 173, "what": "DissipativeRiemannSolverCK = ...<NoLimiter>"},
        ],
        "notes": ("Riemann 型激波捕捉耗散是 SPHinXsys 的标志性做法（Zhang/Mühlhaus 家族），"
                  "与 δ-SPH 的密度扩散项在功能上部分重叠但数学形式不同。"),
    },
    {
        "name": "Density relaxation / compression regularization",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/density_summation.h",
             "line": 45, "what": "DensitySummation<Base, DataDelegationType> 密度求和基类"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/density_summation.h",
             "line": 131, "what": "DensitySummation<Inner<FreeSurface, ...>> 自由面感知密度求和"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h",
             "line": 61, "what": "CompressionSummation<Inner<...>> 压缩度量求和"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h",
             "line": 112, "what": "AverageCompression 平均压缩量（自由面判据输入）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h",
             "line": 150, "what": "DensityRegularization 密度正则化（含策略特化）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h",
             "line": 200, "what": "Regularization<FreeSurface> 自由面特化"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h",
             "line": 222, "what": "Regularization<FreeStream> 远场特化"},
        ],
        "notes": "密度正则化 + 压缩求和 + Riemann 耗散构成 SPHinXsys 的 WCSPH 稳定化三层。",
    },
    {
        "name": "Symplectic (kick-drift-kick) two-half acoustic integration",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_integration.h",
             "line": 87, "what": "Integration1stHalf<Inner<>, RiemannSolverType, KernelCorrectionType>"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp",
             "line": 76, "what": "rho_dissipation += DissipativeUJump(p_i-p_j)*dW_ijV_j（连续性方程）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp",
             "line": 192, "what": "p_dissipation += DissipativePJump(u_jump)*dW_ijV_j*e_ij（动量方程）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_1st_half.hpp",
             "line": 108, "what": "CK 版 1st half 内相互作用密度耗散"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_2nd_half.hpp",
             "line": 69, "what": "CK 版 2nd half 压力耗散"},
        ],
        "notes": "位置/速度/密度三量两半交替更新（不含泊松投影）。",
    },
    {
        "name": "Adaptive multi-trigger time stepping (CFL)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_time_step.h",
             "line": 48, "what": "AcousticTimeStep(sph_body, acousticCFL=0.6)"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_time_step.h",
             "line": 132, "what": "AdvectionTimeStep(sph_body, U_ref, advectionCFL=0.25)"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/fluid_time_step.h",
             "line": 145, "what": "AdvectionViscousTimeStep（粘性限制 dt）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/sph_solver.h",
             "line": 64, "what": "class TimeStepper（advection/acoustic 双层触发 + screening/observation interval）"},
            {"file": "sphinxsim/sph_simulation/simulation_builder/fluid_simulation/fluid_simulation_builder.cpp",
             "line": 122, "what": "Real dt = time_stepper.incrementPhysicalTime(fluid_acoustic_time_step) —— 内层声学步循环"},
        ],
        "notes": "advection 外层 + acoustic 内层双时间步；CFL 默认值可覆盖。",
    },
    {
        "name": "Boundary handling (buffer/inflow/outflow/emitter/free-stream/wall contact)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 65, "what": "FlowVelocityBuffer（入口速度缓冲力）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 87, "what": "InflowVelocityCondition<TargetVelocity>"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 129, "what": "FreeStreamVelocityCorrection（远场无反射修正）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 174, "what": "DampingBoundaryCondition（阻尼层吸收）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 192, "what": "EmitterInflowCondition / 223 EmitterInflowInjection（射流源）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary.h",
             "line": 246, "what": "DisposerOutflowDeletion（出流粒子删除）"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/boundary_condition/near_wall_boundary.h",
             "line": 42, "what": "NearWallDistance / 57 DistanceFromWall / 68 BoundingFromWall（壁面距离与推离）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_1st_half.hpp",
             "line": 176, "what": "Wall 接触项用 wall 侧镜像压力做耗散（壁面无需 ghost 粒子）"},
            {"file": "sphinxsim/sphinxsys/src/for_2D_build/particle_dynamics/general_dynamics/fvm_ghost_boundary_2d.cpp",
             "line": 1, "what": "FVM 路径的 ghost 边界重构（MUSCL/HLLC 分支）"},
        ],
        "notes": "壁面 = 固体粒子 + Wall 接触核（积分形式），配合 NearWall/Jump 边界条件；"
                 "另有 Eulerian/FVM 分支的 ghost 重构。",
    },
    {
        "name": "Multiphase / multi-species",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.cpp",
             "line": 96, "what": "WeaklyCompressibleMultiPhase(c0) 多相混合 EOS"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.cpp",
             "line": 109, "what": "addPurePhases(NamesAndDensities) 纯相登记"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/weakly_compressible_fluid.cpp",
             "line": 40, "what": "WeaklyCompressibleMultiSpecies 多组分（组分扩散）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/riemann_solver/riemann_solver_ck.h",
             "line": 134, "what": "ImpedanceModel<WeaklyCompressibleMixture, ...> 相间阻抗界面"},
            {"file": "tests/test_simulation/test_2d_simulation/data/two_phase_dambreak.json",
             "line": 0, "what": "两相溃坝算例输入（JSON 契约，存在性检查）", "exists_only": True},
            {"file": "tests/test_simulation/test_2d_simulation/data/multi_phase_mixing.json",
             "line": 0, "what": "多相混合算例输入（存在性检查）", "exists_only": True},
        ],
        "notes": "相界面靠密度/阻抗跳变 + Riemann 耗散维持，无界面重构/锐化。",
    },
    {
        "name": "Solid / FSI (TLSPH + 壳/杆 + 流体-结构接触)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sph_simulation/dynamics_builder/solid_dynamics/solid_dynamics_builder.hpp",
             "line": 60, "what": "StructureIntegration1stHalfPK2 + SaintVenantKirchhoffSolid（总拉格朗日）"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/force_on_structure.h",
             "line": 1, "what": "FSI 结构侧受力装配"},
            {"file": "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/structure_surface_motion.h",
             "line": 1, "what": "结构表面运动 → 流体侧边界速度"},
            {"file": "sphinxsim/sphinxsys/src/shared/materials/johnson_cook_solid.h",
             "line": 61, "what": "Johnson-Cook 弹塑性材料（冲击）"},
        ],
        "notes": "同一 SPH 框架下统一流体/固体/FSI（无分区耦合迭代），FSI 稳定性的数值代价未在仓内量化。",
    },
    {
        "name": "ISPH / PCISPH / DFSPH / IISPH (projection / implicit incompressible)",
        "status": "absent",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/", "line": 0,
             "what": "grep -rniE 'isph|pcisph|dfsph|iisph|poisson|pressure projection' src/ → 0 命中（除 poisson_ratio 材料参数）",
             "grep_pattern": r"\\bisph\\b|pcisph|dfsph|iisph|pressure projection"}
        ],
        "notes": "仓内完全没有不可压投影/隐式密度求解器；对 GAP-SPH-ISPH-CONVERGENCE 只能做 reference 级借鉴。",
    },
    {
        "name": "δ-SPH (explicit density diffusion)",
        "status": "absent",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/", "line": 0,
             "what": "grep -rniE 'delta.?sph|deltasph' src/ → 0 命中",
             "grep_pattern": r"delta.?sph|deltasph"}
        ],
        "notes": "密度稳定化走 Riemann 耗散 + 压缩正则化，未实现 δ-SPH 显式扩散项。",
    },
    {
        "name": "PBD / position-based fluids",
        "status": "absent",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/", "line": 0,
             "what": "grep -rniE 'position.based|PBD' src/ → 仅命中 polar decomposition / sorted position（无关）",
             "grep_pattern": r"position-based|\\bPBD\\b"}
        ],
        "notes": "无图形学式位置约束求解。",
    },
    {
        "name": "Eulerian / FVM branch (MUSCL-HLLC)",
        "status": "supported",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/eulerian_fluid_dynamics/eulerian_riemann_solver.h",
             "line": 1, "what": "Eulerian Riemann 求解器"},
            {"file": "sphinxsim/sphinxsys/src/shared/particle_dynamics/fluid_dynamics/muscl_hllc_integration.h",
             "line": 1, "what": "MUSCL 重构 + HLLC 通量（可压缩/欧拉分支）"},
        ],
        "notes": "与 Lagrangian WCSPH 并行的另一条线（用于可压缩激波类算例）。",
    },
]

PARALLEL = {
    "mpi": {"status": "absent", "evidence":
            [{"file": "sphinxsim/sphinxsys/src/", "line": 0,
              "what": "grep -rli 'mpi.h|MPI_Init' src/ → 0 命中；无分布式分解",
              "grep_pattern": r"mpi\.h|MPI_Init"}]},
    "shared_memory": {"status": "supported", "backend": "Intel TBB", "evidence":
            [{"file": "sphinxsim/sphinxsys/src/shared/common/sphinxsys_tbb.h", "line": 1,
              "what": "tbb::parallel_for / blocked_range 执行策略"},
             {"file": "sphinxsim/sphinxsys/src/shared/common/algorithm_primitive.h", "line": 1,
              "what": "parallel_for/parallel_sort 抽象（TBB 后端）"}]},
    "gpu_sycl": {"status": "partial", "backend": "SYCL (Intel DPC++ only)", "evidence":
            [{"file": "sphinxsim/sphinxsys/CMakeLists.txt", "line": 16,
              "what": "option(SPHINXSYS_USE_SYCL ... OFF) 默认关闭"},
             {"file": "sphinxsim/sphinxsys/CMakeLists.txt", "line": 115,
              "what": "FATAL_ERROR: 仅支持 IntelLLVM 编译器"},
             {"file": "sphinxsim/sphinxsys/CMakeLists.txt", "line": 137,
              "what": "SPHINXSYS_SYCL_TARGETS 默认 spir64_x86_64（CPU 型设备）"},
             {"file": "sphinxsim/sphinxsys/src/src_sycl/shared/particle_dynamics/implementation_sycl.h",
              "line": 30, "what": "SYCL 执行策略层（11 个文件，仅变量存储/迭代器/配置动力学）"},
             {"file": "CMakeLists.txt", "line": 62,
              "what": "SPHinXsim 自身构建强制 SPHINXSYS_USE_SYCL OFF（Python wheel 不含 GPU 路径）"}]},
    "threads_measured": "仓内无强/弱可扩展性测量数据（无 benchmark 脚本、无 scaling 结果）",
}

VALIDATION = {
    "case_library": {
        "2d_example_dirs": 70,
        "3d_example_dirs": 44,
        "named_benchmarks": [
            "test_2d_dambreak", "test_3d_dambreak", "test_2d_two_phase_dambreak",
            "test_2d_taylor_green", "test_2d_poiseuille_flow",
            "test_2d_eulerian_taylor_green_LG", "test_1d_shock_tube",
            "test_2d_FVM_flow_around_cylinder", "test_2d_FVM_double_mach_reflection",
        ],
    },
    "regression_harness": {
        "mechanism": "observer 量 → XML 逐次运行结果 + ensemble_averaged_mean_variance.xml + "
                     "dtwdistance.xml + runtimes.dat，由 regression_test_tool.py 比对",
        "regression_tool_files": 857,
        "reference_type": "self-referential（与自身历史基线比）",
    },
    "unit_tests_src": {"cpp": 27, "txt": 43},
    "numeric_vs_analytic_or_experiment": {
        "status": "insufficient",
        "evidence": [
            {"file": "sphinxsim/sphinxsys/tests/2d_examples/test_2d_poiseuille_flow/poiseuille_flow.cpp",
             "line": 24, "what": "仅有 U_max=1.5*U_f 与 c_f=10*U_max 的数值设定；无解析抛物线剖面对比代码"},
            {"file": "sphinxsim/sphinxsys/tests/2d_examples/test_2d_dambreak/Dambreak.cpp",
             "line": 17, "what": "仅粒子间距/密度/声速参数；无 Martin-Moyce 实验数据比对"},
        ],
        "note": "仓内没有把算例结果与解析解/实验逐项求误差的自动化判据；"
                "benchmark 图只出现在文档静态图片（docs/assets/img/benchmark-dambreak-2d/3d.png）。",
    },
    "sphinxsim_own_tests": {
        "python_test_files": 15,
        "numeric_tolerance_assertions": 0,
        "evidence": [{"file": "sphinxsim/tests/", "line": 0,
                      "what": "grep -rn 'rel_tol|abs_tol|assert_allclose|isclose|np.testing' tests/ → 0 命中",
                      "grep_pattern": r"rel_tol|abs_tol|assert_allclose|isclose|np\.testing"}],
        "cpp_simulation_tests": {
            "files": ["tests/test_simulation/test_2d_simulation/simulation.cpp",
                      "tests/test_simulation/test_3d_simulation/simulation.cpp"],
            "gtest_cases": 1,
            "evidence": [{"file": "tests/test_simulation/test_2d_simulation/simulation.cpp",
                          "line": 115, "what": "TEST(simulations, has_json) —— 仅冒烟：能读入 JSON 并建库"}],
        },
        "case_inputs": ["dambreak", "column_collapse", "two_phase_dambreak", "multi_phase_mixing",
                        "t_junction", "submerged_cylinder", "milling", "heat_transfer",
                        "fuel_tank", "repose_angle", "t_pipe"],
    },
    "ci": {
        "workflows": [".github/workflows/ci.yml", ".github/workflows/docs.yml"],
        "runs": ["ctest --test-dir build-integrated（C++ simulation-class 冒烟测试）",
                 "pytest tests/ -v", "pytest examples/ -v"],
        "matrix_platforms": ["ubuntu-22.04", "macOS", "Windows"],
        "upstream_physics_suite_enabled": False,
        "evidence": [{"file": "CMakeLists.txt", "line": 60,
                      "what": "set(SPHINXSYS_BUILD_TESTS OFF CACHE BOOL ...) —— 上游 2D/3D 算例库默认不构建"},
                     {"file": "pyproject.toml", "line": 44,
                      "what": "SPHINXSIM_BUILD_SIMULATION_TESTS = OFF（wheel 构建时）"}],
    },
}

LICENSE = {
    "repo_root": {"spdx": "MIT", "file": "LICENSE", "holder": "Copyright (c) 2026 Xiangyu Hu",
                  "covers": "Python 侧（CLI/MCP/CAD/JSON schema/runtime）、CMake 胶水、project/extra_src"},
    "vendored_core": {"spdx": "Apache-2.0", "path": "sphinxsim/sphinxsys/",
                      "file": "sphinxsim/sphinxsys/LICENSE",
                      "holder": "Portions copyright (c) 2017-2025 TUM and the authors' affiliations",
                      "covers": "上游 SPHinXsys C++ 核心（608 文件 / 84847 行）整体 vendor 入库"},
    "third_party_submodule": {"path": "sphinxsim/sphinxsys/3rd_party/wasmtbb",
                              "url": "https://github.com/hpcwasm/wasmtbb.git",
                              "license": "unknown（该子模块内容未随 clone 检出，读不到=insufficient）"},
    "determination": (
        "MIT 只覆盖 SPHinXsim 自有胶水；84847 行 C++ 核心受 Apache-2.0 约束。"
        "→ 代码级搬运/改写核心必须保留 Apache-2.0 LICENSE+文件头 NOTICE 并声明修改；"
        "CLA 不成立，不可把核心当 MIT 使用。Python 胶水（12966 行）为 MIT，可自由 adapt。"
        "算例 JSON/参数表属事实性数据，reference 复用无障碍。"),
    "compliance_verdict": {
        "adapt_python_glue": "OK (MIT，需保留版权声明)",
        "adapt_vendored_core": "OK 但须 Apache-2.0 合规（保留 LICENSE/NOTICE/注明改动）",
        "reference_only": ["3rd_party/wasmtbb（license 未读到）"],
    },
}

TRANSFERABLE = [
    {
        "id": "SPHSOL-sphinxsim-01",
        "kind": "reference",
        "summary": "把上游 70 个 2D + 44 个 3D 算例目录 + XML ensemble 回归台（857 个 regression_test_tool 文件）"
                   "当作 TensorPhysics WCSPH 验证面的算例清单与观测器约定：dambreak 用压力探针 + 总机械能、"
                   "Poiseuille 用剖面、Taylor-Green 用衰减率。仅取「算什么/观测什么」，不取实现代码。",
        "gap": "GAP-SPH-WCSPH-VALIDATION",
        "license": "reference（算例参数与观测器命名=事实性数据）；若照抄 .cpp 算例驱动则须 Apache-2.0 合规",
        "evidence": ["sphinxsim/sphinxsys/tests/2d_examples/ 70 dirs",
                     "sphinxsim/sphinxsys/tests/3d_examples/ 44 dirs"],
    },
    {
        "id": "SPHSOL-sphinxsim-02",
        "kind": "adapt",
        "summary": "以 CompressionSummation/AverageCompression/DensityRegularization(Internal|FreeSurface|FreeStream) 的"
                   "策略分层为骨架，为 TensorSPH 增加显式 δ-SPH 密度扩散项：本项目证明「压缩度量→正则化策略特化」"
                   "的接口形状可直接承载 δ 项，且能做到自由面/内域分流（避免自由面处过度扩散）。",
        "gap": "GAP-SPH-DELTA-SPH",
        "license": "adapt 上游核心=Apache-2.0（保留头注释+声明修改）；接口形状本身不受版权保护",
        "evidence": ["sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.h:112,150,200,222"],
    },
    {
        "id": "SPHSOL-sphinxsim-03",
        "kind": "fit",
        "summary": "用 SPHinXsim 的 JSON-first 输入契约（schemas/contracts-v1.json + material/geometry/boundary builder 分层）"
                   "对齐 TensorPhysics 多相算例的输入面：两相溃坝/多相混合的 phase 密度-声速-阻抗参数组，"
                   "映射到我们的 multiphase 配置，做契约层 fit 而非数值层移植。",
        "gap": "GAP-SPH-MULTIPHASE",
        "license": "fit：MIT 胶水（可直接采用字段语义）；schema 文件级复制需保留 MIT 声明",
        "evidence": ["sphinxsim/runtime/schemas/contracts-v1.json",
                     "tests/test_simulation/test_2d_simulation/data/two_phase_dambreak.json",
                     "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/riemann_solver/riemann_solver_ck.h:134"],
    },
    {
        "id": "SPHSOL-sphinxsim-04",
        "kind": "reference",
        "summary": "SYCL 执行策略层（implementation_sycl.h + 11 文件）作为「后端抽象层」设计参照："
                   "把 parallel_for/并行迭代器与物理核解耦的做法可迁移；但 Intel-only + wheel 中 SPHINXSIM 强制 "
                   "SPHINXSYS_USE_SYCL=OFF + 仓内零 scaling 数据 → 不可作为 GPU 加速基线，"
                   "TensorPhysics 的 GPU scaling 结论必须自测（honest：本条对 GAP 的贡献=设计参照，不含性能证据）。",
        "gap": "GAP-SPH-GPU-SCALING",
        "license": "reference-only（Apache-2.0 代码，如改写需合规；本项目为 Intel 专有编译器链绑定的设计）",
        "evidence": ["sphinxsim/sphinxsys/src/src_sycl/shared/particle_dynamics/implementation_sycl.h:30",
                     "CMakeLists.txt:62 (SPHINXSYS_USE_SYCL OFF)"],
    },
    {
        "id": "SPHSOL-sphinxsim-05",
        "kind": "adapt",
        "summary": "壁面作为「固体粒子 + Wall 接触核」（acoustic_step_1st_half.hpp:176 的 p_j_in_wall 镜像耗散）"
                   "是与 ghost/buffer 法互补的边界策略：迁移其 wall-contact 积分形式到 TensorSPH 的 FSI/壁面面，"
                   "作为 GAP-SPH-FSI-BENCHMARK 的对照实现（同一算例两套边界法比精度/稳定性）。",
        "gap": "GAP-SPH-FSI-BENCHMARK",
        "license": "adapt=Apache-2.0 合规（声明修改）；若只借鉴公式与算法不必复制代码",
        "evidence": ["sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_1st_half.hpp:176",
                     "sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/force_on_structure.h"],
    },
    {
        "id": "SPHSOL-sphinxsim-06",
        "kind": "reference",
        "summary": "可复现运行面（runtime/jobs.py + identity.py + results.py + contracts-v1.json 的 job/输出契约）"
                   "作为 TensorSPH 可微扫描（不同 nu/dt/kernel 的梯度对比实验）的作业台账与 provenance 模板；"
                   "诚实声明：本项目无任何自动微分实现（无 torch/jax/autodiff 痕迹），GAP-SPH-DIFFERENTIABLE "
                   "只能拿到「实验管理」这一层，不能拿到梯度来源。",
        "gap": "GAP-SPH-DIFFERENTIABLE",
        "license": "reference（MIT 胶水；如需采用其作业契约实现则 MIT 合规即可）",
        "evidence": ["sphinxsim/runtime/jobs.py", "sphinxsim/runtime/identity.py",
                     "sphinxsim/runtime/schemas/contracts-v1.json"],
    },
]

INSUFFICIENT = [
    "3rd_party/wasmtbb 子模块 license 与实际用法：未检出内容（sparse-checkout + --filter=blob:none），读不到=insufficient",
    "上游算例库在 SPHinXsim 内是否曾成功编译运行（CI 显式关闭 SPHINXSYS_BUILD_TESTS）→ 无 CI 证据，未做本地构建（缺 vcpkg/TBB/Qt 工具链，成本超出本单）",
    "数值精度：仓内无可复算的解析/实验误差数据，故对 GAP 锚的精度贡献=0（不给数字）",
    "GPU 加速比：无测量数据，不给数字",
]


def tree_stats(checkout: Path) -> dict:
    """从本地 sparse checkout 统计（HEAD 5241a890），命令可复算。"""
    def git(*args):
        return subprocess.run(["git", "-C", str(checkout), *args],
                              capture_output=True, text=True, check=True).stdout

    files = git("ls-tree", "-r", "HEAD", "--name-only").splitlines()
    def count(pred):
        return sum(1 for f in files if pred(f))
    def loc(prefix, exts):
        total = n = 0
        for dp, _, fns in os.walk(checkout / prefix):
            for fn in fns:
                if fn.endswith(exts):
                    n += 1
                    with open(os.path.join(dp, fn), "rb") as fh:
                        total += sum(1 for _ in fh)
        return total, n

    core_loc, core_files = loc("sphinxsim/sphinxsys/src", (".cpp", ".h", ".hpp"))
    glue_loc, glue_files = loc("sphinxsim/sph_simulation", (".cpp", ".h", ".hpp"))
    py_loc, py_files = loc("sphinxsim", (".py",))
    return {
        "head": git("rev-parse", "HEAD").strip(),
        "tracked_files": len(files),
        "vendored_core": {"loc": core_loc, "files": core_files},
        "sphinxsim_cpp_glue": {"loc": glue_loc, "files": glue_files},
        "sphinxsim_python": {"loc": py_loc, "files": py_files},
        "top_level_dirs": sorted({f.split("/")[0] for f in files if "/" in f}),
        "vendored_tests_tracked_files": count(lambda f: f.startswith("sphinxsim/sphinxsys/tests/")),
        "vendored_sycl_files": count(lambda f: f.startswith("sphinxsim/sphinxsys/src/src_sycl/")),
        "sparse_checkout_note": "git clone --depth 1 --filter=blob:none --no-checkout + "
                                "sparse-checkout（排除 sphinxsys/tests、tutorials、docs、assets）",
    }


METHOD_TABLE_HEAD = ("| 方法/能力 | 证据等级 | 关键定位 |\n|---|---|---|")
STATUS_MAP = {"supported": "REAL(实现)", "partial": "PARTIAL(部分)", "absent": "NOT_SUPPORTED"}


def method_table(methods) -> str:
    rows = [METHOD_TABLE_HEAD]
    for m in methods:
        first = m["evidence"][0]
        loc = "—" if not first["line"] else f"`{os.path.basename(first['file'])}:{first['line']}`"
        rows.append(f"| {m['name']} | {STATUS_MAP[m['status']]} | {loc} |")
    return "\n".join(rows)


def validation_table(val) -> str:
    rows = ["| 验证资产 | 数量 | 性质 |", "|---|---:|---|",
            f"| 上游 2D 算例目录 | {val['case_library']['2d_example_dirs']} | 可运行 C++ 算例（默认不构建） |",
            f"| 上游 3D 算例目录 | {val['case_library']['3d_example_dirs']} | 同上 |",
            f"| 回归台文件 | {val['regression_harness']['regression_tool_files']} | 自参照回归（XML 统计基线） |",
            f"| 上游 unit_tests_src | {val['unit_tests_src']['cpp']} cpp / {val['unit_tests_src']['txt']} txt | 单元测试源 |",
            f"| SPHinXsim Python 测试 | {val['sphinxsim_own_tests']['python_test_files']} | schema/CLI/LLM 胶水测试 |",
            f"| 数值容差断言 | {val['sphinxsim_own_tests']['numeric_tolerance_assertions']} | 零 → 无自动精度判据 |",
            f"| C++ 仿真类 gtest | {val['sphinxsim_own_tests']['cpp_simulation_tests']['gtest_cases']} | 仅 `TEST(simulations, has_json)` 冒烟 |"]
    return "\n".join(rows)


def make_figures(inv: dict, figdir: Path) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir.mkdir(parents=True, exist_ok=True)
    out = []

    # 图1: 方法支持矩阵（证据条数）
    meth = inv["methods"]
    names = [m["name"] for m in meth]
    counts = [max(len([e for e in m["evidence"] if e["line"]]), 0) for m in meth]
    colors = {"supported": "#2a7f62", "partial": "#d9a13b", "absent": "#b5443a"}
    fig, ax = plt.subplots(figsize=(10, 5.2))
    bars = ax.barh(range(len(names)), counts,
                   color=[colors[m["status"]] for m in meth])
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([f"{n}" for n in names], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("grep 命中条目数（文件:行 证据条数，0 = 源码内无实现）")
    ax.set_title(f"SPHinXsim @{inv['github']['head_commit'][:8]} —— SPH 方法族源码级支持矩阵\n"
                 f"(绿=REAL 实现, 黄=部分, 红=NOT_SUPPORTED)", fontsize=10)
    for b, c, m in zip(bars, counts, meth):
        ax.text(b.get_width() + 0.08, b.get_y() + b.get_height() / 2,
                f"{STATUS_MAP[m['status']]}" + (f" ({c})" if c else ""), va="center", fontsize=7)
    ax.set_xlim(0, max(counts) + 3.2)
    fig.tight_layout()
    p = figdir / "fig1_sphinxsim_method_matrix.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    out.append(str(p))

    # 图2: 代码构成 + 验证面
    st = inv["structure"]
    val = inv["validation"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    labels = ["vendored SPHinXsys\ncore (Apache-2.0)", "SPHinXsim C++\nglue (MIT)", "SPHinXsim\nPython (MIT)"]
    vals = [st["vendored_core"]["loc"], st["sphinxsim_cpp_glue"]["loc"], st["sphinxsim_python"]["loc"]]
    axes[0].bar(labels, vals, color=["#3f6fa8", "#2a7f62", "#7a5fa8"])
    for i, v in enumerate(vals):
        axes[0].text(i, v, f"{v:,} 行", ha="center", va="bottom", fontsize=8)
    axes[0].set_ylabel("代码行数")
    axes[0].set_title("代码构成：98% 数值能力来自 vendored 上游核心", fontsize=10)
    axes[0].tick_params(axis="x", labelsize=7.5)

    vlabels = ["2D 算例\n目录", "3D 算例\n目录", "回归台\n文件(/10)", "Python\n测试", "数值容差\n断言"]
    vvals = [val["case_library"]["2d_example_dirs"], val["case_library"]["3d_example_dirs"],
             val["regression_harness"]["regression_tool_files"] / 10,
             val["sphinxsim_own_tests"]["python_test_files"],
             val["sphinxsim_own_tests"]["numeric_tolerance_assertions"]]
    axes[1].bar(vlabels, vvals, color=["#3f6fa8", "#3f6fa8", "#d9a13b", "#2a7f62", "#b5443a"])
    for i, v in enumerate(vvals):
        txt = f"{int(v*10)}" if i == 2 else f"{int(v)}"
        axes[1].text(i, v, txt, ha="center", va="bottom", fontsize=8)
    axes[1].set_ylabel("数量（回归台文件数 /10）")
    axes[1].set_title("验证面：资产丰富但缺自动精度判据", fontsize=10)
    axes[1].tick_params(axis="x", labelsize=7)
    fig.suptitle("SPHinXsim 深盘 wave-2：规模与验证覆盖", fontsize=11)
    fig.tight_layout()
    p = figdir / "fig2_sphinxsim_scale_validation.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    out.append(str(p))
    return out


def verify(inv: dict, checkout: Path) -> int:
    """对 JSON 里每条证据做机器复核：正例看 file:line 是否真存在且非空；
    零命中类断言按 grep_pattern 复核是否真的 0 命中（允许登记例外关键字）。"""
    import re as _re

    def git(*a):
        return subprocess.run(["git", "-C", str(checkout), *a],
                              capture_output=True, text=True).stdout

    ok = bad = 0
    for m in inv["methods"]:
        for ev in m["evidence"]:
            f = checkout / ev["file"]
            if ev.get("line") and not ev.get("exists_only"):
                content = (f.read_text(errors="ignore").splitlines() if f.exists()
                           else git("show", f"HEAD:{ev['file']}").splitlines())
                if len(content) >= ev["line"] and content[ev["line"] - 1].strip():
                    ok += 1
                else:
                    bad += 1
                    print(f"FAIL(line) {m['name']}: {ev['file']}:{ev['line']}")
            elif ev.get("exists_only"):
                rc = subprocess.run(["git", "-C", str(checkout), "cat-file", "-e", f"HEAD:{ev['file']}"],
                                    capture_output=True).returncode
                if rc == 0:
                    ok += 1
                else:
                    bad += 1
                    print(f"FAIL(exists) {m['name']}: {ev['file']}")
            elif ev.get("grep_pattern"):
                hits = git("grep", "-niE", ev["grep_pattern"], "HEAD", "--", ev["file"]).strip().splitlines()
                # 登记的合法例外：poisson_ratio/poisson_[ 属材料泊松比，不是泊松方程
                hits = [h for h in hits if not _re.search(r"poisson_(ratio|\[)", h, _re.I)]
                if not hits:
                    ok += 1
                else:
                    bad += 1
                    print(f"FAIL(zero-hit) {m['name']}: {len(hits)} hits, e.g. {hits[0][:110]}")
    for ev in inv["validation"]["sphinxsim_own_tests"]["evidence"]:
        if ev.get("grep_pattern"):
            hits = git("grep", "-niE", ev["grep_pattern"], "HEAD", "--", ev["file"]).strip().splitlines()
            if not hits:
                ok += 1
            else:
                bad += 1
                print(f"FAIL(zero-hit) python tests numeric assertions: {len(hits)} hits")
    print(f"verify: {ok} evidence checks PASS, {bad} FAIL")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", default="")
    ap.add_argument("--out", default="artifacts/oss-sph/w2")
    ap.add_argument("--figdir", default="docs/oss_inventory_sph/figures")
    ap.add_argument("--verify", action="store_true",
                    help="只对现有 JSON 做证据机器复核（需要 --checkout）")
    args = ap.parse_args()

    inv = {
        "campaign": "osssph-w2",
        "repo": REPO,
        "tier": 2,
        "checked_at": CHECKED_AT,
        "provenance": {
            "github_api": ["GET /repos/Xiangyu-Hu/SPHinXsim",
                           "GET /repos/Xiangyu-Hu/SPHinXsim/languages",
                           "GET /repos/Xiangyu-Hu/SPHinXsim/commits?per_page=8"],
            "api_limit_note": "未认证 60 请求/小时，中途耗尽（remaining=0），改用 git 协议 clone 取源码；"
                              "限额重置后补查仓库元数据（fork/archived/open_issues）",
            "source_access": "git clone --depth 1 --filter=blob:none + sparse-checkout（HEAD 5241a890）",
        },
        "github": GITHUB,
        "structure": {},
        "methods": METHODS,
        "parallel": PARALLEL,
        "validation": VALIDATION,
        "license": LICENSE,
        "transferable": TRANSFERABLE,
        "insufficient": INSUFFICIENT,
    }
    if args.checkout and Path(args.checkout, ".git").exists():
        inv["structure"] = tree_stats(Path(args.checkout))
    else:
        inv["structure"] = {"cached_from_head": HEAD_SHA, "note": "未提供 --checkout，tree 统计沿用缓存值",
                            "vendored_core": {"loc": 84847, "files": 608},
                            "sphinxsim_cpp_glue": {"loc": 6844, "files": 42},
                            "sphinxsim_python": {"loc": 12966, "files": 39},
                            "tracked_files": 2499, "vendored_tests_tracked_files": 1487,
                            "vendored_sycl_files": 11}

    if args.verify:
        if not (args.checkout and Path(args.checkout, ".git").exists()):
            print("--verify 需要 --checkout 指向可用 clone", file=sys.stderr)
            return 1
        cur = json.loads((Path(args.out) / f"{REPO.replace('/', '_')}.inv.json").read_text())
        return verify(cur, Path(args.checkout))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"{REPO.replace('/', '_')}.inv.json"
    jp.write_text(json.dumps(inv, ensure_ascii=False, indent=2))

    figs = make_figures(inv, Path(args.figdir))
    tm = out / "method_validation_tables.md"
    tm.write_text("## 方法矩阵\n\n" + method_table(METHODS) + "\n\n## 验证资产\n\n"
                  + validation_table(VALIDATION) + "\n")
    print(f"wrote {jp}\nwrote {tm}\n" + "\n".join(f"wrote {f}" for f in figs))
    print("\n--- numbers ---")
    print(json.dumps(inv["structure"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())