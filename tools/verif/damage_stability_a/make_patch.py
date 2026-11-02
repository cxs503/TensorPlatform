"""生成 A 指数 p 因子溯源硬化的补丁草案（对被测件零改动：只在内存做替换后 diff）。"""
import difflib, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUT = os.path.join(ROOT, "backend/api/damage_stability.py")
OUT = os.path.join(ROOT, "artifacts/verif/damage_stability_a/damage_stability_a_index.patch")
src = open(SUT).read()

REPL = [
 # 1) ProbabilisticOutput 增加溯源字段
 ("""    method: str = "概率破舱稳性（SOLAS 2009 Reg.6/7.2）"
    Ls: float = 0.0
    input_s_ignored: bool = False""",
  """    method: str = "概率破舱稳性（SOLAS 2009 Reg.6/7.2）"
    Ls: float = 0.0
    input_s_ignored: bool = False
    # --- p 因子溯源（A 必须是几何决定的量；本实现无 Reg.7-1 p 表 ⇒ 降级）---
    p_sum: float = 0.0
    p_source: str = "unknown"          # reg7_1_table | user_supplied
    A_index_status: str = "ok"         # ok | withheld"""),
 # 2) 禁止缺省 p + 累计 Σp
 ("""        for i, zone in enumerate(input_data.damage_zones):
            if "survival" in zone:
                ignored = True          # F2：请求体直传 s 一律忽略
            p = float(zone.get("probability", 0.1))""",
  """        p_sum = 0.0
        for i, zone in enumerate(input_data.damage_zones):
            if "survival" in zone:
                ignored = True          # F2：请求体直传 s 一律忽略
            if "probability" not in zone:
                raise HTTPException(
                    status_code=422,
                    detail="zone 缺少 probability：p 必须由调用方显式给出"
                           "（Reg.7-1 p(x1,x2) 表未实现，禁止缺省 0.1 造数）")
            p = float(zone["probability"])
            p_sum += p"""),
 # 3) Σp 归一化校验
 ("""        Ls = ship.L
        R = solas2009_required_index(Ls, "cargo")""",
  """        if p_sum > 1.0 + 1e-9:
            raise HTTPException(
                status_code=422,
                detail=f"Σp={p_sum:.6f} > 1：概率加权和的权重不归一，"
                       f"A=Σp·s 将越出 [0,1]（SOLAS 2009 Reg.7-1 要求完整分区上 Σp=1）")
        Ls = ship.L
        R = solas2009_required_index(Ls, "cargo")"""),
 # 4) 合规判定降级（p 无规范来源时不得报 compliant）
 ("""            compliant=bool(A >= R),
            zone_contributions=contribs,
            Ls=Ls,
            input_s_ignored=ignored,
        )""",
  """            compliant=bool(A >= R) and False,   # p 无 Reg.7-1 表来源 ⇒ 不构成合规判决
            zone_contributions=contribs,
            Ls=Ls,
            input_s_ignored=ignored,
            p_sum=round(p_sum, 6),
            p_source="user_supplied",
            A_index_status="withheld",
        )"""),
]
out = src
for old, new in REPL:
    assert out.count(old) == 1, ("anchor not unique/missing", old[:60])
    out = out.replace(old, new)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
diff = difflib.unified_diff(src.splitlines(True), out.splitlines(True),
                            fromfile="a/backend/api/damage_stability.py",
                            tofile="b/backend/api/damage_stability.py", n=3)
open(OUT, "w").writelines(diff)
print("patch written:", OUT, os.path.getsize(OUT), "bytes")
