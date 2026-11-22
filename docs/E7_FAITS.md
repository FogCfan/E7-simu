# Ensemble E7 : faits matériels sourcés

Toute valeur utilisée par le code doit apparaître ici avec sa source. Légende : **V** = lu verbatim dans la source, **R** = résumé, à revérifier avant usage critique. La traduction machine de ce fichier est `board/ensemble_e7.yaml`, où chaque entrée porte un champ `source:` du type « fichier + numéro de ligne ».

**Sources épinglées** (relues le 2026-11-20) :
- DFP `alifsemi/alif_ensemble-cmsis-dfp`, commit `e8bd010fa8b0df9781bb15ecf4e2d40dbbac4f99` (branche main, release 2.3.0-rc1). Les liens ci-dessous sont abrégés en `DFP/…`, à préfixer par `https://github.com/alifsemi/alif_ensemble-cmsis-dfp/blob/e8bd010fa8b0df9781bb15ecf4e2d40dbbac4f99/`. Les fichiers propres au SoC sont sous `Device/soc/AE722F80F55D5/`.
  - `DFP/Device/soc/AE722F80F55D5/include/rtss_hp/soc.h` → **hp_soc**
  - `DFP/Device/soc/AE722F80F55D5/include/rtss_he/soc.h` → **he_soc**
  - `DFP/Device/soc/AE722F80F55D5/config/app_mem_regions.h` → **mem_regions**
  - `DFP/Device/soc/AE722F80F55D5/include/soc_features.h` → **soc_features**
  - `DFP/AlifSemiconductor.Ensemble.pdsc` → **pdsc** (section `DsubFamily="E7"`, lignes 1177–1849)
- Template `alifsemi/alif_vscode-template`, commit `ebbf1513edaeffad912407543fe09da3c7ec9700`.
- Renode `renode/renode`, commit `c38ac3babcd38a7144b9498e3ca4c2226b76aa37`, fichier `platforms/cpus/alif_ae822fa0e5597bs0.repl`.

## Carte

- Kit : Ensemble E7 AI/ML AppKit, réf. **AK-E7-AIML**. Page : https://alifsemi.com/support/kits/ensemble-e7appkit/
- SoC : **AE722F80F55D5** (device CMSIS `AE722F80F55D5LS`, boîtier FBGA 194). Lu **V** dans le pdsc, l. 1839–1847.
- Le DFP contient un board `Boards/AppKit-e7-aiml` (exemples Blinky HP/HE et DualCore), en plus de `Boards/DevKit-e7` (**V**, arborescence du DFP).
- Debug : J-Link On-Board, SWD/JTAG. Dans le pdsc, l'access port vaut 0 pour M55_HP et 1 pour M55_HE (`__apid`, l. 1844–1845, **V**).
- Docs : datasheet https://alifsemi.com/download/ADTS0005, HRM https://alifsemi.com/download/AHRM0005, SWRM https://alifsemi.com/download/ASRM0002, kit user guide https://alifsemi.com/download/AUGD0020, SVD https://alifsemi.com/download/SVD0001. Le DFP fournit aussi `Debug/SVD/AE722F80F55D5LS_CM55_{HP,HE}_View.svd`.

## Cœurs

| Cœur | Fréquence max | Particularités | Statut |
|---|---|---|---|
| 2× Cortex-A32 (core id 0, 1) | 800 MHz | Neon, MMU, 512 Ko L2 partagé, 32 Ko L1 I/D | fréquence et id **V** (soc_features l. 22–23, 28) ; le reste **R** (page série) |
| Cortex-M55 HP / RTSS-HP (core id 2) | 400 MHz | `Dfpu="DP_FPU" Dmve="FP_MVE" Dtz="TZ" Ddsp="DSP" Dmpu="MPU"`, r1p0 | **V** pdsc l. 1180, soc_features l. 21, 27 |
| Cortex-M55 HE / RTSS-HE (core id 3) | 160 MHz | idem | **V** pdsc l. 1181, soc_features l. 20, 26 |
| Ethos-U55 | — | 256 MAC, associé au HP | **V** pdsc l. 1779 ; débit 204 GOPS **R** |
| Ethos-U55 | — | 128 MAC, associé au HE | **V** pdsc l. 1780 ; débit 46 GOPS **R** |

Horloges max, lues **V** dans soc_features l. 30–36 : ACLK 400 MHz, HCLK 200 MHz, PCLK 100 MHz, REFCLK 100 MHz, HFOSC 38,4 MHz. La largeur du bus AXI (64 bits) reste **R**.
Caches I/D 32 Ko du M55 : **R** (page série https://alifsemi.com/ensemble-e7-series/).

## Mémoire

Sources : mem_regions (**V**), confirmé par le pdsc l. 1742–1759 (**V**) et par soc_features l. 40–61 (**V**).

| Région (nom YAML) | Base | Taille | Visibilité | Source |
|---|---|---|---|---|
| ITCM HP local (`itcm_hp`) | 0x00000000 | 256 Ko | HP seul | mem_regions l. 160, 190 ; pdsc l. 1756 |
| DTCM HP local (`dtcm_hp`) | 0x20000000 | 1 Mo | HP seul | mem_regions l. 161, 191 ; pdsc l. 1757 |
| ITCM HE local (`itcm_he`) | 0x00000000 | 256 Ko | HE seul | mem_regions l. 160, 187 ; pdsc l. 1758 |
| DTCM HE local (`dtcm_he`) | 0x20000000 | 256 Ko | HE seul | mem_regions l. 161, 188 ; pdsc l. 1759 |
| SRAM0 (`sram0`) | 0x02000000 | 4 Mo | tous | mem_regions l. 57, 61 ; pdsc l. 1744 |
| SRAM1 (`sram1`) | 0x08000000 | 2,5 Mo | tous | mem_regions l. 71, 75 ; pdsc l. 1745 |
| SRAM2 = alias global ITCM HP | 0x50000000 | 256 Ko | tous | mem_regions l. 85, 89 ; pdsc l. 1746 |
| SRAM3 = alias global DTCM HP | 0x50800000 | 1 Mo | tous | mem_regions l. 99, 103 ; pdsc l. 1747 |
| SRAM4 = alias global ITCM HE | 0x58000000 | 256 Ko | tous | mem_regions l. 113, 117 ; pdsc l. 1748 |
| SRAM5 = alias global DTCM HE | 0x58800000 | 256 Ko | tous | mem_regions l. 127, 131 ; pdsc l. 1749 |
| SRAM9 (`sram9`) | 0x60000000 | 768 Ko | tous (**R**) | pdsc l. 1754 |
| SRAM6_A (`sram6_a`) | 0x62000000 | 1 Mo | tous (**R**) | pdsc l. 1750 |
| SRAM6_B (`sram6_b`) | 0x62400000 | 1 Mo | tous (**R**) | pdsc l. 1751 |
| SRAM7 (`sram7`) | 0x63000000 | 512 Ko | tous (**R**) | pdsc l. 1752 |
| SRAM8 (`sram8`) | 0x63200000 | 2 Mo | tous (**R**) | pdsc l. 1753 |
| MRAM (`mram`), 5,5 Mo, accès rx | 0x80000000 | 0x580000 | tous | pdsc l. 1742 ; soc_features l. 40–41 |
| …dont slot app HE | 0x80000000 | 2 Mo | — | mem_regions l. 21, 25 |
| …dont slot app HP | 0x80200000 | 2 Mo | — | mem_regions l. 29, 33 |
| …dont zone user | 0x80400000 | 1,5 Mo | — | mem_regions l. 41, 45 |
| OSPI XIP, slot HE | 0xC0000000 | 2 Mo | tous | mem_regions l. 172, 176 |
| OSPI XIP, slot HP | 0xC0200000 | 2 Mo | tous | mem_regions l. 180, 184 |

- La correspondance « ITCM_HP alias SRAM2 » est lue **V** dans le pdsc (attribut `alias=`, l. 1756–1759). Dans le YAML, l'alias global porte `alias_of: <tcm locale>`.
- Les SRAM6 à SRAM9 (13,5 Mo de RAM au total d'après le commentaire du pdsc, l. 1743) n'apparaissent pas dans `app_mem_regions.h`. Leur visibilité par maître (A32, HP, HE, DMA) est **à vérifier** dans le HRM.
- La visibilité des alias SRAM2 à SRAM5 et des SRAM0/1 depuis les A32 est **R**. Elle est plausible : le stub A32 est chargé en SRAM0 (`loadAddress 0x02000000`, voir ATOC).
- Pile et tas par défaut du template DFP : 0x2000 et 0x4000 par cœur (mem_regions l. 144–152, **V**).

## Périphériques

- **UART0..7** : 0x49018000 → 0x4901F000, au pas de 0x1000. **V** dans hp_soc l. 3285–3292 et he_soc l. 3279–3286 (mêmes adresses vues des deux cœurs). Le bloc `UART_Type` fait 0x100 octets et ses registres 16550 sont en 32 bits (RBR/THR/DLL à +0x0, DLH/IER à +0x4), d'où `wideRegisters: true` (hp_soc l. 1835, **V**).
- **IRQ UART0..7** = 124..131, sur le NVIC HP comme sur le NVIC HE. **V** dans hp_soc l. 208–215 et he_soc l. 219–226.
- **LPUART** : base 0x43008000, définie dans les deux soc.h (hp l. 3250, he l. 3244), même `UART_Type`. Son **IRQ 45 n'existe que côté HE** (he_soc l. 138, **V**). *Correction* : l'ancienne version de ce fichier situait cette IRQ dans `rtss_hp/soc.h`, ce qui est faux.
- **UART du printf** : **UART4** (0x4901C000, IRQ 128), à 115 200 bauds, pour HP comme pour HE. Lu **V** (`#define PRINTF_UART_CONSOLE 4`, l. 46) dans :
  - le template, `hello/RTE/Services/AE722F80F55D5LS_M55_{HP,HE}/retarget_config.h` : https://github.com/alifsemi/alif_vscode-template/blob/ebbf1513edaeffad912407543fe09da3c7ec9700/hello/RTE/Services/AE722F80F55D5LS_M55_HP/retarget_config.h ;
  - le DFP, `Boards/AppKit-e7-aiml/Examples/M55_HP/Blinky/RTE/Services/AE722F80F55D5LS_M55_HP/retarget_config.h`.
  Le template ne fournit pas de dossier `board/appkit-e7` mais seulement `board/devkit-e7`, dont `board_defs.h` donne `BOARD_UARTB_UART_INSTANCE 4`.
- **NPU** : `NPU_HP_BASE` et `NPU_HE_BASE` valent tous deux 0x400E1000, dans la fenêtre locale de chaque cœur. L'IRQ est 55 sur chaque cœur. **V** dans hp_soc l. 3236 et 138 (taille 0x1000, l. 1453) et dans he_soc l. 3231 et 149.
- **NVIC** : `M55HP_NVIC_S_BASE` = `M55HE_NVIC_S_BASE` = 0xE000E100 (hp l. 3337, he l. 3331, **V**). Renode modélise le SCS à 0xE000E000 (architecture v8-M).

### Blocs horloge / PINMUX / power (stubs de l'agent 04)

Bases lues **V** dans les deux soc.h ; tailles = `sizeof(<X>_Type)`, lues **V** dans hp_soc.

| Bloc | Base | Taille struct | Ligne hp_soc (base / struct) | Tag Renode E8 |
|---|---|---|---|---|
| CLKCTL_SYS | 0x1A010000 | 0x828 | 3198 / 966 | — |
| CGU | 0x1A602000 | 0x24 | 3216 / 1119 | clockctrl 0x2c |
| PINMUX | 0x1A603000 | 0x1E0 | 3217 / 1133 | pinctrl 0x1000 |
| AON | 0x1A604000 | 0x44 | 3218 / 1158 | clockctrl 0x48 |
| VBAT | 0x1A609000 | 0x14 | 3219 / 1176 | clockctrl 0x14 |
| ANA | 0x1A60A000 | 0x44 | 3220 / 1198 | — |
| LPGPIO_CTRL | 0x42007000 | 0x20 | 3244 / 1593 | pinctrl 0x1000 |
| M55HE_CFG (global) | 0x43007000 | 0x24 | 3249 / 1765 | clockctrl 0x28 |
| M55HP_CFG (**local HP**, absent de he_soc) | 0x400F0000 | 0x14 | 3238 / 1471 | clockctrl 0x14 |
| CLKCTL_PER_SLV | 0x4902F000 | 0xBC | 3320 / 2487 | clockctrl 0xbc |
| CLKCTL_PER_MST | 0x4903F000 | 0xB0 | 3329 / 2999 | clockctrl 0xb0 |

La fenêtre réelle de chaque bloc peut dépasser la taille de la struct : les Tag Renode E8 couvrent par exemple 0x2c pour CGU et 0x48 pour AON. Pour les stubs, il est plus sûr de couvrir le maximum des deux ou la page de 4 Ko. Les tailles exactes sont **à vérifier** dans le HRM.

## MHU (inter-cœurs)

La plage **0x40000000–0x401FFFFF est une fenêtre locale à chaque M55**. Les mêmes adresses désignent des blocs différents sur HP et sur HE, ce qui est le cas des MHU, de NPU_x, de WDT_x et de M55HP_CFG. Tout est lu **V** : bases dans hp_soc l. 3222–3233 et he_soc l. 3217–3228, IRQ dans hp_soc l. 126–137 et he_soc l. 126–137. Les registres font 0x1000 par instance (`MHU_*_RX_Type` / `MHU_*_TX_Type`, hp l. 1256 et 1301).

| Base (locale) | Vu du HP | IRQ HP | Vu du HE | IRQ HE |
|---|---|---|---|---|
| 0x40000000 | MHU_A32_M55HP_0_RX | 33 | MHU_A32_M55HE_0_RX | 33 |
| 0x40010000 | MHU_M55HP_A32_0_TX | 34 | MHU_M55HE_A32_0_TX | 34 |
| 0x40020000 | MHU_A32_M55HP_1_RX | 35 | MHU_A32_M55HE_1_RX | 35 |
| 0x40030000 | MHU_M55HP_A32_1_TX | 36 | MHU_M55HE_A32_1_TX | 36 |
| 0x40040000 | MHU_SECPU_M55HP_0_RX | 37 | MHU_SECPU_M55HE_0_RX | 37 |
| 0x40050000 | MHU_M55HP_SECPU_0_TX | 38 | MHU_M55HE_SECPU_0_TX | 38 |
| 0x40060000 | MHU_SECPU_M55HP_1_RX | 39 | MHU_SECPU_M55HE_1_RX | 39 |
| 0x40070000 | MHU_M55HP_SECPU_1_TX | 40 | MHU_M55HE_SECPU_1_TX | 40 |
| 0x40080000 | MHU_M55HE_M55HP_0_RX | 41 | MHU_M55HP_M55HE_0_RX | 41 |
| 0x40090000 | MHU_M55HP_M55HE_0_TX | 42 | MHU_M55HE_M55HP_0_TX | 42 |
| 0x400A0000 | MHU_M55HE_M55HP_1_RX | 43 | MHU_M55HP_M55HE_1_RX | 43 |
| 0x400B0000 | MHU_M55HP_M55HE_1_TX | 44 | MHU_M55HE_M55HP_1_TX | 44 |

Appariement (déduit des noms, donc **R**) : le TX `MHU_M55HP_M55HE_n_TX` du HP (0x40090000 ou 0x400B0000) alimente le RX `MHU_M55HP_M55HE_n_RX` du HE (0x40080000 ou 0x400A0000), et inversement. Dans le YAML, cette paire porte le même `link`.
- Le driver DFP (`drivers/source/mhu_*.c`) gère jusqu'à 124 canaux par MHU (`MHU_NUMBER_OF_CHANNELS_MAX`, `drivers/include/mhu.h`, **V**). Le modèle exact de l'IP (Arm MHUv2 ?) et la sémantique des registres pour le stub restent **à vérifier** dans le HRM ou le SVD.

## Toolchain (template officiel)

Source : https://github.com/alifsemi/alif_vscode-template, fichiers `alif.csolution.yml` et `cdefault.yml`, lus **V** lors de la rédaction initiale. Ils n'ont pas été relus dans ce cycle (périmètre de l'agent 02).
- CMSIS-Toolbox 2.12.0, packs `AlifSemiconductor::Ensemble@2.2.1` et `ARM::CMSIS@6.1.0`. GCC est le compilateur par défaut, CLANG en option.
- Flags GCC : `-std=c99 -fdata-sections`, link `--specs=nosys.specs --entry=Reset_Handler -Wl,--gc-sections,-print-memory-usage`, libs `-lm -lc -lgcc`.

## Packaging ATOC (SETOOLS)

Source : `alif_vscode-template/.alif/M55_HP_HE_mram_cfg.json`, lu **V**.
```json
"HP_APP": { "binary": "M55_HP_mram_stub.bin", "mramAddress": "0x80200000", "cpu_id": "M55_HP", "flags": ["boot"], "signed": false }
"HE_APP": { "binary": "M55_HE_mram_stub.bin", "mramAddress": "0x80000000", "cpu_id": "M55_HE", "flags": ["boot"], "signed": false }
"A32_STUB": { "binary": "a32_stub_0.bin", "loadAddress": "0x02000000", "cpu_id": "A32_0", "flags": ["load","boot"], "signed": true }
```
- Les `mramAddress` coïncident avec `APP_MRAM_HP_BASE` et `APP_MRAM_HE_BASE` de mem_regions (**V**).
- Depuis la version 1.107 de SETOOLS, la cible par défaut est l'E8 DevKit. Il faut donc sélectionner l'E7 explicitement.

## Renode

- La version 1.17.0 (sept. 2026) apporte le Cortex-M55, Helium/MVE (couverture large mais pas certifiée complète), FPv5, le profiler Perfetto/speedscope et le monitor telnet (`-P`).
- `platforms/cpus/alif_ae822fa0e5597bs0.repl` (relu **V**, commit `c38ac3b`) décrit un seul cœur RTSS-HE E8 :
  - ITCM et DTCM en MappedMemory globales ;
  - `nvic0` enregistré par `BusPointRegistration { address: 0xe000e000; cpu: cpu0 }`, avec `systickFrequency 160000000` ;
  - UART0..7 en `UART.NS16550` + `wideRegisters: true`, mêmes bases et mêmes IRQ (124..131) que l'E7 ;
  - GPIO et I2C DesignWare, CAN, DWT à 0xe0001000 ;
  - des `Tag` sur les blocs clock et pinctrl.
- Notre `.repl` (`e7twin/renode/e7.repl`) est **généré** depuis le YAML par `python -m e7twin.board --repl`.
- Il n'y a pas de Cortex-A32 dans Renode.

## Affichage (CDC200, MIPI DSI, panneau, tactile)

Sources : pack Alif installé localement `tools/packs/AlifSemiconductor/Ensemble/2.2.1` (PK). Détail et lignes dans la section `display:` de `board/ensemble_e7.yaml`.

| Fait | Valeur | Source |
|---|---|---|
| CDC200 base / taille | 0x49031000 / 0x300 (SVD : 596 octets décodés) | PK `rtss_hp/soc.h` L3322, L2499-2527 ; SVD `<name>CDC</name>` |
| IRQ CDC | scanline 333, fifo_warning 335, fifo_underrun 337, bus_error 339, reg_reload 341 (HP et HE) | `soc.h` L450-454 |
| Registres utiles | GLB_CTRL 0x18 (bit0 EN), ACTW 0x10, TOTALW 0x14 ; 2 calques à 0x100 + n*0x100 : CTRL 0x0C (bit0 LAYER_EN), PIX_FORMAT 0x1C, CFB_ADDR 0x34, CFB_LENGTH 0x38, CFB_LINES 0x3C | `soc.h` L905-929 ; `drivers/include/cdc.h` L52-61 ; `cdc.c` |
| Formats de pixel | 0 ARGB8888, 1 RGB888, 2 RGB565, 3 RGBA8888, 4 AL44, 5 AL8, 6 ARGB1555, 7 ARGB4444 | `cdc.h` L110-116 ; `RTE_Device.h` L1316-1323 |
| DSI base / IRQ | 0x49032000 (404 octets) / 343 ; 2 lanes | `soc.h` L455, L3323 ; SVD ; `RTE_Device.h` L1075 |
| Panneau par défaut | ILI9806E, variante E43GB_MW405, 480x800 (portrait déduit), RGB565 par défaut (`RTE_CDC200_PIXEL_FORMAT 2`) | `RTE_Device.h` (Blinky AppKit) L1037, L1047, L1083-1123, L1325 |
| Framebuffer | section `.bss.lcd_frame_buf`, placée en NS_REGION_0 ou en SRAM0+SRAM1 combinées selon l'éditeur de liens ; 768 000 octets en RGB565 | `linker_ac6_mram.sct` L60-100 ; `demo_cdc200.c` L63 |
| Tactile | GT911, I2C instance 1, reset GPIO4.0, INT GPIO9.x | `Boards/AppKit-e7-aiml/board_defs.h` L89-90, L895-901, L1965-1968 |

**Non sourcé** : adresse réelle du framebuffer (fixée à l'édition de liens) ; taille physique de la dalle ; adresse I2C du GT911 ; numéro de broche INT ; bus exact d'initialisation du panneau (DSI supposé) ; fidélité de l'encodage ACTW/TOTALW (commentaire du driver ambigu) ; commentaire « Default: RGB888 » contredit par la valeur 2.

## Encore à vérifier (liste ouverte)

1. Visibilité par maître des SRAM6_A/B, SRAM7, SRAM8 et SRAM9, ainsi que des alias SRAM2 à SRAM5 et des SRAM0/1 côté A32 (HRM).
2. Taille réelle de la fenêtre XIP OSPI : seuls les deux slots de 2 Mo à 0xC0000000 sont sourcés. Le YAML déclare `ospi_xip` sur 4 Mo.
3. Tailles exactes des fenêtres CGU, PINMUX, AON, CLKCTL… : on connaît la taille des structs (V) mais pas forcément l'étendue décodée (HRM).
4. Modèle IP des MHU et sémantique des registres pour le stub ; appariement TX↔RX (déduit des noms).
5. Caractéristiques A32 (Neon/VFP, L1/L2), débits NPU en GOPS, largeur du bus AXI : tout cela vient de la page série (R).
6. IRQ des UART et MHU côté A32 : non modélisés.
7. Côté Renode, syntaxes à valider par l'agent 04 (ce ne sont pas des faits matériels) :
   - MappedMemory enregistrée à la fois par CPU et globalement ;
   - IRQ multi-destination `-> nvic_hp@N | nvic_he@N` ;
   - `cpuId` ;
   - DWT par CPU.
