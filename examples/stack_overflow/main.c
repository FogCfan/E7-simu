#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif
/* E7 Twin - recursion sans fin sur HP : la pile (8 Ko reserves, en fin de DTCM) deborde.
 * Le comportement attendu sur la carte est un fault (HardFault/BusFault) ; voir tests/test_examples.py
 * pour ce que la simulation montre reellement. */
__attribute__((noinline)) static uint32_t recurse(uint32_t depth)
{
    volatile uint8_t frame[256];
    frame[0] = (uint8_t)depth;
    frame[sizeof frame - 1] = (uint8_t)(depth >> 8);
    return recurse(depth + 1) + frame[0] + frame[sizeof frame - 1];
}

int main(void)
{
    if (stdout_init() != 0) {
        while (1) {
        }
    }

    printf("\r\nSTACK START\r\n");
    printf("result=%lu\r\n", (unsigned long)recurse(0));

    while (1) {
        __WFE();
    }
}
