#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif
/* E7 Twin - firmware de reference : le plus petit programme qui doit passer e7twin check et e7twin run. */
int main(void)
{
    if (stdout_init() != 0) {
        while (1) {
        }
    }

    printf("\r\nHello E7 HP\r\n");

    while (1) {
        __WFE();
    }
}
