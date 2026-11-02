# 并行训练通道（ray_trainer）留出评估契约修复：从「训练行当验证集」到可复算的留出评估

## 1 案例介绍

平台能力「分布式/并行训练」（`backend/services/ray_trainer.py`，Ray Data-Parallel sklearn 通道）对外
宣称返回模型质量指标（`metrics`）。本单审计该指标的**证据属性**：它是留出泛化能力，还是样本内拟合优度。

审计对象是平台代码本身（非船舶几何算例），故无船型主尺度/Cb 几何校验项；判据取**可解析手算的
已知答案测试**：标签与特征独立（真实技能 = 0）与无噪声线性目标（真实技能 = 1）。

| 缺陷 | HEAD 语义（`57e72968`） | 后果 |
|---|---|---|
| L1 训练行当验证集 | 分片训练覆盖全部行，却在 `X_scaled[:min(100, len(X_scaled))]`（训练行）上评估 | 上报 R² 是样本内拟合优度；**纯噪声标签也能报出 R²≈0.505** |
| L2 单类留出折伪造完美分 | `else: metrics = {"accuracy": 1.0, "f1": 1.0}`（未评估任何预测） | 无信息场景上报满分 |
| L3 scaler 全量 fit + 分类任务算回归指标 | `StandardScaler().fit_transform(X)` 在切分之前；单进程回退无视 `task_type` 一律 `r2` | 特征统计量泄漏；分类任务上报 R² |
| L4 Ray Train 分支评估未训练网络 | `create_torch_model(...)`（随机初始化）后 `fit()` 的 checkpoint 未回灌即评估 | 指标与任何已训练模型无关 |

## 2 怎么做的

1. **先读代码定位**：`grep` 全仓 `X_scaled[:` / `fit_transform(X)` 模式，唯一泄漏点在
   `ray_trainer.py:216`；`distributed_training_service.py` 与 `real_training_service.split_training_data`
   均为「先切分后标准化」，无同类问题（横切对照，避免重复修复）。
2. **抽出 numpy-only 评估契约** `backend/services/training_metrics.py`：`holdout_split`（random 复刻
   `train_test_split` 索引语义 / chronological 尾部留出）、`r2/rmse/mae/accuracy/f1_score`（与
   `sklearn.metrics` 定义逐位一致，有 sklearn 时由测试交叉复核）、`evaluate_regression/
   evaluate_classification`（退化留出折返回 `metrics_valid=False` + `status`，**不返回伪造完美分**）。
   解耦动机：本机镜像无 scikit-learn / ray，若评估逻辑绑死 sklearn，该通道无法被独立复核。
3. **修 ray_trainer**：先 `holdout_split` → 训练折上 fit scaler → 只在训练折分片 → 留出折评估；
   Ray 失败回退用**同一**留出折；分类走分类指标；L4 分支显式置 `metrics_valid=False`
   （`status="ray_train_train_checkpoint_not_reloaded"`，诚实降级而非上报随机网络成绩）。
4. **工具链**：Python 3.11 + numpy 2.4.6 + matplotlib 3.10.8 + pytest；全部数字由
   `scripts/audit_ray_trainer_holdout.py` 程序化写入 `artifacts/ray_trainer_holdout/audit.json`，
   本报告数字均由该 JSON 渲染（零手抄）。
5. **测试**：`backend/tests/test_ray_trainer_holdout_contract.py`（9 项，8 通过 1 跳过——sklearn 交叉
   复核因本机无 sklearn 而 skip），模型工厂注入 + 假 Ray shim，**无需 sklearn/ray 即可跑**。

## 3 计算结果

### 3.1 泄漏量级（纯噪声标签，真实技能=0，5 组种子 n=1000）

| 口径 | R²（均值 ± σ） | 说明 |
|---|---|---|
| OLD（训练行） | **0.505 ± 0.052** | 分片 1-NN 在自己见过的行上「预测」自己 |
| NEW（留出折） | **-0.457 ± 0.101** | 无技能模型的诚实读数（<0） |
| 虚高量级 | **96.2 个百分点** | 旧口径把「零技能」报成「中等拟合」 |

### 3.2 灵敏度（修复不得把真实技能一起抹掉）：无噪声线性目标 y=3x₁−2x₂+2

| 量 | 值 |
|---|---|
| NEW 留出 R²（最小二乘） | **1.000** |
| ORACLE 独立复算 | **1.000** |
| OLD（训练行，1-NN） | 0.998 |

### 3.3 泄漏与样本量无关（n 扫描，噪声标签）

| n_samples | OLD R² | NEW R² | 留出折大小 |
|---|---|---|---|
| 200 | 0.481 | -0.320 | 40 |
| 500 | 0.613 | -0.637 | 100 |
| 1000 | 0.365 | -0.405 | 200 |
| 2000 | 0.352 | -0.554 | 400 |

旧口径 R² 不随样本量衰减（0.481 → 0.352），说明它不是统计噪声而是
结构性的评估集错位；新口径始终无技能（≤0）。

### 3.4 退化留出折（L2）

| 口径 | 上报 |
|---|---|
| OLD 单类留出折 | `accuracy=1.0, f1=1.0`（**未评估任何预测**） |
| NEW 单类留出折 | `metrics={'accuracy': 0.0, 'f1': None}, metrics_valid=False, status='single_class_holdout'` |

## 4 结果图

图1：同一数据、同一模型族下 OLD/NEW 上报 R² 对比（左：噪声标签应=0；右：线性目标应=1）。
图2：R² 随样本量的稳定行为——泄漏不随 n 衰减。

## 5 注意点

- **本机无 scikit-learn/ray**：`_train_sklearn_parallel` 的 sklearn 模型本体与 Ray 真集群路径未在本机执行；
  已用「模型工厂注入 + 假 Ray shim」把**评估契约**（切分/集成/指标/退化判定）覆盖到，模型拟合质量本身
  不在本单证据范围。sklearn 指标等价性测试在生产镜像 CI 会实跑（本机 skip）。
- **L4（Ray Train checkpoint 未回灌）只做诚实降级，未做修复**：需要 `result.checkpoint` 回读权重，
  本机无 Ray 无法验证，故显式 `metrics_valid=False` 而非伪造；
- 旧口径在噪声场景报 0.505 而非 1.0，是因为集成把「自己分片内的精确回忆」与
  「另一分片的盲猜」平均——这恰是线上会真实发生的读数，不是人为夸大的上界；
- 下游消费方（监控/MLOps 看板）现需按 `metrics_valid`/`status` 过滤，`metrics` 在不可用时为 `None`；
- `train_distributed` 的 MLP 分支在无 Ray 时转投 `TrainingService`，其指标由 m1918 单独治理，未在本单重复。

## 6 误差判定（关键量 vs 解析基准）

| 关键量 | 实测 | 解析基准 | 误差 | 是否 <3% |
|---|---|---|---|---|
| NEW 线性锚点留出 R² | 1.000 | 1.0（最小二乘精确恢复） | 0.00% | **是** |
| ORACLE 线性留出 R²（独立复算） | 1.000 | 1.0 | 0.00% | **是** |
| OLD 噪声 R² | 0.505 | 0（真实技能=0） | 50.5% | **否**（缺陷本身：虚高 96.2 pp） |
| NEW 噪声 R² | -0.457 | 0（真实技能=0） | 45.7%（符号为负=劣于常数均值预测器） | 判据为「≤0.05 上限」，实测满足；**<3% 点值口径在此不适用**（基准是"无技能"不等式，1-NN 在噪声上本就劣于均值），如实声明 |
| OLD 线性锚点 R² | 0.998 | 1.0 | 0.17% | 是（但该口径在噪声场景同时虚高，不能单独作为合格证据） |
| 单类留出折 f1 | `None`（NEW）/ 1.0（OLD） | 无定义 | — | 不适用：NEW 显式声明不可定义 |

结论：修复后**在线性解析锚点上误差 0.00%（<3%）**；噪声场景由「虚高 96.2 pp」变为
「诚实无技能（≤0）」。未做任何模型精度声明——本单只修评估口径。
