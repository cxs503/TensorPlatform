# 【盘点】发现单（架构专项）——开源 LBM 技术盘点：补全前 30 名清单

- mission: 8308 / repo: TensorPlatform / branch: agent/m8308
- scenario_key: `oss-inventory` · 关联特性: `FEAT-OSS-INVENTORY-LBM`
- 基线：`oss_inventory` 表在册 24 仓（`SELECT repo,tier,focus FROM oss_inventory`）
- 采集时刻：2026-11-09T05:4x–06:3x Z（GitHub API 快照；stars 为快照值，随时间漂移）
- 纪律：**只发现不深盘**（不 clone、不读源码，仅 API 元数据 + README 关键词行）；宁缺毋滥

## 1. 方法：10 个检索面交叉查 + 清单仓收录项

非认证 GitHub API（`/search/repositories`、`/repos/{owner}/{name}`、`/repos/{o}/{n}/readme`）。
每条响应落盘 `artifacts/oss_inventory_discovery/raw/*.json|md` 并记 `sha256`（`manifest.json`），
所有数字由脚本 `merge.py` / `axis_analysis.py` 程序化聚合，零手抄。

| 检索面 | 查询 | 返回 |
|---|---|---|
| topic_lbm | `topic:lattice-boltzmann` | 100 |
| topic_lbm_method | `topic:lattice-boltzmann-method` | 38 |
| topic_multiphase_lbm | `topic:multiphase-lbm` | **0**（该 topic 无仓，命名不可靠） |
| topic_lbm | `topic:lbm` | 100（含噪声，见 §5） |
| name_desc_stars | `lattice boltzmann` sort:stars | 100 |
| rust | `lattice boltzmann language:rust` | 36 |
| julia | `lattice boltzmann language:julia` | 25 |
| jax_ml | `jax OR taichi OR differentiable OR neural` | 50 |
| quantum | `quantum lattice boltzmann` | 20 |
| amr_adaptive | `adaptive mesh refinement` | 7 |
| unstructured | `unstructured OR curvilinear OR off-lattice` | 50 |
| 清单仓 | `sthavishtha/list-lattice-Boltzmann-codes` README | 35 条 github 链接，其中 26 条不在册 |

去重后并集 **278 仓**；与在册 24 仓 diff 得 **259 仓清单外**；259 中 55 仓被 ≥3 个检索面同时命中。

清单仓 26 条不在册链接里，逐条取证后：**1 条转为本单候选**（`apes-suite/musubi`）、
**1 条死链**（`blackoffee/3D-LBM-AMR` → 404）、其余为教学/停更/非本体，见 §4。
换言之清单仓对本单的边际贡献很低（26 条仅 1 条入候选）——**新架构轴主要靠 topic/language 面挖到，而非清单仓**。

## 2. 候选表（8 个清单外仓，按 stars 降序）

| # | repo | stars | 为什么值得盘（存在性证据=API 元数据+README 行） | tier |
|---|---|---|---|---|
| 1 | `tangency-labs/fieldcraft` | 348 | Rust 纯自研 CAE 系统，topics 含 `lattice-boltzmann`/`cfd`/`fem`，Apache-2.0，2026-10-13 建、2026-11-09 活跃；README:"D3Q19 lattice Boltzmann flow with forces on the part's faces"。**在册 24 仓 Rust 为零**，且它示范"LBM 作为 CAE 系统内一个求解器"的集成范式 | 3 |
| 2 | `lanl/MF-LBM` | 132 | LANL/DOE 生产码："portable, scalable and high-performance LB code for DNS of flow in porous media"，directive 式并行使能 CPU/GPU/MIC/ARM。在册 tier1 全是 Shan-Chen/颜色梯度谱系，**缺保守相场 + 多孔两相 DNS** 一支 | 1 |
| 3 | `QCFD-Lab/qlbm` | 38 | 唯一成包量子 LBM：Qiskit 构造量子 LBM 电路 + Pytket 编译到量子机/模拟器；2026-11-06 活跃。给出"量子化碰撞/迁移算符"的形式化定义，是方法谱系的**边界参照**（在册为零） | 3 |
| 4 | `Zitzeronion/Swalbe.jl` | 31 | Julia 薄膜/液滴 LBM（润滑近似薄膜方程），2026-10-29 活跃。**Julia 生态在册为零**；代码量小（可读性强）适合作 Julia 移植模板，且与在册自由面/多相谱系互补 | 1 |
| 5 | `yue-sun/lbrmt` | 26 | JCP **526**(2025) 论文配套：reference map technique 处理大变形固体 + 新 LB 边界条件，实现全耦合 FSI。**非结构/曲线格轴在册仅 1 仓**（LIFE），本仓提供"非共形固体嵌入"的最小可用参照 | 3 |
| 6 | `NatLabRockies/marbles` | 21 | "MARBLES: Multi-scale Adaptively Refined Boltzmann LatticE Solver"，C++ + **AMReX** 块结构 AMR，2026-09-11 活跃。**AMR/自适应轴在册为零**；AMReX 是块 AMR 工业标准，耦合接口（细化比/时空回流）可直接对照 | 3 |
| 7 | `flt-acdesign/OpenLUDWIG` | 16 | Julia + CUDA.jl 的 GPU 原生 D3Q27 LBM（README 31.5 KB、v1.10、2026-02 活跃）。单点覆盖 **Julia + GPU** 双缺口（注：GPU 而非可微） | 2 |
| 8 | `apes-suite/musubi` | 8 | 八叉树自适应 LBM 多物理求解器（APES 生态/HLRS 线），README:"Octree based Lattice-Boltzmann solver with support for multi-physics"，2026-10-16 活跃。与 #6 的块 AMR 是**两条不同自适应路线**。低星≠玩具：属生产求解器 | 3 |

> tier 口径：1 多相 / 2 可微·GPU / 3 HPC·系统。一个仓可跨轴，tier 取其在谱系中最主打的一面。

### 覆盖增益（程序化统计，见 `axis_coverage.json` / 图 1）

| 轴 | 在册 | 本单净增 | 合计 |
|---|---|---|---|
| 多相 | 8 | +2 | 10 |
| 可微·ML·GPU | 9 | +1 | 10 |
| **Rust** | **0** | **+1** | 1 |
| **Julia** | **0** | **+2** | 2 |
| **量子 LBM** | **0** | **+1** | 1 |
| **AMR/自适应** | **0** | **+2** | 2 |
| 非结构·曲线格 | 1 | +1 | 2 |

结论：任务书点名的四条重点轴（Rust/Julia/量子/AMR）在册覆盖率全为 0——不是"漏了几个仓"，
是**整条技术轴缺失**；本单 8 仓把 4 条空轴补到 ≥1 仓，非结构轴从 1 补到 2。

## 3. 机器块（每候选一条，供 oss_inventory 摄入）

```
[OSS-INV]
repo: tangency-labs/fieldcraft
tier: 3
focus: Rust 纯自研 CAE 系统内嵌 D3Q19 LBM（README: 面力 drag per face），Rust 架构轴唯一代表
status: suggested
[/OSS-INV]

[OSS-INV]
repo: lanl/MF-LBM
tier: 1
focus: LANL/DOE 多孔介质多相 DNS 的保守相场 LBM，directive 式并行覆盖 CPU/GPU/MIC/ARM
status: suggested
[/OSS-INV]

[OSS-INV]
repo: QCFD-Lab/qlbm
tier: 3
focus: 量子 LBM 成包实现（Qiskit 建电路 + Pytket 编译），量子化碰撞/迁移算符形式化参照
status: suggested
[/OSS-INV]

[OSS-INV]
repo: Zitzeronion/Swalbe.jl
tier: 1
focus: Julia 薄膜/液滴多相 LBM（润滑近似），Julia 生态与多相谱系互补的最小可读实现
status: suggested
[/OSS-INV]

[OSS-INV]
repo: yue-sun/lbrmt
tier: 3
focus: LBRMT（JCP 2025 配套）reference-map 大变形固体全耦合 FSI 与新 LB 边界条件
status: suggested
[/OSS-INV]

[OSS-INV]
repo: NatLabRockies/marbles
tier: 3
focus: MARBLES：AMReX 块结构自适应细化的多尺度 LBM 求解器（AMR 轴代表）
status: suggested
[/OSS-INV]

[OSS-INV]
repo: flt-acdesign/OpenLUDWIG
tier: 2
focus: Julia+CUDA.jl 的 GPU 原生 D3Q27 LBM（Julia 与 GPU 双缺口的单点覆盖，非可微）
status: suggested
[/OSS-INV]

[OSS-INV]
repo: apes-suite/musubi
tier: 3
focus: 八叉树自适应多物理 LBM（APES/HLRS 生产码），与块 AMR 互补的另一条自适应路线
status: suggested
[/OSS-INV]
```

## 4. 明确排除项及理由

| repo | stars | 排除理由 |
|---|---|---|
| `pmocz/latticeboltzmann-python` | 236 | 单文件教学脚本：无多相/边界条件库/并行工程面，star 高但不可盘 |
| `jviquerat/lbm` | 211 | "A simple full-python 2D lattice-boltzmann code"，纯教学 |
| `zmhhaha/LBM-Cplusplus-A.A.Mohamad` | 150 | 教科书习题 C++ 版；在册已有 Krüger 教科书官方码，重复 |
| `shurikkuzmin/LatticeBoltzmannMethod` | 120 | Jupyter 教学 examples，无工程面 |
| `rlee32/lbm_matlab` | 108 | MATLAB 教学码，2017 停更 |
| `siramirsaman/LBM` | 78 | MATLAB 曲边界实现，理论点有价值但 2018 停更 + MATLAB 不可工程化 |
| `ParticulateFlow/LBDEMcoupling-public` | 75 | Palabos-LIGGGHTS 耦合；Palabos 已在册（`omalaspinas/palabos`），耦合层价值低于占位成本 |
| `Autodesk/Neon` | 70 | **非 LBM 求解器本体**（多设备 grid 框架，含 LBM grid-refinement app，IPDPS 2024）；列观察名单 |
| `mattatz/unity-lbm-*` | 34/64 | Unity/GLSL 纯可视化渲染 |
| `kircher1/LBM-SWE-Unity` | 38 | Unity 浅水可视化包 |
| `randerka/lbm-imgui`、`randerka/lattice-boltzmann-simulator` | 23/22 | OpenGL/WebGL2 交互可视化 |
| `UoB-HPC/advanced-hpc-lbm` | 35 | 课程作业（COMS30053），教学面 |
| `cmendl/LBM-classic` | 39 | Mathematica/教学经典实现，2014 停更 |
| `UCL/LatBo.jl` | 21 | Julia 但 2017 停更、README 238 B、无工程面（Julia 位由 #4/#7 占） |
| `otbrown/LAMBReX` | 14 | AMReX 骨架，2020 停更，README 自陈"只有一个 pulse 示例" |
| `gnzlbg/lbm-rs` | 24 | Rust 但 2017 停更（Rust 位由 #1 占） |
| `loliverhennigh/latnet` | 25 | 2018 停更；同作者 TensorFlow 仓已在册且更完整，重复 |
| `comp-physics/QLBM-frugal` | 5 | archived（归档不再维护） |
| `zhulianhua/fvLBM` | 22 | 有限体积 LBM，2017 停更；非结构/曲线格由 #5 覆盖 |
| `AMReX-Codes/amrex` | — | 通用 AMR 框架，非 LBM 本体（作为 #6 的底座参照） |
| `sthavishtha/list-lattice-Boltzmann-codes` | 265 | 清单仓本身（本次线索源，非候选） |
| `LZF1111/nullflux` | 38 | AI Agent 外壳（调 OpenFOAM/MFIX/ParaView），自身无 LBM 内核 |
| `blackoffee/3D-LBM-AMR` | — | **死链**：API 返回 404，清单仓指向已删/改名仓（清单仓本身需维护） |

### 观察名单（本轮不占候选位，理由=本体性或重复性，非质量）
`VioletSpace/IonSolver`(25★, Rust+OpenCL 扩展 LBM 做等离子体 MHD，2026-05 活跃)、
`Autodesk/Neon`(70★, 多设备 grid 框架)、`grasingerm/lbxflow`(21★ Julia)、
`je-santos/PoreFlow-Net`(81★ 代理模型，非 LBM 求解器)。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **`topic:lbm` 语义污染**：100 条命中里 24 条描述与 topics 都不含 "boltzmann"——最典型
   `Lora-net/SWL2001`(203★) 是 **LoRa Basics Modem**（LBM 缩写撞车）。按 topic 单面收录会直接污染清单，
   必须跨面交叉或 README 复核。`topic:multiphase-lbm` 干脆 **0 条**：topic 命名生态不可靠。
2. **GitHub 上不是全部**：Palabos、OpenLB 主仓在 GitLab（在册 `omalaspinas/palabos` 是镜像）。
   本单只覆盖 GitHub 面，GitLab/自建托管（OpenLB、Palabos 官方、Musubi 老地址）不在统计内——
   **清单的"完整"上限受托管面限制**，这是盘点口径的固有缺口，不是遗漏。
3. **stars ≠ 质量**：`apes-suite/musubi`(8★) 是生产求解器；`pmocz`(236★) 是教学脚本。
   故本单用"存在性证据(API 元数据) + 相关性证据(README 关键词行) + 轴必要性"三合一判据，
   不以 star 排序做收录决定，只以 star 做表内排序。
4. **只发现不深盘**：未读源码，`focus` 依据 README 自述——**可能存在 README 夸大或功能未完成**（如
   `fieldcraft` README 自陈"viewport 不画流场结果(M12)"，LBM 面能力以 README 为准待深盘核实）。
5. **快照时效**：stars/pushed_at 为 2026-11-09 快照；`marbles`/`fieldcraft` 等仓 `pushed_at` 落在快照当日，
   活跃度高但成熟度未验证。

## 6. 误差判定

本单为**发现/盘点产物，无物理量、无解析解基准**，"<3% 误差"判据不适用。可核替代判据：

| 可核量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| 候选仓 stars/description | GitHub API 原始响应（`raw/repo_*.json`、`raw/*.json`，sha256 在 `manifest.json`） | 8/8 条与 API 逐一一致，0 处人工改写 | 0% 偏差 ✔ |
| 候选"不在册"判定 | `oss_inventory` 24 行 diff（`existing_repos.txt`） | 8/8 确不在册 | 0 偏差 ✔ |
| 轴覆盖计数 | `axis_analysis.py` 由显式映射表复算（`axis_coverage.json`） | 可一键复现 | 程序化 ✔ |
| 检索面完整性 | 10 个检索面并集 278 仓 | **不可证完整**（GitLab/自建托管不在面内） | 声明为口径局限 ✘ |

诚实边界：**"补全前 30 名"已达成 24+8=32 仓在册口径**，但"完整"不可证（托管面限制）；
候选 `focus` 一句为 README 自述转写，未做源码级核实。

## 7. 复现

```bash
cd artifacts/oss_inventory_discovery
python3 discover.py        # 10 个检索面（限速 20s，403 退避），落盘 raw/ + manifest.json
python3 merge.py           # 去重 → pool.json / outside_pool.tsv / curated_list_links.json
python3 verify.py          # 检索面未覆盖的清单仓直查 + README grep（shortlist_evidence.json）
python3 verify2.py         # 候选 README 取证（第二来源）
python3 axis_analysis.py   # 轴覆盖统计 + 三张图
```