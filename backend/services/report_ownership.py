"""报告归属门 + 配图魔数门（纯函数实现，无 FastAPI / DB 依赖）。

为什么单独成模块
----------------
历史上 `POST /tensor/reports` 只校验「你能不能发报告」（角色门），从不校验
「这份报告是不是你的单」：任何持 ENGINEER token 的智能体都能把 A 单的成果
写到 B 单号下。后果不是脏数据，而是**判据链断裂**——战役 DAG 按 mission_id
归并战绩与验收，写错单号等于把甲的绿灯记到乙头上，且错误无声（HTTP 200）。
同一位置的配图门只做了 `startswith(b"\\x89PNG")`：四位魔数一过就落盘，
截断 PNG、RIFF/WebP 伪装成 .png、只剩文件头的空壳都能混进报告页（图裂）。

本模块把两条门做成**可离线复算的纯函数**，生产端 `api/tensor_reports.py` 与
测试 import 同一份实现（不抄副本），因此「门存在但没接线」是可被静态+动态
两类测试同时钉住的。

判据（错误码即契约，前端/工装按 code 分支）
------------------------------------------
* `REPORT_MISSION_NOT_OWNED`  (403) mission_id 存在但不属于提交者
* `REPORT_MISSION_NOT_FOUND`  (404) mission_id 在库中不存在（含跨组织）
* `REPORT_FIGURE_NOT_PNG`     (422) 载荷不是 PNG（JPEG/WebP/GIF/BMP/坏 base64）
* `REPORT_FIGURE_TRUNCATED`   (422) PNG 头合法但缺 IEND 段（截断/半截上传）
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping

# ── 契约常量 ────────────────────────────────────────────────────────────────
CODE_MISSION_NOT_OWNED = "REPORT_MISSION_NOT_OWNED"
CODE_MISSION_NOT_FOUND = "REPORT_MISSION_NOT_FOUND"
CODE_FIGURE_NOT_PNG = "REPORT_FIGURE_NOT_PNG"
CODE_FIGURE_TRUNCATED = "REPORT_FIGURE_TRUNCATED"

HTTP_STATUS = {
    CODE_MISSION_NOT_OWNED: 403,
    CODE_MISSION_NOT_FOUND: 404,
    CODE_FIGURE_NOT_PNG: 422,
    CODE_FIGURE_TRUNCATED: 422,
}

#: PNG 签名（RFC 2083 §3.1），八字节，服务端唯一接受的图像容器。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: PNG 必须以带零长度的 IEND 块（含 CRC）收尾，否则就是被截断的流。
PNG_IEND_CHUNK = b"\x00\x00\x00\x00IEND\xaeB`\x82"
#: 人类的期望魔数表示（错误信息里必须给出，便于客户端工装对齐）。
PNG_MAGIC_HEX = "89 50 4E 47 0D 0A 1A 0A"

#: 常见「看着像图、其实不是 PNG」的容器魔数 → 容器名。
FOREIGN_CONTAINERS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"RIFF", "RIFF/WebP"),
    (b"GIF87a", "GIF87a"),
    (b"GIF89a", "GIF89a"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF(LE)"),
    (b"MM\x00*", "TIFF(BE)"),
    (b"\x1f\x8b", "gzip(非图像)"),
)

#: 允许越权提交的角色（平台管理员做人工归档/补录；审计里显式记 bypass）。
OWNERSHIP_BYPASS_ROLES = frozenset({"admin"})


# ── 图像容器识别 ────────────────────────────────────────────────────────────
def sniff_container(raw: bytes) -> str:
    """返回载荷的容器名；PNG 返回 ``"PNG"``，认不出返回 ``"UNKNOWN"``。"""
    if raw.startswith(PNG_MAGIC):
        return "PNG"
    for magic, name in FOREIGN_CONTAINERS:
        if raw.startswith(magic):
            return name
    return "UNKNOWN"


def _hex(raw: bytes, n: int = 8) -> str:
    return " ".join(f"{b:02X}" for b in raw[:n]) or "(空)"


class FigureRejected(ValueError):
    """配图被拒（携带机器可读 code 与人读 detail）。"""

    def __init__(self, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = HTTP_STATUS.get(code, 422)
        self.extra = extra

    def as_detail(self) -> dict[str, Any]:
        """FastAPI ``HTTPException(detail=...)`` 的 JSON 体（含期望魔数）。"""
        out = {
            "code": self.code,
            "message": self.detail,
            "expected_magic": PNG_MAGIC_HEX,
            "expected_container": "PNG",
        }
        out.update(self.extra)
        return out


def validate_png_payload(name: str, png_base64: str | bytes) -> bytes:
    """把 Base64 配图解码并做容器/完整性校验，返回原始 PNG 字节。

    判据（逐条机器可裁）：
      1. base64 必须能严格解码（``validate=True``）；
      2. 首八字节必须等于 PNG 签名；JPEG/WebP/GIF/BMP 等 → ``NOT_PNG``，
         错误信息同时给出**期望魔数**（十六进制）与**实测**魔数；
      3. 流必须以带 CRC 的 IEND 块收尾，否则 → ``TRUNCATED``。
    """
    if isinstance(png_base64, bytes):
        b64 = png_base64.decode("ascii", "replace")
    else:
        b64 = png_base64 or ""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FigureRejected(
            CODE_FIGURE_NOT_PNG,
            f"{name}: base64 无法解码（{type(exc).__name__}）；期望 PNG 魔数 {PNG_MAGIC_HEX}",
            got_container="INVALID_BASE64",
            got_magic="(n/a)",
        ) from exc

    if len(raw) < len(PNG_MAGIC):
        raise FigureRejected(
            CODE_FIGURE_TRUNCATED,
            f"{name}: 载荷仅 {len(raw)} 字节，连 PNG 签名都不完整；"
            f"期望 PNG 魔数 {PNG_MAGIC_HEX}",
            got_container=sniff_container(raw),
            got_magic=_hex(raw),
            size=len(raw),
        )

    if not raw.startswith(PNG_MAGIC):
        container = sniff_container(raw)
        raise FigureRejected(
            CODE_FIGURE_NOT_PNG,
            f"{name}: 载荷是 {container}，不是 PNG；"
            f"期望魔数 {PNG_MAGIC_HEX}（八字节），实测 {_hex(raw)}。"
            f"请用 Pillow: im.convert('RGB').save(path, format='PNG')",
            got_container=container,
            got_magic=_hex(raw),
            size=len(raw),
        )

    if not raw.endswith(PNG_IEND_CHUNK):
        # IEND 必须带 CRC 收尾：允许其后没有数据，也允许末段出现（不宽容后置垃圾）。
        if PNG_IEND_CHUNK not in raw:
            raise FigureRejected(
                CODE_FIGURE_TRUNCATED,
                f"{name}: PNG 缺 IEND 段（{len(raw)} 字节被截断）；"
                f"期望魔数 {PNG_MAGIC_HEX} … 末尾 00 00 00 00 49 45 4E 44 AE 42 60 82",
                got_container="PNG",
                got_magic=_hex(raw),
                size=len(raw),
            )
    return raw


# ── 归属判定 ────────────────────────────────────────────────────────────────
def _norm_role(user: Mapping[str, Any]) -> str:
    return str(user.get("role") or "").split(".")[-1].lower()


def _identity_tokens(user: Mapping[str, Any]) -> set[str]:
    """把提交者的身份拍平成可比字符串集合（id / username / email）。"""
    out: set[str] = set()
    for key in ("id", "user_id", "username", "email"):
        val = user.get(key)
        if val not in (None, ""):
            out.add(str(val))
    return out


def resolve_mission_owner(mission: Mapping[str, Any] | None) -> str | None:
    """解析 mission 的领取者身份；无领取记录返回 None。

    优先级：``claimed_user_id``（0090 迁移后绑定的认证账号）> ``claimed_by``
    （历史字符串，可能是用户名/邮箱/worker id）。两者都空 = 未领取。
    """
    if not mission:
        return None
    claimed_uid = mission.get("claimed_user_id")
    if claimed_uid not in (None, ""):
        return str(claimed_uid)
    claimed_by = mission.get("claimed_by")
    if claimed_by not in (None, ""):
        return str(claimed_by)
    return None


def ownership_verdict(
    user: Mapping[str, Any],
    mission: Mapping[str, Any] | None,
    *,
    mission_id: int | None,
    org_id: int | None = None,
) -> dict[str, Any]:
    """判定「这个 token 能不能把报告挂到这个 mission_id 上」。

    返回值（永不抛异常，便于 CLI / 审计复用）::

        {"ok": True,  "code": None, "status": 200, "owner": ..., "bypass": None}
        {"ok": False, "code": "REPORT_MISSION_NOT_OWNED", "status": 403, ...}

    规则（服务端结构性阻断，不依赖客户端自觉）：
      * ``mission_id is None`` → 放行（报告不挂单，与旧行为一致）；
      * mission 不存在 / 跨组织 → 404 ``REPORT_MISSION_NOT_FOUND``；
      * 提交者角色在 ``OWNERSHIP_BYPASS_ROLES`` → 放行并记 ``bypass``（可审计）；
      * mission 有领取者且领取者身份 ∉ 提交者身份集合 → 403 ``NOT_OWNED``；
      * mission 无领取记录（queued / 人工播种）→ 403 ``NOT_OWNED``：
        没有领取记录就没有「谁该交这份报告」的证据，落库即为不可归属数据。
    """
    tokens = _identity_tokens(user)
    role = _norm_role(user)
    base = {
        "mission_id": mission_id,
        "submitter": sorted(tokens),
        "role": role,
    }

    if mission_id is None:
        return {"ok": True, "code": None, "status": 200, "owner": None,
                "bypass": None, **base}

    if mission is None:
        return {"ok": False, "code": CODE_MISSION_NOT_FOUND,
                "status": HTTP_STATUS[CODE_MISSION_NOT_FOUND], "owner": None,
                "bypass": None, "message": f"mission {mission_id} 不存在",
                **base}

    mission_org = mission.get("organization_id")
    if org_id is not None and mission_org is not None and int(mission_org) != int(org_id):
        # 跨组织一律按「不存在」处理，避免存在性探测。
        return {"ok": False, "code": CODE_MISSION_NOT_FOUND,
                "status": HTTP_STATUS[CODE_MISSION_NOT_FOUND], "owner": None,
                "bypass": None,
                "message": f"mission {mission_id} 不属于当前组织", **base}

    owner = resolve_mission_owner(mission)
    if owner is None:
        return {"ok": False, "code": CODE_MISSION_NOT_OWNED,
                "status": HTTP_STATUS[CODE_MISSION_NOT_OWNED], "owner": None,
                "bypass": None,
                "message": (f"mission {mission_id} 无领取记录（status="
                            f"{mission.get('status')!r}）：报告必须由领取者提交"),
                **base}

    if owner in tokens:
        return {"ok": True, "code": None, "status": 200, "owner": owner,
                "bypass": None, **base}

    if role in OWNERSHIP_BYPASS_ROLES:
        return {"ok": True, "code": None, "status": 200, "owner": owner,
                "bypass": f"role:{role}", **base}

    return {"ok": False, "code": CODE_MISSION_NOT_OWNED,
            "status": HTTP_STATUS[CODE_MISSION_NOT_OWNED], "owner": owner,
            "bypass": None,
            "message": (f"mission {mission_id} 的领取者是 {owner}，"
                        f"与提交者 {sorted(tokens)} 不一致（拒绝静默落库）"),
            **base}


__all__ = [
    "CODE_FIGURE_NOT_PNG",
    "CODE_FIGURE_TRUNCATED",
    "CODE_MISSION_NOT_FOUND",
    "CODE_MISSION_NOT_OWNED",
    "FOREIGN_CONTAINERS",
    "FigureRejected",
    "HTTP_STATUS",
    "OWNERSHIP_BYPASS_ROLES",
    "PNG_IEND_CHUNK",
    "PNG_MAGIC",
    "PNG_MAGIC_HEX",
    "ownership_verdict",
    "resolve_mission_owner",
    "sniff_container",
    "validate_png_payload",
]