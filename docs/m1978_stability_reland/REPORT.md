# m1978 · 静稳性验证欠账干净重落：m1842 修复（KM 重复计入 KB）cherry-pick 至当前 master + 进程内闸门全 PASS

## ① 案例介绍（物理背景/几何/参数）

矩形浮箱（wall-sided pontoon）是**唯一**所有完整稳性量都有教科书闭式解的船型：KB=T/2、BM=B²/(12T)、GZ(φ)=sinφ·(GM+BM/2·tan²φ)（φ≤φ_e=atan(2T/B)）。基准例 L=100 m, B=20 m, T=8 m, D=2T=16 m, KG=3 m, Cb=Cm=Cwp=1：∇=16000 m³, KB=4.0, BM=4.1667, KM=8.1667, GM=5.1667 m, φ_e=38.66°。九例扫描 B∈{10,20,30}×T∈{4,8,12}（L=100, D=2T, KG=3）。

## ② 怎么做的（方法/流程/工具链）

1. **欠账定位**：docs/verification_debt/stability_pontoon/FINDINGS.md（2026-11-02）机器裁决 S1 FAIL（GM 误差 +195.93%，阈值 3%）、S3 FAIL（max|ΔGZ|=0.669 m，消失角 90.8~257.5° 物理不可能）、S4 FAIL（KN 恒等式 0.382 m）；根因 S2 成立：GM_pl−GM_ref≡T/2（KB 重复计入）。

2. **发现 m1842 分支污染**：修复 commit a8ceefe 正确，但其分支上的前置 commit 2422bd2 误删 956 个文件（frontend_kunlun/ 与 backend/api_kunlun/ 全树，97596 行）——直接合并将摧毁仓库。master(97d8452) 至今无此修复，:8001 服务宕机。

3. **干净重落**：ship-performance-platform 新 worktree（branch agent/m1978-ship，基于 master 97d8452）cherry-pick a8ceefe（仅稳定性 3 个 API 文件 + 测试 + 欠账文档，零删除）；新增进程内闸门 stability_gate_inproc.py（:8001 宕机，直调 api.stability_hydrostatics，判据与 stability_gate.py 同口径 S1–S4）。

4. **验证**：pytest 30 passed（test_stability_closed_form.py 145 行 + test_stability_pontoon_gate.py 94 行，离线）；进程内闸门 S1–S4 全 PASS。所有数字由本脚本从 artifacts/stability_gate_inproc_m1978.json 注入，零手抄。

## ③ 计算结果（程序化注入）

| 判据 | 内容 | 修复前(m1808 取证) | 修复后(本单, 进程内) | 阈值 | 结果 |
|---|---|---|---|---|---|
| S1 | max\|GM_err\|/GM_ref | 11.27%~195.93% (9 例) | **0.0000%** | ≤3% | **PASS** |
| S2 | GM_pl−GM_ref 残差（双计 KB 探针） | +T/2（0.44 mm 内 9/9） | **0.00e+00 m** | ≤0.01 m | **PASS** |
| S3 | wall-sided 域 max\|ΔGZ\|；消失角 | 0.669 m；消失角 90.8~257.5° | **2.66e-15 m；[90.0]** | ≤0.05 m；≤90° | **PASS** |
| S4 | KN 恒等式 GZ+KG·sinφ=KN | 0.382 m | **1.78e-15 m** | ≤1e-3 m | **PASS** |

参考例：GM 平台 5.1667 m vs 闭式 5.1667 m；area_0_30 平台 0.7354 vs 闭式 0.7354 m·rad。

九例扫描（GM 误差全部 0.0000%）：

| B | T | GM_ref | GM_plat | 相对误差 | 消失角° |
|---|---|---|---|---|---|
| 10 | 4 | 1.0833 | 1.0833 | 0.0000% | 90.0 |
| 10 | 8 | 2.0417 | 2.0417 | 0.0000% | 90.0 |
| 10 | 12 | 3.6944 | 3.6944 | 0.0000% | 90.0 |
| 20 | 4 | 7.3333 | 7.3333 | 0.0000% | 90.0 |
| 20 | 8 | 5.1667 | 5.1667 | 0.0000% | 90.0 |
| 20 | 12 | 5.7778 | 5.7778 | 0.0000% | 90.0 |
| 30 | 4 | 17.7500 | 17.7500 | 0.0000% | 90.0 |
| 30 | 8 | 10.3750 | 10.3750 | 0.0000% | 90.0 |
| 30 | 12 | 9.2500 | 9.2500 | 0.0000% | 90.0 |

## ④ 结果图

![进程内闸门：GZ 曲线 vs 闭式解 + GM 误差 vs T/B](figs/stability_gate_inproc_m1978.png)

左：参考例 GZ 曲线（平台修复后 vs wall-sided 闭式解，φ_e=38.66° 内重合至 1e-15 m）；右：九例 GM 相对误差 vs T/B（全 0，3% 验收线/15% 不可接受线之下）。**场量云图豁免说明**：本单为后端水静力学公式修复，无流场/应力场等场量，GZ(φ) 一维曲线对比即本域最细粒度场量。

## ⑤ 注意点（坑/局限/适用范围）

1. **分支污染陷阱（本单核心发现）**：agent 在部分检出 worktree 里 `git add -A` 会把「工作树没有的文件」提交成删除——2422bd2 因此删掉 956 文件。m1842 单虽标 succeeded，其分支不可合并；本单以 cherry-pick 单 commit 重落。建议平台对「单 commit 删除 >50 文件」加合并前门禁。

2. **进程内闸门 ≠ 服务级验证**：:8001 宕机，本单验证到模块级（import 直调）；HTTP 层（请求模型/序列化）由 30 条 pytest 覆盖，服务级复跑待 :8001 恢复后执行 stability_gate.py（已随 a8ceefe 落仓）。

3. **适用域**：闭式核对仅覆盖矩形浮箱（wall-sided，φ≤φ_e）；U/V 型剖面、甲板浸没后无闭式解，不外推。BM=B²/(12·Cm·T) 对一般船型是近似（Cm 参数化），浮箱上 Cm=1 精确。

4. **未做**：未合并 master（禁碰 main）、未重启 :8001、未动 m1842 原分支（留待 owner 处置/关闭）。

## ⑥ 误差判定（vs 基准，逐项）

| 关键量 | 计算值 | 基准 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| GM（参考例） | 5.1667 m | 闭式 5.1667 m | 0.0000% | **是** |
| GM（九例最大） | — | 闭式 | 0.0000% | **是** |
| GZ（wall-sided 域最大偏差） | — | 闭式 | 2.66e-15 m（绝对） | **是** |
| area_0_30 | 0.7354 m·rad | 闭式 0.7354 m·rad | 0.0000% | **是** |
| KN 恒等式残差 | 1.78e-15 m | 0（恒等式） | — | **是**（自洽性） |

**结论**：全部关键量 vs 闭式基准误差 <3%（实测 ~1e-15，机器精度）；修复前 +195.93% 的 GM 欠账在模块级清偿。honest 声明：服务级（:8001）复跑未执行（服务宕机），不粉饰为端到端全绿。

## 证据指针

- ship-performance-platform branch `agent/m1978-ship`：ff29d14（修复 cherry-pick）+ 6557f4c（进程内闸门+工件）
- 工件：docs/verification_debt/stability_pontoon/{stability_gate_inproc.py, artifacts/stability_gate_inproc_m1978.json, figs/stability_gate_inproc_m1978.png}（ship 仓）；本仓 docs/m1978_stability_reland/ 镜像