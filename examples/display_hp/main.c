#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include CMSIS_device_header
#include "Driver_CDC200.h"
#include "se_services_port.h"   /* services du Secure Enclave (alimentation de l'ecran sur la carte) */

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif

/* E7 Twin - affichage HP : CDC200 -> DSI -> panneau ILI9806E 480x800 RGB565 (parametres de RTE_Device.h).
 * Image : fond en degrade + barre blanche de 40 lignes qui descend de 40 lignes toutes les 500 ms.
 * UART4 : "DISPLAY frame N" a chaque image. Demo du template Alif (demo_cdc200.c) + animation ajoutee.
 * Pas encore verifie sur carte ; l'alimentation de l'ecran par le SE l'est (voir parity_hp). */
#if RTE_CDC200_PIXEL_FORMAT != 2
#error "display_hp attend RTE_CDC200_PIXEL_FORMAT == 2 (RGB565)"
#endif
#define W RTE_PANEL_HACTIVE_TIME
#define H RTE_PANEL_VACTIVE_LINE
#define BAR 40u

static uint16_t lcd_image[H][W] __attribute__((section(".bss.lcd_frame_buf"), aligned(32)));
extern ARM_DRIVER_CDC200 Driver_CDC200;
static volatile uint32_t ticks;
void SysTick_Handler(void) { ticks++; }
static void cb(uint32_t ev) { (void)ev; }

static void fill(uint32_t y0, uint32_t n, int bar)
{
    for (uint32_t y = y0; y < y0 + n && y < H; y++) {
        uint16_t v = bar ? 0xFFFFu : (uint16_t)(((y * 31u / H) << 11) | (0x20u << 5) | (31u - y * 31u / H));
        for (uint32_t x = 0; x < W; x++) lcd_image[y][x] = v;
    }
    SCB_CleanDCache_by_Addr((uint32_t *)&lcd_image[y0][0], (int32_t)(n * W * 2u));
}

/* Sur la carte, la D-PHY MIPI et les horloges DSI/CDC200 sont coupees tant que l'application
 * ne les demande pas au Secure Enclave : sans cela, Driver_CDC200.Initialize rend -1 (PLL D-PHY non verrouillee).
 * Meme sequence que parity_hp (verifiee sur carte le 2026-11-25), reprise du SDK du porteur
 * (third_party/mlek/platform/source/platform_drivers.c, cas E7 HP), plus PD_DBSS pour garder la J-Link.
 * Le simulateur n'a pas de SE : les appels y expirent, on continue. */
static void se_power_display(void)
{
    run_profile_t rp = {0};
    uint32_t rc, err = 0, e1 = 0, e2 = 0;
    se_services_port_init();
    rp.dcdc_mode       = DCDC_MODE_PWM;
    rp.dcdc_voltage    = DCDC_VOUT_0825;
    rp.memory_blocks   = SERAM_MASK | SRAM0_MASK | SRAM1_MASK | MRAM_MASK | FWRAM_MASK | BACKUP4K_MASK;
    rp.phy_pwr_gating  = LDO_PHY_MASK | MIPI_PLL_DPHY_MASK | MIPI_TX_DPHY_MASK | MIPI_RX_DPHY_MASK;
    rp.ip_clock_gating = MIPI_DSI_MASK | CDC200_MASK | MIPI_CSI_MASK | CAMERA_MASK | LP_PERIPH_MASK | NPU_HE_MASK | NPU_HP_MASK;
    rp.power_domains   = PD_VBAT_AON_MASK | PD_SSE700_AON_MASK | PD_SYST_MASK | PD_SESS_MASK | PD_DBSS_MASK;
    rp.aon_clk_src     = CLK_SRC_LFXO;
    rp.run_clk_src     = CLK_SRC_PLL;
    rp.scaled_clk_freq = SCALED_FREQ_XO_HIGH_DIV_38_4_MHZ;
    rp.cpu_clk_freq    = CLOCK_FREQUENCY_400MHZ;
    rp.vdd_ioflex_3V3  = IOFLEX_LEVEL_1V8;
    rc = SERVICES_set_run_cfg(se_services_s_handle, &rp, &err);
    printf("DISPLAY: se run_cfg rc=%lu err=%lu\r\n", (unsigned long)rc, (unsigned long)err);
    /* Horloges de la D-PHY : 38,4 MHz (reference de la PLL) et 100 MHz (DSI/CSI), comme
     * enable_peripheral_clocks() du SDK. */
    rc = SERVICES_clocks_enable_clock(se_services_s_handle, CLKEN_HFOSC, true, &e1)
       | SERVICES_clocks_enable_clock(se_services_s_handle, CLKEN_CLK_100M, true, &e2);
    printf("DISPLAY: se clocks rc=%lu err=%lu\r\n", (unsigned long)rc, (unsigned long)(e1 | e2));
}

int main(void)
{
    if (stdout_init() != 0) { while (1) { } }
    printf("\r\nDISPLAY: start\r\n");
    se_power_display();                                           /* avant Driver_CDC200.Initialize */
    fill(0, H, 0);
    int32_t r;
    if ((r = Driver_CDC200.Initialize(cb)) != ARM_DRIVER_OK) { printf("DISPLAY: init failed %ld\r\n", (long)r); while (1) { } }
    if ((r = Driver_CDC200.PowerControl(ARM_POWER_FULL)) != ARM_DRIVER_OK) { printf("DISPLAY: power failed %ld\r\n", (long)r); while (1) { } }
    if ((r = Driver_CDC200.Control(CDC200_CONFIGURE_DISPLAY, (uint32_t)lcd_image)) != ARM_DRIVER_OK) { printf("DISPLAY: config failed %ld\r\n", (long)r); while (1) { } }
    if ((r = Driver_CDC200.Start()) != ARM_DRIVER_OK) { printf("DISPLAY: start failed %ld\r\n", (long)r); while (1) { } }
    if (SysTick_Config(SystemCoreClock / 100u)) { while (1) { } }   /* tick 10 ms */

    uint32_t frame = 0, pos = 0;
    for (;;) {
        fill(pos, BAR, 1);
        frame++;
        printf("DISPLAY frame %lu\r\n", (unsigned long)frame);
        uint32_t t = ticks + 50u;                                    /* 500 ms */
        while ((int32_t)(ticks - t) < 0) { __WFI(); }
        fill(pos, BAR, 0);
        pos = (pos + BAR) % H;
    }
}