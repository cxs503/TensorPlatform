"""发布 IN56-N07 外部效度校准报告到 TensorPlatform（图 base64 内嵌，总量控制在 250 KB 内）。"""
import base64
import json
import os
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PWD = os.environ["TENSOR_AGENT_PASS"]

ROOT = "/root/tp-agent/work/m6178"
ACC = ROOT + "/artifacts/IN56-N07/acceptance.json"
MET = ROOT + "/artifacts/IN56-N07/metrics.json"
DS = ROOT + "/artifacts/IN56-N07/dataset.json"
MD = ROOT + "/docs/IN56-N07_external_validity_calibration.md"
FIGDIR = "/tmp/figspng"


def post(path, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def main():
    tok = post("/auth/login", {"username": USER, "password": PWD})
    token = tok.get("access_token") or tok.get("token") or tok.get("data", {}).get("access_token")
    if not token:
        raise SystemExit(f"login failed keys={list(tok)}")

    acc = json.load(open(ACC))
    met = json.load(open(MET))
    ds = json.load(open(DS))
    crit = {c["criterion"]: c for c in acc["criteria"]}
    diag = acc["diagnostics"]
    cert = diag["identifiability_certificate"]
    asc = diag["association_batch_controlled"]
    ctl = diag["instrument_controls"]

    figs = []
    for name, cap in [
        ("fig1_roc_walkforward.png",
         "图1 ROC：L1 walk-forward（判据口径）因测试折全为单类而无定义；同图叠加 L2 关联性 ROC 作对照"),
        ("fig2_calibration.png", "图2 校准/可靠性曲线：折外预测分箱（预测均值 vs 实际合并率），标注 Brier 与 ECE"),
        ("fig3_shap_importance.png", "图3 SHAP 特征重要性（置换采样 Shapley，效率性误差 6.7e-16），红色=批次代理特征"),
        ("fig5_batch_confound.png", "图5 批次混淆诊断：duration_h / goal_len 在两类间近乎完全分离（单特征 AUC 0.011/0.020 反向）"),
        ("fig6_controls.png", "图6 仪器控制：负控 0.552 / 实测无定义 / 正控 0.938，含 0.65 验收线"),
        ("fig7_within_wave.png", "图7 同交付波（批次控制）内判别力：全特征 0.974 / 仅判级 0.613 / 仅批次代理 0.974 / 去批次代理 0.978"),
    ]:
        p = os.path.join(FIGDIR, name)
        with open(p, "rb") as fh:
            figs.append({"name": name, "caption": cap,
                         "png_base64": base64.b64encode(fh.read()).decode()})

    summary = (
        f"IN56-N07 外部效度校准 honest-FAIL（{acc['n_pass']}/{acc['n_total']} 判据通过）。"
        f"队列：4292 张已判级已完结 mission 中仅 {ds['n']} 张（{100*ds['n']/4292:.1f}%）有可观测下游结果，"
        f"阳性 {ds['n_positive']}/负类 {ds['n']-ds['n_positive']}，剔除 {sum(ds['excluded_counts'].values())} 条记 WITHHELD。"
        f"L1 walk-forward pooled AUC 无定义：负类只由一次 PR 回填波产生（{cert['negative_by_finish_day']}），"
        f"其最晚时刻之后仍有 {cert['positive_after_last_negative']} 条阳性，0/{cert['n_folds']} 折测试集含负类；"
        f"Brier 折外子集单类故无意义。L2 批次控制关联性：全特征 AUC {asc['full']['auc_mean']:.3f} 与"
        f"仅批次代理 {asc['batch_proxy_only']['auc_mean']:.3f} 几乎相同，S/E 判级单独仅 "
        f"{asc['grade_only']['auc_mean']:.3f}（同交付波内 {asc['within_delivery_wave']['2026-11-05']['grade_only']:.3f}），"
        f"未达 0.65。仪器正控 AUC {ctl['positive']['pooled_auc']:.3f}、负控 {ctl['negative']['pooled_auc']:.3f} "
        f"证明管线无缺陷，低判别力是数据结构性质。判据 4/5/6/7 通过（SHAP 排序、0 泄露违规、n=538、pytest 19/19）。"
        f"附 4 条数据补救路径（PR 逐单轮询留痕、失败也判级、判级与结果解耦、补 revert 表）。"
    )

    payload = {
        "title": "IN56-N07 S/E 判级外部效度校准（walk-forward AUC/Brier，honest FAIL 4/7）",
        "summary": summary,
        "scenario_key": "evaluation_ops.in56_n07_external_validity",
        "mission_id": 6178,
        "tags": ["evaluation", "external-validity", "walk-forward", "brier-score", "auc",
                 "shap", "calibration", "identifiability", "honest-fail", "in56"],
        "markdown": open(MD, encoding="utf-8").read(),
        "figures": figs,
    }
    size = len(json.dumps(payload).encode())
    print("payload bytes", size)
    if size > 250_000:
        raise SystemExit("payload too large; trim figures")
    res = post("/tensor/reports", payload, token)
    print(json.dumps(res, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()