# 硬件 × 环境需求矩阵

> owner 2026-11-05 定调：项目最终按届时硬件适配环境；当前阶段**主体代码正确性优先**，
> 环境差异不阻断主体交付（环境类红定谳后放行）。本矩阵 = 各硬件目标的环境需求基线，
> 新硬件接入时按行补齐，勿为单一环境过度特化主体代码。

## 1. 矩阵总表

| # | 硬件目标 | 架构/OS | Python | 计算 栈 | 角色 | 适配状态 |
|---|---|---|---|---|---|---|
| H1 | GitHub Actions runner | x86_64 / ubuntu-24.04 | 3.10–3.13 矩阵 | CPU（pip `-e ".[test]"`） | **CI 质量基准面**（四门之 CI 绿的裁判） | ✅ 常态运行 |
| H2 | 5090 服务器（192.168.215.56） | x86_64 / Linux | 3.10+ | torch 2.11+cu128，8×RTX 5090 | 主开发/长跑（`/nfs/wangxi/venvs/tensorlbm`） | ✅ 主力 |
| H3 | 无锡 wxsc Dell | x86_64 / Linux | 3.10 | CPU torch（8×3090 仅 GPU1/4/6 可用） | 备用算力 | ⚠️ 二线（GitHub 更新） |
| H4 | 99 本机容器（lbm_test） | **LoongArch64** / Linux | 3.10 | CPU torch + `torch_sdaa` stub；`/usr/bin/ruff` 0.15 | 平台生产（TensorPlatform :8010 + 全守护） | ✅ 生产 |
| H5 | 外协龙芯 k8s（CPU 池） | LoongArch64 / k8s | 3.10 | CPU torch | 计算池扩容（`tp-` 前缀对象 + nodeSelector 钉扎） | ✅ 已并入计算池 |
| H6 | 外协龙芯 k8s（GPU 池） | LoongArch64 / k8s + volcano | 3.10 | 国产卡（tcaicard16/17）torch | GPU 加速（**必 volcano 调度**，matmul 30–38×） | ✅ 桥已通 |
| H7 | SDAA 加速卡槽位（99，32 槽） | LoongArch64 | 3.10 | `torch_sdaa`（AUTOLOAD=0 配方） | 平台侧加速 | ⚠️ Triton 无 SDAA 后端=CPU 解释 |
| H8 | 申威 psn002 | Sunway / Linux | — | SWLBM 自有栈（LSF + C89 从核） | 超算专项 | 🔒 项目专项（非通用面） |

## 2. 每硬件环境需求（依赖基线）

| 硬件 | 必装 | 版本要点 | 已知坑（勿重试） |
|---|---|---|---|
| H1 CI | 仓内 `pyproject` `[test]` extra + ruff | ruff 0.17.x；矩阵 3.10–3.13 | **依赖声明病**：运行时 import 的库必须进 `dependencies`（判例：DEM scipy / TP numpy / BEM scipy）——装包成功≠能 import |
| H2 5090 | torch+cu128 venv | 2.11+cu128 | 根分区 70G 小：一切大数据走 `/nfs` |
| H4 99 容器 | 平台 `.venv`（psycopg3/asyncpg） | python3.10 | fchmodat2 需 seccomp 单条放行；RTC 时钟；PG socket trust=应急通道 |
| H5/H6 龙芯 k8s | 集群镜像 + lws_bridge | — | GPU 必 volcano；对象名 `tp-` 前缀；凭据 `.lws7*cred` |
| H7 SDAA | `TORCH_DEVICE_BACKEND_AUTOLOAD=0` | — | Triton 只 CPU 解释；autoload 关闭后静默问题多 |
| H8 申威 | SWLBM 构建配方 | C89 从核 | lustre 配额；LSF CWD 规则 |

## 3. 跨硬件等价性判据（已验证）

- **数值等价**：Holtrop 256 工况 LoongArch64↔x86_64 **256/256 逐位一致**（2026-10-30）。
- **判读纪律**：99=loong64、CI=x86——**分架构判读**；实测差 >浮点噪声量级（判例
  +8.6% vs −31.8%）= 方法本身架构敏感，走 strict xfail 双向检测入重定基清单，
  **禁单侧硬 pin**。
- **本地绿≠CI 绿**：容器 loong64 本地 pytest 绿 ≠ GitHub x86 绿（判例 m3312）；
  修红单验收的「CI 绿」一律指 GitHub x86。

## 4. 环境类红的处置规矩（owner 2026-11-05）

1. CI 红先定谳三类：**环境类**（依赖声明/打包/收集死）/ **风格类**（ruff，机器可修）/
   **主体类**（真代码缺陷）。
2. 环境类 → 根修一处（通常 pyproject 一行）+ 受影响 PR `update-branch` 复绿放行，
   **不为环境差异改主体代码**。
3. 风格类 → pusher v6.0 ruff 预检门已在源头自动修/拦（`--fix` 后仍红=F821 类真错才阻断）。
4. 主体类 → 管到底 custody 轮修到合并进 main。
