# m9049 · P475W1-N03 派发前可行性先验校准器 —— 蒸馏

## 交付
- 模块：`tools/pre_dispatch_prior/`（dataset / features / calibrate / backtest / report / cli）
- 用例：`tests/test_pre_dispatch_prior.py`（20 passed）
- 产物：`artifacts/pre_dispatch_prior/`（model.json / backtest.json / ablation_window_platt.json /
  features.json.gz / PAPER.md / REPORT.md / fig_*.png）
- 论文级报告：`docs/pre_dispatch_prior/REPORT.md`；平台报告 id 10597
- 复现：`python3 -m tools.pre_dispatch_prior run --source pg --perm 200 --sensitivity --docs-dir docs/pre_dispatch_prior`

## TRAPS（可复用，按发现顺序）
1. **校准映射不能用「单个中段窗口」拟合**：窗口负例只有 20~30 个、局部判别力≈0，
   无约束 Platt 斜率学成负数（本节点实测 −0.29），把测试窗 AUC 0.66 翻转成 0.33 = 1−AUC。
   正解：训练窗内按计划分组的 **K 折 out-of-fold 分数**拟合校准映射。
2. **校准好了但仍输给气候基准 → 多半是先验漂移**：训练窗验收率 0.84、测试窗 0.90，
   概率整体压低，Brier 反而变差。正解：`Δ = logit(π_cal) − logit(π_train)` 对 logit 平移
   （π_cal 取最近一个已完整结局窗口，派发时刻可得）。
3. **BLAS 线程数在 16 维小矩阵上是 16 倍反优化**：Newton 迭代单次 0.099s vs 0.006s。
   在 `tools/<pkg>/__init__.py` 导入 numpy 之前 `os.environ.setdefault("OPENBLAS_NUM_THREADS","1")` 等。
   副作用：本机 128 核高负载时，串行反而比进程池稳（fork 池在本沙箱偶发挂起）。
4. **`psql -tA` 默认字段分隔符是 `|` 不是 TAB**：直接把行 split("\t") 永远解析失败。
   正解：SQL 里 `SELECT to_jsonb(t)::text FROM (...) t`，每行一个 JSON，无分隔符歧义。
5. **右删失不能当负例**：`campaign_nodes` 里 pending/in-flight 是「还没结局」，当 0 会把
   在跑的活判死。必须剔除并单列 strict 敏感性口径（剔除 infra 运维事故）对照。
6. **matplotlib 宿主无 CJK 字体**：图内中文渲染成方框（UserWarning: Glyph missing）。
   图用英文标签，中文只留在 markdown。
7. **「同输入两次逐字节相同」要防三处**：wall clock 时间戳（改用数据窗内最大 created_at）、
   字典序（`sort_keys=True`）、gzip mtime（`GzipFile(mtime=0)`）。
8. **小样本下 BSS 的置换零分布上界不能卡 0.05**：n_test=64 时 null BSS 标准差 ~0.05，
   判据应卡「零分布均值」+「观测超过零分布上界 + 经验 p 值」；n_test≈390 时才收得紧。
9. **沙箱长任务**：单次 shell ≤120s，`nohup ... &` 的后台进程会随命令返回被收割；
   与其想后台，不如把计算压到 60s 内（本案 200 次置换串行 ~9s）。

## 方法论要点（慢思考）
- 「先验校准器」的验收不能只看 AUC：极不平衡下 Brier/ECE 更贴近「报出的概率能不能用」，
  本节点因此把 ECE 下降、Brier skill>0、置换零分布 p 值三条并列为主判据。
- 任何设计决定（OOF vs 单窗、先验平移有无）都要落成**同快照对照实验**产物，
  否则事后无法复现「为什么这么改」——`ablation_calibration_source()` 就是为此。