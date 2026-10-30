# 样本数据库 Schema v1.0

> 对标指南「知识库 ≥1 万条样本」，本 schema 覆盖流体/结构/多体三学科，字段可扩展。

## 表结构：`samples`

| 字段名 | 类型 | 必填 | 说明 | 学科覆盖 |
|--------|------|------|------|----------|
| `sample_id` | INTEGER PK | ✓ | 自增主键 | 通用 |
| `mission_id` | INTEGER | ✓ | 来源任务编号 (mNNN) | 通用 |
| `case_name` | TEXT | ✓ | 工况名称 (e.g. `sphere_re100`) | 通用 |
| `discipline` | TEXT | ✓ | 学科: `fluid` / `structure` / `multibody` | 通用 |
| `solver_type` | TEXT | ✓ | 求解器: `LBM` / `FVM` / `FEM` / `BEM` | 通用 |
| `created_at` | TEXT | ✓ | 入库时间 ISO-8601 | 通用 |

### 工况参数 (`工况参数`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `reynolds` | REAL | 雷诺数 Re |
| `mach` | REAL | 马赫数 Ma |
| `froude` | REAL | 弗劳德数 Fr |
| `tau_lb` | REAL | LBM 松弛时间 τ |
| `lattice` | TEXT | 格子类型 (D2Q9/D3Q19/D3Q27) |
| `collision` | TEXT | 碰撞模型 (BGK/MRT/KBC) |
| `boundary` | TEXT | 边界条件描述 |
| `n_steps` | INTEGER | 总步数 |
| `grid_dims` | TEXT | 网格尺寸 JSON 数组 `[nx,ny,nz]` |
| `n_cells` | INTEGER | 总网格数 |
| `u_lb` | REAL | 特征速度 (格子单位) |
| `nu_lb` | REAL | 运动粘度 (格子单位) |

### 几何编码 (`几何编码`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `geometry_type` | TEXT | 几何类型: `parametric` / `sdf` / `mesh` / `analytic` |
| `geometry_params` | TEXT | 参数向量 JSON (e.g. `{"L":48,"D":20}`) |
| `geometry_sdf` | TEXT | SDF 描述/路径 (可选) |
| `Cb` | REAL | 方形系数 (船体) |
| `L_pp` | REAL | 垂线间长 |
| `B` | REAL | 型宽 |
| `T` | REAL | 吃水 |
| `blockage_pct` | REAL | 阻塞比 % |

### 结果向量 (`结果向量`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `results_json` | TEXT | 完整结果 JSON (含 Cd, Cl, err_pct 等) |
| `cd_total` | REAL | 总阻力系数 |
| `cl_mean` | REAL | 平均升力系数 |
| `l2_rel_err` | REAL | L2 相对误差 |
| `max_abs_err` | REAL | 最大绝对误差 |
| `mass_drift_pct` | REAL | 质量漂移 % |
| `elapsed_s` | REAL | 计算耗时 (秒) |

### 判据值+PASS 标志 (`判据值+PASS 标志`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `verdict` | TEXT | 判定: `PASS` / `FAIL` / `HONEST_FAIL` / `UNKNOWN` |
| `pass_flag` | INTEGER | 1=PASS, 0=FAIL |
| `gate_criteria` | TEXT | 门限判据 JSON |
| `err_pct_max` | REAL | 最大误差 % |
| `gate_threshold_pct` | REAL | 门限阈值 % (通常 3%) |
| `within_gate` | INTEGER | 1=在门限内, 0=超出 |

### 设备与精度指纹 (`设备与精度指纹`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `device` | TEXT | 设备标识 (e.g. `sdaa`, `cuda:0`) |
| `device_name` | TEXT | 设备名称 (e.g. `/dev/tcaicard5`) |
| `compile_mode` | TEXT | 编译模式: `eager` / `triton` / `torch.compile` |
| `dtype` | TEXT | 数据精度: `fp32` / `fp64` |
| `torch_version` | TEXT | PyTorch 版本 |
| `finite` | INTEGER | 1=有限值, 0=含 NaN/Inf |
| `diverged` | INTEGER | 1=发散, 0=收敛 |

### 谱系指针 (`谱系指针`)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `artifact_path` | TEXT | 工件文件相对路径 |
| `code_sha` | TEXT | 代码 Git SHA |
| `model_version` | TEXT | 模型版本 |
| `parent_mission_id` | INTEGER | 父任务 ID (谱系上游) |
| `round` | INTEGER | 重跑轮次 |
| `source_repo` | TEXT | 来源仓库 |

## 扩展字段 (JSON)

`extra_json` TEXT — 存储学科特有字段，保持 schema 稳定：
- **流体**: `turbulence_model`, `wall_function`, `y_plus`
- **结构**: `material`, `boundary_conditions`, `modal_freqs`
- **多体**: `body_count`, `coupling_method`, `time_step`

## 索引

```sql
CREATE INDEX idx_mission ON samples(mission_id);
CREATE INDEX idx_case ON samples(case_name);
CREATE INDEX idx_discipline ON samples(discipline);
CREATE INDEX idx_verdict ON samples(verdict);
CREATE INDEX idx_device ON samples(device);
```

## 学科覆盖矩阵

| 学科 | 已支持字段 | 扩展字段 (extra_json) |
|------|-----------|----------------------|
| 流体 (fluid) | Re, Ma, τ, lattice, Cd, Cl, err | turbulence_model, wall_function |
| 结构 (structure) | geometry_params, grid_dims | material, modal_freqs, stress |
| 多体 (multibody) | n_steps, elapsed_s | body_count, coupling_method |
