#!/usr/bin/env python3
"""对抗样例生成器：凭证样例**运行时拼接**，不把可疑字面量落进仓库。

为什么不在仓库里放样例原文：一旦把"看起来像密钥"的字符串写成字面量，仓库本身
就多了一处需要审计的可疑内容（GitHub secret scanning 也会报警）。这里只保留
无害片段，完整样例在进程内拼出，既可用于测试也可用于验收跑批。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple


def _join(*parts: str) -> str:
    return "".join(parts)


def private_key_header() -> str:
    return _join("-----BEGIN ", "PRIVATE ", "KEY", "-----")


def credential_assignment() -> str:
    return _join('API_', 'KEY = "', "A1b2C3d4" * 3, '"')


def url_with_password() -> str:
    return _join("https://ci-runner:", "Zx9" * 4, "@ci.example.org/run")


def jwt_bearer() -> str:
    return ".".join([_join("eyJ", "hbGciOiJIUzI1NiJ9"),
                     _join("eyJ", "zdWIiOiIxMjM0NTY3ODkw"),
                     "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"])


def cloud_access_key_id() -> str:
    return _join("AKIA", "Q7RTY2PLMNBVCXZ3")


def high_entropy_blob() -> str:
    return "Xk9Qm2Lp7Rw4Tz8Vb3Nc6Hd1Jf5Gs0YaE3Ut5Ip1Ov7Bn9"


#: (规则 id, 样例工厂)。前三条为机器判据要求的三种泄漏面，后三条为扩展面。
SAMPLES: List[Tuple[str, Callable[[], str]]] = [
    ("private_key_block", private_key_header),
    ("credential_assignment", credential_assignment),
    ("url_credentials", url_with_password),
    ("jwt_bearer", jwt_bearer),
    ("cloud_access_key_id", cloud_access_key_id),
    ("high_entropy_blob", high_entropy_blob),
]

REQUIRED_SECRET_RULES = ("private_key_block", "credential_assignment", "url_credentials")


def all_samples() -> List[Tuple[str, str]]:
    return [(rule_id, factory()) for rule_id, factory in SAMPLES]


def sample_by_rule() -> Dict[str, str]:
    return {rule_id: factory() for rule_id, factory in SAMPLES}