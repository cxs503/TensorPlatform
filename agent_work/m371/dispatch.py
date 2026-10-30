import json, subprocess, os
TOKEN = open('/tmp/tok.txt').read().strip()
BASE = os.environ['TENSOR_API_BASE']
G = []
G.append(("suboff_re1000", """【诊断+修复绕行】SDAA 验证案例扫荡——案例 suboff_re1000（TensorLBM benchmarks/verified/suboff_re1000）【班次 m371 派发·真红未解除】

失败证据（程序化取自平台 API /tensor/missions，零手抄）：
- m319（PARTIAL_FAIL）：Cd_tot=0.061495（+46.43% vs 基准），compile_status=eager_fallback
- m330（诊断单）：status=failed，gate=no-evidence（工作树无任何改动）——诊断结论未验收
- m337（第3次诊断）：pytest 验收闸门 10 passed；--stages neutrality（SDAA, L=16, 200步×2）max|Δf|=0.0；--stages crossdevice（cpu vs sdaa:0, 120步）Cd 相对差 2.0e-5；--stages sweep L=24 step10-200 窗均值 0.061424 = m319 报价 −0.12%
  → m337 只证明"可复现 + 闸门正确"，Cd +46.43% 红线并未解除；且 Ref=1000 的可行网格（L≤32）内从未给出 <3% 的档位
- 前序判定：案例被记为 HONEST_FAIL（coarse grid），但从未给出 L 收敛阶梯证明"红是网格而非几何/受力采样"

诊断方向（按序，禁止自造阈值、禁止放宽 3%/15% 口径）：
1) 读 benchmarks/verified/suboff_re1000/README.md + result.json，锁定判据口径（Cd 基准值、Re 定义、湿表面积/参考面积、收敛判据）
2) 区分两类根因：① 离散误差（L=16/24/32 的 Cd 阶梯是否单调趋向基准）② 几何/受力采样偏差（体素化 Hausdorff、force sample phase、half-way BB 权重、湿面积归一化）
3) CPU 基线优先：CPU 上跑 L=16/24/32/48（步数以力窗稳定为准，记录 wall time），给出 Cd(L) 阶梯与单调性；若 CPU L=48 已把误差压到 <15%，则红线归因为卡上可行网格不足，并给出卡上最小可行 L 与耗时估算（honest-FAIL 时写明"需 L=X/耗时 Y h"）
4) 卡上证据：/dev/tcaicard* 设备号、torch_sdaa 版本、teco_inductor InductorError 的 traceback 落 JSON；eager_fallback 合法但必须显式标注 compile_status
5) 所有判据数字由脚本程序化产出（零手抄）落 artifacts/，受检工作树必须有实质改动（no-evidence 是硬红线）
6) 结论三态之一：PASS（Cd 与基准 <3%）/ HONEST_FAIL（写明数值+物理归因+所需网格/成本）/ NOT_VERIFIABLE
7) 发布论文级六要素报告（含 Cd(L) 收敛图、逐项误差是否 <3% 的显式回答、局限），再 finish

协作注意：m369（S0-SUBOFF 2D 轴对称 smoke）在飞，本单只做 3D Re=1000 的 Cd 阶梯，禁止重复 2D 轴对称工作，也禁止修改其工件。"""))
G.append(("startup_poiseuille", """【诊断+修复绕行】SDAA 验证案例扫荡——案例 startup_poiseuille（TensorLBM benchmarks/verified/startup_poiseuille）【班次 m371 派发·单调收敛判据诊断】

失败证据（程序化取自平台 API，零手抄）：
- m353（实机双网格扫荡，/dev/tcaicard25, torch_sdaa 3.1.1a3）checks 原文："判据复算 python collect_evidence.py sdaa_out --baseline scan_out → evidence.json verdict=FAIL(阈值3条全过, err_decreased_H_to_2H=false)"
  即：与解析解（Stokes 第一问题 erf 解）的绝对误差三条阈值全部通过（单点冒烟 0.071%/0.092%/0.071%，最差 0.476% < 3%），唯一红项是 H→2H 的误差单调性
- 前序 m157（补报）failed（gate=no-evidence），m136 succeeded —— 判据体系历史不一致

诊断方向（禁止放宽 3% 与单调性口径，只允许"重新定义测量窗口并写明理由"）：
1) 读 benchmarks/verified/startup_poiseuille/README.md + collect_evidence.py，确认 err_decreased_H_to_2H 的计算口径：取样时刻、壁面速度剖面窗口、L2/相对范数定义、是否含启动瞬态
2) 判断红项性质：① 真离散误差（细网格未收敛）→ 需要更长域/更高 H，写明 cost；② 测量伪影（启动瞬态未排除、窗口漂移、2H 档步数不足未达同一物理时刻）→ 给出修正后的窗口与复算数字，并保留原始判据与修正后判据两套数字并列
3) 在 SDAA 实机复跑 H=59 → H=119（必要时补 H=237 一档或 2H 时间窗对齐），捕获设备证据（卡号/版本/wall time/step rate）
4) 判据数字全部脚本化产出（零手抄），两套口径（原判据/修正判据）都要出现，禁止只报通过的那套
5) 结论三态：PASS / HONEST_FAIL（写明最差误差数值与 S=2 时的噪声地板）/ NOT_VERIFIABLE
6) 发布论文级六要素报告（含误差 vs 抽样时刻曲线、H 阶梯、是否 <3% 的显式回答），再 finish"""))
G.append(("aircraft_icing_rg15_ipw2", """【诊断+修复绕行】SDAA 验证案例扫荡——案例 aircraft_icing_rg15_ipw2（TensorLBM benchmarks/pending/aircraft_icing_rg15_ipw2）【班次 m371 派发·G3/G11a 真红修复】

失败证据（程序化取自平台 API，零手抄）：
- m354（下探单，succeeded? no → status=failed）gate=no-evidence，reason="工作树无任何改动，不能报成功"
- m357（诊断单，succeeded）headline 原文："HONEST-FAIL（案例级别，非 SDAA 设备问题）；G3 FAIL: per-bin Eulerian closure 3.03e-2 vs ≤1e-6 门限；G11a FAIL: case 3.1 ..."；设备证据 /dev/tcaicard14, torch_sdaa 3.1.1a3+gite3971b9, wall 39.76s, 5.03 steps/s，rime 工况 frozen_kg=1.022e-04, closure_error=6.34e-17
  即：设备路径已跑通，红项在案例级判据（per-bin Eulerian closure 超门限 3.03e-2 vs 1e-6）

诊断方向（禁止调整门限；若判定门限本身不合理，只能提交"证据+理由"给 owner，不得自行改口径）：
1) 读该案例 README + 配置，定位 G3（per-bin Eulerian closure）与 G11a 的实现与门限来源；对比同家族已 PASS 案例 aircraft_icing_naca0012_rime（m347, report 264）的判据口径差异
2) 检查 closure 计算：粒子 bin 计数归一化、Eulerian 场插值权重、边界 bin 处理、shots/warmup 是否满足统计收敛（per-bin 计数是否够统计量，建议给 bin 计数分布与置信区间）
3) 判定红项性质：① 实现缺陷（归一化/插值错误）→ 修实现后复跑并给修正前后数字；② 统计量不足（bin 数太少）→ 给出所需 shots/warmup 与成本；③ 门限与物理量纲不匹配 → 提交证据给 owner，标注 HONEST_FAIL 而非改门限
4) 卡上复跑至少 1 个工况（rime，可加 glaze），捕获设备证据；eager_fallback 合法但必须标注
5) 判据数字脚本化产出（零手抄），落 artifacts/，受检工作树必须有实质改动
6) 结论三态：PASS / HONEST_FAIL（数值+归因）/ NOT_VERIFIABLE（pending 案例若无基准则如实声明）
7) 发布论文级六要素报告（含 closure vs 门限逐 bin 表、是否 <3% 的显式回答——注意该门限为 1e-6 绝对量，须显式说明不适用 3% 相对口径的原因），再 finish"""))

ids = []
for case, goal in G:
    body = json.dumps({"goal": goal, "repo": "TensorLBM", "base_ref": "main",
                       "runner": "native", "origin": "agent"}, ensure_ascii=False)
    open('/tmp/mission_%s.json' % case, 'w').write(body)
    out = subprocess.run(['curl', '-s', '-X', 'POST', BASE + '/tensor/missions',
                          '-H', 'Authorization: Bearer ' + TOKEN,
                          '-H', 'Content-Type: application/json',
                          '-d', '@/tmp/mission_%s.json' % case],
                         capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except Exception:
        d = {"raw": out}
    ids.append({"case": case, "id": d.get("id"), "status": d.get("status"),
                "repo": d.get("repo"), "runner": d.get("runner")})
    print(case, "->", d.get("id"), d.get("status"), d.get("repo"), d.get("runner"),
          "" if d.get("id") else out[:300])
json.dump(ids, open('agent_work/m371/dispatch_result.json', 'w'), ensure_ascii=False, indent=1)
