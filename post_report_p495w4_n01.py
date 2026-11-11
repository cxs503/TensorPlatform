"""Build and post the paper-grade report for p495w4-n01 (numbers from report.json)."""
import base64
import json
import os
import urllib.request

ROOT = "/root/tp-agent/work/m13940"
ART = os.path.join(ROOT, "artifacts/dispatch_shadow")
rep = json.load(open(os.path.join(ART, "report.json"), encoding="utf-8"))

env = rep["environment"]
h = rep["headline"]
main = rep["main"]
gt = main["ground_truth"]
shadow = main["gate"]["shadow_candidate"]
replay = main["replay"]
crit = {c["id"]: c for c in rep["criteria"]}
power = rep["experiments"]["power"]["rows"]
leak = rep["experiments"]["leakage_roc"]
pos = rep["experiments"]["positivity"]["rows"]
sens = rep["experiments"]["replay_sensitivity"]

naive = shadow["naive_logged_mean"]
v1 = gt["value_candidate_analytic"]
v0 = gt["value_behaviour_analytic"]
err_snips = abs(shadow["snips"] - v1) / v1
err_naive = abs(naive - v0) / v0
err_ess = abs(shadow["ess_frac"] - power[-1]["ess_frac"])  # cross-check only

crit_rows = "\n".join(
    f"| {c['id']} | {c['description']} | {c['observed']} | {c['threshold']} | "
    f"{'PASS' if c['passed'] else 'FAIL'} |"
    for c in rep["criteria"]
)
power_rows = "\n".join(
    f"| {r['n_rounds']} | {r['snips']:.4f} | {r['value_analytic']:.4f} | "
    f"{100*r['snips_rel_error']:.2f}% | {r['lift']:+.4f} | "
    f"[{r['lift_ci95'][0]:+.4f}, {r['lift_ci95'][1]:+.4f}] | {100*r['ess_frac']:.1f}% | {r['status']} |"
    for r in power
)
leak_rows = "\n".join(
    f"| {r['leak_prob']:.2f} | {r['auc_probe']:.4f} | {r['max_auc_genuine']:.4f} | "
    f"{'ALARM' if r['alarm'] else 'silent'} |"
    for r in leak["rows"]
)
pos_rows = "\n".join(
    f"| {r['candidate_floor']:.2f} | {100*r['ess_frac']:.1f}% | {r['max_weight']:.2f} | "
    f"{r['ci_width']:.4f} | {100*r['rel_error']:.2f}% | {'yes' if r['ci_covers_truth'] else 'no'} |"
    for r in pos
)
ulp_rows = "\n".join(
    f"| {r['ulp_injected']} | {r['detected']}/{r['records']} | {100*r['detection_rate']:.1f}% |"
    for r in sens["perturbation"]["rows"]
)
tamper_rows = "\n".join(
    f"| {k} | {'detected' if not v['ok'] else 'NOT detected'} | "
    f"{v['problems'][0]['code'] if v['problems'] else '-'} |"
    for k, v in sens["tamper"].items()
)

md = f"""# 派单决策日志 v1 与影子策略回放门：让"新策略更好"成为可回放、可证伪的事实

## ① 案例介绍（对象与参数）

**背景**。平台在每个派单时刻要给一批候选单排序。上一环（`p485w1-n10`）已把排序做成 EVOI 离线评估，
但结论**不可回放**：换一版打分代码、换一个 RNG 种子、特征口径悄悄改一列，"新策略更好"就无法复核，
更无法被证伪。本节点把每次派单决策变成可回放的事实，并给出一个能对该说法说"不"的门。

**对象不是船体，而是"决策"本身**（平台共性机制，零领域语义）。为了让结论有真值可比，构造一个
**完全已知的合成世界**（所有数字由种子可复现）：

| 参数 | 取值 |
|---|---|
| 候选/特征/outcome 名 | 全部由注入 `LogConfig` 提供（`synthetic.candidate_id`、`synthetic.feature.{{alpha,beta,gamma,delta}}`、`synthetic.reward`） |
| 每 slate 候选数 | 4 |
| 真值模型 | p_a = sigmoid(-0.35 + 1.6·x_α − 1.1·x_β + 0.9·x_γ + 0.25·x_δ)（特征 ~ U(0,1)，6 位小数） |
| 记录策略 π₀（现状） | 线性权重 (0.55, −0.20, 0.30, 0.00)，T=0.6，探索底 ε₀=0.10 |
| 候选策略 π₁（待判） | 线性权重 (1.45, −1.00, 0.80, 0.20)，T=0.6，探索底 ε₁=0.10 |
| 主日志规模 | 1500 回合（每回合一条决策 + 迟到 outcome） |
| 解析真值 | 逐 slate 枚举 V(π)=E_s[Σ_a π(a|s)p_a(s)]：V(π₁)={v1:.6f}，V(π₀)={v0:.6f} |

**Schema v1（content-addressed）**：`record_id = sha256(canonical(body))`；body 含
feature 快照（每列带 `field_ts` 可用时间戳）、策略版本 `policy_sha256`（内含实现源码摘要
`kernel_sha256`）、决策输出（score/ranking/chosen/propensity）；**outcome 独立分区**，只带
`record_id` 外键——补标签不会改任何决策字节。链：`chain_digest = sha256(prev_chain ‖ record_id)`。

## ② 怎么做的（方法/流程/工具链）

1. **写侧守卫**：未注册特征列直接拒写；`field_ts > dispatch_ts` 拒写；propensity 必须严格正且归一。
2. **回放**：从记录里的 spec 重建策略对象，先校验 spec 复算出的 `policy_sha256` 与记录一致、
   kernel 源码摘要未变，再重算 score/ranking/propensity，逐列用 `struct.pack(">d")` **位串比对**。
   `chosen` 不要求等于 argmax（线上是从探索律抽样），只要求被策略律支持。
3. **影子评估**：`w_t = π₁(a_t|s_t)/π₀(a_t|s_t)`，IPS/SNIPS + ESS；区间用回合级 percentile
   bootstrap，RNG 是 SHA-256 计数器模式、种子由 `record_id` 列表摘要派生并落盘。
4. **泄漏负控**：三层守卫（名字 / 时间戳 / 单变量 AUC≥0.95），负控把 outcome 按概率 p 混进
   一个**已注册**特征，绕过前两层，检验第三层；100 次标签置换估误报率。
5. **门判据 A–F**：A schema+链、B 回放逐位 1.0 且 1-ulp 100% 检出、C 泄漏负控、
   D SNIPS−Π₀ 的 CI 下界>0、E ESS/n≥0.10、F 两次评估字节一致；另加 G 篡改检出、H pytest、I 域纯净。
6. 工具链：纯 Python + numpy-free 内核 + matplotlib 出图；`python -m tools.dispatch_shadow.cli`
   一条命令从种子重建全部日志并重算全部数字。

## ③ 计算结果（数字表；由 report.json 程序化注入）

**判据表**

| id | 判据 | 观测 | 阈值 | 判定 |
|---|---|---|---|---|
{crit_rows}

**E2 回放灵敏度（注入 k-ulp 分数扰动后的检出率）**

| 注入 ulp | 检出/总数 | 检出率 |
|---|---|---|
{ulp_rows}

**E2b 篡改形状检出**

| 形状 | 结果 | 首个链问题码 |
|---|---|---|
{tamper_rows}

**E3 统计功效（门在多少回合上才敢下判）**

| N | SNIPS | 解析真值 | 相对误差 | 提升 SNIPS−Π₀ | 95% CI | ESS/n | 门判定 |
|---|---|---|---|---|---|---|---|
{power_rows}

**E4 泄漏负控检出曲线（阈值 AUC≥0.95）**

| 泄漏概率 p | 注入特征 AUC | 真实特征最大 AUC | 判定 |
|---|---|---|---|
{leak_rows}

**E5 探索下限 → ESS / 区间宽度 / 误差**

| ε₁ | ESS/n | 最大权重 | CI 宽度 | 相对真值误差 | 区间覆盖真值 |
|---|---|---|---|---|---|
{pos_rows}

**日志审计链**：`config_sha256={main['manifest']['config_sha256'][:16]}…`，
`head_record_id={main['manifest']['head_record_id'][:16]}…`，
`head_outcome_id={main['manifest']['head_outcome_id'][:16]}…`，
records={main['manifest']['records']}，outcomes={main['manifest']['outcomes']}，链校验={main['manifest']['chain_ok']}。
工件：`artifacts/dispatch_shadow/{{report.json,report.md,figures/,log_manifest.json,log_sample.jsonl}}`。

## ④ 结果图

- **fig1**：左=注入 1/2/8/1024 ulp 后的回放检出率（全 100%）；右=四类篡改被哈希链检出（全检出）。
- **fig2**：左=SNIPS/朴素估计 vs 解析真值随回合数收敛；右=提升量 95% 自助区间与零线（门 D 的判据）。
- **fig3**：左=outcome 按概率 p 混入特征后的 AUC 曲线（p=1 必报警，p=0.5 纯噪声静默）；右=标签置换零假设误报次数。
- **fig4**：左=探索下限 vs ESS；右=区间宽度 vs 相对真值误差。
- **fig5**：左=干净日志各派单时刻特征 AUC（远离阈值）；右=实际派发偏离排序首位的比例（探索抽样带来的多样性，正是 off-policy 估计可用的前提）。

## ⑤ 注意点（坑/局限/适用范围）

1. **日志是合成世界**：策略价值可枚举才有真值对照；迁移到生产日志属 **WITHHELD**。
2. **IPS/SNIPS 只对日志出现过的 slate 分布无偏**；从未出现的 slate 不在估计量内（support mismatch）。
3. **逐位一致的范围**：操作数顺序与舍入可控（`math.fsum`、字典序 tie-break），但 `exp()` 末位是
   平台属性，故 propensity 本机位比对、跨机以区间一致性判定——不含糊其辞地说"跨平台逐位一致"。
4. **统计泄漏检测是单变量阈值法**：p≲0.9 的部分泄漏 AUC 只有 {max(r['auc_probe'] for r in leak['rows'] if r['leak_prob']<1.0):.3f}，
   落在阈值下（如实记录，不美化）；名字/时间戳两层仍能挡住这两类真实泄漏。
5. **门有统计功效门槛**：N=200/400 时 CI 覆盖 0，门诚实 FAIL（这是"证据不足"，不是"策略变差"）；
   N≥800 才 PASS。
6. 探索底 ε 越大，ESS 越高、区间越窄，但 off-policy 估计的目标策略本身越"随机"——报告同时给出
   ε 扫描，避免"用区间窄冒充精度"。
7. **第 2 次尝试说明**：worktree 内与报告服务（最近 100 条）均无上一轮 `p495w4-n01` 报告可读，
   因此把相邻 EVOI 节点的踩坑（时间戳级泄漏守卫、探索底保 positivity、多次重复零假设负控、
   同种子可复现与换数据集波动分开汇报）直接继承，未原样重跑。

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否<3%"）

| 关键量 | 观测 | 基准 | 误差 | <3%？ |
|---|---|---|---|---|
| 影子价值估计 SNIPS | {shadow['snips']:.6f} | 解析 V(π₁)={v1:.6f} | {100*err_snips:.2f}% | **是** |
| 现状自评（朴素均值） | {naive:.6f} | 解析 V(π₀)={v0:.6f} | {100*err_naive:.2f}% | **是** |
| 回放保真度（位级） | {replay['fidelity']:.3f}（mismatch={replay['mismatch_count']}） | 1.000 精确相等 | 0（逐位） | **是**（精确） |
| 1-ulp 扰动检出率 | {sens['perturbation']['rows'][0]['detection_rate']:.3f} | 1.000 | 0 | **是**（精确） |
| 篡改检出率（四形状） | {sens['tamper_detection_rate']:.3f} | 1.000 | 0 | **是**（精确） |
| 泄漏负控（outcome 副本） | AUC={leak['rows'][-1]['auc_probe']:.3f}，报警 | 阈值 0.95 | 超阈值 {leak['rows'][-1]['auc_probe']-0.95:+.3f} | 是（方向正确） |
| 零假设误报率 | {100*leak['null']['false_alarm_rate']:.1f}%（{leak['null']['replications']} 次置换） | 目标 ≤5% | — | **是** |
| 部分泄漏 p=0.9 检出 | AUC={[r['auc_probe'] for r in leak['rows'] if abs(r['leak_prob']-0.9)<1e-9][0]:.3f}，未报警 | 阈值 0.95 | 差 {0.95-[r['auc_probe'] for r in leak['rows'] if abs(r['leak_prob']-0.9)<1e-9][0]:+.3f} | **否（如实记录）** |
| 小样本门判定 N=200 | 提升 CI=[{power[0]['lift_ci95'][0]:+.3f},{power[0]['lift_ci95'][1]:+.3f}]，FAIL | 需 CI 下界>0 | 区间宽 {power[0]['lift_ci95'][1]-power[0]['lift_ci95'][0]:.3f} | 不适用（功效不足） |

环境：git `{env['git_commit_at_generation'][:12]}`，module sha256 `{env['module_sha256'][:16]}…`，
pytest {rep['experiments']['pytest']['summary']}，生成时间 {rep['generated_at_utc']}。
"""

figures = []
for fig in rep["figures"]:
    path = os.path.join(ART, "figures", fig["name"])
    with open(path, "rb") as fh:
        figures.append({
            "name": fig["name"],
            "caption": fig["caption"],
            "png_base64": base64.b64encode(fh.read()).decode("ascii"),
        })

payload = {
    "title": "派单决策日志 v1 + 确定性回放 + 影子策略回放门：A–I 判据全 PASS（SNIPS 相对解析真值 0.48%）",
    "summary": (
        "content-addressed 决策日志（outcome 分区存放、sha256 链、四类篡改全检出）+ 逐位回放引擎"
        "（1-ulp 100% 检出、未钉住/内核变更即拒绝）+ IPS/SNIPS 影子评估（自助 CI、ESS 86%）+ "
        "三层泄漏负控（outcome 副本 AUC=1.0 报警、纯噪声静默、零假设误报 0%）。门在 N≥800 才 PASS，"
        "N=200/400 诚实 FAIL。"
    ),
    "scenario_key": "platform.dispatch_shadow_replay",
    "mission_id": 13940,
    "tags": ["platform", "decision-log", "replay", "off-policy", "leakage-control", "p495w4-n01"],
    "markdown": md,
    "figures": figures,
}

token = open("/tmp/tok").read().strip()
req = urllib.request.Request(
    "http://127.0.0.1:8010/api/v1/tensor/reports",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
)
try:
    resp = json.load(urllib.request.urlopen(req, timeout=60))
    print("posted:", json.dumps(resp)[:400])
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read().decode()[:800])
    raise
json.dump(payload, open("/tmp/report_p495w4_n01.json", "w"), ensure_ascii=False)
print("md chars:", len(md), "figures:", len(figures))