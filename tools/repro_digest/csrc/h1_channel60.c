/* h1_channel60.c -- H1: ice-laden channel, 60 s of physical time.
 *
 * Physical problem (碎冰通道 / ice-water channel):
 *   Forced channel flow between two plates, H = 1 m, with an ice-fraction
 *   profile phi(z) and a temperature field that makes the effective kinematic
 *   viscosity temperature dependent (Arrhenius-type):
 *       nu_eff = nu0 * RP_EXP(-beta*(T - T_ref))
 *
 *     du/dt = nu_eff(T) * d2u/dz2 + f           (f = body force per mass)
 *     dT/dt = alpha * d2T/dz2
 *
 *   No-slip u=0 at both walls; T(0)=263.15 K (ice-covered wall),
 *   T(H)=271.15 K (open water).  Explicit FTCS, dt = 0.01 s, 6000 steps =
 *   exactly 60 s.  Stability: nu_eff*dt/dz^2 < 0.5 with the numbers below.
 *
 * Why this is a reproducibility artefact:
 *   - the stencil sum (u[i+1] - 2u[i] + u[i-1]) is FMA-contractible
 *     (nu_eff*lap + f is a textbook a*b+c),
 *   - RP_EXP() comes from libm,
 *   - the integral diagnostics sum N terms in a fixed order.
 *   -> three independent divergence mechanisms, separable by compiler flags.
 *
 * Outputs: traj (1 s resolution probe, 4 values x 60 checkpoints) for
 * first-divergence localisation, integral diagnostics, final fields.
 */
#include "common.h"

#define N 64
#define STEPS 6000
#define CKPT 60        /* every 1 s of physical time */
#define NPROBE 4

static double nu_eff(double T)
{
    return 4.0e-3 * RP_EXP(-0.05 * (T - 265.0));
}

int main(void)
{
    static double u[N], un[N], T[N], Tn[N], phi[N];
    const double H = 1.0, dz = H / (N - 1), dt = 0.01;
    const double alpha = 5.0e-4, f = 0.02;
    const double Tw0 = 263.15, Tw1 = 271.15;
    double probe[NPROBE];
    int first = 1;

    for (int i = 0; i < N; i++) {
        double z = i * dz;
        u[i] = 0.0;
        T[i] = Tw0 + (Tw1 - Tw0) * z / H;
        phi[i] = 0.35 * (1.0 - 0.6 * z / H);
    }
    u[0] = u[N - 1] = 0.0;
    T[0] = Tw0; T[N - 1] = Tw1;

    repro_head(stdout, "h1_channel60",
               ",\"steps\":6000,\"dt\":0.01,\"H\":1.0,\"N\":64");

    fprintf(stdout, "\"traj\":[");
    for (int s = 1; s <= STEPS; s++) {
        for (int i = 1; i < N - 1; i++) {
            double nu = nu_eff(T[i]);
            double lap = (u[i + 1] - 2.0 * u[i] + u[i - 1]) / (dz * dz);
            double lapT = (T[i + 1] - 2.0 * T[i] + T[i - 1]) / (dz * dz);
            un[i] = u[i] + dt * (nu * lap + f);
            Tn[i] = T[i] + dt * (alpha * lapT);
        }
        un[0] = 0.0; un[N - 1] = 0.0;
        Tn[0] = Tw0; Tn[N - 1] = Tw1;
        for (int i = 0; i < N; i++) { u[i] = un[i]; T[i] = Tn[i]; }

        if (s % CKPT == 0) {
            probe[0] = u[16]; probe[1] = u[32]; probe[2] = u[48]; probe[3] = T[32];
            repro_traj_point(stdout, first, s, probe, NPROBE);
            first = 0;
        }
    }
    fprintf(stdout, "],");

    /* integral diagnostics: fixed-order sums (trapezoid) */
    double mean_u = 0.0, ke = 0.0, ice_drag = 0.0;
    for (int i = 0; i < N - 1; i++) {
        mean_u += 0.5 * (u[i] + u[i + 1]) * dz;
        ke += 0.25 * (u[i] * u[i] + u[i + 1] * u[i + 1]) * dz;
        ice_drag += 0.5 * (phi[i] * u[i] + phi[i + 1] * u[i + 1]) * dz;
    }
    double tau_wall = nu_eff(T[1]) * (u[1] - u[0]) / dz;
    double mom_res = f * H - 2.0 * tau_wall;

    fprintf(stdout, "\"scalars\":{");
    repro_scalar(stdout, "u_mid", u[32]);       fprintf(stdout, ",");
    repro_scalar(stdout, "T_mid", T[32]);       fprintf(stdout, ",");
    repro_scalar(stdout, "tau_wall", tau_wall); fprintf(stdout, ",");
    repro_scalar(stdout, "mean_u", mean_u);     fprintf(stdout, ",");
    repro_scalar(stdout, "ke", ke);             fprintf(stdout, ",");
    repro_scalar(stdout, "ice_drag", ice_drag); fprintf(stdout, ",");
    repro_scalar(stdout, "mom_residual", mom_res);
    fprintf(stdout, "},");

    fprintf(stdout, "\"fields\":{");
    repro_field(stdout, "u", u, N);
    fprintf(stdout, ",");
    repro_field(stdout, "T", T, N);
    fprintf(stdout, "}}\n");
    return 0;
}