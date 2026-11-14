#!/usr/bin/env python3
"""
DTMB 5415 阻力对标验证脚本
==========================
由 benchmark_record.json 中的 baseline/computed 复算 rel_err，
与 record 存储值差 ≤1e-6 才判一致。
打印 PASS/FAIL 单行结论。
"""
import json
import sys
import os

RECORD_PATH = os.path.join(os.path.dirname(__file__), "..", "artifacts", "bench_dtmb5415", "benchmark_record.json")


def main():
    with open(RECORD_PATH, "r") as f:
        rec = json.load(f)

    baseline = rec["baseline"]
    computed = rec["computed"]
    verdict_stored = rec["verdict"]

    ct_computed = computed["ct"]
    ct_mid = baseline["value"]
    ct_low = baseline.get("low", ct_mid)
    ct_high = baseline.get("high", ct_mid)

    # 复算相对误差
    rel_err_mid = abs(ct_computed - ct_mid) / ct_mid
    rel_err_low = abs(ct_computed - ct_low) / ct_low
    rel_err_high = abs(ct_computed - ct_high) / ct_high

    # 与 record 存储值比较
    stored_mid = verdict_stored["rel_err_mid"]
    stored_low = verdict_stored["rel_err_low"]
    stored_high = verdict_stored["rel_err_high"]

    tol = 1e-6
    ok_mid = abs(rel_err_mid - stored_mid) <= tol
    ok_low = abs(rel_err_low - stored_low) <= tol
    ok_high = abs(rel_err_high - stored_high) <= tol

    pass_15 = rel_err_mid < 0.15
    pass_3 = rel_err_mid < 0.03

    ok_15 = (pass_15 == verdict_stored["pass_15pct"])
    ok_3 = (pass_3 == verdict_stored["pass_3pct"])

    all_ok = ok_mid and ok_low and ok_high and ok_15 and ok_3

    # 打印详细结果
    print(f"rel_err_mid:  computed={rel_err_mid:.8f}  stored={stored_mid:.8f}  diff={abs(rel_err_mid - stored_mid):.2e}  {'OK' if ok_mid else 'MISMATCH'}")
    print(f"rel_err_low:  computed={rel_err_low:.8f}  stored={stored_low:.8f}  diff={abs(rel_err_low - stored_low):.2e}  {'OK' if ok_low else 'MISMATCH'}")
    print(f"rel_err_high: computed={rel_err_high:.8f} stored={stored_high:.8f} diff={abs(rel_err_high - stored_high):.2e}  {'OK' if ok_high else 'MISMATCH'}")
    print(f"pass_15pct:   computed={pass_15}  stored={verdict_stored['pass_15pct']}  {'OK' if ok_15 else 'MISMATCH'}")
    print(f"pass_3pct:    computed={pass_3}  stored={verdict_stored['pass_3pct']}  {'OK' if ok_3 else 'MISMATCH'}")

    if all_ok:
        print("PASS")
        return 0
    else:
        print("FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
