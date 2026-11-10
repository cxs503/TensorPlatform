"""knowledge_distill —— 跨战役知识蒸馏引擎（域无关机制层）。

把已交付节点/战役的验收报告读成**结构化证据条目**（局限 / 下一步 / honest FAIL），
映射到特征空间（TF-IDF 字符 n-gram + 子词哈希嵌入），聚类成候选提案骨架，经新颖性
门控后输出到**提案种子池**，并给出逐条目 sha256 溯源链。

五个纯机制（每个都可单独调用、单独测试）：

1. :func:`extract_report` / :func:`parse_reports` —— 报告解析与覆盖率核对：
   结构化 JSON 字段（``limitation`` / ``next_step`` / ``honest_fail`` 及其别名）与
   markdown 小节（标题关键字经配置注入）双通道；解析失败降级为 ``unparsable`` 而不是抛栈。

2. :class:`FeatureSpace`（``features``）—— TF-IDF（字符 n-gram）+ 子词哈希嵌入
   （确定性、无随机种子、无外部模型）；两者余弦相似度可加权组合。

3. :func:`dbscan`（``cluster``）—— 余弦距离 DBSCAN，输入顺序决定输出顺序
   （同一输入逐字节可复现）；``auto_eps`` 用 k-距离分位点自适应选邻域半径。

4. :func:`gate`（``novelty``）—— 新颖性门控：与既有提案池/已验证案例做
   Jaccard（n-gram）与余弦比较，``novelty = 1 - max_sim`` 高于阈值且**平均相似度**
   低于上限才放行（平均口径是硬约束，避免"一条像、其余不像"式放水）。

5. :func:`distill`（``pipeline``）—— 端到端：解析 → 特征 → 聚类 → 骨架 → 门控 →
   溯源链（每个种子带来源节点 ID / 原始引用文本 / sha256，运行级 manifest 逐文件校验和）。

纪律
----
* 零领域语义：本包不含任何物理域词条；域/仓提示一律由报告字段注入，
  缺字段时如实记为 ``None``（WITHHELD），不猜。
* 纯函数 + 显式参数：阈值全部走 :class:`DistillParams`/``--config``，逻辑里无魔法数。
* 输入只读、输出只写调用方指定目录；同一输入两次运行逐字节相同。
"""

from __future__ import annotations

from .cluster import auto_eps, cluster_profiles, dbscan, medoid, purity
from .extract import extract_report, parse_reports, report_sha256
from .features import FeatureSpace, HashedEmbedding, Tfidf, cosine, ngram_jaccard
from .novelty import ReferenceCorpus, gate
from .params import DistillParams
from .pipeline import distill, seed_digest, write_outputs

__all__ = [
    "DistillParams",
    "FeatureSpace",
    "HashedEmbedding",
    "ReferenceCorpus",
    "Tfidf",
    "auto_eps",
    "cluster_profiles",
    "cosine",
    "dbscan",
    "distill",
    "extract_report",
    "gate",
    "medoid",
    "ngram_jaccard",
    "parse_reports",
    "purity",
    "report_sha256",
    "seed_digest",
    "write_outputs",
]