/* Lattice-BGK channel-flow kernel (D2Q9), used purely as a *timing workload*
 * for cross-architecture benchmark normalisation.  It is a self-contained
 * numerical kernel: no I/O beyond a single JSON line on stdout, no clock in
 * the hot loop, no library math calls.
 *
 * Geometry: nx x ny lattice, periodic in x, halfway bounce-back walls in y,
 * constant body force g in +x.  With BGK relaxation time tau and
 * nu = cs^2 (tau - 0.5), the steady analytic profile is the Poiseuille
 * parabola u(y) = g/(2 nu) y (H - y) with H = ny - 1 (wall-to-wall in
 * lattice units), whose maximum is u_max = g H^2 / (8 nu).
 *
 * Output JSON:
 *   elapsed_s  : internal monotonic wall time of the timed time-stepping loop
 *   u_max_num  : measured peak streamwise velocity
 *   u_max_ana  : analytic peak velocity of the continuum parabola
 *   l2_err     : relative L2 error of the computed profile vs analytic one
 *   mass_drift : |rho(n)-rho(0)| / rho(0)
 *   checksum   : FNV-1a over the double bits of the final velocity field
 *
 * Usage: lbm_channel <nx> <ny> <steps> <tau> <g> [--warmup N] [--repeat R]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <time.h>

#define Q 9

static const int cx[Q] = {0, 1, 0, -1, 0, 1, -1, -1, 1};
static const int cy[Q] = {0, 0, 1, 0, -1, 1, 1, -1, -1};
static const double w[Q] = {4.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0,
                            1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0};
static const int opp[Q] = {0, 3, 4, 1, 2, 7, 8, 5, 6};
static const double cs2 = 1.0 / 3.0;

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

typedef struct {
    int nx, ny;
    double *f;      /* f[i][y*nx + x], i in 0..8 */
    double *rho, *ux, *uy;
} Sim;

static void sim_alloc(Sim *s, int nx, int ny) {
    s->nx = nx; s->ny = ny;
    s->f = (double *)calloc((size_t)Q * nx * ny, sizeof(double));
    s->rho = (double *)calloc((size_t)nx * ny, sizeof(double));
    s->ux = (double *)calloc((size_t)nx * ny, sizeof(double));
    s->uy = (double *)calloc((size_t)nx * ny, sizeof(double));
    if (!s->f || !s->rho || !s->ux || !s->uy) { fprintf(stderr, "oom\n"); exit(2); }
    for (int y = 0; y < ny; y++)
        for (int x = 0; x < nx; x++)
            for (int i = 0; i < Q; i++) s->f[(size_t)i * nx * ny + (size_t)y * nx + x] = w[i];
}

static void sim_free(Sim *s) {
    free(s->f); free(s->rho); free(s->ux); free(s->uy);
}

/* macroscopic moments, optionally with the half-force shift u* = u + g/(2 rho) */
static void macros(Sim *s, double g) {
    const int nx = s->nx, ny = s->ny;
    for (int y = 0; y < ny; y++) {
        for (int x = 0; x < nx; x++) {
            size_t idx = (size_t)y * nx + x;
            double r = 0.0, mx = 0.0, my = 0.0;
            for (int i = 0; i < Q; i++) {
                double fi = s->f[(size_t)i * nx * ny + idx];
                r += fi;
                mx += cx[i] * fi;
                my += cy[i] * fi;
            }
            s->rho[idx] = r;
            s->ux[idx] = mx / r;
            s->uy[idx] = my / r;
        }
    }
}

/* one BGK step with Guo forcing; returns nothing, mutates s->f in place */
static void step(Sim *s, double tau, double g) {
    const int nx = s->nx, ny = s->ny;
    const double om = 1.0 / tau;
    macros(s, g);
    /* collide + stream (pull scheme) */
    static double *tmp = NULL;
    static size_t tmp_n = 0;
    size_t need = (size_t)Q * nx * ny;
    if (need > tmp_n) { free(tmp); tmp = (double *)malloc(need * sizeof(double)); tmp_n = need; }
    if (!tmp) { fprintf(stderr, "oom\n"); exit(2); }
    for (int y = 0; y < ny; y++) {
        for (int x = 0; x < nx; x++) {
            size_t idx = (size_t)y * nx + x;
            double r = s->rho[idx], ux = s->ux[idx], uy = s->uy[idx];
            double uxh = ux + 0.5 * g / r, uyh = uy;    /* Guo: equilibrium uses u*  */
            for (int i = 0; i < Q; i++) {
                double cu = cx[i] * uxh + cy[i] * uyh;
                double feq = w[i] * r * (1.0 + cu / cs2 + 0.5 * (cu * cu) / (cs2 * cs2)
                                         - 0.5 * (uxh * uxh + uyh * uyh) / cs2);
                double src = w[i] * (3.0 * (cx[i] - uxh) * g + 9.0 * (cx[i] * uxh) * cx[i] * g);
                double fpost = s->f[(size_t)i * nx * ny + idx] * (1.0 - om) + om * feq
                               + (1.0 - 0.5 * om) * src;
                int xs = ((x - cx[i]) % nx + nx) % nx;
                int ys = y - cy[i];
                if (ys < 0 || ys >= ny) {                 /* bounce-back */
                    tmp[(size_t)opp[i] * nx * ny + idx] = fpost;
                } else {
                    tmp[(size_t)i * nx * ny + (size_t)ys * nx + xs] = fpost;
                }
            }
        }
    }
    memcpy(s->f, tmp, need * sizeof(double));
}

static double max_u(const Sim *s) {
    double m = 0.0;
    size_t n = (size_t)s->nx * s->ny;
    for (size_t k = 0; k < n; k++) if (s->ux[k] > m) m = s->ux[k];
    return m;
}

static double total_rho(const Sim *s) {
    double t = 0.0;
    size_t n = (size_t)s->nx * s->ny;
    for (size_t k = 0; k < n; k++) t += s->rho[k];
    return t / (double)n;
}

static double profile_l2_rel(const Sim *s, double g, double nu, double *umax_ana) {
    const int nx = s->nx, ny = s->ny;
    const double H = (double)ny;   /* half-way bounce-back: walls at y=0 and y=ny */
    double num = 0.0, den = 0.0, umax = 0.0;
    /* average streamwise velocity over x for each y */
    for (int y = 0; y < ny; y++) {
        double acc = 0.0;
        for (int x = 0; x < nx; x++) acc += s->ux[(size_t)y * nx + x];
        double uy = acc / (double)nx;
        double ym = (double)y;               /* first fluid node at y=0.5 */
        double yp = ym + 0.5;
        double ua = g / (2.0 * nu) * yp * (H - yp);
        num += (uy - ua) * (uy - ua);
        den += ua * ua;
        if (ua > umax) umax = ua;
    }
    *umax_ana = umax;
    return den > 0 ? sqrt(num / den) : 0.0;
}

static uint64_t fnv1a(const void *buf, size_t n) {
    const unsigned char *p = (const unsigned char *)buf;
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ULL; }
    return h;
}

int main(int argc, char **argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s <nx> <ny> <steps> <tau> <g> [--warmup N] [--repeat R]\n", argv[0]);
        return 2;
    }
    int nx = atoi(argv[1]), ny = atoi(argv[2]), steps = atoi(argv[3]);
    double tau = atof(argv[4]), g = atof(argv[5]);
    int warmup = 100, repeat = 1;
    for (int i = 6; i < argc; i++) {
        if (!strcmp(argv[i], "--warmup") && i + 1 < argc) warmup = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--repeat") && i + 1 < argc) repeat = atoi(argv[++i]);
    }
    if (nx < 4 || ny < 4 || steps < 1 || tau <= 0.5) { fprintf(stderr, "bad args\n"); return 2; }

    Sim s; sim_alloc(&s, nx, ny);
    for (int k = 0; k < warmup; k++) step(&s, tau, g);
    double rho0 = total_rho(&s);

    double t0 = now_s();
    for (int r = 0; r < repeat; r++)
        for (int k = 0; k < steps; k++) step(&s, tau, g);
    double elapsed = now_s() - t0;

    double nu = cs2 * (tau - 0.5), umax_ana = 0.0;
    double l2 = profile_l2_rel(&s, g, nu, &umax_ana);
    double drift = fabs(total_rho(&s) - rho0) / rho0;
    uint64_t h = fnv1a(s.ux, (size_t)nx * ny * sizeof(double));

    printf("{\"elapsed_s\":%.9g,\"u_max_num\":%.17g,\"u_max_ana\":%.17g,"
           "\"l2_err\":%.9g,\"mass_drift\":%.9g,\"checksum\":\"%016llx\","
           "\"nx\":%d,\"ny\":%d,\"steps\":%d,\"repeat\":%d}\n",
           elapsed, max_u(&s), umax_ana, l2, drift, (unsigned long long)h,
           nx, ny, steps, repeat);
    sim_free(&s);
    return 0;
}