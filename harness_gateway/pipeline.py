"""harness 修补 → 门链 → 部署/回滚 的执行管线。

一次修补的完整生命周期（**唯一的合法改法**）::

    preflight(mirror vs live source)     源/镜像漂移 → BLOCKED_DRIFT（不许改）
    checkpoint + rollback_drill          git HEAD + 逐文件 sha256 + 备份，回滚门武装
    apply_patch                          逐条精确替换，产出 unified diff 证据
    gate 1 py_compile                    编译/重复定义/明文凭据
    gate 2 canary_single_process         沙箱单进程真跑，零生产副作用
    verdict                              PASS 或 FAIL_ROLLED_BACK（回滚门已经动过手）

补丁不用手写 diff，而用 (file, old, new) 精确替换：old 找不到 = 补丁锚点失效，
直接判 FAIL 并回滚 —— 这比"patch 大致打上了"可靠，也能被红队负例直接复用
（故意把 new 写成语法错/运行期错，验证门是否真的拦得住）。
"""

from __future__ import annotations

import difflib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from harness_gateway import mirror as mirror_mod
from harness_gateway.gates import canary_gate, compile_gate, rollback_gate
from harness_gateway.gates.base import GateResult, GateStatus
from harness_gateway.spec import DEFAULT_UNITS, HarnessUnit, resolve_unit

SCHEMA = "harness-gateway/gate-chain-report/1"


@dataclass
class Replacement:
    """一条精确替换。old 必须在文件里恰好出现 >=1 次。"""

    file: str
    old: str
    new: str
    count: int = 1
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "old": self.old,
            "new": self.new,
            "count": self.count,
            "note": self.note,
        }


@dataclass
class PatchSpec:
    label: str
    replacements: list[Replacement] = field(default_factory=list)
    rationale: str = ""
    author: str = "m5306"

    @classmethod
    def from_dict(cls, data: dict) -> "PatchSpec":
        return cls(
            label=data["label"],
            rationale=data.get("rationale", ""),
            author=data.get("author", "m5306"),
            replacements=[Replacement(**{k: r[k] for k in ("file", "old", "new", "count", "note") if k in r})
                          for r in data.get("replacements", [])],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "PatchSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def files(self) -> list[str]:
        seen: list[str] = []
        for r in self.replacements:
            if r.file not in seen:
                seen.append(r.file)
        return seen

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "rationale": self.rationale,
            "author": self.author,
            "files": self.files,
            "replacements": [r.to_dict() for r in self.replacements],
        }


class HarnessDeployPipeline:
    """门控部署管线。repo_root = 门控仓（git 工作树），mirror_root = 镜像目录。"""

    def __init__(
        self,
        repo_root: str | Path,
        mirror_root: str | Path,
        *,
        src_root: str | Path | None = None,
        units: Sequence[HarnessUnit] = DEFAULT_UNITS,
        artifacts_dir: str | Path = "artifacts/harness_gateway",
        shim_exit: int = 1,
        canary_sleep_s: float = 0.0,
        verify_source: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.mirror_root = Path(mirror_root)
        self.src_root = Path(src_root) if src_root else None
        self.units = tuple(units)
        self.artifacts_dir = Path(artifacts_dir)
        self.shim_exit = shim_exit
        self.canary_sleep_s = canary_sleep_s
        self.verify_source = verify_source

    # ---------------- helpers ----------------
    def _unit_for_file(self, relpath: str) -> HarnessUnit | None:
        return resolve_unit(relpath, self.units)

    def repo_relpath(self, unit: HarnessUnit) -> str:
        """镜像文件相对 **git 工作树** 的路径（git checkout 需要仓内相对路径）。"""
        path = (self.mirror_root / unit.mirror_relpath).resolve()
        return str(path.relative_to(self.repo_root.resolve()))

    def changed_units(self, patch: PatchSpec | None) -> list[HarnessUnit]:
        if patch is None:
            return list(self.units)
        out: list[HarnessUnit] = []
        for rel in patch.files:
            unit = self._unit_for_file(rel)
            if unit is None:
                raise KeyError(f"patch touches a file outside the harness unit whitelist: {rel}")
            out.append(unit)
        return out

    # ---------------- main entry ----------------
    def run(
        self,
        patch: PatchSpec | None = None,
        *,
        run_id: str | None = None,
        negative_control: str | None = None,
        commit: bool = False,
    ) -> dict:
        started = time.time()
        # 白名单前置校验：补丁若触碰清单外文件，立刻 KeyError（不进入门链、不动任何字节）
        if patch is not None:
            self.changed_units(patch)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started))
        run_id = run_id or f"{stamp}_{(patch.label if patch else 'noop')}_{uuid.uuid4().hex[:6]}"
        run_dir = self.artifacts_dir / run_id
        (run_dir / "canary").mkdir(parents=True, exist_ok=True)
        report: dict = {
            "schema": SCHEMA,
            "run_id": run_id,
            "label": patch.label if patch else "noop",
            "negative_control": negative_control,
            "started_at": stamp,
            "repo_root": str(self.repo_root),
            "mirror_root": str(self.mirror_root),
            "src_root": str(self.src_root) if self.src_root else None,
            "unit_whitelist": [u.name for u in self.units],
            "patch": patch.to_dict() if patch else None,
            "stages": [],
            "verdict": "PENDING",
            "caught_by": None,
            "rollback": None,
            "artifacts": {},
            "criteria": {},
        }

        def stage(res: GateResult, required: bool = True) -> None:
            report["stages"].append({**res.to_dict(), "required": required})
            if required and res.status is GateStatus.FAIL:
                report["caught_by"] = res.gate

        # ---- preflight: 镜像完整性 + 生产源是否被绕过门改动 ----
        preflight, block_verdict = self._preflight()
        stage(preflight)
        if preflight.status is GateStatus.FAIL:
            report["verdict"] = block_verdict
            return self._finalize(report, run_dir)

        # ---- checkpoint（回滚门武装） ----
        relpaths = [self.repo_relpath(u) for u in self.units]
        ckpt = rollback_gate.checkpoint(self.repo_root, relpaths, run_dir=run_dir)
        report["checkpoint"] = ckpt.to_dict()
        dres = rollback_gate.drill(run_dir)
        stage(dres)
        if ckpt.pre_existing_diffs:
            blocked = GateResult(
                "precondition_clean_tree",
                GateStatus.FAIL,
                "target files already modified vs HEAD: " + ", ".join(ckpt.pre_existing_diffs),
                {"pre_existing_diffs": ckpt.pre_existing_diffs},
            )
            stage(blocked)
            report["verdict"] = "BLOCKED_DIRTY"
            return self._finalize(report, run_dir)
        stage(
            GateResult(
                "checkpoint",
                GateStatus.PASS,
                f"{len(relpaths)} file(s) checkpointed at {ckpt.git_head[:12]}",
                {"git_head": ckpt.git_head, "files": sorted(relpaths)},
            )
        )

        # ---- apply patch ----
        apply_res = self._apply_patch(patch, run_dir)
        stage(apply_res, required=patch is not None)
        if apply_res.status is GateStatus.FAIL:
            return self._rollback_and_finalize(report, run_dir, ckpt, apply_res)

        # ---- gate 1 ----
        changed = self.changed_units(patch)
        gate_paths = [self.mirror_root / u.mirror_relpath for u in self.units]
        cgate = compile_gate.run_compile_gate(gate_paths)
        stage(cgate)
        if not cgate.ok:
            return self._rollback_and_finalize(report, run_dir, ckpt, cgate)

        # ---- gate 2 ----
        kgate = canary_gate.run_canary_gate(
            self.mirror_root,
            self.units,
            sandbox_root=run_dir / "canary",
            shim_exit=self.shim_exit,
            sleep_s=self.canary_sleep_s,
            only=[u.name for u in changed],
        )
        stage(kgate)
        if not kgate.ok:
            return self._rollback_and_finalize(report, run_dir, ckpt, kgate)

        # ---- 全绿 →（可选）提交 ----
        report["verdict"] = "PASS"
        if commit and patch is not None:
            stage(self._commit(patch, run_id), required=False)
        report["post_state"] = {u.mirror_relpath: mirror_mod.sha256_file(self.mirror_root / u.mirror_relpath)
                                for u in self.units}
        report["deploy_plan"] = self.deploy_plan()
        report["criteria"] = {
            "gates_required": ["preflight_mirror_verify", "rollback_drill", "py_compile", "canary_single_process"],
            "gates_passed": [s["gate"] for s in report["stages"] if s["status"] == "PASS"],
            "patch_files": patch.files if patch else [],
            "canary_units": [u.name for u in changed],
        }
        return self._finalize(report, run_dir)

    # ---------------- internals ----------------
    def _preflight(self) -> tuple[GateResult, str]:
        """两道判据：
        1. 镜像文件必须等于 git HEAD（有人手改镜像 → BLOCKED_MIRROR_TAMPER）；
        2. 生产 harness 必须等于镜像同步基线里的 live_sha256
           （有人绕过门直接改生产 → BLOCKED_DRIFT）。
        镜像领先生产一个"待部署补丁"是正常态（AHEAD），记录但不拦。
        """
        import subprocess as _sp

        res = GateResult("preflight_mirror_verify", GateStatus.PASS, "")
        t0 = time.monotonic()
        rels = [self.repo_relpath(u) for u in self.units]
        dirty = _sp.run(["git", "diff", "--name-only", "HEAD", "--", *rels],
                        cwd=str(self.repo_root), capture_output=True, text=True).stdout.split()
        state = mirror_mod.load_deploy_state(self.mirror_root) if self.src_root else None
        live_drift: list[dict] = []
        ahead: list[str] = []
        sync: list[str] = []
        if self.verify_source and self.src_root is not None:
            for unit in self.units:
                live = self.src_root / unit.relpath
                mirror = self.mirror_root / unit.mirror_relpath
                if not live.exists() or not mirror.exists():
                    live_drift.append({"unit": unit.name, "kind": "missing", "detail": str(live)})
                    continue
                rendered, _ = mirror_mod._render(live.read_bytes())
                same_as_live = mirror_mod.sha256_bytes(rendered) == mirror_mod.sha256_file(mirror)
                recorded = (state or {}).get("units", {}).get(unit.name, {}).get("live_sha256")
                if state is None:
                    # 无基线：退回严格语义（镜像必须等于生产）
                    if not same_as_live:
                        live_drift.append({"unit": unit.name, "kind": "source_drift",
                                           "detail": "no DEPLOY_STATE baseline; mirror != redact(live)"})
                elif recorded is not None and recorded != mirror_mod.sha256_file(live):
                    live_drift.append({"unit": unit.name, "kind": "live_changed_outside_gate",
                                       "detail": f"live sha256 != deploy-state baseline ({recorded[:12]})"})
                elif same_as_live:
                    sync.append(unit.name)
                else:
                    ahead.append(unit.name)
        res.evidence = {
            "mirror_dirty_paths": sorted(dirty),
            "live_drifts": live_drift,
            "units_in_sync_with_live": sync,
            "units_ahead_of_live_pending_deploy": ahead,
            "deploy_state_present": state is not None,
            "criterion": [
                "mirror on disk == git HEAD for every whitelisted unit",
                "live harness sha256 == DEPLOY_STATE baseline (no bypass edits)",
                "mirror != redact(live) is allowed only as a staged deployment (AHEAD)",
            ],
        }
        if dirty:
            res.status = GateStatus.FAIL
            res.detail = "mirror modified outside git: " + ", ".join(sorted(dirty))
            block = "BLOCKED_MIRROR_TAMPER"
        elif live_drift:
            res.status = GateStatus.FAIL
            res.detail = "live harness diverged from gate baseline: " + ", ".join(
                f"{d['unit']}({d['kind']})" for d in live_drift
            )
            block = "BLOCKED_DRIFT"
        else:
            block = ""
            res.detail = (
                f"{len(self.units)} unit(s) gated; {len(sync)} in sync, {len(ahead)} ahead of live (staged deploy pending)"
                + ("" if state is not None else " [no deploy-state baseline: strict compare]")
            )
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        return res, block

    def _commit(self, patch: PatchSpec, run_id: str) -> GateResult:
        """门链的最后一步：把过门的镜像提交进 git（未提交的补丁会让下一次预检判 TAMPER）。"""
        import subprocess as _sp

        res = GateResult("commit_mirror", GateStatus.PASS, "")
        rels = [self.repo_relpath(u) for u in self.units]
        add = _sp.run(["git", "add", "--", *rels], cwd=str(self.repo_root), capture_output=True, text=True)
        if add.returncode != 0:
            res.status = GateStatus.FAIL
            res.detail = f"git add failed: {add.stderr.strip()[:200]}"
            return res
        msg = f"harness-gateway: {patch.label} (gates PASS, run {run_id})"
        cm = _sp.run(["git", "commit", "-q", "-m", msg, "--", *rels],
                     cwd=str(self.repo_root), capture_output=True, text=True)
        head = _sp.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo_root),
                       capture_output=True, text=True).stdout.strip()
        res.evidence = {"message": msg, "git_head": head, "files": rels,
                        "stdout": cm.stdout.strip()[:200], "stderr": cm.stderr.strip()[:200]}
        if cm.returncode != 0:
            res.status = GateStatus.FAIL
            res.detail = f"git commit failed: {cm.stderr.strip()[:200]}"
        else:
            res.detail = f"committed {len(rels)} path(s) at {head[:12]}"
        return res

    def _apply_patch(self, patch: PatchSpec | None, run_dir: Path) -> GateResult:
        res = GateResult("apply_patch", GateStatus.PASS, "")
        t0 = time.monotonic()
        if patch is None:
            res.status = GateStatus.SKIP
            res.detail = "no patch supplied (gate-only run)"
            res.duration_ms = int((time.monotonic() - t0) * 1000)
            return res
        diffs: list[str] = []
        applied: list[dict] = []
        problems: list[str] = []
        for rep in patch.replacements:
            path = self.mirror_root / rep.file
            if not path.exists():
                problems.append(f"{rep.file}: not in mirror (outside gated repo)")
                continue
            before = path.read_text(encoding="utf-8")
            occurrences = before.count(rep.old)
            if occurrences < rep.count:
                problems.append(f"{rep.file}: anchor not found (needs {rep.count}, found {occurrences})")
                continue
            after = before.replace(rep.old, rep.new, rep.count)
            path.write_text(after, encoding="utf-8")
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{rep.file}",
                    tofile=f"b/{rep.file}",
                    n=3,
                )
            )
            diffs.append(diff)
            applied.append({"file": rep.file, "count": rep.count, "note": rep.note,
                            "old_sha256": mirror_mod.sha256_bytes(before.encode()),
                            "new_sha256": mirror_mod.sha256_bytes(after.encode())})
        (run_dir / "patch.diff").write_text("\n".join(diffs), encoding="utf-8")
        res.evidence = {"applied": applied, "problems": problems, "diff_lines": sum(d.count("\n") for d in diffs)}
        if problems:
            res.status = GateStatus.FAIL
            res.detail = "; ".join(problems)[:600]
        else:
            res.detail = f"{len(applied)} replacement(s) applied across {len(patch.files)} file(s)"
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        return res

    def _rollback_and_finalize(self, report: dict, run_dir: Path, ckpt, cause: GateResult) -> dict:
        rb = rollback_gate.rollback(self.repo_root, ckpt)
        report["rollback"] = rb.to_dict()
        report["stages"].append({**rb.to_dict(), "required": True})
        report["verdict"] = "FAIL_ROLLED_BACK" if rb.ok else "FAIL_ROLLBACK_INCOMPLETE"
        report["caught_by"] = cause.gate
        report["failure_detail"] = cause.detail
        report["post_state"] = {u.mirror_relpath: mirror_mod.sha256_file(self.mirror_root / u.mirror_relpath)
                                for u in self.units if (self.mirror_root / u.mirror_relpath).exists()}
        report["criteria"] = {
            "failed_gate": cause.gate,
            "rollback_files_matched": sum(1 for f in rb.evidence.get("files", []) if f["match"]),
            "rollback_files_total": len(rb.evidence.get("files", [])),
            "criterion": "verdict FAIL_ROLLED_BACK requires every file byte-identical to checkpoint",
        }
        return self._finalize(report, run_dir)

    def _finalize(self, report: dict, run_dir: Path) -> dict:
        report["artifacts"] = {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.json"),
            "patch_diff": str(run_dir / "patch.diff") if (run_dir / "patch.diff").exists() else None,
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report

    # ---------------- deployment ----------------
    def deploy_plan(self, target_root: str | Path | None = None) -> dict:
        """把镜像映射成"对生产 harness 的写入计划"（默认 dry-run，只出计划不落盘）。"""
        target = Path(target_root) if target_root else self.src_root
        plan: dict = {"target_root": str(target) if target else None, "units": [], "actions": {}}
        if target is None:
            plan["note"] = "no target root; plan is mirror-internal only"
            return plan
        for unit in self.units:
            src = self.mirror_root / unit.mirror_relpath
            dst = target / unit.relpath
            entry = {
                "unit": unit.name,
                "mirror": str(src),
                "target": str(dst),
                "mirror_sha256": mirror_mod.sha256_file(src) if src.exists() else None,
                "target_sha256": mirror_mod.sha256_file(dst) if dst.exists() else None,
                "target_exists": dst.exists(),
            }
            mirror_text = src.read_text(encoding="utf-8") if src.exists() else ""
            entry["carries_redaction_placeholder"] = "<REDACTED:" in mirror_text
            if not dst.exists():
                entry["action"] = "create"
            elif entry["mirror_sha256"] == entry["target_sha256"]:
                entry["action"] = "noop"
            elif entry["carries_redaction_placeholder"]:
                # 镜像里的口令是占位符：直投生产等于把口令换成占位符 → 必须先做凭据外置补丁
                entry["action"] = "blocked_redaction"
                entry["reason"] = "mirror holds a redacted credential placeholder; deploy only after credential extraction"
            else:
                entry["action"] = "update"
            plan["units"].append(entry)
        counts: dict[str, int] = {}
        for u in plan["units"]:
            counts[u["action"]] = counts.get(u["action"], 0) + 1
        plan["actions"] = counts
        return plan

    def apply_deploy(self, target_root: str | Path, *, dry_run: bool = True, run_dir: str | Path | None = None) -> dict:
        """按计划写入生产 harness。dry_run 默认 True —— 门控仓的原则是"默认不动生产"。"""
        plan = self.deploy_plan(target_root)
        written: list[dict] = []
        for entry in plan["units"]:
            if entry["action"] in ("noop", "blocked_redaction"):
                continue
            if dry_run:
                written.append({**entry, "would_write": True})
                continue
            dst = Path(entry["target"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(Path(entry["mirror"]).read_bytes())
            written.append({**entry, "would_write": False,
                            "written_sha256": mirror_mod.sha256_file(dst)})
        result = {"dry_run": dry_run, "target_root": str(target_root), "plan": plan, "writes": written}
        if run_dir:
            Path(run_dir).mkdir(parents=True, exist_ok=True)
            (Path(run_dir) / "deploy_result.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return result