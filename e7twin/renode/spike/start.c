/* Table des vecteurs + Reset_Handler écrits à la main (sans SDK).
 * Le .isr_vector est placé à l'adresse de base du slot MRAM du cœur (XIP). */
#include "common.h"

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;
extern int main(void);

void Reset_Handler(void);
void Default_Handler(void) { for (;;) { } }
void HardFault_Handler(void) {
    uart_puts(E7_FAULT_UART, "\nHARDFAULT HFSR=");
    uart_puthex(E7_FAULT_UART, REG32(0xE000ED2Cu));
    uart_puts(E7_FAULT_UART, " CFSR=");
    uart_puthex(E7_FAULT_UART, REG32(0xE000ED28u));
    uart_puts(E7_FAULT_UART, "\n");
    for (;;) { }
}
void NMI_Handler(void)         __attribute__((weak, alias("Default_Handler")));
void MemManage_Handler(void)   __attribute__((weak, alias("HardFault_Handler")));
void BusFault_Handler(void)    __attribute__((weak, alias("HardFault_Handler")));
void UsageFault_Handler(void)  __attribute__((weak, alias("HardFault_Handler")));
void SecureFault_Handler(void) __attribute__((weak, alias("HardFault_Handler")));
void SVC_Handler(void)         __attribute__((weak, alias("Default_Handler")));
void PendSV_Handler(void)      __attribute__((weak, alias("Default_Handler")));
void SysTick_Handler(void)     __attribute__((weak, alias("Default_Handler")));
/* IRQ de l'UART7 (fan-out HP+HE du scénario dual) */
void UART7_IRQHandler(void)    __attribute__((weak, alias("Default_Handler")));
/* IRQ du MHU HP<->HE canal 0 (scénario mhu_pingpong) : RX = message reçu, TX = acquittement */
void MHU_RX_IRQHandler(void)   __attribute__((weak, alias("Default_Handler")));
void MHU_TX_IRQHandler(void)   __attribute__((weak, alias("Default_Handler")));

__attribute__((section(".isr_vector"), used))
const void *const vectors[16 + E7_NUM_IRQS] = {
    [0] = &_estack,
    [1] = Reset_Handler,
    [2] = NMI_Handler,
    [3] = HardFault_Handler,
    [4] = MemManage_Handler,
    [5] = BusFault_Handler,
    [6] = UsageFault_Handler,
    [7] = SecureFault_Handler,
    [11] = SVC_Handler,
    [14] = PendSV_Handler,
    [15] = SysTick_Handler,
    [16 + E7_UART7_IRQ] = UART7_IRQHandler,
    [16 + E7_MHU_RX_IRQ] = MHU_RX_IRQHandler,
    [16 + E7_MHU_TX_IRQ] = MHU_TX_IRQHandler,
};

void Reset_Handler(void) {
    uint32_t *s = &_sidata, *d = &_sdata;
    while (d < &_edata) *d++ = *s++;
    for (d = &_sbss; d < &_ebss;) *d++ = 0;
    REG32(0xE000ED88u) |= (0xFu << 20); /* CPACR : accès complet CP10/CP11 (FPU + MVE) */
    __asm volatile("dsb\n isb");
    main();
    for (;;) { }
}
