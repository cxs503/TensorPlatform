#!/usr/bin/env python3
"""SPH-EXA 平滑核/积分器独立复算（playbook 步②判据 4：第二个脚本复算报告里的数字）。

做法：把上游源码的 **header-only** 数值部件（sph/sph_kernels.hpp 的核函数与归一化
kernel_3D_k、sph/positions.hpp 的 leapfrog 位置/速度更新、sph/kernels.hpp 的
tsKCourant/artificial_viscosity）直接编译进来，用一个探针程序跑出数字，
然后与 **解析值**（Wendland 1995 / Dehnen & Aly 2012 归一化常数、常加速度解析解）
逐项比对 —— 不是抄报告，是当场复算。

用法:
  python tools/oss_inventory/sphexa_kernel_check.py \
      --checkout /path/to/sphexa --commit <sha> --out artifacts/oss-sph-sphexa/kernel_check.json \
      --figdir artifacts/oss-sph-sphexa
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

PROBE = r"""
#include <cstdio>
#include <cmath>
#include <array>
#include "sph/sph_kernels.hpp"
#include "sph/positions.hpp"

int main()
{
    using K = sph::SincN1SincN2<double>;
    double Ksinc4 = sph::kernel_3D_k(sph::SincN<double>{4.0});
    double Ksinc9 = sph::kernel_3D_k(sph::SincN<double>{9.0});
    double Kmix   = sph::kernel_3D_k(K{});
    double Kc2    = sph::kernel_3D_k(sph::WendlandC2<double>{});
    double Kc4    = sph::kernel_3D_k(sph::WendlandC4<double>{});
    double Kc6    = sph::kernel_3D_k(sph::WendlandC6<double>{});
    printf("K_sinc4 %.17g\nK_sinc9 %.17g\nK_hardcoded_sinc4 %.17g\nK_hardcoded_sinc9 %.17g\n",
           Ksinc4, Ksinc9, K::K1, K::K2);
    printf("K_mix %.17g\nK_wendlandC2 %.17g\nK_wendlandC4 %.17g\nK_wendlandC6 %.17g\n", Kmix, Kc2, Kc4, Kc6);

    // --- 核函数形状采样（x = r/h, 支撑域 [0,2]）---
    const char* names[] = {"sinc4", "sinc9", "mix", "wc2", "wc4", "wc6"};
    double Ks[]         = {Ksinc4, Ksinc9, Kmix, Kc2, Kc4, Kc6};
    for (int k = 0; k < 6; ++k)
    {
        printf("SHAPE %s", names[k]);
        for (int j = 0; j <= 100; ++j)
        {
            double x = 2.0 * j / 100.0, w = 0;
            if (k == 0) w = sph::SincN<double>{4.0}(x);
            if (k == 1) w = sph::SincN<double>{9.0}(x);
            if (k == 2) w = K{}(x);
            if (k == 3) w = sph::WendlandC2<double>{}(x);
            if (k == 4) w = sph::WendlandC4<double>{}(x);
            if (k == 5) w = sph::WendlandC6<double>{}(x);
            printf(" %.10g", Ks[k] * w);
        }
        printf("\n");
    }

    // --- leapfrog（positions.hpp::positionUpdate）在常加速度场下 vs 解析解 ---
    // a = const, 变步长序列；解析解 x = x0 + v0 t + 0.5 a t^2, v = v0 + a t
    cstone::Box<double> box(-1e30, 1e30, cstone::BoundaryType::open);
    double              dts[] = {0.1, 0.05, 0.02, 0.01, 0.005, 0.001};
    for (double dt : dts)
    {
        // positionUpdate 由 dX/dt_m1 反解半步速度，故初值必须给 dX = v0*dt_m1 才能得到真实 v0
        const double v0[3] = {1.0, 0.0, -2.0};
        cstone::Vec3<double> X{0, 0, 0}, A{0.5, -0.25, 0.125};
        // 约定：dX/dt_m1 反解出的是半步速度 v_{n-1/2}，故初值取 v_{-1/2} = v0 - 0.5*A*dt
        cstone::Vec3<double> vhalf{v0[0] - 0.5 * A[0] * dt, v0[1] - 0.5 * A[1] * dt, v0[2] - 0.5 * A[2] * dt};
        cstone::Vec3<double> dX{vhalf[0] * dt, vhalf[1] * dt, vhalf[2] * dt};
        int                  n     = int(1.0 / dt + 0.5);
        for (int s = 0; s < n; ++s)
        {
            auto [Xn, Vn, dXn] = sph::positionUpdate<double>(dt, dt, X, A, dX, box);
            X = Xn; dX = dXn;
            (void)Vn;
        }
        double t = n * dt;
        double ex = 0.0 + v0[0] * t + 0.5 * A[0] * t * t;
        double ey = 0.0 + v0[1] * t + 0.5 * A[1] * t * t;
        double ez = 0.0 + v0[2] * t + 0.5 * A[2] * t * t;
        double err = std::sqrt((X[0] - ex) * (X[0] - ex) + (X[1] - ey) * (X[1] - ey) + (X[2] - ez) * (X[2] - ez));
        printf("LEAP dt %.10g t %.10g err %.6g\n", dt, t, err);
    }

    // --- CFL 时间步 tsKCourant ---
    printf("CFL %.17g\n", sph::tsKCourant(0.0, 1.0, 10.0, 0.2));   // maxvsignal=0 -> 用 c: 0.2*1/10 = 0.02
    printf("CFL %.17g\n", sph::tsKCourant(30.0, 1.0, 10.0, 0.2));  // 用 v=30: 0.2/30
    // --- 人工黏性（Monaghan 型）---
    printf("AV %.17g\n", sph::artificial_viscosity(1.0, 1.0, 10.0, 10.0, -5.0));  // 0.25*2*20 - 2*(-5) = 15 -> *5
    printf("AV %.17g\n", sph::artificial_viscosity(1.0, 1.0, 10.0, 10.0, 5.0));   // 趋近 -> 0
    return 0;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--commit", default="unknown")
    ap.add_argument("--out", required=True)
    ap.add_argument("--figdir", default=None)
    ap.add_argument("--cxx", default="g++")
    args = ap.parse_args()
    root = os.path.abspath(args.checkout)

    tmpdir = tempfile.mkdtemp(prefix="sphexa_probe_")
    src = os.path.join(tmpdir, "probe.cpp")
    exe = os.path.join(tmpdir, "probe")
    with open(src, "w") as f:
        f.write(PROBE)

    cmd = [args.cxx, "-std=c++20", "-O2", "-o", exe, src,
           "-I", os.path.join(root, "sph/include"), "-I", os.path.join(root, "domain/include"), "-I", root]
    cc = subprocess.run(cmd, capture_output=True, text=True)
    if cc.returncode != 0:
        print(cc.stderr[-4000:], file=sys.stderr)
        print("COMPILE FAILED", file=sys.stderr)
        return 2
    run = subprocess.run([exe], capture_output=True, text=True)
    if run.returncode != 0:
        print(run.stderr[-2000:], file=sys.stderr)
        return 2

    vals, shapes, leap, cfl, av = {}, {}, [], [], []
    for line in run.stdout.splitlines():
        p = line.split()
        if p[0] == "SHAPE":
            shapes[p[1]] = [float(v) for v in p[2:]]
        elif p[0] == "LEAP":
            leap.append({"dt": float(p[2]), "t": float(p[4]), "abs_err": float(p[6])})
        elif p[0] == "CFL":
            cfl.append(float(p[1]))
        elif p[0] == "AV":
            av.append(float(p[1]))
        elif len(p) == 2:
            vals[p[0]] = float(p[1])

    # 解析基准（Wendland 1995 / Dehnen & Aly 2012, 3D, 支撑半径 2h）
    analytic = {
        "K_wendlandC2": 21.0 / (16.0 * math.pi),
        "K_wendlandC4": 495.0 / (256.0 * math.pi),
        "K_wendlandC6": 1365.0 / (512.0 * math.pi),
        "K_mix": 1.0,  # 混合核按构造应归一化为 1
    }
    checks = []
    for k, ref in analytic.items():
        got = vals[k]
        checks.append({"quantity": k, "code": got, "reference": ref,
                       "source": "analytic normalization (Wendland 1995 / Dehnen & Aly 2012 JCP 231:759)",
                       "rel_err": abs(got - ref) / abs(ref), "pass_3pct": abs(got - ref) / abs(ref) < 0.03})
    # 硬编码常数 vs 源码运行期复算（kernel_3D_k）：应逐位一致
    for name, hard, recomp in [("K_sinc4", "K_hardcoded_sinc4", "K_sinc4"),
                               ("K_sinc9", "K_hardcoded_sinc9", "K_sinc9")]:
        got, ref = vals[hard], vals[recomp]
        checks.append({"quantity": f"{name} hardcoded-vs-recomputed", "code": got, "reference": ref,
                       "source": "sph/sph_kernels.hpp:157-158 constant vs kernel_3D_k() at runtime",
                       "rel_err": abs(got - ref) / abs(ref), "pass_3pct": abs(got - ref) / abs(ref) < 0.03})

    # CFL 解析值
    cfl_ref = [0.2 * 1.0 / 10.0, 0.2 / 30.0]
    for got, ref in zip(cfl, cfl_ref):
        checks.append({"quantity": "tsKCourant", "code": got, "reference": ref,
                       "source": "dt = Kcour*h/max(v_signal,c)  (sph/include/sph/kernels.hpp:16-22)",
                       "rel_err": abs(got - ref) / abs(ref), "pass_3pct": abs(got - ref) / abs(ref) < 0.03})
    # 人工黏性解析值: w_ij<0 -> (-(0.25*(a_i+a_j)*(c_i+c_j) - beta*w_ij)) * w_ij, beta=2
    av_ref = [(0.25 * 2.0 * 20.0 + 2.0 * 5.0) * 5.0, 0.0]
    for got, ref in zip(av, av_ref):
        checks.append({"quantity": "artificial_viscosity", "code": got, "reference": ref,
                       "source": "sph/include/sph/kernels.hpp:147-160 (Monaghan-type, beta=2)",
                       "rel_err": (abs(got - ref) / abs(ref)) if ref else abs(got - ref),
                       "pass_3pct": ((abs(got - ref) / abs(ref)) if ref else abs(got - ref)) < 0.03})

    max_leap = max(x["abs_err"] for x in leap) if leap else float("nan")
    doc = {
        "repo": "sphexa-org/sphexa",
        "commit": args.commit,
        "compiler": subprocess.run([args.cxx, "--version"], capture_output=True, text=True).stdout.splitlines()[0],
        "probe": {"flags": "-std=c++20 -O2", "headers": ["sph/sph_kernels.hpp", "sph/positions.hpp"]},
        "kernel_constants": vals,
        "kernel_shapes": shapes,
        "leapfrog": leap,
        "leapfrog_max_abs_err": max_leap,
        "cfl": cfl,
        "artificial_viscosity": av,
        "checks": checks,
        "summary": {
            "checked": len(checks),
            "all_within_3pct": all(c["pass_3pct"] for c in checks),
            "max_rel_err": max(c["rel_err"] for c in checks),
            "leapfrog_exact_for_const_accel": max_leap < 1e-12,
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(json.dumps(doc["summary"], indent=1))
    for c in checks:
        print(f"  {c['quantity']:<34} code={c['code']:.12g} ref={c['reference']:.12g} rel={c['rel_err']:.3e}")

    if args.figdir:
        make_figures(doc, args.figdir)
    return 0 if doc["summary"]["all_within_3pct"] else 1


def make_figures(doc, figdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(figdir, exist_ok=True)
    x = [2.0 * j / 100.0 for j in range(101)]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    labels = {"sinc4": "sinc^4 (K=%.4f)" % doc["kernel_constants"]["K_sinc4"],
              "sinc9": "sinc^9 (K=%.4f)" % doc["kernel_constants"]["K_sinc9"],
              "mix": "0.9*sinc^4+0.1*sinc^9 (SPHYNX)",
              "wc2": "Wendland C2 (K=%.4f)" % doc["kernel_constants"]["K_wendlandC2"],
              "wc4": "Wendland C4 (K=%.4f)" % doc["kernel_constants"]["K_wendlandC4"],
              "wc6": "Wendland C6 (K=%.4f)" % doc["kernel_constants"]["K_wendlandC6"]}
    for k, y in doc["kernel_shapes"].items():
        ax[0].plot(x, y, label=labels.get(k, k), lw=1.6)
    ax[0].set_xlabel("x = r/h")
    ax[0].set_ylabel("W(x)/h^3  (K-normalized)")
    ax[0].set_title("SPH-EXA smoothing kernels (sph_kernels.hpp)")
    ax[0].legend(fontsize=7)
    ax[0].grid(alpha=.3)

    q = [c for c in doc["checks"]]
    names = [c["quantity"].replace("K_", "").replace(" hardcoded-vs-recomputed", "\n(hard-coded vs recomp.)") for c in q]
    errs = [max(c["rel_err"], 1e-18) for c in q]
    ax[1].barh(range(len(errs)), errs, color="#3b7dd8")
    ax[1].set_yticks(range(len(errs)))
    ax[1].set_yticklabels(names, fontsize=6.5)
    ax[1].axvline(0.03, color="r", ls="--", lw=1, label="3% acceptance")
    ax[1].set_xscale("log")
    ax[1].set_xlabel("relative error vs analytic reference")
    ax[1].set_title("Independent recomputation (max rel err = %.1e)" % doc["summary"]["max_rel_err"])
    ax[1].legend(fontsize=7)
    ax[1].grid(alpha=.3, axis="x")
    fig.tight_layout()
    fig.savefig(os.path.join(figdir, "fig1_kernels_and_recompute.png"), dpi=150)

    fig2, ax2 = plt.subplots(figsize=(5.2, 3.6))
    dts = [d["dt"] for d in doc["leapfrog"]]
    errs = [max(d["abs_err"], 1e-20) for d in doc["leapfrog"]]
    ax2.loglog(dts, errs, "o-", label="leapfrog vs analytic (const a)")
    ax2.set_xlabel("dt")
    ax2.set_ylabel("|x_num - x_exact|")
    ax2.set_title("positionUpdate() exactness on constant acceleration")
    ax2.grid(alpha=.3, which="both")
    ax2.legend(fontsize=8)
    fig2.tight_layout()
    fig2.savefig(os.path.join(os.path.dirname(os.path.join(figdir, "x")), "fig2_leapfrog_error.png"), dpi=150)
    print("figures written to", figdir)


if __name__ == "__main__":
    sys.exit(main())