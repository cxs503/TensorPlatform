#!/usr/bin/env python3
"""边界门对抗语料生成器（P20-N08 / 边界门假阴性率实测）。

作者与门禁作者不同人（门禁 = IN17-B1 / m5016，语料 = 本单），因此语料
**对作者盲**：本文件只含 6 个结构性变异算子，全部域语义（词条槽位、同义
映射、外文映射、数值符号池、引用目标）来自
``data/boundary/adversarial_corpus_seeds.json``。

6 类变异算子（对应任务书 (i)-(vi)）
-----------------------------------
* ``synonym_rewrite``   同义/迂回改写 → 词表**覆盖度**盲区
* ``symbol_notation``   符号化（Cb / Re_tau / L-B-T 三元组 / MW）→ 符号盲区
* ``cross_language``    跨语言混写（其它语种/转写）→ 语种盲区
* ``block_embedding``   表格 / 代码块 / docstring 内嵌（含跨相邻行断开）
* ``comment_url``       注释 / URL 内嵌（百分号编码 / 零宽 / 同形字 / 数字替换）
* ``long_range_reference`` 长程引用（本行只写路径+节号，语义落在被引用工件）

每个样本带三个标签，供度量与归因使用：

``label``      positive（应当判红）/ negative（应当放行，防误报）
``predicted``  对**现行门禁**判决的预测（FAIL/PASS）——只作设计意图记录，不参与断言
``remediable_by``  该漏检在原理上能否被结构性判据救回（base/vocabulary/R1/R2/R3/none）

负样本在生成期即被机器校验「整行不含任何阻断词条」，因此误报率的分母是干净的。

用法
----
    python3 tools/boundary/adversarial_corpus.py --out data/boundary/adversarial_corpus.json
    python3 tools/boundary/adversarial_corpus.py --check      # 复现性自检（哈希稳定）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):  # 允许 python3 tools/boundary/adversarial_corpus.py 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.boundary import pr_gate_scan as gate  # type: ignore
else:  # pragma: no cover
    from . import pr_gate_scan as gate  # type: ignore

SCHEMA = "tp.boundary.adversarial_corpus/v1"
SEEDS_SCHEMA = "tp.boundary.adversarial_seeds/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDS_REL = "data/boundary/adversarial_corpus_seeds.json"
LEXICON_REL = gate.DEFAULT_LEXICON_REL
DEFAULT_SEED = 20261107

CLASSES: Tuple[str, ...] = (
    "synonym_rewrite",
    "symbol_notation",
    "cross_language",
    "block_embedding",
    "comment_url",
    "long_range_reference",
)

PROVENANCE_OF_CLASS: Dict[str, str] = {
    "synonym_rewrite": "IN17-B2/#608 语义清洗后的同义回流形态 + IN17-B5/#616 报告改写",
    "symbol_notation": "IN17-B3/#610 主尺度 dict 字面量 + IN17-B5/#616 三元组/功率",
    "cross_language": "IN17-B2/#608 中英文混写 + IN17-B5/#616 域文档引用",
    "block_embedding": "IN17-B2/#608 docstring 内嵌 + IN17-B5/#616 报告表格",
    "comment_url": "IN17-B2/#608 字符串比较内嵌 + IN17-B3/#610 demo 注释",
    "long_range_reference": "IN17-B5/#616 「引用不复述」规则的对抗形态",
}

# 中性拉丁词，用于同形/数字替换类负样本（不含任何域语义）
_PLAIN_NEUTRAL = ["digest", "index", "offset", "code", "registry", "throughput"]

_HOMOGLYPHS = {
    "a": "\u0430",  # а
    "c": "\u0441",  # с
    "e": "\u0435",  # е
    "i": "\u0456",  # і
    "o": "\u043e",  # о
    "p": "\u0440",  # р
    "s": "\u0455",  # ѕ
    "x": "\u0445",  # х
    "y": "\u0443",  # у
}
_LEET = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"}
_ZWSP = "\u200b"


# --------------------------------------------------------------------------- #
# 遮蔽算子（结构性，与语言无关）
# --------------------------------------------------------------------------- #
def percent_encode(text: str) -> str:
    """空格/连字符百分号编码——URL 里最常见的遮蔽形态。"""

    return text.replace(" ", "%20").replace("-", "%2D")


def zero_width(text: str) -> str:
    """在字符串中部插一个零宽空格。"""

    if len(text) < 2:
        return text
    mid = len(text) // 2
    return text[:mid] + _ZWSP + text[mid:]


def homoglyph(text: str) -> str:
    """前两个可替换字符换成西里尔同形字（视觉同形、字节不同）。"""

    out, swapped = [], 0
    for ch in text:
        low = ch.lower()
        if swapped < 2 and low in _HOMOGLYPHS and ch.isalpha():
            out.append(_HOMOGLYPHS[low])
            swapped += 1
        else:
            out.append(ch)
    return "".join(out)


def leet(text: str) -> str:
    """前两个可替换字符换成数字（i→1, e→3 …）。"""

    out, swapped = [], 0
    for ch in text:
        low = ch.lower()
        if swapped < 2 and low in _LEET:
            out.append(_LEET[low])
            swapped += 1
        else:
            out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------- #
# 数据装载
# --------------------------------------------------------------------------- #
def load_seeds(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SEEDS_SCHEMA:
        raise ValueError(f"seeds schema mismatch: {data.get('schema')!r} != {SEEDS_SCHEMA!r}")
    return data


def blocking_values(lexicon: gate.Lexicon) -> List[Tuple[str, str]]:
    """(term_id, value) 全部阻断词条取值——语料槽位 {TERM} 的来源。"""

    out: List[Tuple[str, str]] = []
    for term in lexicon.terms:
        for value in term.values:
            out.append((term.term_id, value))
    return out


def cjk_values(lexicon: gate.Lexicon) -> List[str]:
    """substring 类词条取值（无词边界语言），用于跨语言混写的「轻」样本。"""

    return [v for t in lexicon.terms if t.kind == "substring" for v in t.values]


def scan_hits(text: str, lexicon: gate.Lexicon) -> List[Dict[str, str]]:
    return [
        {"term_id": h.term_id, "matched": h.matched}
        for h in gate.scan_line(text, lexicon)
        if h.blocking and not h.exempt_reason
    ]


def assert_clean(text: str, lexicon: gate.Lexicon, where: str) -> None:
    """生成期硬校验：中性材料/负样本里绝不允许出现阻断词条。"""

    hits = scan_hits(text, lexicon)
    if hits:
        raise SystemExit(f"neutral material carries blocking vocabulary at {where}: {hits} in {text!r}")


def assert_no_vocab(text: str, lexicon: gate.Lexicon, where: str) -> None:
    """正向「词表外」材料（同义改写/外文）必须在词表覆盖之外，否则该类不成其为盲区。"""

    assert_clean(text, lexicon, where)


# --------------------------------------------------------------------------- #
# 样本构造
# --------------------------------------------------------------------------- #
def _diff(path: str, lines: Sequence[str], new_file: bool = True) -> List[str]:
    """把若干新增行包成统一 diff（-U0），供 gate.scan_diff 直接食用。"""

    head = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644" if new_file else "index 1111111..2222222 100644",
        "--- /dev/null" if new_file else f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    return head + ["+" + ln for ln in lines]


class Builder:
    def __init__(self, seeds: Dict[str, Any], lexicon: gate.Lexicon, rng: random.Random) -> None:
        self.seeds = seeds
        self.lex = lexicon
        self.rng = rng
        self.pools = seeds["slots"]
        self.values = blocking_values(lexicon)
        # 槽位 {TERM} 只取单词类取值（substring 类另走 CJK 槽；连字符前缀类自身已带 '-'
        # 再拼模板里的 '-' 会产生 'x--id' 这类非真实形态，故排除，其覆盖由复合名形态承担）
        self.word_values = [(tid, v) for tid, v in self.values
                            if not v.endswith("-")
                            and not any(t.term_id == tid and t.kind == "substring" for t in lexicon.terms)]
        self.cjk = cjk_values(lexicon)
        self.paraphrase = {k.lower(): v for k, v in seeds["paraphrase"]["map"].items()}
        self.foreign = {k.lower(): v for k, v in seeds["foreign"]["map"].items()}
        self.term_free = self._pick_term_free_targets()
        self._validate_material()

    # -- 材料校验 -----------------------------------------------------------
    def _pick_term_free_targets(self) -> List[str]:
        out = []
        for rel in self.seeds["reference_targets"]["term_free_candidates"]:
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            hits = scan_hits(path.read_text(encoding="utf-8", errors="replace"), self.lex)
            if not hits:
                out.append(rel)
        if not out:
            raise SystemExit("no term-free reference target available for negatives")
        return out

    def _validate_material(self) -> None:
        for value, para in self.paraphrase.items():
            # 同义改写必须是词表外措辞；用「{phrase} 工况 x」做上下文无关的最小检测
            assert_no_vocab(f"notes = \"{para}\"", self.lex, f"paraphrase:{value}")
        for value, forms in self.foreign.items():
            for form in forms:
                assert_no_vocab(f"label = \"{form} 记录\"", self.lex, f"foreign:{value}:{form}")
        for target in self.seeds["reference_targets"]["domain_bearing"]:
            if not (REPO_ROOT / target).exists():
                raise SystemExit(f"domain-bearing reference target missing: {target}")

    # -- 槽位 ---------------------------------------------------------------
    def _pick_word_term(self, rng: random.Random) -> Tuple[str, str]:
        return rng.choice(self.word_values)

    def _para_of(self, value: str) -> str:
        """取同义改写；映射缺项时退到字典序第一项（不硬编码任何词条）。"""

        para = self.paraphrase.get(value.lower())
        return para if para else self.paraphrase[sorted(self.paraphrase)[0]]

    def _foreign_of(self, value: str) -> str:
        forms = self.foreign.get(value.lower()) or self.foreign[sorted(self.foreign)[0]]
        return self.rng.choice(forms)

    def render(self, template: str, cls: str, rng: random.Random) -> Tuple[str, Dict[str, str]]:
        tid, term = self._pick_word_term(rng)
        cjk = rng.choice(self.cjk)
        dims = rng.choice(self.pools["numbers"]["dims"])
        foreign_pool = self.foreign.get(term.lower()) or self.foreign[sorted(self.foreign)[0]]
        neutral = rng.choice(self.pools["neutral_terms"])
        plain_neutral = rng.choice(_PLAIN_NEUTRAL)
        para = self._para_of(term)
        slots = {
            "TERM": term,
            "PARA": para,
            "FOREIGN": rng.choice(foreign_pool),
            "CJK": cjk,
            "NEUTRAL": neutral,
            "ID": rng.choice(self.pools["ids"]),
            "W": rng.choice(self.pools["neutral_words"]),
            "N": str(rng.randint(2, 99)),
            "CB": rng.choice(self.pools["numbers"]["block_coeff"]),
            "RETAU": rng.choice(self.pools["numbers"]["re_tau"]),
            "POWER": rng.choice(self.pools["numbers"]["power_mw"]),
            "DISP": rng.choice(self.pools["numbers"]["displacement"]),
            "DIMS0": str(dims[0]),
            "DIMS1": str(dims[1]),
            "DIMS2": str(dims[2]),
            "ENC": percent_encode(term),
            "ZW": zero_width(term),
            "HOMO": homoglyph(term),
            "LEET": leet(term),
            "ZWN": zero_width(plain_neutral),
            "HOMON": homoglyph(plain_neutral),
            "LEETN": leet(plain_neutral),
            "PATH_D": rng.choice(self.seeds["reference_targets"]["domain_bearing"]),
            "PATH_T": rng.choice(self.term_free),
            "ANCHOR": rng.choice(self.seeds["reference_targets"]["anchors"]),
        }
        return template.format(**slots), {"term_id": tid, "term_value": term}

    # -- 单样本 -------------------------------------------------------------
    def sample(self, cls: str, variant: str, template: str, idx: int, rng: random.Random) -> Dict[str, Any]:
        text, meta = self.render(template, cls, rng)
        stem = rng.choice(self.pools["ids"])
        path = rng.choice([
            f"tests/test_{stem}_ledger.py",
            f"tools/{stem}/pipeline.py",
            f"backend/api/{stem}_runs.py",
            f"src/{stem}/registry.py",
            f"scripts/{stem}_job.sh",
            f"optimizer/{stem}/search.py",
            f"docs/reports/{stem}_gate.py",  # 白名单目录内的代码：仍按代码扫描
        ])
        label = "negative" if variant == "neg" else "positive"
        plain = variant == "plain"
        if plain:
            predicted, mechanism, remedy = "FAIL", "in_vocabulary", "base"
        elif cls == "synonym_rewrite":
            if variant == "pos_light":
                predicted, mechanism, remedy = "FAIL", "in_vocabulary_partial", "base"
            else:
                predicted, mechanism, remedy = "PASS", "paraphrase", "vocabulary"
        elif cls == "cross_language":
            predicted = "FAIL" if variant == "pos_light" else "PASS"
            mechanism, remedy = ("in_vocabulary", "base") if variant == "pos_light" else ("script_shift", "vocabulary")
        elif cls == "symbol_notation":
            predicted = "FAIL" if variant == "pos_light" else "PASS"
            mechanism, remedy = ("in_vocabulary", "base") if variant == "pos_light" else ("notation_gap", "R2_notation")
        elif cls == "block_embedding":
            predicted = "FAIL" if variant == "pos_light" else "PASS"
            if variant == "pos_light":
                mechanism, remedy = "in_vocabulary", "base"
            elif "\n" in text:
                mechanism, remedy = "line_split", "R1_normalize"
            else:
                mechanism, remedy = "notation_gap", "R2_notation"
        elif cls == "comment_url":
            predicted = "FAIL" if variant == "pos_light" else "PASS"
            mechanism, remedy = ("in_vocabulary", "base") if variant == "pos_light" else ("obfuscation", "R1_normalize")
        elif cls == "long_range_reference":
            predicted = "FAIL" if variant == "pos_light" else "PASS"
            if variant == "pos_light":
                mechanism, remedy = "in_vocabulary", "base"
            elif "{PATH_D}" in template:
                mechanism, remedy = "cross_reference", "R3_reference"
            else:
                mechanism, remedy = "cross_reference", "none"
        else:  # pragma: no cover - 防御
            raise ValueError(f"unknown class {cls}")
        if label == "negative":
            mechanism, remedy = "neutral_control", "none"

        lines = text.split("\n")
        # 负样本硬校验：整行不得含阻断词条（误报率分母必须干净）
        if label == "negative":
            for ln in lines:
                assert_clean(ln, self.lex, f"negative:{cls}:{idx}")
        sample = {
            "id": f"{cls}-{variant}-{idx:03d}",
            "class": cls,
            "variant": variant,
            "label": label,
            "predicted": predicted,
            "mechanism": mechanism,
            "remediable_by": remedy,
            "path": path,
            "lines": lines,
            "template": template,
            "provenance": PROVENANCE_OF_CLASS[cls],
            "slot_meta": meta,
        }
        return sample


def build_corpus(
    seeds_path: Path = REPO_ROOT / SEEDS_REL,
    lexicon_path: Path = REPO_ROOT / LEXICON_REL,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    seeds = load_seeds(seeds_path)
    lexicon = gate.Lexicon.load(lexicon_path)
    rng = random.Random(seed)
    builder = Builder(seeds, lexicon, rng)
    quota = seeds["quota"]
    per_variant = {
        "pos_light": (quota["per_class_pos_min"] + 1) // 2,
        "pos_heavy": (quota["per_class_pos_min"] + 1) // 2,
        "neg": quota["per_class_neg_min"],
    }
    # 自伤控制样本：已知必被门禁抓住的一行（C4 用）
    control_line = 'VALUE = "' + builder.word_values[0][1] + '"'
    samples: List[Dict[str, Any]] = []
    for cls in CLASSES:
        spec = seeds["templates"][cls]
        for variant, target in per_variant.items():
            templates: List[str] = list(spec[variant])
            for i in range(target):
                tpl = templates[i % len(templates)]
                samples.append(builder.sample(cls, variant, tpl, i + 1, rng))
    counts = {
        "total": len(samples),
        "by_class": {
            cls: {
                "positive": sum(1 for s in samples if s["class"] == cls and s["label"] == "positive"),
                "negative": sum(1 for s in samples if s["class"] == cls and s["label"] == "negative"),
            }
            for cls in CLASSES
        },
    }
    canonical = json.dumps(samples, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    corpus = {
        "schema": SCHEMA,
        "version": "1.0.0",
        "seed": seed,
        "generator": {
            "path": "tools/boundary/adversarial_corpus.py",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "version": "1.0.0",
        },
        "seeds": {
            "path": str(seeds_path.relative_to(REPO_ROOT)) if seeds_path.is_absolute() else str(seeds_path),
            "sha256": hashlib.sha256(seeds_path.read_bytes()).hexdigest(),
        },
        "lexicon": {"path": lexicon.path, "sha256": lexicon.sha256, "version": lexicon.version},
        "quota": quota,
        "counts": counts,
        "corpus_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "control": {
            "id": "self-injury-control",
            "label": "positive",
            "expect": "FAIL",
            "path": "backend/api/probe_guard.py",
            "lines": [control_line],
            "note": "已知会被拦的一行（词表内词条裸写进代码路径）；门禁若判绿=度量链路坏了，度量必须中止",
        },
        "samples": samples,
    }
    return corpus


def corpus_hash(corpus: Dict[str, Any]) -> str:
    canonical = json.dumps(corpus["samples"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_diff(sample: Dict[str, Any]) -> str:
    return "\n".join(_diff(sample["path"], sample["lines"])) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="边界门对抗语料生成（确定性，可复现）")
    ap.add_argument("--out", default=str(REPO_ROOT / "data/boundary/adversarial_corpus.json"))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--seeds", default=str(REPO_ROOT / SEEDS_REL))
    ap.add_argument("--lexicon", default=str(REPO_ROOT / LEXICON_REL))
    ap.add_argument("--check", action="store_true", help="与已落盘语料比对哈希（复现性自检）")
    args = ap.parse_args(argv)

    corpus = build_corpus(Path(args.seeds), Path(args.lexicon), args.seed)
    if args.check:
        existing = json.loads(Path(args.out).read_text(encoding="utf-8"))
        same = existing.get("corpus_sha256") == corpus["corpus_sha256"]
        print(json.dumps({"stable": same,
                          "existing": existing.get("corpus_sha256"),
                          "regenerated": corpus["corpus_sha256"],
                          "counts": corpus["counts"]}, ensure_ascii=False, indent=2))
        return 0 if same else 1
    Path(args.out).write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out, "corpus_sha256": corpus["corpus_sha256"],
                      "counts": corpus["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())