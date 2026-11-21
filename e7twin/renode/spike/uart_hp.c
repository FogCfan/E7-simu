/* (a) HP : "Hello E7 HP" sur UART4 en polling 16550, exécuté en XIP depuis le slot MRAM HP. */
#include "common.h"

int main(void) {
    uart_puts(E7_UART4_BASE, "Hello E7 HP\n");
    for (;;) __asm volatile("wfi");
}
