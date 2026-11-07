"""协调官诊断单 → 可执行动作。

问题（IN27-H2 的前提）：协调官/影子官产出的是**报告**（"XX daemon 有隐患，
建议修 YY"），人类或下一个 agent 读完后手工照做，中间没有任何机器判据。
本模块把诊断单变成**结构化可执行清单**：

    {"schema": "coordinator-diagnostic-order/1", "order_id": "IN27-H2-D1",
     "items": [{"id": "D1", "op": "mirror_verify", "expect": {"drifts": 0}},
               {"id": "D2", "op": "patch_apply", "params": {"patch_file": "..."},
                "on_failure": "stop"}]}

执行器只认白名单 op，每个 op 背后是门链里的一个既有动作（不新造旁路）。
不认识的 op → 状态 NOT_EXECUTABLE（写明原因），而不是假装执行成功 ——
这正是"报告型诊断"与"执行型诊断"的分界线：**能被拒绝的才是可执行的**。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from harness_gateway import mirror as mirror_mod
from harness_gateway.gates import canary_gate, compile_gate, rollback_gate
from harness_gateway.pipeline import HarnessDeployPipeline, PatchSpec

SCHEMA_ORDER = "coordinator-diagnostic-order/1"
SCHEMA_REPORT = "harness-gateway/diagnostic-execution/1"


@dataclass
class ItemResult:
    id: str
    op: str
    status: str  # PASS | FAIL | NOT_EXECUTABLE | SKIPPED
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "op": self.op,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "evidence": self.evidence,
        }


class DiagnosticExecutor:
    """把诊断单映射到门链动作。"""

    def __init__(self, pipeline: HarnessDeployPipeline, *, run_dir: str | Path | None = None) -> None:
        self.pipeline = pipeline
        self.run_dir = Path(run_dir) if run_dir else pipeline.artifacts_dir / "diagnostics"
        self.ops: dict[str, Callable[[dict], ItemResult]] = {
            "mirror_verify": self._op_mirror_verify,
            "mirror_sync": self._op_mirror_sync,
            "gate_compile": self._op_gate_compile,
            "gate_canary": self._op_gate_canary,
            "patch_apply": self._op_patch_apply,
            "rollback_to_head": self._op_rollback_to_head,
            "deploy_dry_run": self._op_deploy_dry_run,
        }

    # ---------- ops ----------
    def _op_mirror_verify(self, item: dict) -> ItemResult:
        src = self.pipeline.src_root
        if src is None:
            return ItemResult(item["id"], "mirror_verify", "NOT_EXECUTABLE", "pipeline has no src_root")
        drifts = mirror_mod.verify_mirror(src, self.pipeline.mirror_root, self.pipeline.units)
        expect = item.get("expect", {}).get("drifts", 0)
        status = "PASS" if len(drifts) == expect else "FAIL"
        return ItemResult(
            item["id"],
            "mirror_verify",
            status,
            f"{len(drifts)} drift(s) (expected {expect})",
            {"drifts": [d.to_dict() for d in drifts], "units_checked": len(self.pipeline.units)},
        )

    def _op_mirror_sync(self, item: dict) -> ItemResult:
        src = self.pipeline.src_root
        if src is None:
            return ItemResult(item["id"], "mirror_sync", "NOT_EXECUTABLE", "pipeline has no src_root")
        rep = mirror_mod.mirror_harness(src, self.pipeline.mirror_root, self.pipeline.units)
        return ItemResult(
            item["id"],
            "mirror_sync",
            "PASS",
            f"{len(rep.entries)} unit(s) mirrored, {rep.redaction_total} credential(s) redacted",
            {"manifest": str(self.pipeline.mirror_root / "MIRROR_MANIFEST.json"),
             "redaction_total": rep.redaction_total,
             "units": [e.name for e in rep.entries]},
        )

    def _op_gate_compile(self, item: dict) -> ItemResult:
        paths = [self.pipeline.mirror_root / u.mirror_relpath for u in self.pipeline.units]
        res = compile_gate.run_compile_gate(paths)
        return ItemResult(item["id"], "gate_compile", "PASS" if res.ok else "FAIL", res.detail, res.to_dict())

    def _op_gate_canary(self, item: dict) -> ItemResult:
        sandbox = self.run_dir / f"canary_{item['id']}"
        res = canary_gate.run_canary_gate(
            self.pipeline.mirror_root,
            self.pipeline.units,
            sandbox_root=sandbox,
            shim_exit=self.pipeline.shim_exit,
            sleep_s=self.pipeline.canary_sleep_s,
        )
        return ItemResult(item["id"], "gate_canary", "PASS" if res.ok else "FAIL", res.detail, res.to_dict())

    def _op_patch_apply(self, item: dict) -> ItemResult:
        params = item.get("params", {})
        if "patch_file" in params:
            patch = PatchSpec.from_file(params["patch_file"])
        elif "patch" in params:
            patch = PatchSpec.from_dict(params["patch"])
        else:
            return ItemResult(item["id"], "patch_apply", "NOT_EXECUTABLE", "params needs patch_file or patch")
        report = self.pipeline.run(
            patch,
            run_id=params.get("run_id"),
            negative_control=params.get("negative_control"),
            commit=bool(params.get("commit")),
        )
        expect_verdict = item.get("expect", {}).get("verdict")
        if expect_verdict:
            # 负例项：期望"被拦住"。拦不住 = FAIL（说明门漏了），这才是红队语义
            status = "PASS" if report["verdict"] == expect_verdict else "FAIL"
        else:
            status = "PASS" if report["verdict"] == "PASS" else "FAIL"
        return ItemResult(
            item["id"],
            "patch_apply",
            status,
            f"pipeline verdict {report['verdict']}"
            + (f" (expected {expect_verdict})" if expect_verdict else "")
            + (f" caught_by {report['caught_by']}" if report.get("caught_by") else ""),
            {"verdict": report["verdict"], "caught_by": report.get("caught_by"),
             "run_id": report["run_id"], "report": report["artifacts"]["report"],
             "rollback": report.get("rollback") is not None},
        )

    def _op_rollback_to_head(self, item: dict) -> ItemResult:
        rels = [self.pipeline.repo_relpath(u) for u in self.pipeline.units]
        ckpt = rollback_gate.checkpoint(self.pipeline.repo_root, rels, run_dir=self.run_dir / f"ckpt_{item['id']}")
        rb = rollback_gate.rollback(self.pipeline.repo_root, ckpt)
        return ItemResult(item["id"], "rollback_to_head", "PASS" if rb.ok else "FAIL", rb.detail, rb.to_dict())

    def _op_deploy_dry_run(self, item: dict) -> ItemResult:
        target = item.get("params", {}).get("target_root") or self.pipeline.src_root
        if target is None:
            return ItemResult(item["id"], "deploy_dry_run", "NOT_EXECUTABLE", "no target_root")
        plan = self.pipeline.deploy_plan(target)
        return ItemResult(
            item["id"],
            "deploy_dry_run",
            "PASS",
            "actions: " + ", ".join(f"{k}={v}" for k, v in sorted(plan["actions"].items())),
            plan,
        )

    # ---------- driver ----------
    def execute(self, order: dict | str | Path) -> dict:
        if not isinstance(order, dict):
            order = json.loads(Path(order).read_text(encoding="utf-8"))
        if order.get("schema") != SCHEMA_ORDER:
            raise ValueError(f"order schema must be {SCHEMA_ORDER}")
        items = order.get("items", [])
        results: list[ItemResult] = []
        aborted = False
        for item in items:
            if aborted:
                results.append(ItemResult(item.get("id", "?"), item.get("op", "?"), "SKIPPED", "aborted by on_failure=stop"))
                continue
            t0 = time.monotonic()
            op = item.get("op", "")
            if op not in self.ops:
                results.append(
                    ItemResult(
                        item.get("id", "?"),
                        op,
                        "NOT_EXECUTABLE",
                        f"no gate-bound executor for op '{op}' (report-only item; add an op to make it executable)",
                    )
                )
                continue
            try:
                res = self.ops[op](item)
            except Exception as exc:  # 执行器自身异常也要成为证据
                res = ItemResult(item.get("id", "?"), op, "FAIL", f"executor error: {type(exc).__name__}: {exc}")
            res.duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(res)
            if res.status == "FAIL" and item.get("on_failure", "stop") in ("stop", "rollback"):
                aborted = True
        statuses = {r.status for r in results}
        if "FAIL" in statuses:
            verdict = "EXECUTED_FAIL"
        elif "NOT_EXECUTABLE" in statuses:
            verdict = "PARTIAL"
        else:
            verdict = "EXECUTED_PASS"
        report = {
            "schema": SCHEMA_REPORT,
            "order_id": order.get("order_id"),
            "issued_by": order.get("issued_by"),
            "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "verdict": verdict,
            "executable_ops": sorted(self.ops),
            "items": [r.to_dict() for r in results],
            "counts": {s: sum(1 for r in results if r.status == s) for s in ("PASS", "FAIL", "NOT_EXECUTABLE", "SKIPPED")},
            "criterion": "verdict EXECUTED_PASS requires every item PASS (NOT_EXECUTABLE downgrades to PARTIAL)",
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        out = self.run_dir / f"{order.get('order_id', 'order')}_execution.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report["artifacts"] = {"execution_report": str(out)}
        return report