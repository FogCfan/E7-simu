#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif
/* E7 Twin - cas d'echec memoire : tableau de 2 Mo place dans la DTCM du HE (reelle : 256 Ko).
 *
 * Pour que le firmware COMPILE et que ce soit `e7twin check` qui refuse, RTE/Device/.../app_mem_regions.h
 * de CE projet declare une DTCM HE de 4 Mo (APP_HE_DTCM_SIZE) au lieu de APP_SRAM5_SIZE (256 Ko).
 * Le lien reussit donc, mais l'image occupe 0x20000000..0x2020xxxx : hors de la DTCM reelle.
 * Ne jamais flasher cet ELF. */
#define BIG_BYTES (2u * 1024u * 1024u)

/* .bss : le script de lien le place dans la DTCM. volatile : le tableau ne peut pas etre elimine. */
static volatile uint8_t big[BIG_BYTES];

int main(void)
{
    if (stdout_init() != 0) {
        while (1) {
        }
    }

    for (uint32_t i = 0; i < BIG_BYTES; i += 4096u) {
        big[i] = (uint8_t)i;
    }
    printf("\r\ntoo_big: %lu\r\n", (unsigned long)big[4096]);

    while (1) {
        __WFE();
    }
}
