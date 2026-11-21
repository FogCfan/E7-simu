/* (c) Dual : même source compilée en HP (-DDUAL_HP, UART4) et HE (-DDUAL_HE, LPUART).
 * Teste : textes distincts, TCM locales (même adresse 0x20000000, contenu propre à chaque cœur),
 * mémoire partagée (SRAM0) et IRQ UART7 envoyée aux deux NVIC (HP lève la ligne, HE la voit aussi). */
#include "common.h"

#if defined(DUAL_HP)
#  define CON  E7_UART4_BASE
#  define NAME "HP"
#  define MARK 0xA5A5A5A5u
#else
#  define CON  E7_LPUART_BASE
#  define NAME "HE"
#  define MARK 0x5A5A5A5Au
#endif

/* Drapeaux partagés en SRAM0 */
#define SH ((volatile uint32_t *)E7_SHARED_BASE)
#define SH_HP_SEEN 0
#define SH_HE_SEEN 1

static volatile uint32_t irq_seen;
/* Placé en DTCM locale : même adresse sur les deux cœurs, contenu distinct. */
static volatile uint32_t tcm_marker;

void UART7_IRQHandler(void) {
    irq_seen++;
    NVIC_ICER(E7_UART7_IRQ) = 1u << (E7_UART7_IRQ & 31);
#if defined(DUAL_HP)
    SH[SH_HP_SEEN] = 1;
#else
    SH[SH_HE_SEEN] = 1;
#endif
}

static void delay(volatile uint32_t n) { while (n--) { } }

int main(void) {
    int ok = 1;
    NVIC_ISER(E7_UART7_IRQ) = 1u << (E7_UART7_IRQ & 31);
#if defined(DUAL_HP)
    SH[SH_HP_SEEN] = 0;
    SH[SH_HE_SEEN] = 0;
#endif
    tcm_marker = MARK;
    uart_puts(CON, "Hello E7 " NAME " dual\n");
    delay(200000); /* laisse l'autre cœur écrire sa propre marque à la même adresse locale */
    ok &= (tcm_marker == MARK);
    uart_puts(CON, "tcm " NAME " addr=");
    uart_puthex(CON, (uint32_t)(uintptr_t)&tcm_marker);
    uart_puts(CON, ok ? " intact\n" : " CLOBBERED\n");

#if defined(DUAL_HP)
    /* HP lève la ligne UART7 : interruption THRE (IER bit1) -> IRQ vers nvic_hp ET nvic_he */
    UART_IER(E7_UART7_BASE) = 0x2;
    uint32_t t = 2000000;
    while ((!irq_seen || !SH[SH_HE_SEEN]) && --t) { }
    UART_IER(E7_UART7_BASE) = 0;
    ok &= (irq_seen == 1) && SH[SH_HE_SEEN];
    uart_puts(CON, "irq fanout hp=");
    uart_puthex(CON, irq_seen);
    uart_puts(CON, " he=");
    uart_puthex(CON, SH[SH_HE_SEEN]);
    uart_puts(CON, "\n");
#else
    uint32_t t = 2000000;
    while (!irq_seen && --t) { }
    ok &= (irq_seen == 1);
    uart_puts(CON, "irq fanout he=");
    uart_puthex(CON, irq_seen);
    uart_puts(CON, "\n");
#endif
    uart_puts(CON, ok ? "DUAL " NAME " OK\n" : "DUAL " NAME " FAIL\n");
    for (;;) __asm volatile("wfi");
}
