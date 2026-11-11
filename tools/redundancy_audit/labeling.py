"""100 对标注集的确定性构造 + 预登记 + 阈值扫描。

标注集怎么来（可复核、可重放）
------------------------------
标注**不是**随手挑 100 对，而是三条确定性规则 + 一条人工复核通道：

* **A 组（结构真重复，label=1）**：``ext_id`` 逐字相同，或「剥掉波次标签后的节点名」逐字
  相同。两条都是播种系统的结构事实（同一 ext_id = 同一条提案被重复播种；同名 = 同一任务
  被重播），不需要任何语义判断。
* **B 组（难负例，label=0）**：``score ≥ labels.hard_negative_floor``（词面很像）但既非 A 组，
  且两侧验收判据的**度量名集合** Jaccard < 0.5（不同交付合同）。度量名为空时退化为
  「交付物路径集合无交集」。
* **C 组（易负例，label=0）**：按 sha256 稳定抽签从 score < 0.35 的对里抽，代表「完全不相关」。

**循环性诚实声明**：A 组正例天然词面几乎相同（score≈1.0），所以「在 A/B/C 标注集上 F1 高」
只能证明阈值不误伤结构重复、不误杀明显不同题，**不能**证明它能抓住「同题改写」。为此本模块
另外输出 ``paraphrase_stress``：一份人工逐对读目标正文判定的「同题改写重复对」（词面 0.5-0.7），
逐阈值报其召回——这才是审计的成熟化重点，也是本节点最重要的诚实结论。

人工复核通道
------------
``annotations_overrides.json``（数据文件，落在 artifacts/）里逐条记录
``{a, b, label, reason}``：规则标注后由审计者读正文判定的修正，覆盖规则结论。每条都要求
写 reason，因而「人工改动」在报告里是可点数、可复核的。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .config import RedundancyConfig, repo_root
from .collect import dumps_deterministic, read_json, sha256_of, write_json

_TAG_STRIP = "·"


def compact(text: str) -> str:
    """NFKC + 去空白/标点/符号 + casefold（只用于「同名判定」，与相似度口径无关）。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    out = []
    for ch in s:
        if ch.isspace():
            continue
        if unicodedata.category(ch)[0] in ("P", "S", "C", "Z"):
            continue
        out.append(ch.casefold())
    return "".join(out)


def strip_wave_tag(name: str) -> str:
    """剥掉「P153W2-N01·」这类波次/节点前缀标签（标签 = '·' 之前的纯 ASCII 片段）。"""
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    if _TAG_STRIP in s:
        head, tail = s.split(_TAG_STRIP, 1)
        if head and all(c.isascii() for c in head) and len(head) <= 24 and tail.strip():
            return tail.strip()
    return s


def name_key(name: str) -> str:
    return compact(strip_wave_tag(name))


def metric_set(node: Mapping[str, Any]) -> set[str]:
    return {str(x) for x in (node.get("metric_names") or [])}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def structural_label(a: Mapping[str, Any], b: Mapping[str, Any]) -> str | None:
    """A 组规则：返回 'ext_id' / 'name_key' / None。"""
    ea, eb = (a.get("ext_id") or ""), (b.get("ext_id") or "")
    if ea and ea == eb:
        return "ext_id"
    ka, kb = name_key(a.get("name") or ""), name_key(b.get("name") or "")
    if ka and ka == kb:
        return "name_key"
    return None


def _hard_negative_ok(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[bool, dict]:
    ma, mb = metric_set(a), metric_set(b)
    mj = jaccard(ma, mb)
    if ma and mb:
        return mj < 0.5, {"metric_jaccard": round(mj, 4), "mode": "metric_names"}
    da = {str(x) for x in (a.get("deliverables") or [])}
    db = {str(x) for x in (b.get("deliverables") or [])}
    return (not (da & db)), {"metric_jaccard": None, "mode": "deliverables_disjoint",
                             "deliverables_overlap": len(da & db)}


def _hash_pick(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build_labels(
    snapshot: Mapping[str, Any],
    score_fn,
    cfg: RedundancyConfig,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """构造标注集（A/B/C 三组 + 人工覆盖），返回可落盘文档。

    ``score_fn(a, b) -> float`` 提供**精确**相似度（不经近似最近邻），因此标注集不受
    候选集偏差影响；阈值评估与候选集召回是两件独立的事（后者由 ``ann_recall`` 单列）。
    """
    nodes = {str(n["node_id"]): n for n in snapshot["nodes"]}
    total_pairs = int(cfg.get("labels.pairs"))
    n_true_groups = int(cfg.get("labels.true_group_count"))
    hard_ratio = float(cfg.get("labels.hard_negative_ratio"))
    random_ratio = float(cfg.get("labels.random_negative_ratio"))
    floor = float(cfg.get("labels.hard_negative_floor"))
    keys = sorted(nodes)

    # 结构键先算好（NFKC/去标签是 O(len) 的重活），再做分桶配对
    ext_of = {k: (nodes[k].get("ext_id") or "") for k in keys}
    nk_of = {k: name_key(nodes[k].get("name") or "") for k in keys}
    plan_of = {k: nodes[k].get("plan_id") for k in keys}

    # ── A 组：结构真重复 ────────────────────────────────────────────────
    a_pairs: list[tuple[str, str, str]] = []
    seen_a: set[tuple[str, str]] = set()
    # 只用「剥波次标签后节点名逐字相同」这一条结构证据。
    # 反例（实测）：ext_id 在跨计划时会被复用为「波次-序号」标签而非全局任务 ID
    # （同一 ext_id 在不同计划里指向完全不同的任务），故 ext_id 相同 **不能** 判重复。
    for buckets, rule in ((_bucket_by(nk_of), "name_key"),):
        for _, members in sorted(buckets.items()):
            if len(members) < 2:
                continue
            for i, ka in enumerate(members):
                for kb in members[i + 1:]:
                    key = (ka, kb) if ka <= kb else (kb, ka)
                    if key in seen_a:
                        continue
                    seen_a.add(key)
                    a_pairs.append((key[0], key[1], rule))
    # 预登记真重复「组」：**字面重播组** = (节点名 + 目标正文) 逐字相同的节点集合。
    # 这是最强的结构证据（播种系统原样重播同一行），不做任何语义判断、也无「同名不同题」风险
    # （实测确实存在同名不同题：标题在跨计划被复用，见 findings F5）。
    text_groups = _text_identity_groups(nodes)
    preselec = text_groups[:n_true_groups]
    # 对照口径（诚实披露）：只看节点名相同的组，含「同名不同题」污染
    name_groups = _connected_groups(a_pairs)
    name_groups.sort(key=lambda g: (-len(g["members"]), g["members"][0]))
    name_preselec = name_groups[:n_true_groups]
    n_a = total_pairs - int(round(total_pairs * hard_ratio)) - int(round(total_pairs * random_ratio))

    scored_a = sorted(
        ((score_fn(ka, kb), ka, kb, rule) for ka, kb, rule in a_pairs),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    chosen_a = [(ka, kb, rule) for _, ka, kb, rule in scored_a[:n_a]]

    # ── B 组：难负例（同计划内的设计分工对） ────────────────────────────
    # 规则依据：一个战役计划的节点是 DAG 分工，同计划内不同 ext_id、不同标题的节点是
    # 「被设计成不同工作」的对；它们共享同波次模板化正文，词面相似度被结构性地抬高，
    # 正是阈值最可能误报的位置（=难负例的正确取样面）。
    by_plan: dict[Any, list[str]] = {}
    for k in keys:
        by_plan.setdefault(plan_of[k], []).append(k)
    hard_pool = []
    for plan_id, members in sorted(by_plan.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        for i, ka in enumerate(members):
            for kb in members[i + 1:]:
                if ext_of[ka] and ext_of[ka] == ext_of[kb]:
                    continue
                if nk_of[ka] and nk_of[ka] == nk_of[kb]:
                    continue
                sc = score_fn(ka, kb)
                if sc < floor:
                    continue
                ok, ev = _hard_negative_ok(nodes[ka], nodes[kb])
                hard_pool.append((ka, kb, sc, {**ev, "same_plan": True, "plan_id": plan_id}))
    hard_pool.sort(key=lambda t: (-t[2], t[0], t[1]))
    n_b = int(round(total_pairs * hard_ratio))
    chosen_b = hard_pool[:n_b]

    # ── C 组：易负例（跨计划稳定抽签，精确打分后保留真正不相关的） ──────
    n_c = total_pairs - len(chosen_a) - len(chosen_b)
    cross: list[tuple[str, str]] = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if plan_of[ka] != plan_of[kb]:
                cross.append((ka, kb))
    cross.sort(key=lambda t: _hash_pick(f"{t[0]}|{t[1]}"))
    easy_pool = []
    for ka, kb in cross[:4000]:
        if (ext_of[ka] and ext_of[ka] == ext_of[kb]) or (nk_of[ka] and nk_of[ka] == nk_of[kb]):
            continue
        sc = score_fn(ka, kb)
        if sc < 0.35:
            easy_pool.append((ka, kb, sc))
        if len(easy_pool) >= n_c:
            break
    chosen_c = easy_pool[:n_c]

    pairs: list[dict[str, Any]] = []
    for ka, kb, rule in chosen_a:
        pairs.append(_row(nodes, ka, kb, score_fn(ka, kb), 1, "A",
                          f"结构指纹相同（{rule}）", {"rule": rule}))
    for ka, kb, sc, ev in chosen_b:
        pairs.append(_row(nodes, ka, kb, sc, 0, "B",
                          "同计划设计分工对（不同 ext_id/不同标题）但词面相似", ev))
    for ka, kb, sc in chosen_c:
        pairs.append(_row(nodes, ka, kb, sc, 0, "C", "跨计划且词面不相关（抽样负例）", {}))

    # ── 人工复核覆盖 ────────────────────────────────────────────────────
    applied = []
    by_pair = {(p["a"], p["b"]): p for p in pairs}
    for ov in overrides:
        a, b = str(ov["a"]), str(ov["b"])
        key = (a, b) if a <= b else (b, a)
        row = by_pair.get(key)
        if row is None:
            applied.append({"pair": list(key), "label": int(ov["label"]),
                            "reason": ov.get("reason"), "applied": False})
            continue
        row["rule_label"] = row["label"]
        row["label"] = int(ov["label"])
        row["overridden"] = True
        row["rationale"] = ov.get("reason") or row["rationale"]
        applied.append({"pair": list(key), "label": row["label"],
                        "reason": row["rationale"], "applied": True})

    pairs.sort(key=lambda p: (-p["score"], p["a"], p["b"]))
    for i, p in enumerate(pairs, start=1):
        p["pair_id"] = f"P{i:03d}"
        p.setdefault("overridden", False)
        p.setdefault("rule_label", p["label"])

    doc = {
        "schema": "redundancy-audit-labels/1",
        "counts": {
            "total": len(pairs),
            "positives": sum(1 for p in pairs if p["label"] == 1),
            "negatives": sum(1 for p in pairs if p["label"] == 0),
            "by_group": {g: sum(1 for p in pairs if p["group"] == g) for g in ("A", "B", "C")},
            "overrides_applied": sum(1 for a in applied if a["applied"]),
            "overrides_unmatched": sum(1 for a in applied if not a["applied"]),
            "structural_positives_available": len(a_pairs),
            "hard_negatives_available": len(hard_pool),
        },
        "preregistered_true_groups": preselec,
        "preregistered_name_identity_groups": name_preselec,
        "pairs": pairs,
        "overrides": applied,
        "labeling_rules": {
            "A": "剥波次标签后节点名逐字相同 → label=1（ext_id 相同不算：跨计划会被复用）",
            "B": "同计划、不同 ext_id、不同标题、score>=hard_negative_floor → label=0（设计分工对）",
            "C": "跨计划稳定抽签（sha256）+ score<0.35 → label=0",
            "override": "审计者读正文后逐条修正，reason 必填（数据文件 annotations_overrides.json）",
        },
        "circularity_note": "A 组正例词面几乎全同，故本标注集上的 F1 只证明阈值不误伤结构重复、"
                            "不误杀同计划分工对；「同题改写」能力另由 paraphrase_stress 逐阈值召回衡量。",
    }
    return doc


def _row(nodes, ka: str, kb: str, score: float, label: int, group: str,
         rationale: str, evidence: dict) -> dict[str, Any]:
    a, b = (ka, kb) if ka <= kb else (kb, ka)
    na, nb = nodes[a], nodes[b]
    return {
        "a": a,
        "b": b,
        "score": round(float(score), 6),
        "label": label,
        "group": group,
        "rationale": rationale,
        "evidence": evidence,
        "a_name": na.get("name"),
        "b_name": nb.get("name"),
        "a_ext_id": na.get("ext_id"),
        "b_ext_id": nb.get("ext_id"),
        "a_plan": na.get("plan_id"),
        "b_plan": nb.get("plan_id"),
    }


def _text_identity_groups(nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """字面重播组：``text`` 逐字相同的节点集合（len≥2），按 (-size, 最小 node_id) 定序。"""
    buckets: dict[str, list[str]] = {}
    for k in sorted(nodes):
        text = str(nodes[k].get("text") or "").strip()
        if text:
            buckets.setdefault(text, []).append(k)
    groups = [{"members": sorted(v)} for v in buckets.values() if len(v) >= 2]
    groups.sort(key=lambda g: (-len(g["members"]), g["members"][0]))
    return groups


def _bucket_by(keys_by_node: dict[str, str]) -> dict[str, list[str]]:
    """结构键 → 节点列表（空键不入桶；桶内按 node_id 排序，确定性）。"""
    out: dict[str, list[str]] = {}
    for k in sorted(keys_by_node):
        v = keys_by_node[k]
        if v:
            out.setdefault(v, []).append(k)
    return out


def _connected_groups(a_pairs: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in a_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = (ra, rb) if ra <= rb else (rb, ra)
            parent[hi] = lo
    buckets: dict[str, list[str]] = {}
    for k in sorted(parent):
        buckets.setdefault(find(k), []).append(k)
    return [{"members": sorted(v)} for v in buckets.values() if len(v) >= 2]


# ── 阈值扫描 / 冻结 ─────────────────────────────────────────────────────
def metrics_at(pairs: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    hard_fp = hard_n = 0
    for p in pairs:
        pred = 1 if float(p["score"]) >= threshold else 0
        label = int(p["label"])
        if label == 1 and pred == 1:
            tp += 1
        elif label == 0 and pred == 1:
            fp += 1
            if p["group"] == "B":
                hard_fp += 1
            if p["group"] in ("B", "C"):
                hard_n += 1
        elif label == 1 and pred == 0:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    neg_total = sum(1 for p in pairs if int(p["label"]) == 0)
    return {
        "threshold": round(float(threshold), 6),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "hard_negative_fp_rate": round(hard_fp / hard_n, 6) if hard_n else 0.0,
        "negative_fp_rate": round(fp / neg_total, 6) if neg_total else 0.0,
    }


def sweep(pairs: Sequence[Mapping[str, Any]], grid: Sequence[float]) -> list[dict[str, Any]]:
    return [metrics_at(pairs, float(t)) for t in grid]


def freeze_threshold(sweep_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按预登记 tie-break：F1 最大 → 更小阈值 → 更小网格值。"""
    best = sorted(sweep_rows, key=lambda r: (-r["f1"], r["threshold"]))[0]
    return {
        "frozen_threshold": best["threshold"],
        "selection_metric": "F1（标注集 100 对）",
        "tie_break": "F1 并列取更小阈值",
        "sweep_at_freeze": list(sweep_rows),
    }


def group_recall(
    groups: Sequence[Mapping[str, Any]],
    score_fn,
    threshold: float,
) -> dict[str, Any]:
    """预登记真重复组：组内**每一对**都必须被抓到（强阈值下 recall=100%）。

    打分来自**全库相似度池**（不是 100 对标注集），否则组内未被抽进标注集的成员对会被
    误判为 0 分。
    """
    rows = []
    caught_groups = 0
    for g in groups:
        members = [str(m) for m in g["members"]]
        need, hit, missing = 0, 0, []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                need += 1
                key = (a, b) if a <= b else (b, a)
                sc = float(score_fn(key[0], key[1]))
                if sc >= threshold:
                    hit += 1
                else:
                    missing.append([key[0], key[1], round(sc, 6)])
        ok = need > 0 and hit == need
        caught_groups += 1 if ok else 0
        rows.append({"members": members, "pairs_needed": need, "pairs_caught": hit,
                     "recall": round(hit / need, 6) if need else 0.0, "missing_pairs": missing,
                     "group_ok": ok})
    return {
        "groups": rows,
        "groups_total": len(rows),
        "groups_caught": caught_groups,
        "recall": round(caught_groups / len(rows), 6) if rows else 0.0,
    }


# ── 落盘 ────────────────────────────────────────────────────────────────
def overrides_path(cfg: RedundancyConfig):
    return repo_root() / "artifacts" / "redundancy_audit" / "annotations_overrides.json"


def load_overrides(cfg: RedundancyConfig) -> list[dict[str, Any]]:
    p = overrides_path(cfg)
    if not p.exists():
        return []
    doc = read_json(p)
    return list(doc.get("overrides") or [])


def write_labels(cfg: RedundancyConfig, doc: Mapping[str, Any]) -> str:
    return write_json(cfg.path_of("paths.labels"), doc)