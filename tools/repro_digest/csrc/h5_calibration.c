/* h5_calibration.c -- H5: three-stage calibration chain (ice/resistance model).
 *
 * Physical problem (校准链 / calibration chain):
 *   A resistance / ice-drag model couples three calibrations that are chained
 *   in order -- each stage consumes the previous stage's fitted parameter, so
 *   a last-bit difference in stage A is amplified by stages B and C:
 *
 *   Stage A  rheology consistency: fit (K, n) of a power-law fluid from
 *            12 synthetic viscometry points  tau_j = Kt gd_j^nt (1 + 0.3% noise),
 *            gd_j log-spaced 0.1 .. 10 1/s  -> log-log least squares.
 *   Stage B  drag law: fit (a, b) of  Cd(V) = a V^b  from 12 tow-test points
 *            V_j = 0.5 .. 6.0 m/s, *weighted* by w_j = gd_j^(n_hat - 1)
 *            (rheology -> drag coupling; the fitted exponent re-enters via pow).
 *   Stage C  added-resistance law: fit (c, p) of  Radd(Fr) = c Fr^p  from 12
 *            Froude-number points Fr_j = 0.05 .. 0.27, by 3 IRLS rounds with
 *            w2_j = Fr_j^(-b_hat) / max(|r_j|, 1e-6)  (fixed 3 rounds =>
 *            deterministic; the drag exponent b_hat weights the wave stage).
 *
 *   Truth (synthetic generators): K=0.35, n=0.60, a=2.10, b=-0.25,
 *   c=0.150, p=2.00.  Noise comes from a fixed LCG (no rand(), no clock,
 *   no environment dependency).
 *
 * Reproducibility mechanisms: pow/exp/log from libm on every stage, three
 * nested reductions with data-dependent magnitudes, and IRLS divisions that
 * amplify last-bit differences.  This is the most drift-amplifying of the
 * three benchmarks -- exactly the case a naive bit-digest gate cannot survive.
 */
#include "common.h"

#define ND 12
#define NPROBE 4

static unsigned long long lcg_state = 20261107ULL;

static double lcg_uni(void) /* uniform in [0,1), 53-bit */
{
    lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
    return (double)(lcg_state >> 11) / 9007199254740992.0;
}

/* weighted log-log least squares y = c0 + s*x  ->  (coef=RP_EXP(c0), exp=s) */
static void wloglog(const double *x, const double *y, const double *w, int n,
                    double *coef, double *expo, double *cost)
{
    double sw = 0.0, swx = 0.0, swy = 0.0, swxx = 0.0, swxy = 0.0;
    for (int j = 0; j < n; j++) {
        double lx = RP_LOG(x[j]), ly = RP_LOG(y[j]);
        sw += w[j]; swx += w[j] * lx; swy += w[j] * ly;
        swxx += w[j] * lx * lx; swxy += w[j] * lx * ly;
    }
    *expo = (sw * swxy - swx * swy) / (sw * swxx - swx * swx);
    double c0 = (swy - *expo * swx) / sw;
    *coef = RP_EXP(c0);
    *cost = 0.0;
    for (int j = 0; j < n; j++)
        *cost += w[j] * (RP_LOG(y[j]) - (c0 + *expo * RP_LOG(x[j]))) * (RP_LOG(y[j]) - (c0 + *expo * RP_LOG(x[j])));
}

int main(void)
{
    static double gd[ND], tau[ND], V[ND], Cd[ND], Fr[ND], Ra[ND];
    static double w[ND], w2[ND], rA[ND], rC[ND], theta[9];
    const double Kt = 0.35, nt = 0.60, at = 2.10, bt = -0.25, ct = 0.150, pt = 2.00;
    const double noise = 0.003;

    for (int j = 0; j < ND; j++) {
        gd[j] = 0.1 * RP_POW(100.0, (double)j / (ND - 1));
        tau[j] = Kt * RP_POW(gd[j], nt) * (1.0 + noise * (2.0 * lcg_uni() - 1.0));
        V[j] = 0.5 + 0.5 * j;
        Cd[j] = at * RP_POW(V[j], bt) * (1.0 + noise * (2.0 * lcg_uni() - 1.0));
        Fr[j] = 0.05 + 0.02 * j;
        Ra[j] = ct * RP_POW(Fr[j], pt) * (1.0 + noise * (2.0 * lcg_uni() - 1.0));
    }

    repro_head(stdout, "h5_calibration",
               ",\"nd\":12,\"K_true\":0.35,\"n_true\":0.60,\"a_true\":2.10,"
               "\"b_true\":-0.25,\"c_true\":0.150,\"p_true\":2.00,\"noise\":0.003");

    /* ---------------- Stage A ---------------- */
    for (int j = 0; j < ND; j++) w[j] = 1.0;
    double Khat, nhat, J_A;
    wloglog(gd, tau, w, ND, &Khat, &nhat, &J_A);

    /* ---------------- Stage B (weights from stage A) ---------------- */
    for (int j = 0; j < ND; j++) w[j] = RP_POW(gd[j], nhat - 1.0);
    double ahat, bhat, J_B;
    wloglog(V, Cd, w, ND, &ahat, &bhat, &J_B);

    /* ---------------- Stage C: 3 fixed IRLS rounds, weights from b_hat ---- */
    double chat = 0.15, phat = 2.0, J_C = 0.0, spread = 0.0;
    int first = 1;
    fprintf(stdout, "\"traj\":[");
    for (int it = 1; it <= 3; it++) {
        for (int j = 0; j < ND; j++) {
            rC[j] = RP_LOG(Ra[j]) - (RP_LOG(chat) + phat * RP_LOG(Fr[j]));
            double ar = fabs(rC[j]);
            w2[j] = RP_POW(Fr[j], -bhat) / (ar > 1.0e-6 ? ar : 1.0e-6);
        }
        wloglog(Fr, Ra, w2, ND, &chat, &phat, &J_C);
        double m = 0.0, v = 0.0;
        for (int j = 0; j < ND; j++) {
            rC[j] = RP_LOG(Ra[j]) - (RP_LOG(chat) + phat * RP_LOG(Fr[j]));
            m += rC[j]; v += rC[j] * rC[j];
        }
        m /= ND; v = sqrt(v / ND - m * m); spread = v;
        double probe[NPROBE];
        probe[0] = chat; probe[1] = phat; probe[2] = ahat; probe[3] = bhat;
        repro_traj_point(stdout, first, it, probe, NPROBE);
        first = 0;
    }
    fprintf(stdout, "],");

    for (int j = 0; j < ND; j++) {
        rA[j] = RP_LOG(tau[j]) - (RP_LOG(Khat) + nhat * RP_LOG(gd[j]));
        rC[j] = RP_LOG(Ra[j]) - (RP_LOG(chat) + phat * RP_LOG(Fr[j]));
    }

    /* parameter vector (what a downstream consumer would ingest) */
    theta[0] = Khat; theta[1] = nhat; theta[2] = ahat; theta[3] = bhat;
    theta[4] = chat; theta[5] = phat; theta[6] = J_A; theta[7] = J_B; theta[8] = J_C;

    fprintf(stdout, "\"scalars\":{");
    repro_scalar(stdout, "K_hat", Khat);   fprintf(stdout, ",");
    repro_scalar(stdout, "n_hat", nhat);   fprintf(stdout, ",");
    repro_scalar(stdout, "a_hat", ahat);   fprintf(stdout, ",");
    repro_scalar(stdout, "b_hat", bhat);   fprintf(stdout, ",");
    repro_scalar(stdout, "c_hat", chat);   fprintf(stdout, ",");
    repro_scalar(stdout, "p_hat", phat);   fprintf(stdout, ",");
    repro_scalar(stdout, "J_A", J_A);      fprintf(stdout, ",");
    repro_scalar(stdout, "J_B", J_B);      fprintf(stdout, ",");
    repro_scalar(stdout, "J_C", J_C);      fprintf(stdout, ",");
    repro_scalar(stdout, "resid_spread_C", spread);
    fprintf(stdout, "},");

    fprintf(stdout, "\"fields\":{");
    repro_field(stdout, "theta", theta, 9);
    fprintf(stdout, ",");
    repro_field(stdout, "resid_A", rA, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "resid_C", rC, ND);
    fprintf(stdout, "},\"data\":{");
    repro_field(stdout, "gd", gd, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "tau", tau, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "V", V, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "Cd", Cd, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "Fr", Fr, ND);
    fprintf(stdout, ",");
    repro_field(stdout, "Radd", Ra, ND);
    fprintf(stdout, "}}\n");
    return 0;
}