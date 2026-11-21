#include "RTE_Components.h"
#include <stdint.h>
#include <stddef.h>
#include CMSIS_device_header

#include "mhu.h"

/* E7 Twin - cote HE du ping-pong MHU (voir dual_core_mhu_hp/main.c). Repond PONG n a chaque PING n.
 * Aucune UART : seul le HP ecrit sur l'UART4 partagee. */
#define ROUNDS      100u
#define PING(n)     (0x50490000u | (n))
#define PONG(n)     (0x504F0000u | (n))
#define SPIN        50000000u

static uint32_t tx_list[1] = {MHU_RTSS_S_TX_BASE};
static uint32_t rx_list[1] = {MHU_RTSS_S_RX_BASE};
static mhu_driver_out_t mhu;
static volatile uint32_t acks, rx_count, rx_val;
volatile uint32_t he_served, he_error;   /* observables au debogueur */

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
    mhu_driver_in_t in = {
        .sender_base_address_list = tx_list, .receiver_base_address_list = rx_list, .mhu_count = 1,
        .send_msg_acked_callback = on_acked, .rx_msg_callback = on_rx, .debug_print = NULL,
    };
    MHU_driver_initialize(&in, &mhu);
    NVIC_EnableIRQ(MHU_RTSS_S_RX_IRQ_IRQn);
    NVIC_EnableIRQ(MHU_RTSS_S_TX_IRQ_IRQn);

    for (uint32_t n = 1; n <= ROUNDS; n++) {
        if (wait_for(&rx_count, n) || rx_val != PING(n)) { he_error = n; break; }
        if (wait_for(&acks, n - 1u) || mhu.send_message(0, 0, PONG(n)) != MHU_SEND_OK) { he_error = 0x100u + n; break; }
        he_served = n;
    }
    while (1) { __WFE(); }
}
