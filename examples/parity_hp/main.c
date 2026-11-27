#include "RTE_Components.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include CMSIS_device_header
#include "Driver_CDC200.h"
#include "se_services_port.h"   /* services du Secure Enclave (alimentation de l'ecran sur la carte) */

#if defined(RTE_CMSIS_Compiler_STDOUT_Custom)
#include "retarget_stdout.h"
#include <retarget_init.h>
#endif

/* E7 Twin - parity_hp : test de parite carte / simulateur (coeur HP).
 * Execute des controles deterministes (CRC32, entiers 64 bits, flottants exacts, memoire, tas, horloge),
 * les affiche a l'ecran du panneau (CDC200, 480x800 RGB565) ET sur UART4 sous la meme forme.
 * Lignes "T"  : resultat attendu identique partout (PASS/FAIL), comptees dans la signature.
 * Lignes "I"  : information (CPUID, cycles/tick...), peut legitimement differer carte/sim, hors signature.
 * Ligne finale : "SIG xxxxxxxx" = CRC32 des lignes T. Meme signature carte et simu => meme comportement logiciel.
 * Utilisation : e7twin run build/examples/parity_hp.elf, puis comparer l'UART et l'ecran avec ceux de la carte.
 * Verifie sur carte (AppKit E7, 2026-11-25) : SIG E1C0E658 10/10, comme dans Renode. Ne flashe rien. */
#if RTE_CDC200_PIXEL_FORMAT != 2
#error "parity_hp attend RTE_CDC200_PIXEL_FORMAT == 2 (RGB565)"
#endif
#define W RTE_PANEL_HACTIVE_TIME
#define H RTE_PANEL_VACTIVE_LINE

static uint16_t lcd_image[H][W] __attribute__((section(".bss.lcd_frame_buf"), aligned(32)));
extern ARM_DRIVER_CDC200 Driver_CDC200;
static volatile uint32_t ticks;
void SysTick_Handler(void) { ticks++; }
static void cb(uint32_t ev) { (void)ev; }

/* ---- police 5x7 (colonnes), ASCII 0x20..0x5A ; minuscules -> majuscules, '_' a part ---- */
static const uint8_t font[59][5] = {
    {0x00,0x00,0x00,0x00,0x00},{0x00,0x00,0x5F,0x00,0x00},{0x00,0x07,0x00,0x07,0x00},{0x14,0x7F,0x14,0x7F,0x14},
    {0x24,0x2A,0x7F,0x2A,0x12},{0x23,0x13,0x08,0x64,0x62},{0x36,0x49,0x56,0x20,0x50},{0x00,0x05,0x03,0x00,0x00},
    {0x00,0x1C,0x22,0x41,0x00},{0x00,0x41,0x22,0x1C,0x00},{0x2A,0x1C,0x7F,0x1C,0x2A},{0x08,0x08,0x3E,0x08,0x08},
    {0x00,0x50,0x30,0x00,0x00},{0x08,0x08,0x08,0x08,0x08},{0x00,0x60,0x60,0x00,0x00},{0x20,0x10,0x08,0x04,0x02},
    {0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},{0x42,0x61,0x51,0x49,0x46},{0x21,0x41,0x45,0x4B,0x31},
    {0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},{0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},
    {0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1E},{0x00,0x36,0x36,0x00,0x00},{0x00,0x56,0x36,0x00,0x00},
    {0x08,0x14,0x22,0x41,0x00},{0x14,0x14,0x14,0x14,0x14},{0x00,0x41,0x22,0x14,0x08},{0x02,0x01,0x51,0x09,0x06},
    {0x32,0x49,0x79,0x41,0x3E},{0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
    {0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},{0x3E,0x41,0x49,0x49,0x7A},
    {0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},{0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},
    {0x7F,0x40,0x40,0x40,0x40},{0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
    {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},{0x46,0x49,0x49,0x49,0x31},
    {0x01,0x01,0x7F,0x01,0x01},{0x3F,0x40,0x40,0x40,0x3F},{0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},
    {0x63,0x14,0x08,0x14,0x63},{0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43}};

#define SC 2u                    /* echelle : cellule 12 x 18 px -> 40 colonnes, 44 lignes */
#define CELL_W (6u * SC)
#define CELL_H (9u * SC)
#define RGB(r, g, b) ((uint16_t)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3)))
#define C_BG RGB(0, 0, 0)
#define C_FG RGB(255, 255, 255)
#define C_OK RGB(0, 230, 0)
#define C_BAD RGB(255, 40, 40)
#define C_INFO RGB(120, 180, 255)
#define C_HEAD RGB(255, 220, 0)

static void put_char(uint32_t x, uint32_t y, char c, uint16_t fg, uint16_t bg)
{
    if (c >= 'a' && c <= 'z') c = (char)(c - 32);
    const uint8_t *g = 0;
    static const uint8_t under[5] = {0x40, 0x40, 0x40, 0x40, 0x40};
    if (c == '_') g = under; else if (c >= 0x20 && c <= 0x5A) g = font[c - 0x20];
    for (uint32_t cy = 0; cy < 8u * SC; cy++)
        for (uint32_t cx = 0; cx < 6u * SC; cx++) {
            uint32_t col = cx / SC, row = cy / SC;
            uint16_t v = (g && col < 5u && row < 7u && ((g[col] >> row) & 1u)) ? fg : bg;
            if (x + cx < W && y + cy < H) lcd_image[y + cy][x + cx] = v;
        }
}

static void line_draw(uint32_t row, const char *s, uint16_t fg)
{
    uint32_t y = row * CELL_H, x = 0;
    for (uint32_t i = 0; i < W / CELL_W; i++) { put_char(x, y, s && s[i] ? s[i] : ' ', fg, C_BG); if (s && !s[i]) s = 0; x += CELL_W; }
    SCB_CleanDCache_by_Addr((uint32_t *)&lcd_image[y][0], (int32_t)(CELL_H * W * 2u));
}

/* ---- sortie : ecran + UART ---- */
static uint32_t crc32_upd(uint32_t crc, const uint8_t *p, uint32_t n)
{
    crc = ~crc;
    while (n--) { crc ^= *p++; for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u))); }
    return ~crc;
}

static uint32_t g_row, g_sig, g_tests, g_pass;

static void emit(char kind, const char *name, const char *val, int ok)
{
    char buf[64];
    int n = snprintf(buf, sizeof buf, "%c %-9s %s", kind, name, val);
    if (kind == 'T') n += snprintf(buf + n, sizeof buf - (size_t)n, " %s", ok ? "PASS" : "FAIL");
    printf("%s\r\n", buf);
    if (kind == 'T') { g_sig = crc32_upd(g_sig, (const uint8_t *)buf, (uint32_t)strlen(buf)); g_tests++; g_pass += (uint32_t)(ok != 0); }
    line_draw(g_row++, buf, kind == 'T' ? (ok ? C_OK : C_BAD) : C_INFO);
}

#define TEST(name, valfmt, valarg, cond) do { char v_[32]; snprintf(v_, sizeof v_, valfmt, valarg); emit('T', name, v_, (cond)); } while (0)

/* ---- controles deterministes ---- */
static uint8_t sram_buf[4096];

static void run_tests(void)
{
    uint8_t d[256];
    for (uint32_t i = 0; i < 256; i++) d[i] = (uint8_t)(i * 7u + 3u);
    uint32_t c = crc32_upd(0, d, 256);
    TEST("CRC32", "%08lX", (unsigned long)c, c == 0x78825239u);

    volatile uint64_t a = 0x0123456789ABCDEFull, m = 12345u, dv = 678u;
    uint64_t p = a * m;
    TEST("U64DIV", "%08lX", (unsigned long)((p / dv) & 0xFFFFFFFFu), (p / dv) == 0x0053C5CD5A91B217ull);
    TEST("U64MOD", "%lu", (unsigned long)(p % dv), (p % dv) == 0x14Du);

    volatile int32_t s1 = -2147483647 - 1, s2 = 7;
    TEST("S32DIV", "%ld", (long)(s1 / s2), (s1 / s2) == -306783378L);

    volatile float f = 0.0f, h = 1.0f;
    for (int i = 0; i < 20; i++) { h *= 0.5f; f += h; }
    uint32_t fb; memcpy(&fb, (const void *)&f, 4);
    TEST("FLOAT", "%08lX", (unsigned long)fb, f == 1048575.0f / 1048576.0f);

    volatile double dd = 1.0;
    for (int i = 0; i < 10; i++) dd *= 2.0;
    TEST("DOUBLE", "%lu", (unsigned long)dd, dd == 1024.0);

    for (uint32_t i = 0; i < sizeof sram_buf; i++) sram_buf[i] = (uint8_t)(i ^ (i >> 8));
    uint32_t bad = 0;
    for (uint32_t i = 0; i < sizeof sram_buf; i++) bad += sram_buf[i] != (uint8_t)(i ^ (i >> 8));
    TEST("RAMRW", "ERR=%lu", (unsigned long)bad, bad == 0);

    void *hp = malloc(1024);
    int heap_ok = hp != NULL;
    if (hp) { memset(hp, 0xA5, 1024); heap_ok = ((uint8_t *)hp)[1023] == 0xA5; free(hp); }
    TEST("HEAP", "%s", heap_ok ? "OK" : "NULL", heap_ok);

    TEST("CLOCK", "%luMHZ", (unsigned long)(SystemCoreClock / 1000000u), SystemCoreClock == 400000000u);

    uint32_t t0 = ticks, spin = 0;
    while ((int32_t)(ticks - (t0 + 10u)) < 0 && spin < 200000000u) { __WFI(); spin++; }
    TEST("SYSTICK", "%lu", (unsigned long)(ticks - t0), (ticks - t0) == 10u);
}

/* Sur la carte, la D-PHY MIPI et les horloges DSI/CDC200 sont coupees tant que l'application
 * ne les demande pas au Secure Enclave : sans cela, Driver_CDC200.Initialize rend -1 (PLL D-PHY non verrouillee).
 * Profil repris du SDK du porteur (third_party/mlek/platform/source/platform_drivers.c, cas E7 HP),
 * plus PD_DBSS pour garder la J-Link. Le simulateur n'a pas de SE : les appels y expirent, on continue.
 * Etat lisible par J-Link : g_se_rc / g_se_err et g_clk_rc / g_clk_err (0 = succes), g_disp_rc (0 = ecran demarre). */
volatile uint32_t g_se_rc = 0xFFFFFFFFu, g_se_err = 0xFFFFFFFFu;
volatile int32_t g_disp_rc = 1;
volatile uint32_t g_clk_rc = 0xFFFFFFFFu, g_clk_err = 0xFFFFFFFFu;

static void se_power_display(void)
{
    run_profile_t rp = {0};
    uint32_t err = 0;
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
    g_se_rc  = SERVICES_set_run_cfg(se_services_s_handle, &rp, &err);
    g_se_err = err;
    printf("PARITY: se run_cfg rc=%lu err=%lu\r\n", (unsigned long)g_se_rc, (unsigned long)g_se_err);
    /* Horloges de la D-PHY : 38,4 MHz (reference de la PLL) et 100 MHz (DSI/CSI), comme
     * enable_peripheral_clocks() du SDK (third_party/mlek/platform/source/platform_drivers.c). */
    uint32_t e1 = 0, e2 = 0;
    g_clk_rc  = SERVICES_clocks_enable_clock(se_services_s_handle, CLKEN_HFOSC, true, &e1)
              | SERVICES_clocks_enable_clock(se_services_s_handle, CLKEN_CLK_100M, true, &e2);
    g_clk_err = e1 | e2;
    printf("PARITY: se clocks rc=%lu err=%lu\r\n", (unsigned long)g_clk_rc, (unsigned long)g_clk_err);
}

int main(void)
{
    if (stdout_init() != 0) { while (1) { } }
    printf("\r\nPARITY: start\r\n");
    se_power_display();                                        /* avant Driver_CDC200.Initialize */
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) lcd_image[y][x] = C_BG;
    SCB_CleanDCache_by_Addr((uint32_t *)lcd_image, (int32_t)sizeof lcd_image);
    int32_t r;
    /* Un echec de l'ecran n'arrete pas le test : les resultats restent sur UART4, en memoire
     * (g_sig, g_pass, g_tests) et dans lcd_image, lisibles par J-Link. */
    if ((r = Driver_CDC200.Initialize(cb)) != ARM_DRIVER_OK) printf("PARITY: init failed %ld\r\n", (long)r);
    else if ((r = Driver_CDC200.PowerControl(ARM_POWER_FULL)) != ARM_DRIVER_OK) printf("PARITY: power failed %ld\r\n", (long)r);
    else if ((r = Driver_CDC200.Control(CDC200_CONFIGURE_DISPLAY, (uint32_t)lcd_image)) != ARM_DRIVER_OK) printf("PARITY: config failed %ld\r\n", (long)r);
    else if ((r = Driver_CDC200.Start()) != ARM_DRIVER_OK) printf("PARITY: start failed %ld\r\n", (long)r);
    g_disp_rc = r;
    if (SysTick_Config(SystemCoreClock / 100u)) { while (1) { } }   /* tick 10 ms */

    line_draw(g_row++, "E7 TWIN PARITY HP", C_HEAD);
    line_draw(g_row++, "T = MUST MATCH  I = INFO", C_HEAD);
    printf("PARITY HP v1\r\n");
    run_tests();

    char v[32];
    snprintf(v, sizeof v, "%08lX", (unsigned long)SCB->CPUID);
    emit('I', "CPUID", v, 0);
    snprintf(v, sizeof v, "%08lX", (unsigned long)(uintptr_t)sram_buf);
    emit('I', "SRAMBUF", v, 0);
    snprintf(v, sizeof v, "%08lX", (unsigned long)(uintptr_t)&g_row);
    emit('I', "ADDR_VAR", v, 0);

    g_row++;
    snprintf(v, sizeof v, "SIG %08lX  %lu/%lu OK", (unsigned long)g_sig, (unsigned long)g_pass, (unsigned long)g_tests);
    printf("%s\r\n", v);
    line_draw(g_row++, v, g_pass == g_tests ? C_OK : C_BAD);
    printf("PARITY: done\r\n");

    uint32_t last = 0;
    for (;;) {                                  /* temoin de vie : temps ecoule, 1 Hz */
        uint32_t s = ticks / 100u;
        if (s != last) {
            last = s;
            snprintf(v, sizeof v, "UPTIME %lu S", (unsigned long)s);
            line_draw(g_row + 1u, v, C_FG);
            printf("%s\r\n", v);
        }
        __WFI();
    }
}
