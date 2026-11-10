"""生成能力审计 spec：``config/capability_audit_gate/spec.json``。

输入三样东西（全部是**数据**，代码零领域语义）：

1. ``config/capabilities.json`` —— 21 项能力的定义与模式匹配规则（既有注册表）；
2. ``CURATED`` —— 人审过的显式证据绑定（实现/测试路径）与基准工件口径；
3. 默认阈值 ``spec.DEFAULT_THRESHOLDS``。

用法::

    python3 -m scripts.gen_capability_audit_spec \
        --registry config/capabilities.json \
        --out config/capability_audit_gate/spec.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.capability_audit_gate import spec as spec_mod
from tools.capability_audit_gate.spec import SpecError

#: 人审证据面：能力 → 显式绑定 + 基准工件口径。
#: 只登记「读得到、可复算」的证据；未登记的能力退回路径名模式匹配，最多判 WITHHELD。
CURATED_PATH = Path(__file__).resolve().parents[1] / "config" / "capability_audit_gate" / "curated.json"


def load_curated(path: Path) -> dict[str, dict]:
    """人审证据面是**数据**：域内容（能力 id/路径/基准口径）全部在 curated.json。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    caps = raw.get("capabilities")
    if not isinstance(caps, dict):
        raise SpecError("curated.json 必须是 {capabilities: {id: {...}}}")
    return caps


def build(registry_path: Path, curated_path: Path) -> dict:
    CURATED = load_curated(curated_path)
    base = spec_mod.spec_from_registry(registry_path)
    caps = []
    for cap in base["capabilities"]:
        patch = CURATED.get(cap["id"], {})
        caps.append({
            "id": cap["id"],
            "bindings": patch.get("bindings", {}),
            "patterns": cap["patterns"],
            "benchmark": patch.get("benchmark"),
            "expected_level": None,
        })
    support = spec_mod.validate_spec({
        "schema_version": "1.0",
        "title": "能力台账自动审计 spec（注册表 + 人审证据面）",
        "thresholds": {},
        "capabilities": caps,
    })
    return support


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成能力审计 spec")
    ap.add_argument("--registry", default="config/capabilities.json")
    ap.add_argument("--curated", default="config/capability_audit_gate/curated.json")
    ap.add_argument("--out", default="config/capability_audit_gate/spec.json")
    args = ap.parse_args(argv)
    built = build(Path(args.registry), Path(args.curated))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(built, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    curated = sum(1 for c in built["capabilities"] if c["bindings"])
    bench = sum(1 for c in built["capabilities"] if c["benchmark"])
    print(f"spec -> {out}: {len(built['capabilities'])} capabilities, "
          f"{curated} with explicit bindings, {bench} with benchmark definitions")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())