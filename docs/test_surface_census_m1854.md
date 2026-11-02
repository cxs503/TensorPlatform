# 静默死测试普查（test-surface census）与 4 处失效测试清偿 — m1854

**席位**：tp-ver-2（verifier 席自找活） · **日期**：2026-11-02 · **仓**：TensorPlatform
**EDIT_WHITELIST**: `tools/audit/`, `tests/`, `artifacts/`, `docs/`

## 1. 案例介绍（问题背景）

pytest 的绿灯有两种：**验证通过**与**根本没有验证**。第二类在本仓真实存在，且
此前只靠人工发现（report 1548 手工复活两个"静默死测试模块"），没有留下任何探测手段。

普查动机来自一次具体取证：

```
$ python3 -m pytest tests/test_m406_lineage.py --collect-only -q
no tests collected in 1.91s
```

`tests/test_m406_lineage.py` 是 229 行的 M406 谱系追溯 e2e 脚本，文件名匹配
`test_*.py`，pytest 会收集并**报告通过**，但它全部校验（10 条谱系记录、四个 SHA-256
指纹完整性）都写在 `main()` 里、由 `if __name__ == "__main__"` 保护 —— 在 pytest 下
一条都不执行。更严重的是它在**导入期**改写全局状态：

```python
os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared"
database.config.engine = test_engine        # 整个 pytest 会话被重定向
database.config.SessionLocal = sessionmaker(...)
```

即：任何一次全量收集都会把平台的 `engine` 悄悄指向 SQLite 内存库，污染同会话其它
测试。这与"失效测试"同源：验证面存在，但不成立。

## 2. 怎么做的（方法 / 工具链）

### 2.1 探测工具 `tools/audit/test_surface_census.py`

**纯 AST 静态普查**（不导入、不执行、不联网、不建库），因此可在高负载下反复跑
（本机 load15≈122，nproc=128，只用轻量分析）。六类缺陷：

| 类别 | 判据 |
| --- | --- |
| `vacuous_module` | 文件名匹配 `test_*.py`，但可收集测试符号为 0 |
| `assertion_free` | 测试函数体内无 oracle（assert / raises / warns / self.assert* / 校验器抛错） |
| `orphan_oracle` | 含 assert 的模块级 helper，从任何测试**不可达**（传递闭包判定） |
| `module_level_skip` | `pytest.skip(..., allow_module_level=True)` |
| `unconditional_skip` | 裸 `@pytest.mark.skip`，或函数体首句 `pytest.skip/xfail` |
| `import_side_effect` | 测试文件模块级改写 `os.environ[...]` / `*.engine` |

两个关键设计（都由本轮踩坑逼出来，见 §5）：

* **收集模型对齐 pytest 语义**：`Test*` 类、任意 `unittest.TestCase` 子类的方法都计入
  （朴素 `grep "def test_"` 在 `tests/` 只看到 1636 个，实际 2405 个，漏 32%）。
* **传递可达性 + 委托 oracle**：helper 的 oracle 可被测试委托（`def test_x(): check(x)`），
  因此判 `assertion_free` 时沿调用链传递求值；判 `orphan_oracle` 同样取传递闭包。
  `if __name__ == "__main__"` 保护的函数视为脚本入口，不算死 oracle。

### 2.2 反回归门 `tests/test_test_surface_census.py`（14 项）

* **负控制 5 项**：为每类缺陷合成一个模块，断言探测器必须命中（"从不报警的探测器"
  本身就是死测试）；
* **正控制 4 项**：干净模块、unittest 类、异常型 oracle（`json.dumps` 抛错）、
  传递委托 helper —— 均不得误报；
* **回归门 5 项**：真实树上 `vacuous_module == 0`；新增债务 ⊆ 基线白名单（白名单条目
  必须带 `reason`）；普查确定性；收集模型计数下限。

### 2.3 失效测试清偿（4 个模块）

1. `tests/test_m406_lineage.py` — **复活**为 5 个真测试：10 个代表性执行（阻力/推进/
   耐波/操纵/稳性/绿色航行）→ 10 条谱系记录、四个指纹均为 64 位十六进制且唯一、
   指纹确定性（同输入同值、键序无关、`_compute_hash(None)==sha256(b"{}")`）、
   按 scenario 成链（3/2/2/1/1/1）、`execution_id` 唯一约束由 schema 实际强制。
   数据库改为**测试私有内存引擎**，模型经 `importlib` 直载（注入一次性
   `database.config` stub 并在加载后移除），**零全局改写**；`main()` 保留为
   `artifacts/lineage_test.json` 产出器。
2. `tests/test_seakeeping_rao_verification.py::test_verification_summary` — 原来
   `print("All 8 verification criteria PASSED")` **纯硬编码**，V1–V8 全坏也照印 PASS。
   改为按执行聚合：`tests/support/verification_aggregate.py` 重跑 V1–V8 判据类，
   任一判据失败则 summary 失败并点名；`coverage_gaps` 保证新增判据类漏登记即报错。
3. `tests/test_ship_calc_verification.py::test_verification_summary` — 同样问题，
   且正文含手抄数字（"validated against published values (< 0.1% error)"）。改为
   C1–C5 判据聚合执行，打印表全部来自运行结果。
4. `tests/test_resistance_m1775_verification.py::test_ct_documented` — 只打印误差表、
   无任何断言（Ct 为 NaN/负值也绿）。补 oracle：Ct 有限且为正、报送 Fn 与
   `V/sqrt(gL)` 独立复算一致（rel 1e-3）、Ct 误差在物理区间内。

## 3. 计算结果

探测器在同一份硬化版本下对 **HEAD 版 4 个模块** 与 **修后全树** 计数：

| 类别 | 修前（4 模块 @ HEAD） | 修后（tests/ + tests_broken_ice/） |
| --- | --- | --- |
| `vacuous_module` | **1** | **0** |
| `assertion_free` | **3** | 1（已基线化：m1797 已知缺口 xfail） |
| `orphan_oracle` | 0 | 0 |
| `module_level_skip` | 0 | 1（已基线化：:8001 不可达时整模块跳过） |
| `unconditional_skip` | 0 | 1（同上 m1797 xfail） |
| `import_side_effect` | **3** | **0** |
| 可收集测试函数 | 47 | **2405**（全树） |

图 `artifacts/test_surface_census_m1854.png`：左=六类缺陷修前/修后计数；右=静态收集
模型下的测试函数量。原始数字：`artifacts/test_surface_census_m1854.json`（程序化注入）。

测试执行（本轮实跑）：

```
python3 -m pytest tests/test_m406_lineage.py tests/test_ship_calc_verification.py \
  tests/test_seakeeping_rao_verification.py tests/test_resistance_m1775_verification.py \
  tests/test_test_surface_census.py -q
=> 85 passed, 1 warning in 9.78s
```

## 4. 注意点（坑 / 局限 / 适用范围）

1. **导入期依赖是真陷阱**：`models.lineage` 正常导入会执行 `models/__init__` →
   `database.config`，在 import 时就 `create_engine(postgresql://…)`，本镜像无
   `psycopg2` 直接 `ModuleNotFoundError`。原作者的解法（改 `DATABASE_URL` + 重绑全局
   engine）正是要清除的污染源；最终用 importlib 直载 + 一次性 stub 解决。
2. **朴素 grep 会把测试数低估 32%**：必须先建模收集语义，否则"修前 1636 / 修后 2405"
   这种口径差会被误当成增量。
3. **探测器自身需要负控制**：首版把 `json.dumps`（非序列化抛错）、`jsonschema.validate`
   这类"以抛错为断言"的写法误判为 `assertion_free`，加入正控制后修正。
4. **局限**：静态普查不执行代码，看不到条件型 skip 的实际触发率，也不能判断 oracle
   *是否足够强*（例如 `assert x is not None` 很弱但合规）。它只保证"验证面存在且可执行"，
   不保证"验证充分"。基线白名单是人工判断结果，新增条目需写明理由。

## 5. 误差判定 / 是否 <3%

本轮是**测试基础设施**工作，不产生物理量，无"vs 基准数值误差"可报。可冻结的判据
均为**离散口径**，逐项列出：

| 判据 | 基准 | 实测 | 判定 |
| --- | --- | --- | --- |
| 死测试模块数 `vacuous_module` | 0（目标） | 0 | 达标 |
| 导入期全局污染 `import_side_effect` | 0 | 0 | 达标 |
| 未基线化的新增测试面债务 | 0 | 0 | 达标 |
| m406 可收集测试数 | ≥1 | 5 | 达标 |
| m406 谱系记录/指纹完整性 | 10/10、4×64 hex | 10/10、全部通过 | 达标 |
| 反回归门自身 | 负控制必命中、正控制不误报 | 14/14 passed | 达标 |

**是否 <3%？** 对离散计数判据（0 vs 0）该问题不适用；对可数增量（修前 4 模块 7 项
真实债务 → 修后全树 0 项非基线债务）为**完全清零**，不存在 3% 量级的残余。物理量
误差判定不在本轮范围内（未做数值仿真）。**如实声明**：未运行的项 —— 全量 `pytest`
套件基线漂移巡更（本机 load15≈122，容量纪律只允许轻量活）不在本轮，故"全树绿灯"未断言。