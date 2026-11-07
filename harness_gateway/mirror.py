"""harness 镜像：源 → 门控仓镜像目录，sha256 双向可复算。

镜像不是"复制一份文件"这么简单，它要提供两件事：
1. **可复算**：manifest 记录每个单元的源字节 sha256 与镜像字节 sha256；
   任何人可以从源重算并验证镜像未被手改（mirror_tamper），
   也能发现源被旁路改动（source_drift，说明有人绕过门直接改了生产 harness）。
2. **无凭据**：入库前统一走 redact，镜像内不得出现明文口令。

镜像目录结构::

    harness_gateway/mirror/
        MIRROR_MANIFEST.json
        campaign_dispatcher.py
        tp_worker.py
        ...
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from harness_gateway import redact
from harness_gateway.spec import DEFAULT_UNITS, HarnessUnit

SCHEMA = "harness-gateway/mirror-manifest/1"
DEPLOY_STATE_SCHEMA = "harness-gateway/deploy-state/1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


@dataclass
class MirrorEntry:
    name: str
    role: str
    relpath: str
    canary: str
    source_sha256: str
    mirror_sha256: str
    source_bytes: int
    mirror_bytes: int
    redactions: list[dict] = field(default_factory=list)
    staged_patch: bool = False  # 镜像领先源：仓里已有过门的补丁尚未部署

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "relpath": self.relpath,
            "canary": self.canary,
            "source_sha256": self.source_sha256,
            "mirror_sha256": self.mirror_sha256,
            "source_bytes": self.source_bytes,
            "mirror_bytes": self.mirror_bytes,
            "redactions": self.redactions,
            "staged_patch": self.staged_patch,
        }


@dataclass
class MirrorReport:
    src_root: str
    mirror_root: str
    entries: list[MirrorEntry] = field(default_factory=list)
    created_at: str = ""

    @property
    def redaction_total(self) -> int:
        return sum(len(e.redactions) for e in self.entries)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "created_at": self.created_at,
            "staged_patch_units": [e.name for e in self.entries if e.staged_patch],
            "src_root": self.src_root,
            "mirror_root": self.mirror_root,
            "units": [e.to_dict() for e in self.entries],
            "unit_count": len(self.entries),
            "redaction_total": self.redaction_total,
        }


@dataclass
class Drift:
    kind: str  # missing_source | missing_mirror | source_drift | mirror_tamper
    unit: str
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "unit": self.unit, "detail": self.detail}


def _render(src_bytes: bytes) -> tuple[bytes, list[dict]]:
    """源字节 → 镜像字节（脱敏）。返回 (bytes, findings)。"""
    text = src_bytes.decode("utf-8")
    out, report = redact.redact(text)
    return out.encode("utf-8"), [f.to_dict() for f in report.findings]


def mirror_harness(
    src_root: str | Path,
    mirror_root: str | Path,
    units: tuple[HarnessUnit, ...] = DEFAULT_UNITS,
    *,
    write_manifest: bool = True,
    force: bool = False,
) -> MirrorReport:
    src_root = Path(src_root)
    mirror_root = Path(mirror_root)
    mirror_root.mkdir(parents=True, exist_ok=True)
    report = MirrorReport(
        src_root=str(src_root),
        mirror_root=str(mirror_root),
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    for unit in units:
        src = src_root / unit.relpath
        if not src.exists():
            raise FileNotFoundError(f"harness unit missing in source root: {src}")
        src_bytes = src.read_bytes()
        mirror_bytes, findings = _render(src_bytes)
        dst = mirror_root / unit.mirror_relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 已过门但尚未部署的补丁不允许被一次 sync 静默抹掉（staged patch 保护）
        staged = dst.exists() and dst.read_bytes() != mirror_bytes and not force
        if not staged:
            dst.write_bytes(mirror_bytes)
        on_disk = dst.read_bytes()
        report.entries.append(
            MirrorEntry(
                name=unit.name,
                role=unit.role,
                relpath=unit.relpath,
                canary=unit.canary,
                source_sha256=sha256_bytes(src_bytes),
                mirror_sha256=sha256_bytes(on_disk),
                source_bytes=len(src_bytes),
                mirror_bytes=len(on_disk),
                redactions=findings,
                staged_patch=staged,
            )
        )
    if write_manifest:
        (mirror_root / "MIRROR_MANIFEST.json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_deploy_state(mirror_root, report)
    return report


def write_deploy_state(mirror_root: str | Path, report: MirrorReport) -> dict:
    """记录"镜像 ↔ 生产源"的同步基线。

    有了它，预检才能分清两种情况：
        * 生产 harness 被绕过门改了（live sha 与基线不符）→ 必须 BLOCKED
        * 镜像领先生产一个待部署补丁（mirror != redact(live)，但 live 未动）→ 允许，标记 AHEAD
    没有基线时退化为严格比对（等价于"镜像必须等于生产"）。
    """
    state = {
        "schema": DEPLOY_STATE_SCHEMA,
        "updated_at": report.created_at,
        "src_root": report.src_root,
        "units": {
            e.name: {"live_sha256": e.source_sha256, "mirror_sha256": e.mirror_sha256}
            for e in report.entries
        },
    }
    (Path(mirror_root) / "DEPLOY_STATE.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return state


def load_deploy_state(mirror_root: str | Path) -> dict | None:
    path = Path(mirror_root) / "DEPLOY_STATE.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(mirror_root: str | Path) -> dict:
    path = Path(mirror_root) / "MIRROR_MANIFEST.json"
    if not path.exists():
        raise FileNotFoundError(f"mirror manifest missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_mirror(
    src_root: str | Path,
    mirror_root: str | Path,
    units: tuple[HarnessUnit, ...] = DEFAULT_UNITS,
    *,
    manifest: dict | None = None,
) -> list[Drift]:
    """校验镜像与源一致（允许且仅允许"脱敏差异"）。

    三类漂移：
        source_drift  源文件相对 manifest 变了 —— 有人绕过门直接改生产 harness
        mirror_tamper 镜像文件相对 manifest 变了 —— 有人手改门控仓镜像
        missing_*     文件缺失
    """
    src_root = Path(src_root)
    mirror_root = Path(mirror_root)
    manifest = manifest or load_manifest(mirror_root)
    recorded = {u["name"]: u for u in manifest.get("units", [])}
    drifts: list[Drift] = []
    for unit in units:
        rec = recorded.get(unit.name)
        if rec is None:
            drifts.append(Drift("missing_manifest_entry", unit.name, "unit not present in MIRROR_MANIFEST.json"))
            continue
        src = src_root / unit.relpath
        if not src.exists():
            drifts.append(Drift("missing_source", unit.name, str(src)))
            continue
        expected_bytes, _ = _render(src.read_bytes())
        if rec.get("staged_patch"):
            # 已过门的待部署补丁：镜像与源不同是预期态，交给 preflight 判 AHEAD
            pass
        elif sha256_bytes(expected_bytes) != rec["mirror_sha256"]:
            drifts.append(
                Drift(
                    "source_drift",
                    unit.name,
                    f"rendered source sha256 {sha256_bytes(expected_bytes)[:12]} != manifest {rec['mirror_sha256'][:12]}",
                )
            )
        dst = mirror_root / unit.mirror_relpath
        if not dst.exists():
            drifts.append(Drift("missing_mirror", unit.name, str(dst)))
            continue
        if sha256_file(dst) != rec["mirror_sha256"]:
            drifts.append(
                Drift(
                    "mirror_tamper",
                    unit.name,
                    f"mirror sha256 {sha256_file(dst)[:12]} != manifest {rec['mirror_sha256'][:12]}",
                )
            )
    return drifts


def stale_units(src_root: str | Path, mirror_root: str | Path, units=DEFAULT_UNITS) -> list[str]:
    """返回需要重新同步的单元名（便捷包装）。"""
    return [d.unit for d in verify_mirror(src_root, mirror_root, units)]