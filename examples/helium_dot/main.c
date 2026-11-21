#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif
/* E7 Twin - produit scalaire int8 en MVE (Helium) compare au calcul scalaire. Sur HP.
 * N n'est pas multiple de 16 : la queue passe par le predicat vctp8q. */
#include <arm_mve.h>

#define N 250

static int8_t a[N];
static int8_t b[N];

static int32_t dot_scalar(const int8_t *x, const int8_t *y, int n)
{
    int32_t acc = 0;
    for (int i = 0; i < n; i++) {
        acc += (int32_t)x[i] * (int32_t)y[i];
    }
    return acc;
}

static int32_t dot_mve(const int8_t *x, const int8_t *y, int n)
{
    int32_t acc = 0;
    for (int i = 0; i < n; i += 16) {
        mve_pred16_t p = vctp8q((uint32_t)(n - i));
        int8x16_t vx = vldrbq_z_s8(x + i, p);
        int8x16_t vy = vldrbq_z_s8(y + i, p);
        acc = vmladavaq_p_s8(acc, vx, vy, p);
    }
    return acc;
}

int main(void)
{
    if (stdout_init() != 0) {
        while (1) {
        }
    }

    for (int i = 0; i < N; i++) {
        a[i] = (int8_t)((i * 7) % 251 - 125);
        b[i] = (int8_t)((i * 13) % 241 - 120);
    }

    int32_t s = dot_scalar(a, b, N);
    int32_t v = dot_mve(a, b, N);
    printf("\r\nscalar=%ld mve=%ld\r\n", (long)s, (long)v);
    printf(s == v ? "MVE OK\r\n" : "MVE FAIL\r\n");

    while (1) {
        __WFE();
    }
}
