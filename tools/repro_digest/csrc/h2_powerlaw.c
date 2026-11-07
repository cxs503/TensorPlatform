/* h2_powerlaw.c -- H2: power-law (Ostwald-de Waele) slit channel.
 *
 * Physical problem (幂律槽道 / shear-thinning rheology):
 *   Fully developed 1D slit flow of a power-law fluid between plates H = 1 m,
 *   pressure gradient G = -dp/dx = 1 Pa/m:
 *
 *       d/dz ( mu(gdot) du/dz ) = -G ,   mu(gdot) = K |gdot|^(n-1)
 *
 *   with K = 0.35 Pa s^n, n = 0.6 (shear-thinning).  Solved with Picard
 *   iteration on the apparent viscosity (fixed 300 sweeps, no
 *   convergence-dependent control flow -- a deliberate choice so that
 *   iteration-count divergence does not masquerade as arithmetic divergence),
 *   each sweep solving a tridiagonal system by the Thomas algorithm.
 *
 * Analytic reference (exact solution of the steady problem):
 *       u(z) = n/(n+1) (G/K)^(1/n) [ (H/2)^((n+1)/n) - |z-H/2|^((n+1)/n) ]
 *
 * Reproducibility mechanisms exercised:
 *   - RP_POW() from libm, called once per node per sweep (heavily libm-bound),
 *   - Thomas forward/back substitution: sequential dependent sums,
 *   - flow-rate integral Q = int u dz: reduction whose order matters,
 *   - mu_face = 0.5*(mu_i + mu_{i+1}) : FMA-contractible in the stencil.
 */
#include "common.h"

#define NI 48                 /* interior nodes */
#define NG (NI + 2)           /* grid nodes incl. walls */
#define SWEEPS 300
#define NPROBE 4

int main(void)
{
    static double z[NG], u[NG], un[NG], gd[NG], mu[NG], muf[NG];
    static double a[NG], b[NG], c[NG], d[NG], cp[NG], dp[NG];
    const double H = 1.0, G = 1.0, K = 0.35, n = 0.6;
    const double dz = H / (NG - 1), dz2 = dz * dz;
    const double gfloor = 1e-12;
    double probe[NPROBE];
    int first = 1, sweep;

    for (int i = 0; i < NG; i++) { z[i] = i * dz; u[i] = 0.0; }

    repro_head(stdout, "h2_powerlaw",
               ",\"Ni\":48,\"H\":1.0,\"G\":1.0,\"K\":0.35,\"n\":0.6,\"sweeps\":300");

    fprintf(stdout, "\"traj\":[");
    for (sweep = 1; sweep <= SWEEPS; sweep++) {
        /* apparent viscosity from the current velocity field (central diff) */
        for (int i = 0; i < NG; i++) {
            double du;
            if (i == 0)          du = (u[1] - u[0]) / dz;
            else if (i == NG - 1) du = (u[NG - 1] - u[NG - 2]) / dz;
            else                  du = (u[i + 1] - u[i - 1]) / (2.0 * dz);
            gd[i] = fabs(du);
            mu[i] = K * RP_POW(gd[i] + gfloor, n - 1.0);
        }
        for (int i = 0; i < NG - 1; i++) muf[i] = 0.5 * (mu[i] + mu[i + 1]);

        /* tridiagonal assembly for interior nodes */
        for (int i = 1; i <= NI; i++) {
            a[i] = muf[i - 1] / dz2;
            c[i] = muf[i] / dz2;
            b[i] = -(muf[i - 1] + muf[i]) / dz2;
            d[i] = -G;
        }
        /* Thomas algorithm */
        cp[1] = c[1] / b[1];
        dp[1] = d[1] / b[1];
        for (int i = 2; i <= NI; i++) {
            double m = b[i] - a[i] * cp[i - 1];
            cp[i] = c[i] / m;
            dp[i] = (d[i] - a[i] * dp[i - 1]) / m;
        }
        un[NI] = dp[NI];
        for (int i = NI - 1; i >= 1; i--) un[i] = dp[i] - cp[i] * un[i + 1];
        un[0] = 0.0; un[NG - 1] = 0.0;
        for (int i = 0; i < NG; i++) u[i] = un[i];

        if (sweep % 3 == 0 || sweep == 1) {
            probe[0] = u[NG / 2]; probe[1] = u[NG / 4];
            probe[2] = mu[NG / 2]; probe[3] = muf[NG / 2 - 1];
            repro_traj_point(stdout, first, sweep, probe, NPROBE);
            first = 0;
        }
    }
    fprintf(stdout, "],");

    /* diagnostics */
    double Q = 0.0, res = 0.0, err = 0.0, umax = 0.0, scale = 0.0;
    for (int i = 0; i < NG - 1; i++) Q += 0.5 * (u[i] + u[i + 1]) * dz;
    for (int i = 1; i <= NI; i++) {
        double flux = (muf[i] * (u[i + 1] - u[i]) - muf[i - 1] * (u[i] - u[i - 1])) / dz2;
        res += (flux + G) * (flux + G);
    }
    res = sqrt(res / NI);
    /* analytic profile + max-norm error */
    double pref = (n / (n + 1.0)) * RP_POW(G / K, 1.0 / n);
    double half = H / 2.0;
    for (int i = 0; i < NG; i++) {
        double e = fabs(z[i] - half);
        double ua = pref * (RP_POW(half, (n + 1.0) / n) - RP_POW(e, (n + 1.0) / n));
        double diff = u[i] - ua;
        err += diff * diff;
        if (fabs(u[i]) > umax) umax = fabs(u[i]);
    }
    err = sqrt(err / NG) / umax;
    double tau_wall = muf[0] * (u[1] - u[0]) / dz;
    double gdot_wall = RP_POW(tau_wall / K, 1.0 / n);
    /* analytic flow rate, Q_ref = int_0^H u dz */
    double Qref = 2.0 * pref * (RP_POW(half, (n + 1.0) / n) * half
                 - RP_POW(half, (2.0 * n + 1.0) / n) * n / (2.0 * n + 1.0));
    scale = Qref;

    fprintf(stdout, "\"scalars\":{");
    repro_scalar(stdout, "u_max", umax);            fprintf(stdout, ",");
    repro_scalar(stdout, "u_mid", u[NG / 2]);       fprintf(stdout, ",");
    repro_scalar(stdout, "flow_rate_Q", Q);         fprintf(stdout, ",");
    repro_scalar(stdout, "Q_ref_analytic", Qref);   fprintf(stdout, ",");
    repro_scalar(stdout, "tau_wall", tau_wall);     fprintf(stdout, ",");
    repro_scalar(stdout, "gdot_wall", gdot_wall);   fprintf(stdout, ",");
    repro_scalar(stdout, "momentum_residual", res); fprintf(stdout, ",");
    repro_scalar(stdout, "rel_l2_err_vs_analytic", err);
    fprintf(stdout, "},");

    fprintf(stdout, "\"fields\":{");
    repro_field(stdout, "u", u, NG);
    fprintf(stdout, ",");
    repro_field(stdout, "gdot", gd, NG);
    fprintf(stdout, ",");
    repro_field(stdout, "mu", mu, NG);
    fprintf(stdout, "},");
    repro_scalar(stdout, "Q_scale", scale);
    fprintf(stdout, "}\n");
    return 0;
}