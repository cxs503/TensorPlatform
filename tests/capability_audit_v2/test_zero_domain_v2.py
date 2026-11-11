"""零领域语义回归防护：新增代码/测试对仓内域词表必须 0 命中。

判例 #608：平台机制层不得硬编码任何域词条/域判据/域算例。本测试直接复用仓内
扫描器（config/domain_word_denylist.json 是数据），把"零领域语义"从口号变成红灯。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("tools/capability_audit_v2", "tests/capability_audit_v2")


def test_new_code_has_zero_domain_hits(tmp_path):
    out = tmp_path / "scan.json"
    proc = subprocess.run(
        [sys.executable, "tools/audit/domain_leak_scan.py", "--paths", *TARGETS,
         "--json", str(out), "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    assert out.is_file(), proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hit_count"] == 0, data.get("hits")
    assert proc.returncode == 0
    assert len(data["files_scanned"]) >= 8