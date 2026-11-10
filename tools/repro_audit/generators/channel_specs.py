"""repro_audit.generators.channel_specs —— 数值通道清单（冻结清单）。

**一个数值通道** = 一个已提交工件里的一个关键数字（头部断言量）
             ＋ 一条在提交树内重放它的派生式（由明细行/记录入量算出该数字）。

本清单是 p167w2-n09 的**冻结样本**：36 条通道，先冻结（sha256 入报告）后审计，
因此覆盖率的分子不能被「事后删掉失败通道」操控。

两类通道（口径写进报告，不混用）：

* ``formula`` —— 目标量是**另一个量的算术派生**（pass_rate / accuracy /
  error_pct / rel_diff_pct / completeness_rate）。派生式的入量是工件事先记录的
  独立量（分子分母），因此属于「重算派生断言」。
* ``tally``   —— 目标量是**明细行的归约**（count / len / sum / min）。
  这类通道验证「头部数字 ↔ 明细行」一致：头部被手改过、或明细被裁剪过，都会翻红。

每条 spec：
  channel_id  稳定标识（artifact#target）
  artifact    仓库内相对路径（提交树内）
  target      被断言的 JSON 路径
  expr        派生式（只允许 ``n.<reducer>(...)`` 与四则运算，见 derive.py）
  kind        formula | tally
  rtol        记录精度容差（默认 1e-9；四舍五入到 4 位小数的记录量放宽到 1e-3）
  note        人读说明：这个数在报告里代表什么

**纪律**：spec 里不允许出现等于被断言值的数值常量（maturity 会扫出来并降级），
也不允许把 ``expected`` 写进来 —— 期望值只能从工件的 target 路径读。
"""
from __future__ import annotations

__all__ = ["CHANNELS", "MANIFEST_VERSION"]

MANIFEST_VERSION = "p167w2-n09/2026-11-10"

#: 冻结的 36 通道样本
CHANNELS: list[dict] = [
    # ── artifacts/benchmark_validation.json ──────────────────────────────
    dict(channel_id="benchmark_validation#total_cases",
         artifact="artifacts/benchmark_validation.json",
         target="total_cases", expr="n.len('results')", kind="tally",
         note="基准验证用例总数 == results 明细行数"),
    dict(channel_id="benchmark_validation#summary.passed",
         artifact="artifacts/benchmark_validation.json",
         target="summary.passed", expr="n.count_true('results.pass')", kind="tally",
         note="通过数 == results 里 pass 为真的行数"),
    dict(channel_id="benchmark_validation#summary.failed",
         artifact="artifacts/benchmark_validation.json",
         target="summary.failed", expr="n.count_false('results.pass')", kind="tally",
         note="失败数 == results 里 pass 为假的行数"),

    # ── artifacts/m1388/benchmark_audit.json ────────────────────────────
    dict(channel_id="m1388_benchmark_audit#total_cases",
         artifact="artifacts/m1388/benchmark_audit.json",
         target="total_cases", expr="n.len('cases')", kind="tally",
         note="审计用例总数 == cases 明细行数"),
    dict(channel_id="m1388_benchmark_audit#status_counts.CONSISTENT",
         artifact="artifacts/m1388/benchmark_audit.json",
         target="status_counts.CONSISTENT",
         expr="n.count_eq('cases.status','CONSISTENT')", kind="tally",
         note="CONSISTENT 计数 == cases.status 分布"),
    dict(channel_id="m1388_benchmark_audit#status_counts.PARTIAL",
         artifact="artifacts/m1388/benchmark_audit.json",
         target="status_counts.PARTIAL",
         expr="n.count_eq('cases.status','PARTIAL')", kind="tally",
         note="PARTIAL 计数 == cases.status 分布"),

    # ── artifacts/m1388/chain_results.json ──────────────────────────────
    dict(channel_id="m1388_chain#SUBOFF.summary.total_cases",
         artifact="artifacts/m1388/chain_results.json",
         target="SUBOFF.summary.total_cases",
         expr="n.len('SUBOFF.results')", kind="tally",
         note="SUBOFF 用例数 == results 行数"),
    dict(channel_id="m1388_chain#SUBOFF.summary.passed",
         artifact="artifacts/m1388/chain_results.json",
         target="SUBOFF.summary.passed",
         expr="n.count_true('SUBOFF.results.pass')", kind="tally",
         note="SUBOFF 通过数 == results.pass 真值计数"),
    dict(channel_id="m1388_chain#DTMB-5415.summary.pass_rate",
         artifact="artifacts/m1388/chain_results.json",
         target="DTMB-5415.summary.pass_rate",
         expr="n.count_true('DTMB-5415.results.pass')/n.len('DTMB-5415.results')*100",
         kind="formula", note="DTMB-5415 通过率(%) == 通过数/总数"),

    # ── artifacts/m1388/friction_crosscheck.json ────────────────────────
    dict(channel_id="m1388_friction#Re1e6.rel_diff_pct",
         artifact="artifacts/m1388/friction_crosscheck.json",
         target="[0].rel_diff_pct",
         expr="n.abs(n.get('[0].ittc57')-n.get('[0].schoenherr'))"
              "/n.get('[0].schoenherr')*100",
         kind="formula", note="Re=1e6 两式摩擦系数相对差(%) == |ITTC57-Schoenherr|/Schoenherr"),
    dict(channel_id="m1388_friction#Re1e9.rel_diff_pct",
         artifact="artifacts/m1388/friction_crosscheck.json",
         target="[3].rel_diff_pct",
         expr="n.abs(n.get('[3].ittc57')-n.get('[3].schoenherr'))"
              "/n.get('[3].schoenherr')*100",
         kind="formula", note="Re=1e9 两式摩擦系数相对差(%)"),

    # ── artifacts/m642/batch_report.json ────────────────────────────────
    dict(channel_id="m642_batch#total_cases",
         artifact="artifacts/m642/batch_report.json",
         target="total_cases", expr="n.len('cases')", kind="tally",
         note="批量执行用例总数 == cases 行数"),
    dict(channel_id="m642_batch#success_count",
         artifact="artifacts/m642/batch_report.json",
         target="success_count", expr="n.count_eq('cases.status','success')",
         kind="tally", note="成功数 == cases.status=='success' 计数"),
    dict(channel_id="m642_batch#failed_count",
         artifact="artifacts/m642/batch_report.json",
         target="failed_count", expr="n.count_eq('cases.status','failed')",
         kind="tally", note="失败数 == cases.status=='failed' 计数"),

    # ── artifacts/pilot_verification_report.json ────────────────────────
    dict(channel_id="pilot_verification#total_questions",
         artifact="artifacts/pilot_verification_report.json",
         target="total_questions", expr="n.len('results')", kind="tally",
         note="核验问题总数 == results 行数"),
    dict(channel_id="pilot_verification#pass_count",
         artifact="artifacts/pilot_verification_report.json",
         target="pass_count", expr="n.count_eq('results.verdict','pass')",
         kind="tally", note="通过数 == results.verdict=='pass' 计数"),
    dict(channel_id="pilot_verification#fail_count",
         artifact="artifacts/pilot_verification_report.json",
         target="fail_count", expr="n.count_eq('results.verdict','fail')",
         kind="tally", note="失败数 == results.verdict=='fail' 计数"),

    # ── artifacts/timing_baseline.json ──────────────────────────────────
    dict(channel_id="timing_baseline#total_records",
         artifact="artifacts/timing_baseline.json",
         target="total_records", expr="n.len('timing_records')", kind="tally",
         note="计时记录总数 == timing_records 行数"),
    dict(channel_id="timing_baseline#baseline.stability.count",
         artifact="artifacts/timing_baseline.json",
         target="baseline.stability.count",
         expr="n.len('verification.sample_results')", kind="tally",
         note="稳定性基线样本数 == verification.sample_results 行数"),

    # ── artifacts/capability_ledger/capability_ledger_resolved.json ─────
    dict(channel_id="capability_ledger_resolved#summary.capabilities",
         artifact="artifacts/capability_ledger/capability_ledger_resolved.json",
         target="summary.capabilities", expr="n.len('capabilities')", kind="tally",
         note="能力总数 == capabilities 行数"),
    dict(channel_id="capability_ledger_resolved#summary.with_evidence",
         artifact="artifacts/capability_ledger/capability_ledger_resolved.json",
         target="summary.with_evidence",
         expr="n.count_gt('capabilities.evidence_count',0)", kind="tally",
         note="有证据能力数 == evidence_count>0 计数"),
    dict(channel_id="capability_ledger_resolved#summary.without_evidence",
         artifact="artifacts/capability_ledger/capability_ledger_resolved.json",
         target="summary.without_evidence",
         expr="n.count_eq('capabilities.evidence_count',0)", kind="tally",
         note="无证据能力数 == evidence_count==0 计数"),
    dict(channel_id="capability_ledger_resolved#divergence_tally.UNVERIFIED",
         artifact="artifacts/capability_ledger/capability_ledger_resolved.json",
         target="summary.divergence_tally.UNVERIFIED",
         expr="n.count_eq('capabilities.divergence','UNVERIFIED')", kind="tally",
         note="UNVERIFIED 分歧数 == divergence 分布"),

    # ── artifacts/optimizer_toy.json ────────────────────────────────────
    dict(channel_id="optimizer_toy#optimization_DE.n_eval",
         artifact="artifacts/optimizer_toy.json",
         target="optimization_DE.n_eval",
         expr="n.len('optimization_DE.convergence_history')", kind="tally",
         note="DE 目标函数评估次数 == 收敛历史行数"),
    dict(channel_id="optimizer_toy#optimization_DE.best_fun",
         artifact="artifacts/optimizer_toy.json",
         target="optimization_DE.best_fun",
         expr="n.min('optimization_DE.convergence_history.fun')", kind="tally",
         note="最优目标值 == 收敛历史最小值"),
    dict(channel_id="optimizer_toy#surrogate_verification.error_pct",
         artifact="artifacts/optimizer_toy.json",
         target="surrogate_verification.error_pct",
         expr="n.abs(n.get('surrogate_verification.predicted_Rt_kN')"
              "-n.get('surrogate_verification.true_Rt_kN'))"
              "/n.get('surrogate_verification.true_Rt_kN')*100",
         kind="formula", note="代理模型相对误差(%) == |预测-真值|/真值"),

    # ── artifacts/planner/golden_annotation_set.json ────────────────────
    dict(channel_id="planner_golden#total",
         artifact="artifacts/planner/golden_annotation_set.json",
         target="total", expr="n.len('train')+n.len('held_out')", kind="tally",
         note="标注集总数 == train+held_out 行数"),
    dict(channel_id="planner_golden#train_size",
         artifact="artifacts/planner/golden_annotation_set.json",
         target="train_size", expr="n.len('train')", kind="tally",
         note="训练集大小 == train 行数"),

    # ── artifacts/planner/scoring_report.json ───────────────────────────
    dict(channel_id="planner_scoring#train.accuracy",
         artifact="artifacts/planner/scoring_report.json",
         target="train.accuracy",
         expr="n.get('train.correct')/n.get('train.total_cases')", kind="formula",
         note="训练集准确率 == 正确数/总数"),
    dict(channel_id="planner_scoring#train.correct",
         artifact="artifacts/planner/scoring_report.json",
         target="train.correct",
         expr="n.get('train.confusion_matrix.dispatch.dispatch')"
              "+n.get('train.confusion_matrix.reject.reject')"
              "+n.get('train.confusion_matrix.merge.merge')",
         kind="tally", note="训练集正确数 == 混淆矩阵对角线和"),
    dict(channel_id="planner_scoring#held_out.correct",
         artifact="artifacts/planner/scoring_report.json",
         target="held_out.correct",
         expr="n.get('held_out.confusion_matrix.dispatch.dispatch')"
              "+n.get('held_out.confusion_matrix.reject.reject')"
              "+n.get('held_out.confusion_matrix.merge.merge')",
         kind="tally", note="留出集正确数 == 混淆矩阵对角线和"),

    # ── artifacts/lineage_test.json ─────────────────────────────────────
    dict(channel_id="lineage_test#total_executions",
         artifact="artifacts/lineage_test.json",
         target="total_executions", expr="n.len('executions')", kind="tally",
         note="执行总数 == executions 行数"),
    dict(channel_id="lineage_test#completeness_rate",
         artifact="artifacts/lineage_test.json",
         target="completeness_rate",
         expr="n.get('total_lineage_records')/n.get('total_executions')",
         kind="formula", note="谱系完整率 == 记录数/执行数"),

    # ── artifacts/benchmark_integrity/audit.json ─────────────────────────
    dict(channel_id="benchmark_integrity#n_cases",
         artifact="artifacts/benchmark_integrity/audit.json",
         target="n_cases", expr="n.len('cases')", kind="tally",
         note="完整性审计用例数 == cases 行数"),
    dict(channel_id="benchmark_integrity#verdict_counts.WITHHELD",
         artifact="artifacts/benchmark_integrity/audit.json",
         target="verdict_counts.WITHHELD",
         expr="n.count_eq('cases.verdict','WITHHELD')", kind="tally",
         note="WITHHELD 计数 == cases.verdict 分布"),

    # ── artifacts/baseline_integrity/audit_canon_v3.json ────────────────
    dict(channel_id="baseline_integrity#summary.cases",
         artifact="artifacts/baseline_integrity/audit_canon_v3.json",
         target="summary.cases", expr="n.len('case_results')", kind="tally",
         note="基线审计用例数 == case_results 行数"),
]


def by_id() -> dict[str, dict]:
    """channel_id → spec。"""
    return {c["channel_id"]: c for c in CHANNELS}


def manifest_size() -> int:
    return len(CHANNELS)