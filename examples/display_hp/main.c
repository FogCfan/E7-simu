#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include CMSIS_device_header
#include "Driver_CDC200.h"

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif

/* E7 Twin - affichage HP : CDC200 -> DSI -> panneau ILI9806E 480x800 RGB565 (parametres de RTE_Device.h).
 * Image : fond en degrade + barre blanche de 40 lignes qui descend de 40 lignes toutes les 500 ms.
 * UART4 : "DISPLAY frame N" a chaque image. Pas de verification matérielle : demo du template Alif (demo_cdc200.c)
 * + animation ajoutee. */
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

int main(void)
{
    if (stdout_init() != 0) { while (1) { } }
    printf("\r\nDISPLAY: start\r\n");
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