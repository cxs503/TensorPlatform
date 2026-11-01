"""对船舶总体性能平台 (:8001) 操纵性端点的在线符合性探针.

设计原则
--------
1. **只读**：仅发 POST 计算请求，不写任何服务端状态。
2. **不因失败而中断**：5xx / 超时都记为 ``error``，由判据层升级为 BROKEN，
   这是"端点必崩"这类缺陷的唯一可靠证据来源。
3. **可离线复用**：探针结果落 JSON（``probe_matrix.json``），判据层可在无
   网络时对同一份 JSON 反复裁定（CI 里跑判据，不跑网络）。

环境变量
--------
``SHIP_API_BASE``  默认 ``http://127.0.0.1:8001``（船舶平台）
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")

# 平台自带语料（backend/data/test_data/maneuvering_kcs.json, SIMMAN 2008/2014）
KCS_PARTICULARS = dict(L=230.0, B=32.2, T=10.8, Cb=0.651, GM=0.60, kzz_over_Lpp=0.25,
                       speed_kn=24.0, Dp=7.6, Ar=115.0, displacement=52030.0)
# KVLCC2（SIMMAN 2008 基准船）
KVLCC2_PARTICULARS = dict(L=320.0, B=58.0, T=20.8, Cb=0.8098, KG=18.6,
                          Dp=9.86, n=1.6, Ar=112.5, speed_kn=15.5, displacement=312622.0)


@dataclass
class Probe:
    """一次探针：入参 + 返回或错误。"""
    endpoint: str
    label: str
    input: Dict
    result: Optional[Dict] = None
    status: Optional[int] = None
    error: str = ""
    elapsed_s: float = 0.0

    def as_dict(self) -> Dict:
        return dict(endpoint=self.endpoint, label=self.label, input=self.input,
                    result=self.result, status=self.status, error=self.error,
                    elapsed_s=round(self.elapsed_s, 4))


def _post(base: str, path: str, body: Dict, timeout: float = 60.0) -> Probe:
    import time
    url = base.rstrip("/") + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            payload = json.loads(f.read().decode())
            return Probe(path, "", body, result=payload, status=f.status,
                         elapsed_s=time.time() - t0)
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        return Probe(path, "", body, status=e.code,
                     error=f"HTTP {e.code}: {body_txt}", elapsed_s=time.time() - t0)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return Probe(path, "", body, error=f"unreachable/timeout: {e}",
                     elapsed_s=time.time() - t0)
    except Exception as e:  # pragma: no cover - 防御
        return Probe(path, "", body, error=f"{type(e).__name__}: {e}",
                     elapsed_s=time.time() - t0)


def base_body(particulars: Dict, rudder_angle: float, speed_override: Optional[float] = None) -> Dict:
    """构造 /maneuvering/* 的请求体（字段名按平台 ManeuveringRequest）。"""
    spd = speed_override if speed_override is not None else particulars["speed_kn"]
    return dict(L=particulars["L"], B=particulars["B"], T=particulars["T"],
                displacement=particulars["displacement"], Cb=particulars["Cb"],
                KG=particulars.get("KG", 0.5 * particulars["T"] + 0.6 * particulars["B"] / 2),
                Dp=particulars["Dp"], Ar=particulars["Ar"],
                speed=spd, rudder_angle=rudder_angle)


def mmg_body(particulars: Dict, rudder_angle: float, speed_override: Optional[float] = None) -> Dict:
    """构造 /maneuvering-mmg/* 的请求体（字段名按平台 TurningTestInput）。"""
    b = base_body(particulars, rudder_angle, speed_override)
    b.pop("KG", None)
    v_ms = b.pop("speed") * 0.5144          # kn -> m/s
    b["V0"] = v_ms
    b["n"] = particulars.get("n", 1.6)
    return b


def build_turning_matrix(particulars: Dict, endpoints: Optional[List[str]] = None) -> List[Dict]:
    """回转探针矩阵：舵角扫描 + 三个物理参数的缩放扫描（用于证伪响应性）。"""
    endpoints = endpoints or ["/api/v1/maneuvering/turning", "/api/v1/maneuvering-mmg/turning"]
    rows: List[Dict] = []
    L = particulars["L"]
    for ep in endpoints:
        maker = mmg_body if "maneuvering-mmg" in ep else base_body
        for ang in (5.0, 10.0, 20.0, 35.0):
            rows.append(dict(endpoint=ep, label=f"rudder_{int(ang)}deg",
                             input=maker(particulars, ang)))
        for mult in (0.5, 1.0, 3.0):
            b = maker(particulars, 35.0)
            b["Ar"] = particulars["Ar"] * mult
            rows.append(dict(endpoint=ep, label=f"Ar_x{mult}", input=b))
        for mult in (0.7, 1.5):
            b = maker(particulars, 35.0)
            b["displacement"] = particulars["displacement"] * mult
            rows.append(dict(endpoint=ep, label=f"disp_x{mult}", input=b))
        for mult in (0.7, 1.5):
            b = maker(particulars, 35.0)
            if "maneuvering-mmg" in ep:
                b["V0"] = b["V0"] * mult
            else:
                b["speed"] = b["speed"] * mult
            rows.append(dict(endpoint=ep, label=f"speed_x{mult}", input=b))
    return rows


def build_zigzag_matrix(particulars: Dict,
                        endpoints: Optional[List[str]] = None) -> List[Dict]:
    endpoints = endpoints or ["/api/v1/maneuvering/zigzag",
                              "/api/v1/maneuvering-mmg/zigzag"]
    rows: List[Dict] = []
    for ep in endpoints:
        maker = mmg_body if "maneuvering-mmg" in ep else base_body
        for ang in (10.0, 20.0):
            b = maker(particulars, ang)
            if "maneuvering-mmg" in ep:
                b["heading_angle"] = ang
            rows.append(dict(endpoint=ep, label=f"zigzag_{int(ang)}_{int(ang)}", input=b))
        for mult in (0.7, 1.5):
            b = maker(particulars, 20.0)
            if "maneuvering-mmg" in ep:
                b["heading_angle"] = 20.0
            b["displacement"] = particulars["displacement"] * mult
            rows.append(dict(endpoint=ep, label=f"zigzag20_disp_x{mult}", input=b))
        for mult in (0.7, 1.5):
            b = maker(particulars, 20.0)
            if "maneuvering-mmg" in ep:
                b["heading_angle"] = 20.0
                b["V0"] = b["V0"] * mult
            else:
                b["speed"] = b["speed"] * mult
            rows.append(dict(endpoint=ep, label=f"zigzag20_speed_x{mult}", input=b))
    return rows


def run_matrix(base: str, rows: List[Dict], timeout: float = 60.0) -> List[Dict]:
    """顺序执行探针矩阵（操纵性计算很轻，无需并发）。"""
    out: List[Dict] = []
    for r in rows:
        p = _post(base, r["endpoint"], r["input"], timeout=timeout)
        p.label = r["label"]
        out.append(p.as_dict())
    return out


def kt_probe(base: str, particulars: Dict) -> List[Dict]:
    """K-T 指数端点探针（同一船不同舵角 ⇒ K 不应改变，T 也不应变）。"""
    rows = []
    for ang in (35.0, 20.0, 10.0):
        p = _post(base, "/api/v1/maneuvering/kt", base_body(particulars, ang))
        p.label = f"kt_rudder_{int(ang)}"
        rows.append(p.as_dict())
    # 速度敏感：K 应随速度弱变（K 是有量纲增益 [1/s]），但不应 ∝ 1/kn 粗暴反比
    for mult in (0.7, 1.5):
        b = base_body(particulars, 35.0, speed_override=particulars["speed_kn"] * mult)
        p = _post(base, "/api/v1/maneuvering/kt", b)
        p.label = f"kt_speed_x{mult}"
        rows.append(p.as_dict())
    # 舵面积敏感：K 的物理来源含舵力 ⇒ 舵面积 3 倍必须改变 K（硬证伪点）
    for mult in (0.5, 1.0, 3.0):
        b = base_body(particulars, 35.0)
        b["Ar"] = particulars["Ar"] * mult
        p = _post(base, "/api/v1/maneuvering/kt", b)
        p.label = f"kt_Ar_x{mult}"
        rows.append(p.as_dict())
    # 方形系数敏感：K 依赖船体水动力 ⇒ Cb 变化必须改变 K
    for cb in (0.60, 0.82):
        b = base_body(particulars, 35.0)
        b["Cb"] = cb
        p = _post(base, "/api/v1/maneuvering/kt", b)
        p.label = f"kt_Cb_{cb}"
        rows.append(p.as_dict())
    return rows


def health(base: str, timeout: float = 5.0) -> Dict:
    """平台存活探测（无副作用）。"""
    for path in ("/health", "/api/v1/health", "/"):
        try:
            with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as f:
                return dict(reachable=True, path=path, status=f.status)
        except Exception:
            continue
    return dict(reachable=False)