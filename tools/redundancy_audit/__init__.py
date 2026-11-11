"""跨战役重复劳动冗余聚类审计（P393W2-N02）。

一句话：把「战役节点任务书」当语料，用**词面相似度 + 阈值聚类**找出做同一件事的节点，
再用**节点→mission 的资源证据**折算出这些重复劳动烧掉的 wall-clock / 预算，逐簇给出
keep / merge / archive 处置建议。

模块分工
--------
* :mod:`tools.redundancy_audit.config`   —— 配置装载（fail-closed，无内置默认阈值）
* :mod:`tools.redundancy_audit.collect`  —— 平台只读 API → 快照（sha256 钉死）
* :mod:`tools.redundancy_audit.index`    —— 字符 n-gram 倒排 + 长度过滤 + 上界剪枝（近似最近邻）
* :mod:`tools.redundancy_audit.cluster`  —— 阈值聚类（强边连通分量）与 keep/merge/archive
* :mod:`tools.redundancy_audit.ledger`   —— 成本归集、簇级冗余成本、独立复算
* :mod:`tools.redundancy_audit.labeling` —— 100 对标注集（规则 + 人工复核通道）、阈值扫描与冻结
* :mod:`tools.redundancy_audit.report`   —— 编排：快照 → 报告 JSON（确定性、幂等）
* :mod:`tools.redundancy_audit.figures`  —— 阈值敏感性与簇成本图
* :mod:`tools.redundancy_audit.markdown` —— 论文级 markdown（零手抄）

快速重放
--------
    python -m tools.redundancy_audit.collect            # 重采快照（只读 GET，需 API 凭据）
    python -m tools.redundancy_audit.report --write-labels
    python -m tools.redundancy_audit.markdown           # 图 + markdown
    python tools/audit/domain_leak_scan.py --paths tools/redundancy_audit \\
        tests/test_redundancy_audit.py config/redundancy_audit.json
    pytest tests/test_redundancy_audit.py -q
"""

__all__ = ["config", "collect", "index", "cluster", "ledger", "labeling", "report", "figures", "markdown"]