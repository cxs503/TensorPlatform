/* common.h -- minimal JSON emitter for the cross-ISA reproducibility kernels.
 *
 * Design rule: the kernel C code carries NO hashing. It emits exact hex
 * floating point literals ("%a", C99, round-trip exact) and the Python layer
 * (tools/repro_digest/emit_digest.py) is the single source of truth for
 * digests / quantization / ULP arithmetic. This keeps the "what was computed"
 * layer independent of the "how is it judged" layer.
 */
#ifndef REPRO_COMMON_H
#define REPRO_COMMON_H

/* ---- libm switch -------------------------------------------------------
 * REPRO_LIBM_ALT=1 routes pow/exp/log through the long-double entry points
 * and rounds back to double.  On this platform long double is 128-bit
 * soft-float (x86: 80-bit x87), i.e. a *different rounding path* with its own
 * argument-reduction constants -- a controlled proxy for "built against a
 * different libm", which is what a real cross-ISA comparison faces.
 */
#ifdef REPRO_LIBM_ALT
#define RP_POW(x, y) ((double)powl((long double)(x), (long double)(y)))
#define RP_EXP(x)    ((double)expl((long double)(x)))
#define RP_LOG(x)    ((double)logl((long double)(x)))
#else
#define RP_POW(x, y) pow((x), (y))
#define RP_EXP(x)    exp(x)
#define RP_LOG(x)    log(x)
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef BUILD_ARCH
#define BUILD_ARCH "unknown"
#endif
#ifndef BUILD_CC
#define BUILD_CC "unknown"
#endif
#ifndef BUILD_FLAGS
#define BUILD_FLAGS "unknown"
#endif
#ifndef BUILD_VARIANT
#define BUILD_VARIANT "base"
#endif

static void repro_hex(char *buf, size_t nb, double x)
{
    if (x != x) { snprintf(buf, nb, "nan"); return; }
    if (x > 1.7976931348623157e308) { snprintf(buf, nb, "inf"); return; }
    if (x < -1.7976931348623157e308) { snprintf(buf, nb, "-inf"); return; }
    snprintf(buf, nb, "%a", x);
}

/* Open the JSON object + metadata block. */
static void repro_head(FILE *f, const char *bench, const char *extra_meta)
{
    fprintf(f, "{\"bench\":\"%s\",", bench);
    fprintf(f, "\"meta\":{\"arch\":\"%s\",\"cc\":\"%s\",\"flags\":\"%s\",\"variant\":\"%s\"%s},",
            BUILD_ARCH, BUILD_CC, BUILD_FLAGS, BUILD_VARIANT,
            extra_meta ? extra_meta : "");
}

static void repro_field(FILE *f, const char *name, const double *v, int n)
{
    char b[64];
    fprintf(f, "\"%s\":{\"n\":%d,\"values\":[", name, n);
    for (int i = 0; i < n; i++) {
        repro_hex(b, sizeof b, v[i]);
        fprintf(f, "%s\"%s\"", i ? "," : "", b);
    }
    fprintf(f, "]}");
}

static void repro_scalar(FILE *f, const char *name, double x)
{
    char b[64];
    repro_hex(b, sizeof b, x);
    fprintf(f, "\"%s\":\"%s\"", name, b);
}

/* traj point: step + a few probe hexes (fixed width encoded by caller). */
static void repro_traj_point(FILE *f, int first, long step, const double *probe, int np)
{
    char b[64];
    fprintf(f, "%s{\"step\":%ld,\"probe\":[", first ? "" : ",", step);
    for (int i = 0; i < np; i++) {
        repro_hex(b, sizeof b, probe[i]);
        fprintf(f, "%s\"%s\"", i ? "," : "", b);
    }
    fprintf(f, "]}");
}

#endif