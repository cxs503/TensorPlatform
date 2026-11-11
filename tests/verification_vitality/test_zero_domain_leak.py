"""零领域语义回归防护：本节点新增的代码/测试/脚本对仓内域词表必须 0 命中。

机制层（巡检/交叉/升账）是共性能力：换一份 ``config/capabilities.json`` 就该换一套
能力拓扑，因此包里不得出现任何域词条。这条测试把"零领域语义"从口号变成红灯——
判例 #608 的同类防线（见 ``tests/capability_audit_v2/test_zero_domain_v2.py``）。

域词表是数据（``config/domain_word_denylist.json``），扫描器是仓内工具
（``tools/audit/domain_leak_scan.py``），本测试只负责把两者接到本节点的路径上。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "src/verification_vitality",
    "tests/verification_vitality",
    "scripts/p491w2_n01_vitality_artifacts.py",
    "scripts/p491w2_n01_render_doc.py",
    "scripts/p491w2_n01_report.py",
)


def test_new_code_has_zero_domain_hits(tmp_path):
    scanner = ROOT / "tools" / "audit" / "domain_leak_scan.py"
    if not scanner.is_file():
        return  # 扫描器不在本仓时跳过（不制造假红灯）
    out = tmp_path / "scan.json"
    proc = subprocess.run([sys.executable, str(scanner), "--paths", *TARGETS,
                           "--json", str(out), "--quiet"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=240)
    assert out.is_file(), proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hit_count"] == 0, data.get("hits")
    assert len(data["files_scanned"]) >= 10