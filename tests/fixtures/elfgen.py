"""Générateur d'ELF minimaux pour les tests du checker (agent 03, à relire par l'agent 07).

Compile avec arm-none-eabi-gcc une image sans runtime : table des vecteurs (SP initial + Reset),
``Reset_Handler`` Thumb, et au besoin un tableau ``.bss``. Les adresses viennent de l'appelant
(qui les lit dans ``board.load()``) : aucune constante matérielle ici. Rien n'est versionné.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_SRC = """\
extern unsigned char _estack;
void Reset_Handler(void);
static void Default_Handler(void) { for (;;) {} }
__attribute__((section(".isr_vector"), used))
void (* const vectors[])(void) = {
    (void (*)(void))&_estack, Reset_Handler, Default_Handler, Default_Handler,
};
#if BSS_BYTES
volatile unsigned char big[BSS_BYTES];
#endif
volatile int sink;
#if VARIANT == 1   /* trame de FRAME octets */
__attribute__((noinline)) int deep(int n) { volatile char b[FRAME]; b[0] = (char)n; return b[0] + b[FRAME - 1]; }
#elif VARIANT == 2 /* récursion (le + x empêche la récursion terminale) */
__attribute__((noinline)) int rec(int n) { volatile int x = n; return n ? rec(n - 1) + x : 0; }
#elif VARIANT == 3 /* appel indirect : blx rN */
__attribute__((noinline)) int helper(int n) { return n + 1; }
int (*volatile fp)(int) = helper;
#elif VARIANT == 4 /* appelée loin (> 16 Mo) : ld insère un veneer */
__attribute__((section(".far"), noinline)) int far_fn(int n) { volatile char b[FRAME]; b[0] = (char)n; return b[0] + b[FRAME - 1]; }
#elif VARIANT == 5 /* crt0 newlib : _mainCRTStartup appelle main par blx rN, + une entrée .init_array */
__attribute__((noinline)) int main(void) { volatile char b[FRAME]; b[0] = 1; return b[0] + b[FRAME - 1]; }
__attribute__((noinline)) void ctor(void) { sink = 7; }
__attribute__((section(".init_array"), used)) void (*const init_entry)(void) = ctor;
int (*volatile main_fp)(void) = main;
void (*volatile ctor_fp)(void) = ctor;
__attribute__((noinline)) void _mainCRTStartup(void) { ctor_fp(); sink = main_fp(); }
#endif
void Reset_Handler(void) {
#if BSS_BYTES
    big[0] = 1;
#endif
#if VARIANT == 1
    sink = deep(1);
#elif VARIANT == 2
    sink = rec(3);
#elif VARIANT == 3
    sink = fp(1);
#elif VARIANT == 4
    sink = far_fn(1);
#elif VARIANT == 5
    _mainCRTStartup();
#endif
    for (;;) {}
}
"""

_LD = """\
ENTRY(Reset_Handler)
_estack = {sp:#x};
__StackTop = {sp:#x};
__StackLimit = {sp:#x} - {stack_size:#x};
MEMORY {{
  CODE (rx)  : ORIGIN = {code_base:#x}, LENGTH = 0x01000000
  RAM  (rwx) : ORIGIN = {ram_base:#x},  LENGTH = 0x01000000
  FAR  (rx)  : ORIGIN = {far_base:#x},  LENGTH = 0x00100000
}}
SECTIONS {{
  .isr_vector : {{ KEEP(*(.isr_vector)) }} > CODE
  .text : {{ *(.text*) *(.rodata*) }} > CODE
  .init_array : {{ KEEP(*(.init_array*)) }} > CODE
  .far : {{ *(.far*) }} > FAR
  .data : {{ *(.data*) }} > RAM AT > CODE
  .bss (NOLOAD) : {{ *(.bss*) *(COMMON) }} > RAM
  /DISCARD/ : {{ *(.comment) }}
}}
"""

_FLAGS = {
    "cortex-m55": ["-mcpu=cortex-m55", "-mfloat-abi=hard"],
    "cortex-m4": ["-mcpu=cortex-m4", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"],
}


def build_elf(gcc: str | Path, out_dir: Path, name: str, *, code_base: int, ram_base: int, sp: int,
              bss_bytes: int = 0, cpu: str = "cortex-m55", variant: int = 0, frame: int = 64,
              stack_size: int = 0x2000, far_base: int = 0x80000000) -> Path:
    """Compile et lie ``<out_dir>/<name>/<name>.elf`` (vecteurs à ``code_base``, ``.bss`` à ``ram_base``).

    ``variant`` : 0 rien, 1 trame de ``frame`` octets, 2 récursion, 3 appel indirect, 4 appel d'une
    fonction ``.far`` placée à ``far_base`` (> 16 Mo du code : veneer de l'éditeur de liens). Le dossier
    ``<name>/`` contient aussi le ``.su`` (``-fstack-usage``). ``__StackTop``/``__StackLimit`` =
    ``sp`` et ``sp - stack_size``.
    """
    out_dir = out_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    src, ld, elf = out_dir / f"{name}.c", out_dir / f"{name}.ld", out_dir / f"{name}.elf"
    src.write_text(_SRC, encoding="utf-8")
    ld.write_text(_LD.format(sp=sp, code_base=code_base, ram_base=ram_base, stack_size=stack_size,
                           far_base=far_base),
                  encoding="utf-8")
    cmd = [str(gcc), *_FLAGS[cpu], "-mthumb", "-Os", "-fstack-usage", "-ffreestanding", "-nostdlib", "-nostartfiles",
           f"-DBSS_BYTES={bss_bytes}", f"-DVARIANT={variant}", f"-DFRAME={frame}", f"-T{ld}", "-Wl,--no-warn-rwx-segments", str(src), "-o", str(elf)]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"échec de compilation de {name} :\n{cp.stdout}\n{cp.stderr}")
    return elf
