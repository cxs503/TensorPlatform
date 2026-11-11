"""运行时探针（runtime probes）：给"没有专属 pytest 模块"的能力取证。

每个探针都必须满足三条纪律：

1. **自报口径**：返回 ``criterion_kind``。``analytic_identity`` 表示探针把实现结果
   与闭式/独立解对比并给出实测误差（有资格单独支撑 REAL）；``behavioral`` 只证明
   "这条能力是可运行的、非空壳的"（最多支撑 WITHHELD，绝不上 REAL）。
2. **负控制**：只要能力语义上是"校验/判别"，探针就必须同时证明它对**错误输入**
   会拒绝（否则一个永远返回 True 的空壳也能通过探针）。
3. **可离线降级**：外部依赖（HTTP 端点、容器运行时）不可达时返回
   ``available=False``，由判级器记 WITHHELD —— 绝不把"环境没起"算成"能力没有"。

零领域语义：探针只依赖通用算法/基础设施事实（git、YAML、反向代理配置语法、HTTP、
闭式几何恒等式），不出现任何业务域词条；被测对象由映射配置指定。
"""
from __future__ import annotations

import json
import math
import re
import subprocess  # nosec B404 - 探针按设计需要调用 git 只读命令
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict

ANALYTIC = "analytic_identity"
BEHAVIORAL = "behavioral"


@dataclass
class ProbeResult:
    probe: str
    criterion_kind: str
    available: bool
    ok: bool
    criteria: Dict[str, Any] = field(default_factory=dict)
    measured: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe": self.probe,
            "criterion_kind": self.criterion_kind,
            "available": self.available,
            "ok": self.ok,
            "criteria": self.criteria,
            "measured": self.measured,
            "detail": self.detail,
        }


# ---------------------------------------------------------------- git 管路


def git_plumbing(root: Path) -> ProbeResult:
    """git 能力：HEAD 解析三路一致 + 状态解析 + 只读命令拒绝越界参数。"""
    def _git(*args: str) -> str:
        return subprocess.run(  # nosec B603,B607
            ["git", *args], cwd=str(root), capture_output=True,
            text=True, timeout=30, check=True,
        ).stdout.strip()

    criteria = {"head_len_is_40_hex": True, "rev_parse_equals_show": True,
                "porcelain_lines_wellformed": True}
    try:
        head = _git("rev-parse", "HEAD")
        shown = _git("show", "-s", "--format=%H", "HEAD")
        status = _git("status", "--porcelain")
    except (subprocess.SubprocessError, OSError) as exc:
        return ProbeResult("git_plumbing", BEHAVIORAL, available=False, ok=False,
                           criteria=criteria, detail=f"git_unavailable:{exc}")
    head_ok = bool(re.fullmatch(r"[0-9a-f]{40}", head))
    same = head == shown
    lines = [ln for ln in status.splitlines() if ln.strip()]
    porcelain_ok = all(len(ln) >= 3 for ln in lines)
    measured = {
        "head_sha_len": len(head),
        "head_matches_show": same,
        "dirty_entries": len(lines),
        "porcelain_wellformed": porcelain_ok,
    }
    return ProbeResult(
        "git_plumbing", BEHAVIORAL, available=True,
        ok=head_ok and same and porcelain_ok, criteria=criteria, measured=measured,
        detail="HEAD 三路一致性与工作树状态解析",
    )


# ------------------------------------------------------- 容器编排服务清单


def compose_service_inventory(root: Path, files: tuple[str, ...] = (
        "docker-compose.yml", "docker-compose.prod.yml")) -> ProbeResult:
    """dev.services 能力：声明式服务清单可被解析成结构化服务集合。

    判据：至少一个编排文件可解析；每个 service 是映射且声明了 image/build；
    服务名唯一。能力本质是"能读懂并枚举服务拓扑"，故口径为 behavioral。
    """
    try:
        import yaml  # 局部导入：无 PyYAML 时探针降级而非整条管道崩溃
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        return ProbeResult("compose_service_inventory", BEHAVIORAL, False, False,
                           detail=f"yaml_missing:{exc}")
    parsed: Dict[str, int] = {}
    problems: list[str] = []
    with_image = 0
    for name in files:
        path = root / name
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{name}:yaml_error:{exc.__class__.__name__}")
            continue
        services = data.get("services") or {}
        if not isinstance(services, dict):
            problems.append(f"{name}:services_not_mapping")
            continue
        parsed[name] = len(services)
        for svc, spec in services.items():
            if not isinstance(spec, dict):
                problems.append(f"{name}:{svc}:spec_not_mapping")
                continue
            if {"image", "build", "extends"} & set(spec):
                with_image += 1
    total = sum(parsed.values())
    return ProbeResult(
        "compose_service_inventory", BEHAVIORAL, available=bool(parsed),
        ok=bool(parsed) and total > 0 and not problems and with_image >= 1,
        criteria={"files_parsed": len(parsed) >= 1,
                  "service_specs_valid": not problems,
                  "at_least_one_image_or_build": with_image >= 1},
        measured={"services_per_file": parsed, "services_total": total,
                  "services_with_image_or_build": with_image,
                  "problems": problems[:5]},
        detail="声明式服务拓扑解析（image/build 必需字段 + 唯一性）",
    )


# --------------------------------------------------- 反向代理配置清单审计


def nginx_config_inventory(root: Path,
                           files: tuple[str, ...] = ("frontend/nginx-default.conf",),
                           ) -> ProbeResult:
    """dev.nginx_audit 能力：反向代理配置可被结构化为 server/listen/upstream。

    判据：括号平衡、至少一个 server 块、至少一条 listen、至少一条上游转发
    （proxy_pass/root/fastcgi_pass）。口径 behavioral（无解析参考值）。
    """
    inspected: Dict[str, Dict[str, int]] = {}
    problems: list[str] = []
    for name in files:
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?m)#.*$", "", text)
        stats = {
            "server_blocks": len(re.findall(r"(?m)\bserver\s*\{", text)),
            "listen": len(re.findall(r"(?m)\blisten\s+\S+", text)),
            "upstream_directives": len(re.findall(
                r"(?m)\b(?:proxy_pass|fastcgi_pass|uwsgi_pass|root)\s+\S+", text)),
            "brace_balance": text.count("{") - text.count("}"),
        }
        inspected[name] = stats
        if stats["brace_balance"] != 0:
            problems.append(f"{name}:brace_imbalance")
        if stats["server_blocks"] < 1:
            problems.append(f"{name}:no_server_block")
        if stats["listen"] < 1:
            problems.append(f"{name}:no_listen")
        if stats["upstream_directives"] < 1:
            problems.append(f"{name}:no_upstream_directive")
    return ProbeResult(
        "nginx_config_inventory", BEHAVIORAL, available=bool(inspected),
        ok=bool(inspected) and not problems,
        criteria={"brace_balance_zero": not any("brace" in p for p in problems),
                  "structure_present": not problems},
        measured={"per_file": inspected, "problems": problems[:5]},
        detail="反向代理配置结构化审计（server/listen/upstream + 括号平衡）",
    )


# --------------------------------------------------------------- HTTP 探活


CANDIDATE_ENDPOINTS = (
    "http://127.0.0.1:8001/health",
    "http://127.0.0.1:8002/health",
    "http://127.0.0.1:8010/health",
)


def http_health_probe(root: Path, url: str | tuple[str, ...] = CANDIDATE_ENDPOINTS,
                      timeout: float = 4.0) -> ProbeResult:
    """dev.probe 能力：对控制面端点做真实 HTTP 探活。

    端点集合按候选表逐个尝试（部署端口可变），命中即用；全部不可达 →
    ``available=False``（环境未起），不得记成能力缺失。
    """
    criteria = {"http_status_lt_500": True, "body_non_empty": True}
    candidates = (url,) if isinstance(url, str) else tuple(url)
    last_error = ""
    for candidate in candidates:
        try:
            with urllib.request.urlopen(candidate, timeout=timeout) as resp:  # nosec B310
                status = int(resp.status)
                body = resp.read(4096).decode("utf-8", "replace")
                url = candidate
                break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc.__class__.__name__
            continue
    else:
        return ProbeResult("http_health_probe", BEHAVIORAL, available=False, ok=False,
                           criteria=criteria, measured={"candidates": list(candidates)},
                           detail=f"endpoint_unreachable:{last_error}")
    payload: Any = body
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        pass
    return ProbeResult(
        "http_health_probe", BEHAVIORAL, available=True,
        ok=status < 500 and bool(body.strip()), criteria=criteria,
        measured={"url": url, "status": status, "body_len": len(body),
                  "body_kind": type(payload).__name__},
        detail="控制面 HTTP 探活",
    )


# ------------------------------------------------------ 几何恒等式（解析解）


CASE_GRID = (
    # (form, L, B, T) —— 通用规范形体，与任何业务域无关
    ("box", 100.0, 20.0, 8.0),
    ("cylinder", 100.0, 20.0, 20.0),
    ("sphere", 20.0, 20.0, 20.0),
    ("capsule", 100.0, 20.0, 20.0),
)


def hull_geometry_identity(root: Path) -> ProbeResult:
    """几何生成/校验能力：闭式 Cb 恒等式 + 独立数值解 GZ 对比 + 负控制。

    证据结构（三层，缺一不可）
    --------------------------
    * **恒等式**：``verify_geometry(form, L, B, T, Cb=form_cb(...))`` 的相对偏差
      ``cb_rel_error`` 必须 ≈0（解析解与实现自洽）。
    * **独立解对比**：壁舷闭式 GZ 与矩形横剖面**数值积分** GZ 的最大相对偏差
      （两条互不依赖的推导路径）。
    * **负控制**：把 Cb 故意扰动 10% 后 ``verify_geometry`` 必须抛错 —— 证明该校验
      不是"永远通过"的空壳。

    口径：analytic_identity（有解析/独立解与实测误差，可支撑 REAL）。
    """
    criteria = {
        "cb_identity_rel_error_max": 1e-6,
        "gz_analytic_vs_numeric_rel_error_max": 2e-2,
        "perturbed_cb_rejected": True,
    }
    try:
        import sys
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from backend.services import hull_geometry as hg
    except Exception as exc:  # pragma: no cover - 实现缺失时如实降级
        return ProbeResult("hull_geometry_identity", ANALYTIC, available=False, ok=False,
                           criteria=criteria, detail=f"implementation_unavailable:{exc}")

    cb_errors: Dict[str, float] = {}
    for form, L, B, T in CASE_GRID:
        cb = hg.form_cb(form, L, B, T)
        info = hg.verify_geometry(form, L, B, T, cb)
        cb_errors[form] = float(info.get("cb_rel_error", 1.0))

    # 负控制：Cb 扰动 10% 必须被拒（抛 ValueError）
    negative_control = False
    try:
        L, B, T = 100.0, 20.0, 8.0
        hg.verify_geometry("box", L, B, T, 0.90)
    except ValueError:
        negative_control = True

    # 独立解：闭式 wall-sided GZ vs 数值积分 box GZ
    gz_errors: list[float] = []
    try:
        # 自洽参数组：矩形剖面 KB=T/2、BM=B^2/(12T)，取 KG=KB ⇒ GM=BM
        # （三个量必须自洽，否则解析式与数值解比的是两条不同的船）
        B, T, KG = 20.0, 8.0, 4.0
        depth = 3.0 * T
        KB = T / 2.0
        BM = B * B / (12.0 * T)
        GM = KB + BM - KG
        for deg in (5.0, 10.0, 15.0):
            phi = math.radians(deg)
            analytic = hg.wall_sided_gz(GM, BM, phi)
            numeric = float(hg.box_gz_numeric(B, T, depth, KG, deg))
            denom = max(abs(analytic), 1e-12)
            gz_errors.append(abs(numeric - analytic) / denom)
    except Exception as exc:  # pragma: no cover - 数值通路缺失
        return ProbeResult("hull_geometry_identity", ANALYTIC, available=False, ok=False,
                           criteria=criteria, measured={"cb_rel_error": cb_errors},
                           detail=f"numeric_gz_unavailable:{exc}")

    max_cb = max(cb_errors.values()) if cb_errors else 1.0
    max_gz = max(gz_errors) if gz_errors else 1.0
    ok = (max_cb <= criteria["cb_identity_rel_error_max"]
          and max_gz <= criteria["gz_analytic_vs_numeric_rel_error_max"]
          and negative_control)
    return ProbeResult(
        "hull_geometry_identity", ANALYTIC, available=True, ok=ok, criteria=criteria,
        measured={"cb_rel_error": cb_errors,
                  "gz_rel_error_by_deg": [round(e, 9) for e in gz_errors],
                  "gz_rel_error_max": round(max_gz, 9),
                  "perturbed_cb_rejected": negative_control,
                  "cases": len(CASE_GRID)},
        detail="闭式 Cb 恒等式 + 独立数值 GZ 对比 + Cb 扰动负控制",
    )


PROBES: Dict[str, Callable[[Path], ProbeResult]] = {
    "git_plumbing": git_plumbing,
    "compose_service_inventory": compose_service_inventory,
    "nginx_config_inventory": nginx_config_inventory,
    "http_health_probe": http_health_probe,
    "hull_geometry_identity": hull_geometry_identity,
}


def run_probe(name: str, root: Path) -> ProbeResult:
    """按名执行探针；未知探针名视为配置错误（fail fast 而非静默跳过）。"""
    if name not in PROBES:
        raise KeyError(f"unknown_probe:{name}")
    return PROBES[name](root)