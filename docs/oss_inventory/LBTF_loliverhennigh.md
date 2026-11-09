# 【盘点】Lattice-Boltzmann-fl —— loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow

> 单项目深盘（tier 2 · TensorFlow LBM 先驱 / 可微化思想源头）
> 证据来源：`git clone https://github.com/loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow.git`（本机 SNI 窗口内成功，未进入我们任何仓），
> 检出 SHA `af077443176fbe8faf201ffc4d6918cf101e9e27`（HEAD = master，31 commits，2016-09-21 → 2018-02-19）。
> 全部结论均给 `文件:行` 指针；带【运行确认】的条目另附 `artifacts/oss_inventory_lbtf/` 下的可复现脚本与日志。

## 一、概况

| 项 | 值 |
|---|---|
| 语言 | Python（TensorFlow 1.x 图模式 API：`tf.app.flags`、`xrange`、旧式 `tf.split(x, n, axis=)`、`cv2.cv.CV_FOURCC`）——100% Python，无 C/CUDA 扩展 |
| 规模 | 12 个 `.py` 共 1161 行；核心库 `LatFlow/` 仅 5 文件 442 行（`D2Q9.py` 37 / `D3Q15.py` 52 / `D3Q19.py` 61 / `Domain.py` 269 / `utils.py` 23） |
| 许可证 | **无 LICENSE 文件**（`.gitignore`/根目录均无 LICENSE、无 `setup.py`、无 `requirements.txt`、无 CI 配置）→ 默认「保留所有权利」 |
| **对我们的可用性判断** | **不可拷贝代码**（无许可证=默认 all-rights-reserved），只能「读方法、自己重写」。特别是 `LatFlow/D2Q9.py:12-34`、`D3Q15.py:17-51`、`D3Q19.py:18-60` 的 STREAM/BOUNCE 数字表——虽然它们是离散事实、可以独立推导，但**不得字面拷贝**（且本仓那张表本身有缺陷，见 §二/§八，抄了反而引入 bug）。可用的是**方法/架构思想**：把 streaming 实现成卷积、把时间循环搬进自动微分图。 |
| 活跃度 | 已停更：31 commits，作者单人，最后一次 2018-02-19（`les training`）；单分支 `master`，无 issue 治理、无测试 |
| 定位一句话 | 2016–2017 年第一批「用 TF 计算图跑 LBM」的实现之一：**核心贡献不是物理完备性，而是把整个 LBM 时间推进写成可微图**（`Domain.py:134-140,186-196,244-253`），为之后的数据驱动/可微 CFD 提供了最小范式；物理侧只有单相 BGK + Smagorinsky LES。 |

## 二、方法谱系

### 格子（3 套，全部显式表驱动）
- `D2Q9`：权重 `D2Q9.py:6`，离散速度 `D2Q9.py:9`（注意第三分量补 0，便于与 3D 共用 `C` 张量形状），bounce 表 `D2Q9.py:12-21`，stream 表 `D2Q9.py:24-34`。
- `D3Q15`：`D3Q15.py:6,9-14,17-32,35-51`。
- `D3Q19`：`D3Q19.py:6,9-15,18-37,41-60`。
- 选择方式：字符串分派 `Domain.py:24-46`（`method in {"D2Q9","D3Q15","D3Q19"}`），三套统一被 reshape 成 `[1, *Ndim, q]`（f）与 `[1, *Ndim, q, 3]`（C）。
  **没有 D3Q27，没有 D1Q3/D2Q5 之类低维**。

### 碰撞（只有 BGK-SRT）
- 平衡态：多项式形式 `Feq = W·ρ·(1 + 3 v·c/Cs + 4.5 (v·c)²/Cs² − 1.5 v²/Cs²)`，`Domain.py:118`（`Cs=dx/dt`，`Domain.py:55`；默认 dx=dt=1 故等价于 c_s=1/√3 的格子单位写法）。
- 碰撞：`NonEq = f - Feq; f = f - NonEq/tau`，`Domain.py:121-128`——单松弛 BGK。**无 TRT / MRT / KBC / regularized / cumulant**：全仓 `grep -i "trt\|mrt\|kbc"` 零命中，只有 `Domain.py:128` 一处 `/tau`。
- 松弛时间：`tau = 3·ν·dt/dx² + 0.5`，`Domain.py:78`。
- 畸变项：`self.Cs = dx/dt`（`Domain.py:55`）；宏观量反算 `Rho=Σf`（`Domain.py:183`）、`Vel=Σ f c/(Cs·Rho)`（`Domain.py:184-185`）。
- 体积力：**无**。`BForce` 只在 `Domain.py:73,86` 声明为变量，全仓无任何引用/赋值（`grep -n "BForce"` 仅两行）→ 没有 Guo、没有 shifted-velocity、没有 RK 修正；`Domain.py:110,158` 只有被注释掉的力项草稿。

### LES（本仓唯一「非纯 BGK」的物理扩展）
- `Domain.py:122-127`：`Q = sqrt(2 Σ_i (f_i−f_i^eq)² · EEk_i)`，`tau = 0.5(τ0 + sqrt(τ0² + 6·Q·Sc/ρ))`，常系数 `Sc=0.17`（`Domain.py:57`）。
- `EEk` 构造：`EEk = Σ_{n,m} |c_n c_m|`，`Domain.py:90-96`——用**离散速度分量的绝对值卷积**替代标准 Smagorinsky 里的非平衡应力张量模 `Π_ab = Σ (f−f^eq) c_a c_b`，是作者自创的「廉价各向同性代理」，与文献式（Hou 1996 的 `18√2 C_s² Π/ρ`）**不等价**，需重新标定才能用（见 §五/§八 风险）。
- 关闭开关：`Domain(..., les=False)`，`Domain.py:21,51,126-127`。

### 组织方式（库/案例解耦度）
- 库：`Domain` 类 + 每步 3 个 TF op（`setup_step` 外部注入、`collide_step`、`stream_step`），`Domain.Solve` `Domain.py:225-242` 是唯一的推进循环。
- 案例：5 个平铺脚本 `lid_cavity.py` / `car.py` / `car_steady_state.py` / `cube_3d.py` / `les_train.py`，每个都**重抄**一遍边界/初始化代码（例：`Feq` 初始化在 `car.py:38`、`cube_3d.py:32`、`lid_cavity.py:33`、`les_train.py:33` 各写一遍；进出口重建在 `car.py:59-94` 与 `les_train.py:55-90` 逐行重复）。

## 三、边界处理（逐一定位）

| 型式 | 是否实现 | 位置 | 说明 |
|---|---|---|---|
| Half-way / bounce-back | ✅（掩码版） | `Domain.py:99-101` + `BOUNCE` 表 `D2Q9.py:12-21` / `D3Q15.py:17-32` / `D3Q19.py:18-37`，混合 `Domain.py:131-132` | 把 `BOUNCE`（q×q 反相置换矩阵）当 1×1 卷积核，作用在**边界掩码内的**分布上：`f_boundary = conv2d/conv3d(F·boundary, Op)`，再与无边界部分按 `(1−boundary)` 加权合并。等价于「在壁面格点内部做反射」而非严格意义的格点间 half-way，壁面位置有 0.5 格系统偏差。 |
| 周期边界 | ✅（隐式，全局） | `utils.py:16-22` `pad_mobius` | 三轴皆环形 padding（mobius），物理上=全周期；因此所有「开放边界」都要靠脚本层打补丁。 |
| Zou-He（速度进口 / 压力出口） | ⚠️ 脚本内手写、不可复用 | 进口 `car.py:59-76`（`ρ=(f0+f2+f4+2(f3+f6+f7))/(1−u)`，再解 `f1,f5,f8`），出口 `car.py:88-94`（`vx=−1+(...)` 后解 `f3,f6,f7`）；`les_train.py:55-90` 是同一段代码的复制 | 典型 2D D2Q9 直角 Zou-He；只支持「左速度进口 + 右压力/速度出口」，硬编码在案例里，`Domain` 类无 BC 抽象。 |
| 移动壁面 | ✅ 脚本内 | `lid_cavity.py:44-56`（每步 `vel[:,1:]` 平移 + 顶行加 `value`）、`cube_3d.py:43-62` | 直接覆写宏观 `Vel` 张量，不经边界条件算子。 |
| 复杂几何 | ✅ 掩码（光栅化） | `car.py:21-31`（`cv2.imread` + `cv2.resize` + `−rint(img/255)+1` 二值化）、`cars/car_00N.png/txt` 素材集 | 用 PNG 轮廓生成 0/1 掩码，天然阶梯状；无插值、无曲率修正。 |
| 曲率边界（Bouzidi/插值/部分饱和） | ❌ | — | 全仓无插值权重、无 `q/(q+κ)` 类公式。 |
| 浸没边界 IBM（直接力/反馈） | ❌ | `IsSolid` 仅声明 `Domain.py:88`，全仓无引用 | 只留下一个空变量名，属未实现的规划痕迹。 |
| 压力边界 / 外推 / Neumann | ❌ | — | 出口靠“重建分布”硬算，没有通用外推。 |

## 四、多相 / 多组元 —— **无（本仓没有可用实现）**

诚实结论：**本仓没有任何可运行的多相/多组元能力**，只有「规划残留 + 被注释掉的草稿」：

1. **参数骨架在、全部死代码**：`Domain.__init__` 声明了 SCMP 味道的状态表
   `self.G = []`、`self.Gs = []`、`self.Rhoref = []`、`self.Psi = []`、`self.Gmix = 0.0`（`Domain.py:64-68`），
   并赋默认值 `G=0.0, Gs=0.0, Rhoref=200.0, Psi=4.0`（`Domain.py:79-82`）。
   全仓 `grep -n "self.G\|self.Psi\|Rhoref\|Gmix"` **只在这些声明/赋值行命中**，碰撞与流动里一次都没用到
   → 「互作用势 Psi(ρ) 形式」= **不存在**（`Psi` 只是一个标量 4.0，不是函数）。
2. **被注释掉的多组元实现**：`Domain.py:143-176`（`CollideMP`）与 `Domain.py:198-212`（`StreamMP`）整段包在 `"""..."""` 里，
   内容是一种「单格子 MCMP」：多组分各自持有一份分布 `self.F`/`self.Rho`/`self.Vel`（`Domain.py:70-88` 支持 `Nl=len(nu)` 组），
   碰撞前用 **τ 加权混合速度**把各组分塌缩成一个共同速度场：
   `vel_mix = Σ(Vel·Rho/tau) / Σ(Rho/tau + 1e-10)`，`Domain.py:151-153`（`rho_mix` 同式）。
   这是彩色/MCMP 思想的极简变体，但**缺了最关键的一半**：没有 Shan-Chen 力的「非局域势梯度」
   `F = −G·Ψ(ρ)·Σ w_i Ψ(ρ(x+e_i)) e_i`，也就不存在相分离驱动源（无 `Σ w Ψ e`、无 `G`、无 `Gs`）。
   注释里唯一残留的力项写法是被注释的 `vel = vel + dt·tau·(bforce/rho)`（`Domain.py:110,158`），
   **既不是 Guo 也不是 exact-difference**。
3. 逐项核对任务要求的能力：
   - SCMP（单组分多相，Ψ 型伪势）：**无**（无 Ψ 函数、无力项）。
   - MCMP（多组分多相）：**无**（只有一段注释里的 τ 加权混合速度草稿 `Domain.py:151-153`）。
   - 自由能 / 相场 Allen-Cahn / Cahn-Hilliard：**无**（`grep -i "free.energy\|cahn\|allen\|order.parameter"` 零命中）。
   - 颜色梯度模型（R-K）：**无**（无 recoloring 算子）。
   - free-surface（质量跟踪/VOF）：**无**（无 fill fraction、无 interface 重建）。
   - 密度比能力：**无从谈起**（单相，`Rhoref=200.0` 只是死参数）。
   - spurious current 处理：**无**（没有多相，也就没有该项）。
4. 结论定级：**多相 = STUB（空壳）**。可复用的只有一条**负面/参考**信息：多组分状态容器
   `self.F/Rho/Vel` 以 `list` 组织（`Domain.py:70-88`），说明作者选择「一组格子、多张分布面」的 AoS-by-component 布局，
   这种布局做 MCMP 时会让每步的 conv 次数 × 组分数，是我们**应当避免**的实现方式（我们 TensorLBM 用单张 `[C, q, ...]` 融合批处理更优）。

## 五、GPU / 并行

- **数据布局**：`f` 形状 `[1, *Ndim, q]`（`Domain.py:83`），即 NHWC + 「q 在最后一维」——**按方向的 SoA 藏在 channel 维里**（AoS-by-cell / SoA-by-direction 混合）。C 张量 `[1,*Ndim,q,3]`（`Domain.py:28`）、Op `[1,*Ndim,q,q]`（`Domain.py:29`）。
  x 方向是最后一维 → 线程沿 x 连续取样时可合并访存；q 连续也利于 tensor core/implicit-GEMM 通道pad。
- **算子融合的全部秘密**：`simple_conv` 把「stream」变成一次 3×3（2D）/3×3×3（3D）卷积（`utils.py:4-14`），
  「bounce」变成一次 1×1 通道卷积（`Domain.py:99-101`）；卷积核是**常量表**（`STREAM`/`BOUNCE`），因此整个 LBM 步在 TF 里就是
  `conv2d + conv3d + 若干 elementwise + reduce`，可被 XLA/cuDNN 自行融合。**没有手写 CUDA kernel、没有 fusion pass。**
- **并行**：单 `tf.Session`（`car.py:139`），所有步在默认设备（有 GPU 则整场常驻显存）。
  **无 MPI、无 domain decomposition、无多卡、无通信隐藏**（`grep -i "mpi\|nccl\|allreduce\|horovod"` 零命中）。
- **每步开销**：`Domain.Solve` 是 Python while 循环 + 每步 `sess.run`（`Domain.py:236-242`），每步都要重跑整条 conv 管线；
  `pad_mobius` 用 3 次 `tf.concat` 复制全字段（`utils.py:16-22`），`assign` 再整场拷贝一次（`Domain.py:188-191`）
  → **每步 4~5 次全量内存搬运，无 AA-pattern（f/df 双缓冲交换）、无 in-place**。这是典型的「框架时代早期」写法：
  编程最短、带宽最贵。精度固定 `np.float32`（`Domain.py:83-88`），无混合精度。
- 教学价值：这套「卷积即 streaming」在 torch/tf 里两行就能复现，且 autograd 透明——
  但**性能上不如 roll+gather**（搬运次数多一个数量级），我们若要吸收必须自己 bench。

## 六、可微 / 代码生成

本仓的核心资产，也是最该被我们记住的一点：**把 LBM 时间循环搬进自动微分图**。

- `graph_unroll=True` 分支：`CollideSC` 在 `Domain.py:134-140` 区分「create steps（`assign` 进 Variable）」与「put computation back in graph（`self.F[0] = f`，纯张量）」；
  `StreamSC` 同构 `Domain.py:192-196`（并额外维护 `Rho_step/Vel_step`）。
- `Unroll(start_f, num_steps, setup_computation)`，`Domain.py:244-253`：Python 侧循环 N 步，把每步 `f` 追加进 `F_return_state` 列表
  → 得到一条长 N·q 的中间张量链，任何「物理参数」只要进了图就能 BPTT。`Unroll_les_train`（`Domain.py:255-264`）是同一函数的复制（命名暗示专供 LES 训练）。
- 关键可微细节：`tau` 在图内由 `Domain.py:125` 动态算出（依赖 `Q`、`rho`、`self.Sc`），`self.Sc` 是普通 Python 标量（`Domain.py:57`）
  → 只要换成 `tf.Variable`，梯度就能从 N 步后的场流回 Smagorinsky 常数。这是 2016 年就写出来的「可微湍流模型」骨架。
- **代码生成 / 符号推导：无**。没有 `tf.function`/XLA 标注、没有 custom_gradient、没有 CodeGen/Loopy/SymPy 推导、没有 kernel fusion pass。
- ⚠️ **`les_train.py` 是未完成骨架**（诚实记录）：`les_train.py:124-143` 意图训练，但 `train_op = tf.train.AdamOptimizer(lr).minimize(total_loss)`（`les_train.py:143`）里的
  `lr` 和 `total_loss` **在文件中从未定义**，`les_train.py:136` 还有一行裸 `domain`；`flow_in` placeholder 定义了（`:129`）却没人用。
  → 「可微 LBM」= 设计意图 + 半个骨架，不是可跑的训练脚本。

## 七、与我们功能点的映射

| feature_ledger slug | 本仓对应实现 | 结论 / 可否吸收 |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | 物理：**无**（§四）。但**通道进出口 BC** 有一个可借的具体写法：`car.py:59-94`（速度进口 + 出口重建，含 `ρ=(f0+f2+f4+2(f3+f6+f7))/(1−u)` 与 `vx` 反算 `f3,f6,f7`）；`Domain.py:151-153`（注释态 MCMP 的 τ 加权混合速度） | `adapt`：Zou-He 进出口那两组式子可直接改成我们 3D 通道算例的驱动 BC（D3Q19 版需要自己推 9 个未知量）；τ 加权混合速度可作 MCMP 的**对照基线**（但缺 Shan-Chen 力，不能单独成算例） |
| MCMP 高密度比 | 无（无 Ψ、无 G、无 recoloring） | **NEW：上游无可吸收**——高密度比必须我们自己实现（Shan-Chen + Guo 力 + Ψ 选择），本仓提供不了 |
| 自由面 / free-surface | 无 | **NEW：无** |
| 相场 Allen-Cahn / Cahn-Hilliard | 无 | **NEW：无** |
| GPU 融合 kernel | `utils.py:4-14`（stream=3×3/3×3×3 conv）+ `Domain.py:99-101`（bounce=1×1 conv，核为 BOUNCE 反相置换矩阵） | `reference`：这是一个**「让框架自己融合」**的范式（kernel 表当权重、q 当通道），对我们 torch 后端可写成 `F.conv3d(f, St)`/`einsum` 两行；但每步 pad+assign 的全量搬运使其**性能劣于 roll+gather**，只能作 reference，不进热路径 |
| 分布式多卡 | 无（单 Session，无 MPI） | **NEW：无** |
| 稀疏 / 多孔介质 | 无（只有 0/1 掩码阶梯壁面） | **NEW：无**（掩码→灰度/部分饱和的部分饱和法也未实现） |
| 案例组织 | 好的一面：`Domain.Solve(sess, Tf, initialize_step, setup_step, save_step, save_interval)`（`Domain.py:225-242`）把「每步 setup 回调 + 输出节奏」做成注入式钩子；坏的一面：5 个脚本各自重抄 BC（`car.py:59-94` ≈ `les_train.py:55-90`） | `adapt`：`setup_step(domain, sess)` 回调形态值得借（我们 case API 就是 per-step callback）；**反面教训**=BC 不能写在案例里 |
| 测试与验证面 | **无**：0 个测试文件、无 `.travis.yml`/`pytest`、无解析解对比；`car.py:114-119` 等出图一律 `255*frame/max(frame)` 归一化（画面好看，但**最大值归一化后不可作为精度证据**） | **NEW：无**（我们自己的验证面必须自建） |
| CI | 无（无 `setup.py`/`requirements.txt`/CI 配置） | **NEW：无** |

## 八、可吸收 Top3 + 风险

### Top1 ——「算子即卷积」：stream/bounce 用固定卷积核表达（可直接落地到 TensorLBM torch 后端）
- 位置：`utils.py:4-14`（`simple_conv` = `tf.nn.conv2d/conv3d`，VALID，padding 由 `pad_mobius` 提供）、`Domain.py:178-185`（stream）、`Domain.py:99-101`（bounce）。
- 做法：把 q 当通道，`STREAM` 表展开成 `(3,3,q,q)`（3D 为 `(3,3,3,q,q)`）常量核，`BOUNCE` 展开成 `(1,1,q,q)` 置换核；
  一次 conv 同时完成「迁移 + 方向重排」，1×1 conv 完成「壁面反射」。对 autograd 完全透明。
- 对我们的价值：①给 TensorLBM 一个**framework-agnostic 的 stream 实现路径**（torch `conv3d` / `einsum` 两行），可作为 gather 版的对拍基准（两实现同场跑、逐位比较）；
  ②**bounce-back 写成 q×q 置换矩阵**这个技巧在我们后端里就是一次 `TensorPermute`/matmul，比逐方向 if 分支干净得多，天然支持任意格子的向量化实现。
- 风险：**表格必须程序化生成 + 自检**——本仓 D3Q19 表就错了（见下）；且每步 pad/assign 搬运量约 4~5 倍全字段，性能不如 roll+gather，进热路径前必须 bench。

### Top2 —— 可微 LBM：`graph_unroll` + 参数进图（BPTT）
- 位置：`Domain.py:134-140`（collide 的 in-graph 分支）、`Domain.py:192-196`（stream 同构）、`Domain.py:244-253`（`Unroll` 返回整串状态）、`Domain.py:122-128`（`tau` 与 `Sc` 在图中耦合）。
- 对我们价值：TensorLBM 若要做「数据驱动湍流闭合」demo（LES 常数/亚格子系数由 DNS/实验数据反演），这是**最小可跑范式**：
  N 步 unroll → 损失（如与参考速度场 L2）→ 反传到模型参数；且天然兼容我们已有的 torch autograd 后端。
- 风险：显存随 unroll 步数线性增长（每步保留整场 19 通道）；梯度爆炸/消失需要 checkpointing 或短窗；**必须加有限差分梯度校验**（上游完全没做，`les_train.py:143` 还是坏的）。

### Top3 —— per-step 回调式案例组织 + 掩码混合的边界合成
- 位置：`Domain.py:225-242`（`Solve` 的 `initialize_step / setup_step / save_step / save_interval` 四钩子）、`Domain.py:131-132`（`f = f·(1−boundary) + f_boundary` 的掩码合成）。
- 对我们价值：我们的 case API 可以直接采用「每步回调 `setup(case_state)` + 输出节奏」形态，库与案例零耦合；
  掩码合成（`f·(1−mask) + f_bc·mask`）是**单个张量表达式**同时处理任意复杂几何 BC 的干净写法，比按 cell 列表循环快得多，也适配 GPU。
- 风险：回调在 Python 侧每步执行（`Domain.py:236-242` 每步 `sess.run`），图模式下会拖慢；我们用 `torch.compile`/graph capture 时须保证回调不引入 host 同步。

### 额外可吸收（小件，成本低）
1. **LES 的廉价结构函数代理**：`Q = sqrt(2 Σ (f−f^eq)² · Σ_{n,m}|c_n c_m|)`（`Domain.py:90-96,123-124`）+ `tau=0.5(τ0+sqrt(τ0²+6 Q Sc/ρ))`（`Domain.py:125`）——把非平衡应力张量模换成「平方和 × |c||c| 求和」的标量代理，省一个张量收缩；可作为我们 LES 的 fast path，**但常系数必须重标定**（与 Hou 1996 的 `18√2 C_s² Π/ρ` 不等价）。
2. **PNG→掩码的几何导入**：`car.py:21-31`（`cv2.imread`→`resize`→`−rint(img/255)+1`）——船舶算例从 CAD 截图快速起步的土办法；只适用于 2D 阶梯壁面，正式算例仍须 Cb/L/B 几何校验。

### 【运行确认】上游 D3Q19 streaming 表存在真实缺陷（q=3）
- 缺陷点：`LatFlow/D3Q19.py:44` `STREAM[0,0,1, 3,3] = 1.0` —— 该空间槽位与 `D3Q19.py:56` 的 `STREAM[0,0,1,15,15]` **完全相同**；
  而 `LVELOC[3] = [0,1,0]`（`D3Q19.py:12`），`LVELOC[15] = [0,1,1]`（`D3Q19.py:14`）。
  按 `utils.py:4-14` 的 VALID 卷积 + `utils.py:16-22` 的环形 padding 语义（TF conv = 互相关，输出取上游偏移），
  该槽位 (0,0,1) 对应的上游位移是 `(0,1,1)` → **q=3 的 +y 方向分布被沿斜向 (0,1,1) 迁移**，即「纯 +y 的动量在 y-z 面上斜着走」。
- 我们的独立复现（`artifacts/oss_inventory_lbtf/verify_latflow.py`，纯 numpy 复刻其 conv 语义，未用 TF）：

| 格子 | STREAM↔LVELOC 自检 | 冲突细节 | 重复槽位 |
|---|---|---|---|
| D2Q9 | PASS | — | — |
| D3Q15 | PASS | — | — |
| D3Q19 | FAIL | q=3: LVELOC=(0, 1, 0) vs STREAM=(0, 1, 1) | [[0, 0, 1]] |
| D3Q19-patched | PASS | — | — |

| 格子 | BOUNCE 是否「映射到反相分量」的双射 | 问题 |
|---|---|---|
| D2Q9 | PASS | — |
| D3Q15 | PASS | — |
| D3Q19 | PASS | — |

| 格子 | 单分量冲量迁移（应沿自身 e_i） | 不符项 |
|---|---|---|
| D2Q9 | 9/9 | — |
| D3Q15 | 15/15 | — |
| D3Q19 | 18/19 | q=3: 期望 (0, 1, 0) 实测 (0, 1, 1) |
| D3Q19-patched | 19/19 | — |

- 影响范围：`D2Q9`、`D3Q15` 全对；`D3Q19` 19 个方向错 1 个。**上游唯一的 3D 演示用的是 D3Q15**（`cube_3d.py:80` `dom.Domain("D3Q15", ...)`），
  因此这个 bug 在作者自己的案例里从未暴露 → 属**潜伏缺陷**，谁照抄这张表谁中招。
- 一行修复：`D3Q19.py:44` 改为 `STREAM[1,0,1, 3,3] = 1.0`（我们脚本里已验证修正后 19/19 全对，见上表第 4 行）。
- 量化后果（同一复现器上的 12³ 周期盒、τ=0.6、`u_y = A·sin(k z)` 横波，ν_解析=(τ−0.5)/3=0.03333）：

| 表格版本 | 步数 | 质量漂移 | max\|u_y\|/A | 解析 exp(−νk²t) | 幅值误差 | 伪横向 max\|u_z\|/A |
|---|---|---|---|---|---|---|
| shipped（上游原样） | 40 | 3.290e-15 | 0.53162 | 0.69382 | 23.4% | 1.970e-01 |
| D3Q19.py:44 修正后 | 40 | 3.684e-15 | 0.64989 | 0.69382 | 6.3% | 1.058e-14 |

  - 关键判读：修正后 `max|u_z|/A` 降到机器零（约 1e−14，格子对称性精确成立）；上游原样则出现 `0.197` 的**伪横向速度**（即 19.7% 的剪切幅值被错误耦合到 z 方向），
    且剪切衰减幅值误差从 6.3% 恶化到 23.4%。
  - **注意口径**：这张表是我们 numpy 复刻器跑出来的（上游 TF1 代码在本机无法执行：无 `tensorflow`、无 `python2`，作者用了 `tf.app.flags`/`xrange`/旧 `tf.split`），
    复刻器严格按 `utils.py:4-22` + `Domain.py:99-101,178-185` 的算子语义实现，冲量迁移（上一张表）是最直接的离散事实，可视为对表本身的**运行确认**；
    横波数字还含我们自己的 BGK/分辨率选择，只能用于「修正前后相对比较」，不可当上游精度指标。

## 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 本仓/复现值 | 误差 | <3%？ |
|---|---|---|---|---|
| STREAM 表自洽性（19 方向） | LVELOC 定义（`D3Q19.py:12-14`） | 18/19 一致，q=3 偏 (0,1,1) | 1 个方向定性错误（离散事实，无百分比意义） | **否（upstream）/ 是（一行修正后 19/19）** |
| BOUNCE 表自洽性（9/15/19 方向） | 「映射到反相分量」双射 | 9/9、15/15、19/19 全对 | 0 | **是（100%）** |
| 单分量迁移方向（冲量测试） | 自身 e_i | D2Q9 9/9、D3Q15 15/15、D3Q19 18/19 | 1 方向 | 否（upstream） |
| 剪切波幅值衰减 vs 解析 exp(−νk²t) | 解析解（ν=(τ−0.5)/3） | 修正表 65.0%，解析 69.4% | 6.3% | **否**（12³ 网格 + BGK 可压缩误差，不是精度证据，只作相对比较） |
| 上游物理精度（阻力/升力/速度剖面 vs 实验） | 无任何基准 | **无验证面**（0 测试、出图做 max 归一化 `car.py:114-119`） | 不可评估 | **无基准 ⇒ 不能声称达标**（诚实判定：上游精度不可证） |
| 多相/密度比能力 | 需要 >1 相 | **0 相**（§四，仅死代码） | 能力缺失 | **否**（STUB） |

> 结论：本仓**不具备任何可引用的精度证据**；唯一可机器裁定的量化结论是「D3Q19 表有 1 处方向错误、一行可修」，已运行确认。

## 复现

```bash
cd artifacts/oss_inventory_lbtf
python3 verify_latflow.py          # 表格自检 + 冲量测试 + 12^3 BGK 横波对比，输出 verify_latflow_results.json
python3 make_figures.py            # fig1 冲量迁移 / fig2 横波衰减与伪横向速度 / fig3 三套表汇总
```
图：`artifacts/oss_inventory_lbtf/figures/fig1_impulse_advection.png`、`fig2_shear_wave.png`、`fig3_table_summary.png`。

## 证据分级（本报告自身）

| 级别 | 条目 |
|---|---|
| REAL（代码级确证，读源得） | 所有 §二/§三/§五/§六 的架构描述；§四「多相为死代码」（grep 全仓仅声明行命中）；无 LICENSE/无测试/无 CI（目录列举） |
| 运行确认（我们复刻器） | D3Q19 STREAM q=3 缺陷、BOUNCE 双射性、冲量迁移、横波伪横向速度（`verify_latflow.py` + JSON + 图） |
| 推断（标注为推断） | LES 代理 `Q` 与标准 Smagorinsky 不等价（结构对比得出，未做数值标定）；「卷积 streaming 比 roll+gather 慢」基于搬运量分析，未在本机 bench 上游实现 |
| 未知 / 未检查 | 上游在 GPU 上的实际吞吐、`cars/` 素材来源与许可、作者其他分支（仅 master）、视频产物质量 |

[OSS-INV]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
lang: Python (TensorFlow 1.x graph mode)
license: NONE (no LICENSE file; default all-rights-reserved)
methods: D2Q9/D3Q15/D3Q19 (explicit velocity/weight/stream/bounce tables); BGK-SRT only (Domain.py:121-128); no TRT/MRT/KBC/RR; no forcing term; Smagorinsky LES with |cc| proxy (Domain.py:122-127); streaming via fixed convolution kernels (utils.py:4-14, Domain.py:178-185)
multiphase: 无 — 仅死代码骨架 (G/Gs/Rhoref=200/Psi=4 at Domain.py:64-68,79-82, never used) + 注释态 MCMP with tau-weighted mixed velocity (Domain.py:143-176,151-153); no Shan-Chen force, no free energy/phase field/free surface/colorgradient
boundary: half-way-style bounce-back as qxq permutation conv on mask (Domain.py:99-101,131-132; tables D2Q9.py:12-21/D3Q15.py:17-32/D3Q19.py:18-37); global mobius periodic padding (utils.py:16-22); Zou-He velocity inlet + reconstructed outlet hard-coded in drivers (car.py:59-94); moving lid by overwriting Vel (lid_cavity.py:44-56); PNG->mask geometry (car.py:21-31); no curvature/IBM/Zou-He abstraction
gpu: NHWC tensor [1,*Ndim,q] with q as channel, streaming=3x3(x3) conv and bounce=1x1 conv so cuDNN/XLA can fuse; single tf.Session, no MPI/domain decomposition/multi-GPU; 4-5 full-field copies per step (pad_mobius 3 concats + assign), no AA-pattern, float32 only
summary: 2016-2017 年 TF1 图模式 LBM 先驱实现，唯一真正有新意的是把整个时间循环搬进计算图以获得可微性（Domain.py:134-140,192-196,244-253），物理侧只有单相 BGK+自创 Smagorinsky 代理；多相/IBM/曲率/分布式全无，且自带的 D3Q19 streaming 表 q=3 方向有真实错误（D3Q19.py:44，一行可修，我们已运行确认）。
[/OSS-INV]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: GAP-LBM-MCMP-POISEUILLE
tag: zou-he-channel-inlet-outlet-reconstruction
method: 2D D2Q9 通道进出口的分布重建：进口先用已知分量反算密度 rho=(f0+f2+f4+2(f3+f6+f7))/(1-u)，再解 f1,f5,f8；出口用 vx=-1+(...) 反算 f3,f6,f7；每步在 setup_step 里覆写第一/最后一行格点。
files: car.py:59-76, car.py:88-94, les_train.py:55-90
conventions: 格子单位 dx=dt=1、u=0.1、nu=u*0.0015（car.py:123-124）；进出口速度型/压力型混用；3D 版本需自行推 D3Q19 的 9 个未知量
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: GAP-LBM-MCMP-POISEUILLE
tag: shan-chen-mcmp-tau-mixed-velocity
method: 多组分单格子 MCMP 草稿：每组分布独立演化，碰撞前用 tau 加权把各组塌缩为一个共同速度 vel_mix=Σ(Vel*Rho/tau)/Σ(Rho/tau)，各组再以此速度算各自平衡态。
files: Domain.py:143-176, Domain.py:151-153, Domain.py:70-88
conventions: 每组 tau 来自 nu 列表（Domain.py:77-78）；Rhoref=200.0、Psi=4.0 为纯占位死参数；缺 Shan-Chen 非局域力，故无相分离能力
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: NEW:GPU 融合 kernel —— stream/bounce 表达为固定卷积核（框架自融合）
tag: conv-as-streaming-fixed-kernel
method: 把离散分布按 [1,*Ndim,q] 存放（q 当通道），stream = 一次 3x3(2D)/3x3x3(3D) 常量卷积核卷积、bounce = 一次 1x1 卷积（核为 BOUNCE 反相置换矩阵），迁移与方向重排/壁面反射都交给框架的卷积实现融合。
files: utils.py:4-14, LatFlow/Domain.py:178-185, LatFlow/Domain.py:99-101, LatFlow/D3Q15.py:35-51
conventions: VALID padding + mobius 3 次 concat 环形 padding（utils.py:16-22）；核表为常量、每步不变；float32；TF conv 为互相关 ⇒ 核索引编码上游偏移
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: NEW:可微 LBM / 数据驱动湍流闭合（BPTT 穿过 LBM 步进）
tag: graph-unroll-bptt-les-parameter
method: 提供 graph_unroll 分支让 collide/stream 返回图内张量而非写回 Variable，Unroll() 在 Python 侧串起 N 步并返回状态链；tau 由场量与标量 Sc 在图内算出，故梯度可从 N 步后的场回传到模型常数。
files: LatFlow/Domain.py:134-140, LatFlow/Domain.py:192-196, LatFlow/Domain.py:244-253, LatFlow/Domain.py:122-128
conventions: Sc=0.17 默认（Domain.py:57）；LES 代理 Q=sqrt(2*Σ(f-feq)^2*EEk)，EEk=Σ|c_n c_m|（Domain.py:90-96）；上游 les_train.py 的训练部分未完成（lr/total_loss 未定义）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: NEW:案例组织 —— per-step 回调钩子 + 掩码合成边界
tag: per-step-setup-hook-and-mask-blend
method: Solve(sess,Tf,initialize_step,setup_step,save_step,save_interval) 把每步的前处理、输出节奏以回调注入；边界条件用单个张量表达式 f=f*(1-boundary)+f_bc*boundary 合成任意几何。
files: LatFlow/Domain.py:225-242, LatFlow/Domain.py:131-132
conventions: boundary 为 0/1 float32 掩码张量（Domain.py:60）；save_interval 按 self.time 判定（Domain.py:237-238）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: NEW:LES 结构函数代理 —— |c_n c_m| 绝对值卷积替代非平衡应力张量模
tag: les-scalar-q-abs-cc-proxy
method: 用 Q=sqrt(2*Σ_i (f_i-f_i^eq)^2 * EEk_i)（EEk=Σ_{n,m}|c_n c_m|）作为 Π_ab 模的标量代理，再 tau=0.5*(tau0+sqrt(tau0^2+6*Q*Sc/rho)) 做 Smagorinsky 型涡粘。
files: LatFlow/Domain.py:90-96, LatFlow/Domain.py:121-127
conventions: Sc=0.17（Domain.py:57）；与文献 18*sqrt(2)*Cs^2*Π/rho 不等价，系数需重标定；LES 默认开（Domain.py:21）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow
slug: NEW:表格驱动的格子定义 + 缺自检的教训（D3Q19 q=3 streaming 表错误）
tag: table-driven-lattice-selfcheck
method: 每套格子由 WEIGHTS/LVELOC/BOUNCE/STREAM 四张常量表定义，Domain 用 reshape 统一装配；但上游未对 STREAM 与 LVELOC 做一致性自检，导致 D3Q19 的 q=3 槽位抄成 q=15 的槽位、+y 分布沿 (0,1,1) 迁移。
files: LatFlow/D3Q19.py:44, LatFlow/D3Q19.py:56, LatFlow/D3Q19.py:12-14, LatFlow/Domain.py:40-46
conventions: 修复= D3Q19.py:44 STREAM[0,0,1,3,3] -> STREAM[1,0,1,3,3]；启用前必须做 STREAM↔LVELOC 与 BOUNCE 反相双射自检（见 artifacts/oss_inventory_lbtf/verify_latflow.py）
transfer: adapt
[/OSS-SOL]
