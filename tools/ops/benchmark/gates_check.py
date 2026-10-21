#!/usr/bin/env python3
"""四门机械复算（benchmark 闭环线 A 改进：验收判断变机器）。

agent 在 gates.json 里把每个判决数字声明为 值-文件-键 三元组；本脚本逐一
打开文件、走键路径、重取数值比对（零手抄的机器面）。派发器在 push 前调用，
未过不推。

gates.json 约定（放在 benchmarks/verified/<case>/ 或 pending/<case>/ 下）：
{
  "case": "sphere_re100_mem",
  "verdict": "promote" | "terminal",
  "reason": "terminal 必填：终裁根因一句话",
  "gates": [
    {"gate": "library_entry", "pass": true,
     "evidence": [{"v": "from tensorlbm",
                   "f": "benchmarks/verified/x/run.py",
                   "k": "grep:from tensorlbm"}]},
    {"gate": "direct_observable", "pass": true,
     "evidence": [两个以上数值三元组，|v|<=3.0]},
    {"gate": "monotone_convergence", "pass": true, "mode": "abs_decrease",
     "evidence": [按档位顺序的 err 三元组]},
    {"gate": "reference_locked_first", "pass": true,
     "evidence": [锁定/运行时间三元组]}
  ]
}
键路径支持 a.b[2].c；k 以 "grep:" 开头时做文件内子串匹配（v 必须在文中）。

paper 档（owner 2026-10-20 质量门升级：案例须有细节介绍+云图+曲线）追加两门，
这两门**不信 gates.json 声明**，由本脚本读文件本体机械检查：
  - case_readme：README.md 存在、≥1200B、≥2 个 ## 小节、PROVENANCE 块、
    python 运行命令、参考/锚字样；
  - figures：figures.json manifest（figures[].file/kind/caption/source，可附
    field_waiver 豁免理由）——≥1 曲线（curve）、≥1 云图（field）或豁免、
    有云图时合计 ≥2 图、每图真 PNG 魔数且 ≥6KB、caption ≥8 字、source 非空。
promote 在 paper 档须六门俱全；terminal 须两新门 + 至少一门声明未过 + reason。
classic 档保持原四门（在飞/历史条目不追溯）。
用法：gates_check.py --worktree <wt> --case <case> [--gates <path>]
                [--profile classic|paper]
退出码：0 复算通过；1 复算不通过；2 gates.json 缺失/格式坏。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

GATE_NAMES = {"library_entry", "direct_observable",
              "monotone_convergence", "reference_locked_first"}
# paper 档追加两门（owner 2026-10-20 质量门升级：案例要有细节介绍+云图+曲线）。
# classic 档保持原四门——在飞/已终态的旧条目不受追溯影响。
PAPER_GATES = {"case_readme", "figures"}
README_MIN_BYTES = 1200          # 案例细节介绍的深度下限
FIG_MIN_BYTES = 6144             # 防 1x1 占位图
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TOL = 1e-9


def walk(doc, key: str):
    """走 a.b[2].c 键路径；返回 (found, value)。

    含点键（如 result.json 的 "phi0.343"）用回溯最长匹配兼容：先试更长的
    真实键名，失败再回退短切分（m64 permeability3d 实战暴露的缺陷）。
    """
    def rec(cur, rest: str):
        if rest == "":
            return True, cur
        if not isinstance(cur, dict):
            return False, None
        for name in sorted(cur.keys(), key=len, reverse=True):
            # 形式一：键名整体等于剩余段的开头（键名本身可含点）
            if rest == name or rest.startswith(name + "."):
                ok, val = rec(cur[name],
                              "" if len(rest) == len(name) else rest[len(name) + 1:])
                if ok:
                    return True, val
            # 形式二：键名后紧跟数组索引 name[0].…
            m = re.match(re.escape(name) + r"((?:\[-?\d+\])+)(\.|$)", rest)
            if m:
                node = cur[name]
                try:
                    for i in re.findall(r"\[(-?\d+)\]", m.group(1)):
                        node = node[int(i)]
                except (TypeError, IndexError):
                    continue
                tail = rest[m.end():]
                ok, val = rec(node, tail[1:] if tail.startswith(".") else tail)
                if ok:
                    return True, val
        return False, None

    return rec(doc, key)


def verify_triple(ev: dict, worktree: Path) -> tuple[bool, object, str]:
    """复算一个 值-文件-键 三元组。返回 (ok, found, note)。"""
    f = str(ev.get("f", ""))
    k = str(ev.get("k", ""))
    v = ev.get("v")
    path = Path(f) if f.startswith("/") else worktree / f
    if not path.exists():
        return False, None, f"file missing: {f}"
    if k.startswith("grep:"):
        text = path.read_text(encoding="utf-8", errors="replace")
        needle = k[len("grep:"):]
        if needle not in text:
            return False, None, f"grep miss: {needle!r} not in {f}"
        return str(v) in text, v, f"grep ok"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, None, f"json unreadable: {f}: {exc}"
    found, val = walk(doc, k)
    if not found:
        return False, None, f"key miss: {k} in {f}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            fv = float(val)
        except (TypeError, ValueError):
            return False, val, f"non-numeric at {k}: {val!r}"
        ok = abs(fv - float(v)) <= TOL * max(1.0, abs(float(v)))
        return ok, fv, f"{fv!r} vs {v!r}"
    ok = str(val) == str(v)
    return ok, val, f"{val!r} vs {v!r}"


def _expand_braces(s: str) -> list[str]:
    """case_H{16,32}_fp64.json → 两份逐字展开（多组花括号递归展开）。"""
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    pre, post = s[:m.start()], s[m.end():]
    out: list[str] = []
    for a in m.group(1).split(","):
        out.extend(_expand_braces(pre + a + post))
    return out or [s]


def _src_exists(case_dir: Path, tok: str) -> bool:
    """P3：source 里点名的工件是否存在。相对路径按案例目录解析（或裸文件名），
    绝对路径（/nfs 大盘跑档）按原样。花括号 glob 由 _expand_braces 先行展开，
    展开后的每个文件都必须在——点名了却被清档=溯源断链。"""
    tok = tok.split(":")[0]
    p = Path(tok)
    if p.is_absolute():
        return p.exists()
    return (case_dir / p).exists() or (case_dir / p.name).exists()


def readme_rows(case_dir: Path) -> list[dict]:
    """case_readme 门的机械检查行：不信 gates.json 声明，直接读 README 本体。

    五条硬检查：存在、≥1200B、≥2 个 ## 小节、PROVENANCE 块、运行命令与参考锚。
    （owner 2026-10-20 质量门：案例必须独立成篇可复现，不是一行结论。）
    """
    p = case_dir / "README.md"
    if not p.exists():
        return [{"v": "README.md", "f": "README.md", "k": "check:exists",
                 "found": None, "ok": False, "note": "README.md 缺失"}]
    text = p.read_text(encoding="utf-8", errors="replace")
    size = p.stat().st_size
    secs = len(re.findall(r"^#{2,3}\s+\S", text, re.M))
    checks = [
        ("check:size>=1200B", size >= README_MIN_BYTES, f"{size}B"),
        ("check:sections>=2", secs >= 2, f"{secs} 个小节"),
        ("check:PROVENANCE", "PROVENANCE" in text.upper(), ""),
        ("check:run_command", "python" in text, ""),
        ("check:reference_anchor",
         bool(re.search(r"参考|Reference|文献|anchor|锚", text, re.I)), ""),
    ]
    return [{"v": True, "f": "README.md", "k": k, "found": note or ("ok" if ok else "fail"),
             "ok": ok, "note": note or ("ok" if ok else "不满足")}
            for k, ok, note in checks]


def figures_rows(case_dir: Path) -> list[dict]:
    """figures 门的机械检查行：figures.json manifest + 文件本体。

    硬检查：≥2 图、≥1 曲线（kind=curve）、≥1 云图（kind=field）或 field_waiver
    豁免理由（无空间场量的案，如碰撞核弛豫谱）、每图真 PNG（魔数）且 ≥6KB、
    caption ≥8 字、source 非空（图从哪个工件+脚本生成，零手抄的图件面）。
    """
    def row(ok, k, note):
        return {"v": True, "f": "figures.json", "k": k,
                "found": note, "ok": ok, "note": note}

    p = case_dir / "figures.json"
    if not p.exists():
        return [row(False, "check:manifest_exists", "figures.json 缺失")]
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [row(False, "check:manifest_json", f"不可解析: {type(exc).__name__}")]
    figs = man.get("figures") if isinstance(man, dict) else None
    if not isinstance(figs, list) or not figs:
        return [row(False, "check:figures_list", "figures 列表为空")]
    rows: list[dict] = []
    kinds: list[str] = []
    for i, fg in enumerate(figs):
        if not isinstance(fg, dict):
            rows.append(row(False, f"check:fig[{i}]", "非对象"))
            continue
        f, kind = str(fg.get("file") or ""), str(fg.get("kind") or "")
        cap, src = str(fg.get("caption") or ""), str(fg.get("source") or "")
        if kind in ("field", "curve", "diagram"):
            kinds.append(kind)
        else:
            rows.append(row(False, f"check:fig[{i}].kind", f"非法 kind={kind!r}"))
        if len(cap) < 8:
            rows.append(row(False, f"check:fig[{i}].caption", f"caption 过短 {cap!r}"))
        if not src:
            rows.append(row(False, f"check:fig[{i}].source", "source 缺失"))
        else:
            # source 溯源落地（P3）：source 里点名的工件文件必须真实存在
            # （图件面的零手抄——写了来源却给不出文件=来源存疑）。
            # 花括号 glob（case_H{16,32}_fp64.json）先展开再逐个核对。
            toks: set[str] = set()
            for one in _expand_braces(src):
                toks.update(re.findall(
                    r"[\w./\-]+\.(?:npz|json|py|txt|md|csv|log|dat)", one))
            for tok in sorted(toks):
                if not _src_exists(case_dir, tok):
                    rows.append(row(False, f"check:fig[{i}].source_file",
                                    f"source 提到的文件不存在: {tok}"))
        fp = case_dir / f
        if not f or not fp.exists():
            rows.append(row(False, f"check:fig[{i}].file", f"文件缺失 {f}"))
            continue
        size = fp.stat().st_size
        with fp.open("rb") as fh:
            head = fh.read(8)
        if head != PNG_MAGIC:
            rows.append(row(False, f"check:fig[{i}].png", f"非 PNG 文件 {f}"))
        elif size < FIG_MIN_BYTES:
            rows.append(row(False, f"check:fig[{i}].size", f"{f} 仅 {size}B"))
        else:
            # 防空白占位图（P3 启发式）：真图（场/曲线）压缩后字节多样性高，
            # 纯色/空白图 zlib 压缩流近乎单字节重复
            with fp.open("rb") as fh:
                fh.seek(16)
                sample = fh.read(4096)
            if len(set(sample)) < 8:
                rows.append(row(False, f"check:fig[{i}].blank",
                                f"{f} 疑似空白/单色占位图（字节多样性 {len(set(sample))}）"))
            else:
                rows.append(row(True, f"check:fig[{i}]",
                                f"{f} {kind} {size}B div={len(set(sample))}"))
    if "curve" not in kinds:
        rows.append(row(False, "check:curve_required", "缺曲线图（kind=curve）"))
    if "field" not in kinds:
        waiver = str(man.get("field_waiver") or "").strip() if isinstance(man, dict) else ""
        if waiver:
            rows.append(row(True, "check:field_waiver", f"云图豁免: {waiver[:60]}"))
        else:
            rows.append(row(False, "check:field_required",
                            "缺云图（kind=field）且无 field_waiver 豁免理由"))
    if "field" in kinds and len(figs) < 2:
        rows.append(row(False, "check:count>=2", f"图件数 {len(figs)} < 2"))
    return rows


def check_gates(spec: dict, worktree: Path, profile: str = "classic",
                case_dir: Path | None = None) -> dict:
    out = {"case": spec.get("case"), "verdict": spec.get("verdict"),
           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "gates": [], "all_pass": False, "problems": []}
    verdict = spec.get("verdict")
    if verdict not in ("promote", "terminal"):
        out["problems"].append(f"verdict 非法: {verdict!r}")
        return out
    gates = spec.get("gates")
    if not isinstance(gates, list) or not gates:
        out["problems"].append("gates 为空")
        return out
    if profile not in ("classic", "paper"):
        out["problems"].append(f"profile 非法: {profile!r}")
        return out
    if case_dir is None:
        case_dir = worktree
    names = set()
    for g in gates:
        name = str(g.get("gate") or "")
        names.add(name)
        # paper 两门不信声明：检查行由本脚本读文件本体合成（evidence 仅留痕）
        if name == "case_readme":
            rows = readme_rows(case_dir)
        elif name == "figures":
            rows = figures_rows(case_dir)
        else:
            rows = []
            for ev in (g.get("evidence") or []):
                ok, found, note = verify_triple(ev, worktree)
                rows.append({"v": ev.get("v"), "f": ev.get("f"), "k": ev.get("k"),
                             "found": found, "ok": ok, "note": note})
        recomputed = all(r["ok"] for r in rows) and bool(rows)
        gate_pass = bool(g.get("pass"))
        probs = []
        if not recomputed:
            bad = [r for r in rows if not r["ok"]]
            probs.append(f"{len(bad)}/{len(rows)} 三元组复算不过")
            if name in PAPER_GATES:
                # 合成门的失败细节上浮（派发器日志只截顶层 problems）
                probs.extend(f"{r['k']}: {r['note']}" for r in bad)
        if name == "direct_observable" and verdict == "promote":
            nums = [r for r in rows
                    if isinstance(r["v"], (int, float)) and not isinstance(r["v"], bool)]
            if len(nums) < 2:
                probs.append("直接观测量不足 2 档")
            if any(abs(float(r["found"] if isinstance(r["found"], (int, float))
                            else r["v"])) > 3.0 for r in nums):
                probs.append("存在 |err|>3% 档")
        if name == "monotone_convergence":
            nums = [float(r["v"]) for r in rows
                    if isinstance(r["v"], (int, float)) and not isinstance(r["v"], bool)]
            if len(nums) < 2:
                probs.append("单调门不足 2 档")
            else:
                mode = g.get("mode", "abs_decrease")
                seq = [abs(x) for x in nums] if mode == "abs_decrease" else nums
                if not all(seq[i + 1] < seq[i] - 1e-12
                           for i in range(len(seq) - 1)):
                    probs.append(f"序列非严格单调({mode}): {seq}")
        if name == "reference_locked_first":
            dates = {}
            for r in rows:
                kk = str(r.get("k") or "")
                # 信工件不信声明：日期序用文件里实取的值（found）判
                val = r["found"] if isinstance(r.get("found"), str) else r.get("v")
                if isinstance(val, str) and re.match(r"^\d{4}-\d{2}-\d{2}", val):
                    if re.search(r"lock|chain|prereg|snapshot|fetched", kk.lower()):
                        dates.setdefault("locked", []).append(val[:10])
                    if re.search(r"ran|run_date|finished|created", kk.lower()):
                        dates.setdefault("ran", []).append(val[:10])
            if "locked" in dates and "ran" in dates:
                if min(dates["locked"]) > max(dates["ran"]):
                    probs.append(f"参考锁定期晚于运行期: {dates}")
        if verdict == "promote" and not gate_pass:
            probs.append("promote 但该门声明未过")
        out["gates"].append({"gate": name, "declared_pass": gate_pass,
                             "recomputed_pass": recomputed and not probs,
                             "evidence": rows, "problems": probs})
    # 具体原因上浮到顶层（派发器日志只截顶层 problems，笼统信息没法诊断）
    gate_probs = [f"{g['gate']}: {m}" for g in out["gates"] for m in g["problems"]]
    if verdict == "promote":
        required = GATE_NAMES | (PAPER_GATES if profile == "paper" else set())
        missing = required - names
        if missing:
            out["problems"].append(f"promote 缺门: {sorted(missing)}")
        out["problems"].extend(gate_probs)
    else:  # terminal
        if not str(spec.get("reason") or "").strip():
            out["problems"].append("terminal 缺 reason")
        if not any(not p["declared_pass"] for p in out["gates"]):
            out["problems"].append("terminal 须至少一门声明未过")
        if profile == "paper":
            missing = PAPER_GATES - names
            if missing:
                out["problems"].append(
                    f"terminal(paper) 缺门: {sorted(missing)}")
        out["problems"].extend(gate_probs)
    out["all_pass"] = not out["problems"]
    return out


def find_gates(worktree: Path, case: str) -> Path | None:
    for sub in ("verified", "pending"):
        p = worktree / "benchmarks" / sub / case / "gates.json"
        if p.exists():
            return p
    return None


def run_one(gp: Path, wt: Path, profile: str) -> dict:
    """复算单个 gates.json 并写审计产物；返回带 rc 的结果 dict。"""
    try:
        spec = json.loads(gp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"case": None, "rc": 2, "error": str(exc), "gates_path": str(gp)}
    out = check_gates(spec, wt, profile=profile, case_dir=gp.parent)
    out["all_pass"] = not out["problems"]
    out["rc"] = 0 if out["all_pass"] else 1
    out["profile"] = profile
    out["gates_path"] = str(gp)
    dest = gp.parent / "gates_verified.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--case", default="")
    ap.add_argument("--gates", default="")
    ap.add_argument("--profile", choices=("classic", "paper"), default="classic",
                    help="paper=追加 case_readme+figures 两门（2026-10-20 起新派发条目）")
    ap.add_argument("--auto", action="store_true",
                    help="自查模式（harness 预检用）：复算 worktree 内发现的全部"
                         " gates.json；每案 profile 按其是否声明 paper 两门自定。")
    args = ap.parse_args()
    wt = Path(args.worktree)

    if args.auto:
        found = sorted(set(wt.glob("benchmarks/*/*/gates.json")))
        results = []
        for gp in found:
            try:
                spec = json.loads(gp.read_text(encoding="utf-8"))
                declared = {str(g.get("gate") or "")
                            for g in (spec.get("gates") or []) if isinstance(g, dict)}
                profile = "paper" if declared & PAPER_GATES else "classic"
            except (json.JSONDecodeError, OSError):
                profile = "classic"
            results.append(run_one(gp, wt, profile))
        agg = {"auto": True, "checked": len(results), "rc": 0}
        for r in results:
            if r.get("rc") != 0:
                agg["rc"] = 1
        agg["cases"] = [{"case": r.get("case"), "profile": r.get("profile"),
                         "rc": r.get("rc"), "problems": r.get("problems")}
                        for r in results]
        print(json.dumps(agg, ensure_ascii=False))
        return agg["rc"]

    if not args.case:
        print(json.dumps({"rc": 2, "error": "--case 未给且未用 --auto"},
                         ensure_ascii=False))
        return 2
    gp = Path(args.gates) if args.gates else find_gates(wt, args.case)
    if gp is None or not gp.exists():
        print(json.dumps({"case": args.case, "rc": 2,
                          "error": "gates.json 缺失（verified/ 与 pending/ 均无）"},
                         ensure_ascii=False))
        return 2
    try:
        spec = json.loads(gp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"case": args.case, "rc": 2, "error": str(exc)},
                         ensure_ascii=False))
        return 2
    out = check_gates(spec, wt, profile=args.profile, case_dir=gp.parent)
    if spec.get("case") and spec["case"] != args.case:
        out["problems"].append(f"case 不符: gates.json 声明 {spec['case']!r}"
                               f" vs 请求 {args.case!r}")
    out["all_pass"] = not out["problems"]
    out["rc"] = 0 if out["all_pass"] else 1
    out["profile"] = args.profile
    out["gates_path"] = str(gp)
    dest = gp.parent / "gates_verified.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case": out.get("case"), "verdict": out.get("verdict"),
                      "profile": args.profile,
                      "all_pass": out["all_pass"],
                      "problems": out["problems"], "rc": out["rc"],
                      "verified": str(dest)}, ensure_ascii=False))
    return out["rc"]


if __name__ == "__main__":
    sys.exit(main())
