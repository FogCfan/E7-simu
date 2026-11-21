/* Spike Renode : helpers communs. Aucune adresse en dur : tout vient de e7_board.h (généré). */
#pragma once
#include <stdint.h>
#include "e7_board.h"

#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define UART_THR(b) REG32((b) + (0u << E7_UART_REG_SHIFT))
#define UART_IER(b) REG32((b) + (1u << E7_UART_REG_SHIFT))
#define UART_LSR(b) REG32((b) + (5u << E7_UART_REG_SHIFT))
#define LSR_THRE 0x20u

static inline void uart_putc(uintptr_t base, char c) {
    while (!(UART_LSR(base) & LSR_THRE)) {
    }
    UART_THR(base) = (uint32_t)(uint8_t)c;
}

static inline void uart_puts(uintptr_t base, const char *s) {
    while (*s) {
        if (*s == '\n') uart_putc(base, '\r');
        uart_putc(base, *s++);
    }
}

static inline void uart_puthex(uintptr_t base, uint32_t v) {
    static const char d[] = "0123456789abcdef";
    uart_puts(base, "0x");
    for (int i = 28; i >= 0; i -= 4) uart_putc(base, d[(v >> i) & 0xF]);
}

/* NVIC (SCS) : ISER/ICER, adresses architecturales Armv8-M (communes aux deux M55). */
#define NVIC_ISER(n) REG32(0xE000E100u + 4u * ((n) >> 5))
#define NVIC_ICER(n) REG32(0xE000E180u + 4u * ((n) >> 5))
