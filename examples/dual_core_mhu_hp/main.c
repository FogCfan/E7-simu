#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include CMSIS_device_header

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif

#include "mhu.h"

/* E7 Twin - ping-pong MHU HP -> HE -> HP avec le pilote MHU du SDK Alif (drivers/source/mhu_*.c).
 * Lien HP<->HE n°0, canal 0. HP envoie PING n (0x5049nnnn), HE repond PONG n (0x504Fnnnn), 100 fois.
 * Seul le HP ecrit sur l'UART4 (le HE n'initialise aucune UART : pas d'entrelacement sur la console partagee).
 * Le SDK ne gere pas l'IRQ lui-meme : les handlers ci-dessous appellent ceux du pilote. */
#define ROUNDS      100u
#define PING(n)     (0x50490000u | (n))
#define PONG(n)     (0x504F0000u | (n))
#define SPIN        50000000u

static uint32_t tx_list[1] = {MHU_RTSS_S_TX_BASE};
static uint32_t rx_list[1] = {MHU_RTSS_S_RX_BASE};
static mhu_driver_out_t mhu;
static volatile uint32_t acks, rx_count, rx_val;

static void on_acked(uint32_t id, uint32_t ch) { (void)id; (void)ch; acks++; }
static void on_rx(uint32_t id, uint32_t ch, uint32_t data) { (void)id; (void)ch; rx_val = data; rx_count++; }

void MHU_RTSS_S_TX_IRQHandler(void) { mhu.sender_irq_handler(0); }
void MHU_RTSS_S_RX_IRQHandler(void) { mhu.receiver_irq_handler(0); }

static int wait_for(volatile uint32_t *v, uint32_t target)
{
    for (uint32_t t = SPIN; t; t--) {
        if (*v == target) return 0;
    }
    return -1;
}

int main(void)
{
    if (stdout_init() != 0) { while (1) { } }

    mhu_driver_in_t in = {
        .sender_base_address_list = tx_list, .receiver_base_address_list = rx_list, .mhu_count = 1,
        .send_msg_acked_callback = on_acked, .rx_msg_callback = on_rx, .debug_print = NULL,
    };
    MHU_driver_initialize(&in, &mhu);
    NVIC_EnableIRQ(MHU_RTSS_S_RX_IRQ_IRQn);
    NVIC_EnableIRQ(MHU_RTSS_S_TX_IRQ_IRQn);

    printf("\r\nHP: MHU ping start\r\n");
    for (uint32_t n = 1; n <= ROUNDS; n++) {
        if (wait_for(&acks, n - 1u)) { printf("HP: FAIL ack %lu\r\n", (unsigned long)n); break; }
        mhu_send_status_t st = mhu.send_message(0, 0, PING(n));
        if (st != MHU_SEND_OK) { printf("HP: FAIL send %lu status=%d\r\n", (unsigned long)n, (int)st); break; }
        if (wait_for(&rx_count, n)) { printf("HP: FAIL timeout PONG %lu\r\n", (unsigned long)n); break; }
        if (rx_val != PONG(n)) { printf("HP: FAIL bad reply %lx for %lu\r\n", (unsigned long)rx_val, (unsigned long)n); break; }
        if (n == ROUNDS) printf("PONG %lu\r\n", (unsigned long)(rx_val & 0xFFFFu));
    }
    while (1) { __WFE(); }
}
