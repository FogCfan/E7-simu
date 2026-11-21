/* (b) HP : produit scalaire int8 MVE (vmladavaq_p, prédication de queue) comparé au scalaire,
 * + quelques autres instructions MVE. Affiche "MVE OK" si tout concorde. */
#include <arm_mve.h>
#include "common.h"

#define N 100 /* volontairement non multiple de 16 : exerce la prédication (VCTP) */
static int8_t a[N], b[N];
static float fa[N], fb[N];

static int32_t dot_mve(const int8_t *x, const int8_t *y, int n) {
    int32_t acc = 0;
    while (n > 0) {
        mve_pred16_t p = vctp8q((uint32_t)n);
        acc = vmladavaq_p_s8(acc, vldrbq_z_s8(x, p), vldrbq_z_s8(y, p), p);
        x += 16; y += 16; n -= 16;
    }
    return acc;
}

static float fdot_mve(const float *x, const float *y, int n) {
    float32x4_t acc = vdupq_n_f32(0.0f);
    while (n > 0) {
        mve_pred16_t p = vctp32q((uint32_t)n);
        acc = vfmaq_m_f32(acc, vldrwq_z_f32(x, p), vldrwq_z_f32(y, p), p);
        x += 4; y += 4; n -= 4;
    }
    /* pas de réduction flottante en MVE : somme des 4 voies */
    return (vgetq_lane_f32(acc, 0) + vgetq_lane_f32(acc, 1)) + (vgetq_lane_f32(acc, 2) + vgetq_lane_f32(acc, 3));
}

int main(void) {
    const uintptr_t u = E7_UART4_BASE;
    uint32_t seed = 12345;
    for (int i = 0; i < N; i++) {
        seed = seed * 1664525u + 1013904223u;
        a[i] = (int8_t)(seed >> 24);
        seed = seed * 1664525u + 1013904223u;
        b[i] = (int8_t)(seed >> 24);
        fa[i] = (float)(i % 7) - 3.0f;
        fb[i] = (float)(i % 5) * 0.5f;
    }
    int32_t ref = 0;
    for (int i = 0; i < N; i++) ref += (int32_t)a[i] * b[i];
    int32_t m = dot_mve(a, b, N);
    int ok = (m == ref);
    uart_puts(u, ok ? "mve.s8dot=OK " : "mve.s8dot=FAIL ");
    uart_puthex(u, (uint32_t)m); uart_puts(u, " ref="); uart_puthex(u, (uint32_t)ref); uart_puts(u, "\n");

    float fref = 0.0f;
    for (int i = 0; i < N; i++) fref += fa[i] * fb[i];
    float fm = fdot_mve(fa, fb, N);
    int fok = (fm == fref); /* valeurs petites, exactes en binaire : égalité stricte */
    uart_puts(u, fok ? "mve.f32dot=OK\n" : "mve.f32dot=FAIL\n");

    /* addition saturante int16 : 0x7FFF + 1 doit saturer à 0x7FFF */
    int16x8_t s = vqaddq_s16(vdupq_n_s16(0x7FFF), vdupq_n_s16(1));
    int sok = (vgetq_lane_s16(s, 3) == 0x7FFF);
    uart_puts(u, sok ? "mve.s16sat=OK\n" : "mve.s16sat=FAIL\n");

    if (ok && fok && sok) uart_puts(u, "MVE OK\n");
    else uart_puts(u, "MVE FAIL\n");
    for (;;) __asm volatile("wfi");
}
