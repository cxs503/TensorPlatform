"""gate.json 证据契约（验收门证据契约成熟化，零领域语义）。

模块分工
--------
* :mod:`tools.gate_contract.contract`  —— 契约结构/摘要/指针解析/算子（纯函数）
* :mod:`tools.gate_contract.verify`    —— gate_verify：结构→存在性→sha256→阈值 复算
* :mod:`tools.gate_contract.ledger`    —— 判定台账（SQLite，逐判据一行，可 SQL 回溯）
* :mod:`tools.gate_contract.backfill`  —— 由注入式 claims 数据回填 gate.json + 权威判据登记表
* :mod:`tools.gate_contract.negcontrol`—— 五类伪证据注入式负控 + 假通过率计量
* :mod:`tools.gate_contract.acceptance`—— dev_acceptance 强制前门 + 补证单

纪律：本包不含任何域词条/域阈值；判据 ID、算子、阈值、单位、工件路径全部由
claims/registry 数据注入，代码只认结构。
"""

from .contract import (CLAIMS_SCHEMA_VERSION, OPS, REGISTRY_SCHEMA_VERSION,
                       SCHEMA_VERSION, ContractError, canonical_json,
                       canonical_sha256, env_fingerprint, sha256_bytes,
                       sha256_file)
from .verify import PASS, FAIL, WITHHELD, verify_gate

__all__ = [
    "CLAIMS_SCHEMA_VERSION", "OPS", "REGISTRY_SCHEMA_VERSION", "SCHEMA_VERSION",
    "ContractError", "canonical_json", "canonical_sha256", "env_fingerprint",
    "sha256_bytes", "sha256_file", "PASS", "FAIL", "WITHHELD", "verify_gate",
]
