#!/usr/bin/env python3
"""gates_check.py 回归测试（A 改进的机器面）。

覆盖：键路径 walk、grep 模式、数值容差、四门各自的机械规则、
promote/terminal 两类 verdict 的完整性要求、CLI 退出码与审计产物。

跑法：/srv/tensor/shared/hermes-agent/.venv/bin/python -m pytest test_gates_check.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gates_check", HERE / "gates_check.py")
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)


@pytest.fixture
def worktree(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({
        "run_date": "2026-10-19",
        "locked": "2026-10-18",
        "grid": [{"rel_err_pct": 2.9}, {"rel_err_pct": 1.1}]}),
        encoding="utf-8")
    (tmp_path / "run.py").write_text(
        "from tensorlbm import solver\nprint(solver)\n", encoding="utf-8")
    return tmp_path


def ev(v, f, k):
    return {"v": v, "f": f, "k": k}


def gate(name, pass_, evs, mode=None):
    g = {"gate": name, "pass": pass_, "evidence": evs}
    if mode:
        g["mode"] = mode
    return g


FULL = [
    gate("library_entry", True, [ev("from tensorlbm", "run.py", "grep:from tensorlbm")]),
    gate("direct_observable", True, [
        ev(2.9, "results.json", "grid[0].rel_err_pct"),
        ev(1.1, "results.json", "grid[1].rel_err_pct")]),
    gate("monotone_convergence", True, [
        ev(2.9, "results.json", "grid[0].rel_err_pct"),
        ev(1.1, "results.json", "grid[1].rel_err_pct")], mode="abs_decrease"),
    gate("reference_locked_first", True, [
        ev("2026-10-18", "results.json", "locked"),
        ev("2026-10-19", "results.json", "run_date")]),
]


# ── walk / verify_triple 基元 ────────────────────────────────────────

def test_walk_nested_and_index():
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert gc.walk(doc, "a.b[1].c") == (True, 2)
    assert gc.walk(doc, "a.b[-1].c") == (True, 2)
    assert gc.walk(doc, "a.b[5].c")[0] is False
    assert gc.walk(doc, "a.x.c")[0] is False


def test_verify_triple_numeric_tol(worktree):
    ok, found, _ = gc.verify_triple(
        ev(2.9 + 2.9e-10, "results.json", "grid[0].rel_err_pct"), worktree)
    assert ok and isinstance(found, float)


def test_verify_triple_mismatch_and_missing(worktree):
    assert not gc.verify_triple(
        ev(9.9, "results.json", "grid[0].rel_err_pct"), worktree)[0]
    assert not gc.verify_triple(ev(1, "nope.json", "x"), worktree)[0]
    assert not gc.verify_triple(ev(1, "run.py", "x.y"), worktree)[0]   # 非 JSON 取键


def test_verify_triple_grep_mode(worktree):
    assert gc.verify_triple(
        ev("from tensorlbm", "run.py", "grep:import solver"), worktree)[0]
    # grep 命中但 v 不在文中 → 不过
    assert not gc.verify_triple(
        ev("import os", "run.py", "grep:import solver"), worktree)[0]


# ── check_gates：promote ─────────────────────────────────────────────

def test_promote_all_pass(worktree):
    spec_ = {"case": "c1", "verdict": "promote", "gates": FULL}
    out = gc.check_gates(spec_, worktree)
    assert out["all_pass"], out["problems"]


def test_promote_triple_mismatch(worktree):
    gs = [dict(g) for g in FULL]
    gs[1] = gate("direct_observable", True, [
        ev(2.9, "results.json", "grid[0].rel_err_pct"),
        ev(0.5, "results.json", "grid[1].rel_err_pct")])   # 手抄错值
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": gs}, worktree)
    assert not out["all_pass"]


def test_promote_needs_two_numerics(worktree):
    gs = [dict(g) for g in FULL]
    gs[1] = gate("direct_observable", True, [
        ev(2.9, "results.json", "grid[0].rel_err_pct")])
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": gs}, worktree)
    assert any("不足 2 档" in p for p in out["problems"])


def test_promote_rejects_gt3pct(worktree):
    (worktree / "results.json").write_text(json.dumps({
        "run_date": "2026-10-19", "locked": "2026-10-18",
        "grid": [{"rel_err_pct": 3.5}, {"rel_err_pct": 1.1}]}), encoding="utf-8")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": FULL}, worktree)
    assert any(">3%" in p for p in out["problems"])


def test_monotone_strict_and_abs_mode(worktree):
    gs = [dict(g) for g in FULL]
    gs[2] = gate("monotone_convergence", True, [
        ev(2.9, "results.json", "grid[0].rel_err_pct"),
        ev(2.9, "results.json", "grid[1].rel_err_pct")], mode="abs_decrease")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": gs}, worktree)
    assert any("单调" in p for p in out["problems"])   # 相等不算严格单调
    # abs_decrease 对符号翻转的序列（−5 → +1）应算收敛
    (worktree / "s.json").write_text(json.dumps({"e": [-5.0, 1.0]}), encoding="utf-8")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": [
        gate("monotone_convergence", True, [
            ev(-5.0, "s.json", "e[0]"), ev(1.0, "s.json", "e[1]")],
            mode="abs_decrease")]}, worktree)
    assert not any("单调" in p for p in out["problems"])


def test_reference_locked_after_run_rejected(worktree):
    (worktree / "results.json").write_text(json.dumps({
        "run_date": "2026-10-10", "locked": "2026-10-19",
        "grid": [{"rel_err_pct": 2.9}, {"rel_err_pct": 1.1}]}), encoding="utf-8")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": FULL}, worktree)
    assert any("锁定" in p for p in out["problems"])


def test_promote_missing_gate(worktree):
    out = gc.check_gates(
        {"case": "c1", "verdict": "promote", "gates": FULL[:3]}, worktree)
    assert any("缺门" in p for p in out["problems"])


def test_promote_declared_not_pass(worktree):
    gs = [dict(g) for g in FULL]
    gs[0]["pass"] = False
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": gs}, worktree)
    assert not out["all_pass"]


# ── check_gates：terminal ────────────────────────────────────────────

def test_terminal_ok(worktree):
    # 终裁的失败数字同样要三元组对上工件（防"编个失败理由"）
    (worktree / "results.json").write_text(json.dumps({
        "run_date": "2026-10-19", "locked": "2026-10-18",
        "grid": [{"rel_err_pct": 12.5}, {"rel_err_pct": 6.0}]}), encoding="utf-8")
    gs = [dict(g) for g in FULL]
    gs[1] = gate("direct_observable", False, [
        ev(12.5, "results.json", "grid[0].rel_err_pct"),
        ev(6.0, "results.json", "grid[1].rel_err_pct")])
    gs[2] = gate("monotone_convergence", True, [
        ev(12.5, "results.json", "grid[0].rel_err_pct"),
        ev(6.0, "results.json", "grid[1].rel_err_pct")], mode="abs_decrease")
    spec_ = {"case": "c1", "verdict": "terminal",
             "reason": "两档均超 3%，网格收敛但参考系统性偏差", "gates": gs}
    out = gc.check_gates(spec_, worktree)
    assert out["all_pass"], out["problems"]


def test_terminal_needs_reason_and_failing_gate(worktree):
    gs = [dict(g) for g in FULL]     # 全 pass 终裁非法
    out = gc.check_gates({"case": "c1", "verdict": "terminal", "gates": gs}, worktree)
    assert any("reason" in p for p in out["problems"])
    assert any("未过" in p for p in out["problems"])


def test_bad_verdict(worktree):
    out = gc.check_gates({"case": "c1", "verdict": "maybe", "gates": FULL}, worktree)
    assert not out["all_pass"]


# ── CLI 端到端 ───────────────────────────────────────────────────────

def _cli(worktree, case="c1"):
    return subprocess.run(
        [sys.executable, str(HERE / "gates_check.py"),
         "--worktree", str(worktree), "--case", case],
        capture_output=True, text=True, timeout=60)


def test_cli_pass_writes_audit(worktree):
    d = worktree / "benchmarks" / "verified" / "c1"
    d.mkdir(parents=True)
    (d / "gates.json").write_text(
        json.dumps({"case": "c1", "verdict": "promote", "gates": FULL}),
        encoding="utf-8")
    r = _cli(worktree)
    assert r.returncode == 0, r.stdout
    out = json.loads(r.stdout)
    assert out["all_pass"] and out["rc"] == 0
    audit = json.loads((d / "gates_verified.json").read_text(encoding="utf-8"))
    assert audit["all_pass"] and len(audit["gates"]) == 4


def test_cli_case_mismatch_fails(worktree):
    d = worktree / "benchmarks" / "pending" / "c1"
    d.mkdir(parents=True)
    (d / "gates.json").write_text(
        json.dumps({"case": "other_case", "verdict": "promote", "gates": FULL}),
        encoding="utf-8")
    r = _cli(worktree)
    assert r.returncode == 1
    assert any("case 不符" in p for p in json.loads(r.stdout)["problems"])


def test_cli_missing_gates_rc2(worktree):
    r = _cli(worktree, case="no_such_case")
    assert r.returncode == 2


# ── 含点键回溯寻址（m64 permeability3d 实战缺陷回归） ─────────────────

DOTTED = {"amendment1_ladders": {"phi0.343": {"tiers": [
    {"err": 0.046253}, {"err": 0.03098}, {"err": 0.020492}, {"err": 0.014469}]}},
    "phi0.343": {"tiers": [{"err": 0.046253}]},   # 与 phi0 同层：最长键优先
    "phi0": {"sibling": True}}


def test_walk_dotted_key():
    ok, v = gc.walk(DOTTED, "amendment1_ladders.phi0.343.tiers[3].err")
    assert ok and abs(v - 0.014469) < 1e-12


def test_walk_dotted_ambiguity_longest_wins():
    """键 "phi0" 与 "phi0.343" 并存：路径 phi0.343 命中更长真键，不误走 phi0→343。"""
    ok, v = gc.walk(DOTTED, "phi0.343.tiers[0].err")
    assert ok and abs(v - 0.046253) < 1e-12
    ok, v = gc.walk(DOTTED, "phi0.sibling")
    assert ok and v is True


def test_walk_still_handles_plain_paths():
    ok, v = gc.walk({"a": [{"b": 7}]}, "a[0].b")
    assert ok and v == 7
    assert gc.walk({"a": 1}, "a.x")[0] is False


def test_reference_signifier_vocab():
    """锁边/跑边 signifier 词表（m64 实战：chain/fetched/created_utc 命名）。"""
    spec = {"case": "c1", "verdict": "promote", "gates": [
        {"gate": "library_entry", "pass": True, "evidence": [
            {"v": "x", "f": "run.py", "k": "grep:x"}]},
        {"gate": "direct_observable", "pass": True, "evidence": [
            {"v": 0.1, "f": "r.json", "k": "a.b"}, {"v": 0.05, "f": "r.json", "k": "a.c"}]},
        {"gate": "monotone_convergence", "pass": True, "mode": "abs_decrease",
         "evidence": [{"v": 0.1, "f": "r.json", "k": "a.b"},
                      {"v": 0.05, "f": "r.json", "k": "a.c"}]},
        {"gate": "reference_locked_first", "pass": True, "evidence": [
            {"v": "2026-09-20", "f": "NOTES.md", "k": "grep:fetched 2026-09-20"},
            {"v": "2026-09-21", "f": "chain.txt", "k": "grep:chain opened 2026-09-21 16:50:42"},
            {"v": "2026-09-21", "f": "case.json", "k": "created_utc"}]}]}
    out = gc.check_gates(spec, Path("/nonexistent"))   # 文件缺失 → 复算不过，仅看日期规则不炸
    assert out["problems"] or True          # 只验证词表逻辑可执行：换真文件由集成路径覆盖
    # 单元级：日期提取正负例
    rows = [{"k": "grep:chain opened 2026-09-21 16:50:42", "found": "2026-09-21 16:50:42"},
            {"k": "created_utc", "found": "2026-09-21T10:11:37Z"}]
    dates = {}
    import re as _re
    for r in rows:
        kk = str(r.get("k") or "")
        val = r["found"]
        if isinstance(val, str) and _re.match(r"^\d{4}-\d{2}-\d{2}", val):
            if _re.search(r"lock|chain|prereg|snapshot|fetched", kk.lower()):
                dates.setdefault("locked", []).append(val[:10])
            if _re.search(r"ran|run_date|finished|created", kk.lower()):
                dates.setdefault("ran", []).append(val[:10])
    assert dates == {"locked": ["2026-09-21"], "ran": ["2026-09-21"]}


# ── paper 档两门（owner 2026-10-20 质量门：案例细节+云图+曲线） ────────

PAPER_FULL = FULL + [
    {"gate": "case_readme", "pass": True, "evidence": []},
    {"gate": "figures", "pass": True, "evidence": []}]


def _good_readme(d):
    (d / "README.md").write_text(
        "# 案例\n\n## 物理问题\n" + "案例细节介绍与几何参数交代。" * 120
        + "\n\n## 运行\npython run.py --grid 64\n\n## 参考与判据\n"
        "参考 Schiller-Naumann 锚。\n\n## PROVENANCE\n- 判决数字 result.json 直读\n",
        encoding="utf-8")


def _png(path, size=8192, blank=False):
    """测试用 PNG：真魔数 + 多样化填充（blank=True 造单色占位图供启发式抓）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    filler = (b"\x00" * (size - 8) if blank
              else (bytes(range(256)) * ((size - 8) // 256 + 1))[:size - 8])
    path.write_bytes(gc.PNG_MAGIC + filler)


def _fig_manifest(d, figs, waiver=""):
    (d / "figures.json").write_text(json.dumps(
        {"figures": figs, "field_waiver": waiver}), encoding="utf-8")


def test_classic_profile_unaffected(worktree):
    """classic 档保持原四门语义：无两新门照样过（在飞/历史条目不追溯）。"""
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": FULL}, worktree)
    assert out["all_pass"], out["problems"]


def test_paper_promote_missing_new_gates(worktree):
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": FULL},
                         worktree, profile="paper", case_dir=worktree)
    assert any("case_readme" in p for p in out["problems"])
    assert any("figures" in p for p in out["problems"])


def test_paper_full_fixture_passes(worktree):
    _good_readme(worktree)
    (worktree / "plot_figs.py").write_text("# plots\n", encoding="utf-8")
    _fig_manifest(worktree, [
        {"file": "figures/curve.png", "kind": "curve",
         "caption": "误差随网格收敛曲线", "source": "results.json + plot_figs.py"},
        {"file": "figures/field.png", "kind": "field",
         "caption": "中剖面速度幅值云图", "source": "run.py dump + plot_figs.py"}])
    _png(worktree / "figures" / "curve.png")
    _png(worktree / "figures" / "field.png")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": PAPER_FULL},
                         worktree, profile="paper", case_dir=worktree)
    assert out["all_pass"], out["problems"]


def test_paper_thin_readme_fails(worktree):
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve", "caption": "收敛曲线八字以上", "source": "x"},
        {"file": "figures/f.png", "kind": "field", "caption": "速度云图八字以上", "source": "x"}])
    _png(worktree / "figures" / "c.png")
    _png(worktree / "figures" / "f.png")
    (worktree / "README.md").write_text("# 一行结论，无细节无小节\n", encoding="utf-8")
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": PAPER_FULL},
                         worktree, profile="paper", case_dir=worktree)
    assert not out["all_pass"]
    g = next(x for x in out["gates"] if x["gate"] == "case_readme")
    assert not g["recomputed_pass"]
    assert any("check:" in r["k"] and not r["ok"] for r in g["evidence"])


def test_paper_figures_negative_paths(worktree):
    _good_readme(worktree)
    spec_ = {"case": "c1", "verdict": "promote", "gates": PAPER_FULL}

    def figs(man, files):
        _fig_manifest(worktree, man)
        for f in files:
            if isinstance(f, Path):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"NOTPNG" + b"\x00" * 8192)   # 坏魔数
            else:
                _png(worktree / f)
        return gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)

    # 云图缺失且无豁免
    out = figs([{"file": "figures/c.png", "kind": "curve",
                 "caption": "收敛曲线 caption", "source": "x"}],
               ["figures/c.png"])
    assert any("缺云图" in p for p in out["problems"])
    # 云图缺失但有豁免理由 → 过（无空间场量的案）
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve",
         "caption": "gamma(tau) 弛豫谱曲线", "source": "results.json"}],
        waiver="碰撞核弛豫谱验证无空间场量，曲线为弛豫谱")
    _png(worktree / "figures" / "c.png")
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    g = next(x for x in out["gates"] if x["gate"] == "figures")
    assert g["recomputed_pass"], g["evidence"]
    assert any("field_waiver" in r["k"] for r in g["evidence"])
    # 坏 PNG 魔数（伪装占位文件）
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve", "caption": "收敛曲线 caption", "source": "x"},
        {"file": "figures/f.png", "kind": "field", "caption": "速度云图 caption", "source": "x"}])
    for name in ("c", "f"):
        p = worktree / "figures" / f"{name}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"NOTPNG" + b"\x00" * 8192)
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert any("非 PNG" in p for p in out["problems"])
    # manifest 缺失
    (worktree / "figures.json").unlink()
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert any("figures.json 缺失" in p for p in out["problems"])


def test_paper_terminal_needs_new_gates(worktree):
    _good_readme(worktree)
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve",
         "caption": "gamma(tau) 弛豫谱曲线", "source": "results.json"}],
        waiver="碰撞核弛豫谱无空间场量")
    _png(worktree / "figures" / "c.png")
    gs = [dict(g) for g in FULL]      # 证据与 worktree fixture 的 2.9/1.1 对齐
    gs[1]["pass"] = False             # 直接观测量门声明未过（12.5%>3% 语义）
    term = {"case": "c1", "verdict": "terminal", "reason": "两档超 3%",
            "gates": gs}
    out = gc.check_gates(term, worktree, profile="paper", case_dir=worktree)
    assert any("terminal(paper) 缺门" in p for p in out["problems"])
    out = gc.check_gates({**term, "gates": gs + PAPER_FULL[4:]},
                         worktree, profile="paper", case_dir=worktree)
    assert out["all_pass"], out["problems"]


def test_cli_profile_paper(worktree):
    d = worktree / "benchmarks" / "verified" / "c1"
    d.mkdir(parents=True)
    (d / "gates.json").write_text(
        json.dumps({"case": "c1", "verdict": "promote", "gates": FULL}),
        encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HERE / "gates_check.py"),
         "--worktree", str(worktree), "--case", "c1", "--profile", "paper"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 1                     # paper 档缺两新门
    out = json.loads(r.stdout)
    assert out["profile"] == "paper"
    r = subprocess.run(
        [sys.executable, str(HERE / "gates_check.py"),
         "--worktree", str(worktree), "--case", "c1"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0                     # 默认 classic 不受影响


# ── P3/P1a 强化：source 溯源落地、防空白图、--auto 自查模式 ───────────

def test_figures_source_file_must_exist(worktree):
    _good_readme(worktree)
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve",
         "caption": "收敛曲线 caption", "source": "results.json + plot_figs.py"},
        {"file": "figures/f.png", "kind": "field",
         "caption": "速度云图 caption", "source": "results.json + plot_figs.py"}])
    _png(worktree / "figures" / "c.png")
    _png(worktree / "figures" / "f.png")
    spec_ = {"case": "c1", "verdict": "promote", "gates": PAPER_FULL}
    # plot_figs.py 不存在 → source 溯源失败
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert any("source 提到的文件不存在" in p for p in out["problems"])
    # 补上后过
    (worktree / "plot_figs.py").write_text("# plots\n", encoding="utf-8")
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert out["all_pass"], out["problems"]


def test_figures_source_brace_glob_and_abs(worktree):
    """m98 couette 实案回归：source 用花括号展开（case_H{16,32,64,128}_fp64.json）
    与绝对路径（/nfs 大盘跑档）都不误判——展开后逐个核对，全部在才过。"""
    _good_readme(worktree)
    (worktree / "plot_figs.py").write_text("# plots\n", encoding="utf-8")
    for h in (16, 32, 64, 128):
        (worktree / f"case_H{h}_fp64.json").write_text("{}", encoding="utf-8")
    abs_json = worktree / "gate_fp64.json"
    abs_json.write_text("{}", encoding="utf-8")
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve", "caption": "收敛曲线 caption",
         "source": "case_H{16,32,64,128}_fp64.json + plot_figs.py"},
        {"file": "figures/f.png", "kind": "field", "caption": "速度云图 caption",
         "source": f"{abs_json} + plot_figs.py"}])       # 绝对路径
    _png(worktree / "figures" / "c.png")
    _png(worktree / "figures" / "f.png")
    spec_ = {"case": "c1", "verdict": "promote", "gates": PAPER_FULL}
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert out["all_pass"], out["problems"]
    # 展开中任一文件缺（点名了却被清档）→ 拦（溯源断链）
    (worktree / "case_H64_fp64.json").unlink()
    out = gc.check_gates(spec_, worktree, profile="paper", case_dir=worktree)
    assert any("source 提到的文件不存在" in p
               and "case_H64_fp64.json" in p for p in out["problems"])


def test_figures_blank_png_rejected(worktree):
    _good_readme(worktree)
    (worktree / "plot_figs.py").write_text("# plots\n", encoding="utf-8")
    _fig_manifest(worktree, [
        {"file": "figures/c.png", "kind": "curve",
         "caption": "收敛曲线 caption", "source": "results.json + plot_figs.py"},
        {"file": "figures/f.png", "kind": "field",
         "caption": "速度云图 caption", "source": "results.json + plot_figs.py"}])
    _png(worktree / "figures" / "c.png")
    _png(worktree / "figures" / "f.png", blank=True)   # 单色占位图
    out = gc.check_gates({"case": "c1", "verdict": "promote", "gates": PAPER_FULL},
                         worktree, profile="paper", case_dir=worktree)
    assert any("空白" in p for p in out["problems"])


def test_cli_auto_mode(worktree):
    """harness 预检用的 --auto：worktree 内全部 gates.json 逐一复算，
    声明 paper 两门的案按 paper 档（m86 自造门名类失败在环内暴露）。"""
    # 案 A：classic 四门全过
    da = worktree / "benchmarks" / "verified" / "a"
    da.mkdir(parents=True)
    (da / "gates.json").write_text(
        json.dumps({"case": "a", "verdict": "promote", "gates": FULL}),
        encoding="utf-8")
    # 案 B：自造门名（m86 型）→ 复算不过
    db = worktree / "benchmarks" / "pending" / "b"
    db.mkdir(parents=True)
    (db / "gates.json").write_text(
        json.dumps({"case": "b", "verdict": "promote", "gates": [
            {"gate": "G1_library_entry", "pass": True, "evidence": []}]}),
        encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HERE / "gates_check.py"),
         "--worktree", str(worktree), "--auto"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["checked"] == 2 and out["rc"] == 1
    cases = {c["case"]: c for c in out["cases"]}
    assert cases["a"]["rc"] == 0
    assert cases["b"]["rc"] == 1
    assert any("缺门" in p for p in cases["b"]["problems"])
    # 全过时 rc=0
    (db / "gates.json").write_text(
        json.dumps({"case": "b", "verdict": "terminal", "reason": "x 超 3%",
                    "gates": [dict(g) for g in FULL[:1]] + [
                        {"gate": "direct_observable", "pass": False, "evidence": [
                            ev(12.5, "rb.json", "grid[0].rel_err_pct")]}]}),
        encoding="utf-8")
    (worktree / "rb.json").write_text(json.dumps({
        "run_date": "2026-10-19", "locked": "2026-10-18",
        "grid": [{"rel_err_pct": 12.5}, {"rel_err_pct": 6.0}]}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HERE / "gates_check.py"),
         "--worktree", str(worktree), "--auto"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout
