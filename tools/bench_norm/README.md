# bench_norm — 跨架构基准测试归一化（平台共性机制，零领域语义）

一句话：**把「同一段数值基准代码在两套架构/两套构建上的耗时」拆成可估计的架构因子
$\alpha_{arch}=T_{arch}/T_{ref}$、线性混合效应模型与置信区间，并用有已知真值的注入层
证明估计器本身是准的（而不是自证）。**

```
tools/bench_norm/
├── schema.py        观测记录（(arm, workload, repetition) 三元组）+ 校验 + JSONL I/O
├── measure.py       测量层：编译并运行数值计时内核，记录 host 指纹 / CV / checksum
├── kernels/lbm_channel.c  D2Q9 BGK 槽道流计时内核（自洽、无库调用、纯数值）
├── alpha.py         α 估计（匹配比值的几何均值 + 按 workload 的 cluster bootstrap）
├── mixed_effects.py 单随机截距 LMM（REML，闭式块结构 + 1 维搜索）+ OLS 对照
├── injection.py     有真值的注入层（calibration/evaluation workload 角色分离）
├── coverage.py      Monte-Carlo：α 精度 / R² / 交叉拟合残差差异 / 模型 CI 覆盖率
├── criteria.py      判据 (a)-(e) 机器裁决（injected 与 measured 两层分开记，不合并）
├── figures.py       报告图：α 柱状图、拟合散点、归一化前后箱线图
├── pipeline.py      编排：测量 → 真实分析 → 注入研究 → 判据 → 图 → 工件
└── cli.py           命令入口
```

## 三条命令

```bash
python3 -m tools.bench_norm.cli physics                      # 稳态物性校验（解析抛物线与 L2 误差）
python3 -m tools.bench_norm.cli measure --flags=-O2 --tag O2 --reps 9   # 在本机采一份记录
python3 -m tools.bench_norm.cli pipeline --no-measure        # 合并测量 + 注入研究 + 判据 + 图
```

## 四个必须记住的方法论点（都是实测踩出来的）

1. **α 与 δ 不能在同一批 workload 上估计**。若用同一批数据既估 α 又测「归一化后差异」，
   差异恒等于 0（α 就是这些比值的几何均值）——判据会自证通过。必须按 role 分离
   calibration / evaluation workload。
2. **bootstrap 只重采样 evaluation workload 会低估区间**：校准步的 host 随机效应是常数，
   重采样 workload 看不见它。报告的 95% CI 用混合模型的方差分量（含 σ_u²/n_host 项），
   workload bootstrap 只作诊断（实测覆盖率 0.75–0.88 < 0.95，就是这么暴露的）。
3. **单台主机 ≈ 单一 arm 时，α 与 host 效应不可分**：此时 α 就是「机器因子」，
   不能声称是「架构因子」。真实的跨架构结论必须有 ≥2 个 ISA 的实测臂。
4. **同一台机器不同构建（-O0 vs -O2）的差异是真实实现差异 δ，不是 α**：
   归一化把它吸收掉是**错误用法**；框架只应吸收「跑同一份代码」的架构/环境因子。

## 观测记录契约

`{"arm","isa","host","workload","features"{numeric},"repetition","t_seconds","source":"measured|injected","meta"{role,...}}`

`features` 只放数值特征（如 cells、steps），α/LMM 都是这些记录上的纯函数；
新增一个基准只要按这个 schema 产出记录，无需改门。