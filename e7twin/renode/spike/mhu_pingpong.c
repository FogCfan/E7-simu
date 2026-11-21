/* Ping-pong MHU HP<->HE, canal 0, par IRQ : 100 allers-retours.
 * Registres : pack Alif 2.2.1 drivers/include/mhu_driver.h ; séquence d'envoi calquée sur
 * drivers/source/mhu_sender.c (ACCESS_REQUEST/READY, CH_INT_EN, CH_SET) et de réception sur mhu_receiver.c
 * (CH_INT_ST0, CH_ST, CH_CLR). Compilé en HP (-DPING_HP, initiateur, UART4) et en HE (-DPING_HE, répondeur, LPUART).
 * Adresses/IRQ : E7_MHU_* de e7_board.h (générées depuis board.load()). */
#include "common.h"

#define ROUNDS 100
#define TX(off) REG32(E7_MHU_TX_BASE + (off))
#define RX(off) REG32(E7_MHU_RX_BASE + (off))
/* trame : après 124 canaux * 0x20 */
#define F 0xF80u
#define TX_ACC_REQ   TX(F + 0x08)
#define TX_ACC_RDY   TX(F + 0x0C)
#define TX_INT_ST    TX(F + 0x10)
#define TX_INT_CLR   TX(F + 0x14)
#define TX_INT_EN    TX(F + 0x18)
#define TX_CH_INT_ST0 TX(F + 0x20)
#define TX_CH_SET    TX(0x0C)
#define TX_CH_INT_CLR TX(0x14)
#define TX_CH_INT_EN TX(0x18)
#define TX_CH_ST     TX(0x00)
#define RX_CH_ST     RX(0x00)
#define RX_CH_CLR    RX(0x08)
#define RX_INT_EN    RX(F + 0x18)
#define RX_CH_INT_ST0 RX(F + 0x20)

#if defined(PING_HP)
#  define CON E7_UART4_BASE
#else
#  define CON E7_LPUART_BASE
#endif

static volatile uint32_t rx_val, rx_count, acks;

void MHU_RX_IRQHandler(void) {
    if (RX_CH_INT_ST0 & 1u) {
        rx_val = RX_CH_ST;
        RX_CH_CLR = 0xFFFFFFFFu;     /* acquitte : le TX voit son canal libéré */
        rx_count++;
    }
}

void MHU_TX_IRQHandler(void) {
    uint32_t st = TX_INT_ST;
    if (st & 4u) {                   /* CHCOMB : un canal acquitté */
        if (TX_CH_INT_ST0 & 1u) {
            TX_CH_INT_CLR = 1u;
            if (TX_CH_ST == 0 && (TX_CH_INT_EN & 1u)) {
                TX_CH_INT_EN = 0u;
                TX_ACC_REQ = 0u;     /* libère l'accès */
                acks++;
            }
        }
    }
    TX_INT_CLR = st & 3u;            /* NR2R / R2NR */
}

static int send(uint32_t v, uint32_t sent_before) {
    uint32_t t = 2000000;
    while (acks != sent_before && --t) { }       /* le message précédent doit être acquitté */
    if (!t) return -1;
    TX_ACC_REQ = 1u;
    t = 2000000;
    while (!(TX_ACC_RDY & 1u) && --t) { }
    if (!t) return -2;
    TX_CH_INT_CLR = 1u;
    TX_CH_INT_EN = 1u;
    TX_CH_SET = v;
    return 0;
}

static int wait_rx(uint32_t n) {
    uint32_t t = 20000000;
    while (rx_count < n && --t) { }
    return t ? 0 : -1;
}

int main(void) {
    NVIC_ISER(E7_MHU_RX_IRQ) = 1u << (E7_MHU_RX_IRQ & 31);
    NVIC_ISER(E7_MHU_TX_IRQ) = 1u << (E7_MHU_TX_IRQ & 31);
    RX_INT_EN = 4u;                  /* CHCOMB */
    TX_INT_EN = 7u;                  /* NR2R | R2NR | CHCOMB */
#if defined(PING_HP)
    uart_puts(CON, "HP ping start\n");
    for (uint32_t i = 1; i <= ROUNDS; i++) {
        int r = send(0x1000u + i, i - 1);
        if (r) { uart_puts(CON, "PING SEND FAIL\n"); break; }
        if (wait_rx(i)) { uart_puts(CON, "PING TIMEOUT "); uart_puthex(CON, i); uart_puts(CON, "\n"); break; }
        if (rx_val != 0x1000u + i + 1u) { uart_puts(CON, "PING BAD VALUE\n"); break; }
        if (i == ROUNDS) { uart_puts(CON, "PONG 100\n"); }
    }
#else
    for (uint32_t i = 1; i <= ROUNDS; i++) {
        if (wait_rx(i)) { uart_puts(CON, "HE TIMEOUT\n"); break; }
        if (send(rx_val + 1u, i - 1)) { uart_puts(CON, "HE SEND FAIL\n"); break; }
        if (i == ROUNDS) { uart_puts(CON, "HE SERVED 100\n"); }
    }
#endif
    for (;;) __asm volatile("wfi");
}
