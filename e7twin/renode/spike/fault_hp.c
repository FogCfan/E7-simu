/* HP : provoque un HardFault (instruction indéfinie -> UsageFault non activée -> escalade) pour tester la détection. */
#include "common.h"

int main(void) {
    uart_puts(E7_UART4_BASE, "ABOUT TO FAULT\n");
    __asm volatile(".short 0xde00"); /* udf #0 */
    uart_puts(E7_UART4_BASE, "NOT REACHED\n");
    for (;;) { }
}
