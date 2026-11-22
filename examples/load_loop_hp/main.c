#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif

/* E7 Twin - charge cyclique HP : un tour = 1 s = 100 ticks SysTick de 10 ms (SysTick 24 bits : 1 s ne tient pas). Calcul de 10..90 % de la
 * periode (dents de scie, 5 paliers), mesure avec le compteur SysTick lui-meme (pas de boucle calibree), puis
 * __WFI jusqu'au tick suivant. Une ligne UART4 par tour : "HP tick <n> load <pct>%".
 * Ligne emise avant le calcul : les deux coeurs partagent l'UART4, ceci evite que leurs lignes se melangent. */
#define LABEL "HP"
#define PHASE 0u

#define BEFORE 1

#define TICKS_PER_PERIOD 100u

static volatile uint32_t ticks;
void SysTick_Handler(void) { ticks++; }

/* temps ecoule en cycles depuis le demarrage de SysTick (lecture coherente ticks/VAL) */
static uint64_t now_cycles(uint32_t reload)
{
    uint32_t t0, v, t1;
    do { t0 = ticks; v = SysTick->VAL; t1 = ticks; } while (t0 != t1);
    return (uint64_t)t0 * reload + (reload - 1u - v);
}

int main(void)
{
    if (stdout_init() != 0) { while (1) { } }
    printf("\r\n" LABEL ": load loop start\r\n");

    uint32_t reload = SystemCoreClock / 100u;     /* 10 ms */
    if (SysTick_Config(reload)) { printf(LABEL ": SysTick_Config failed\r\n"); while (1) { } }
    uint64_t period = (uint64_t)reload * TICKS_PER_PERIOD;
    uint32_t round = 0, sink = 0;

    for (;;) {
        while (ticks < (round + 1u) * TICKS_PER_PERIOD) { __WFI(); }   /* veille jusqu'a la fin de la periode */
        round++;
        uint32_t pct = 10u + 20u * ((round - 1u + PHASE) % 5u);       /* 10,30,50,70,90 */
        uint64_t start = (uint64_t)round * period, stop = start + period * pct / 100u;
        if (BEFORE) printf(LABEL " tick %lu load %lu%%\r\n", (unsigned long)round, (unsigned long)pct);
        while (now_cycles(reload) < stop) {                           /* calcul */
            for (uint32_t i = 0; i < 256u; i++) { sink = sink * 1664525u + 1013904223u + i; }
        }
        if (!(BEFORE)) printf(LABEL " tick %lu load %lu%%\r\n", (unsigned long)round, (unsigned long)pct);
        (void)sink;
    }
}
