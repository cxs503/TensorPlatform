"""交付面卫生（delivery hygiene）：未合并 agent 分支的内容等价三分 + 处置动作 + 只减不增门。

本包只处理 **git / 交付元数据**，不含任何领域词条、领域判据或领域算例：
它的输入是「仓库路径 + ref 模式 + base 名」，输出是「逐分支分类账本 + 处置动作计划 + 门裁决」。

与既有 ``tools/branch_ledger.py`` 的关系（不重复造轮子，也不越界）
--------------------------------------------------------------
* ``branch_ledger`` 是**只读**台账：把它自己的分支分成 ANCHOR / BANKED / CONTENT_SAME / UNIQUE，
  用于人工判断「哪些分支值得先合并」。它明确声明「不建/删/改任何 ref」。
* 本包解决的是台账之后的**闭环**问题：
  1. 四类内容等价判定 A/B/C/D（新增 **B=被另一分支超集覆盖**，这是 ``UNIQUE`` 内部最大的熵源：
     同一批提交被 rebase / 重复 cherry-pick 成多条分支，``branch_ledger`` 会把它们全记成 UNIQUE）；
  2. 机器可读账本（``{repo, branch, class, evidence, decided_by, safe_to_close}``）；
  3. 处置动作：A/D → 归档到 ``refs/attic/`` 并删分支；B → 标注超集来源；C → 开交付补单；
  4. **只减不增门**：基线棘轮（ceiling 只能降不能升）+ CI 拦截。

四类判据与优先级（详见 :mod:`tools.delivery_hygiene.classify`）
--------------------------------------------------------------
===========  ====================================================  ============
类           判据                                                  可安全关闭
===========  ====================================================  ============
``D`` 孤儿    ``base..branch`` 无提交（tip 已在 base 历史内）        是
``A`` 已覆盖  ``unique==0``（patch-id 全命中 base）或净 diff 为空    是
``B`` 超集    独有 patch-id 是另一分支独有集的真子集/等集            是（内容在 S 里）
``C`` 未交付  独有 patch-id 既不在 base 也不在任何其他分支           否（开补单）
===========  ====================================================  ============

纪律
----
* 默认 **dry-run**：只有显式 ``--apply --i-mean-it`` 才会动 ref；动之前先建 ``refs/attic/`` 归档
  并回读校验，再删分支，最后写 manifest（可回滚：``git update-ref refs/heads/<b> <sha>``）。
* 不改 ``.git`` 配置、不改写历史、不 push。
* 零第三方依赖（matplotlib 仅 ``--figures`` 时按需导入）。
"""
from __future__ import annotations

__all__ = [
    "CLASS_A",
    "CLASS_B",
    "CLASS_C",
    "CLASS_D",
    "CLASSES",
    "CLASS_LABELS",
    "SCHEMA_VERSION",
]

# 类代号：A=内容已被 main 覆盖 / B=被另一分支超集覆盖 / C=真未交付 / D=孤儿
CLASS_A = "A"
CLASS_B = "B"
CLASS_C = "C"
CLASS_D = "D"
CLASSES = (CLASS_A, CLASS_B, CLASS_C, CLASS_D)

CLASS_LABELS = {
    CLASS_A: "covered-by-main",
    CLASS_B: "covered-by-superset-branch",
    CLASS_C: "undelivered",
    CLASS_D: "orphan",
}

SCHEMA_VERSION = "1.0"