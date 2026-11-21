# Spike Renode — phase 0 (agent 04)

**Verdict : GO.** Renode 1.17.0 exécute sans modification les binaires `hello` du SDK Alif sur M55-HP et M55-HE,
les instructions MVE courantes, et deux M55 en parallèle avec TCM locales et IRQ partagée. Les blocages rencontrés
tiennent en quatre contournements localisés, listés ci-dessous (dont un à surveiller : le MPU).
Ce qui n'est **pas** établi figure dans `docs/FIDELITE.md`.

Reproduire : `py -3.14 e7twin/renode/spike/run_spike.py --sdk` (code 0 si les 5 scénarios passent) ;
API : `e7twin/sim.py` (`sim.Session`, `sim.main`).

## 1. Ce qui marche (constaté)

| Scénario | Image | Résultat |
|---|---|---|
| `uart` | firmware écrit à la main (`e7twin/renode/spike/`), XIP depuis le slot MRAM HP, UART4 en polling | `Hello E7 HP` |
| `mve` | idem, `arm_mve.h` | `MVE OK` (voir § 3) |
| `dual` | HP (UART4) + HE (LPUART) simultanés | `DUAL HP OK` / `DUAL HE OK` (voir § 4) |
| `sdk_hp` | `build/spike/hello_hp.elf`, ELF du SDK Alif non modifié | `Hello World!` sur UART4 |
| `sdk_he` | `build/spike/hello_he.elf` | `Hello World!` sur UART4 |

Durée : ~10 s réelles pour un `hello` du SDK (dont un palier ~6 s après `SystemInit`, **non expliqué** : à profiler),
< 5 s pour les firmwares du spike.

### Plateforme `e7.repl` (trois syntaxes que l'agent 01 n'avait pas pu tester)
- (a) Même mémoire enregistrée par CPU (`BusPointRegistration { cpu: … }`) **et** via son alias global : **valide**.
  `peripherals` montre `itcm_hp` à 0x0 pour `cpu_hp` et à 0x50000000 global ; HP et HE ont chacun leur 0x0 / 0x20000000.
- (b) IRQ envoyée aux deux NVIC (`-> nvic_hp@N | nvic_he@N`) : **valide** (test UART7 IRQ 131, § 4).
- (c) `cpuId` + un DWT par CPU : **valide**.
- Seule correction du générateur (`e7twin/board.py`) : la fenêtre du NS16550 est limitée à 0x40 (`<base, +0x40>`),
  le reste (registres DesignWare) est un stub (blocage 5). `tests/test_board.py::test_repl_uarts` ajusté.

## 2. Blocages, un par un

| # | Symptôme | Cause | Correction |
|---|---|---|---|
| 0 | `Could not tokenize here` sur `include @"C:/Dev/E7 Twin/…"` | le monitor Renode ne gère pas les espaces dans un chemin | Renode lancé avec le dépôt en répertoire courant, **chemins relatifs** (`sim.Session._rel` copie dans `build/sim/` un fichier hors dépôt). Les `filename:` des `PythonPeripheral` du `.repl` exigent en revanche un chemin **absolu** (espaces acceptés entre guillemets). |
| 0b | `CreateFileBackend @chemin/relatif` : « could not be created » ; `LoadELF … false false cpu` : signature refusée | chemin relatif refusé par ce backend ; signature 1.17 = `LoadELF(fichier, useVirtualAddress, cpu)` | UART capturées par `emulation CreateServerSocketTerminal` + thread (c'est le mécanisme de `sim.py`) ; pas de `cpuLess`. |
| 1 | CFSR = `IACCVIOL` (+ `INVSTATE`), PC = `0xEFFFFFFE` (lockup) dès la 1re instruction en MRAM | **tlib** (cortex-m33/m55/m85, pas m4/m7) refuse l'exécution à toute adresse ≥ 0x80000000 quand aucune région MPU ne l'autorise (`cpu TranslateAddress 0x80200100 InstructionFetch` → échec ; OK à 0x6…, 0x2…, 0x0…). La carte exécute pourtant depuis la MRAM (0x80xxxxxx) avec le MPU éteint au reset. | `sim.mpu_prime()` : avant le dé-halt, une région RWX par région MRAM/XIP du modèle de carte (écritures MPU_RNR/RBAR/RLAR, MAIR0 attr0 = 0xFF, `MPU_CTRL = ENABLE|PRIVDEFENA`), cœurs maintenus `IsHalted true` pendant l'armement. SP/PC viennent ensuite de la table des vecteurs (VTOR). |
| 2 | Le SDK retombe en HardFault (IACCVIOL) juste après `MPU_Setup` et déborde de l'ITCM (`CPU abort … 0x40000`) | `MPU_Setup()` lit `MPU_CTRL` ; s'il est actif (notre armement) il appelle `ARM_MPU_Disable`, et le code en MRAM n'est plus exécutable (blocage 1) | Patch mémoire : la 1re instruction de `MPU_Setup` (adresse lue dans la table de symboles de l ELF) devient `bx lr` (0x4770) via `sysbus WriteWord` (le hook Python `AddHook` marchait aussi mais initialisait IronPython en cours de boot) : on saute `MPU_Setup`. **Conséquence : la table de régions MPU du SDK (attributs, XN) n'est pas appliquée.** Cause racine à traiter si Renode corrige tlib (le contournement serait alors inutile). |
| 3 | Boucle infinie sur lecture de `0x1A410000` (`sys_busy_loop_us`) | compteur générique 32 768 Hz (`S32K_CNTControl` 0x1A400000 / `S32K_CNTRead` 0x1A410000) absent | Stub « counter » : `CNTCVL/H` = temps virtuel × fréquence ; idem REFCLK (0x1A200000 / 0x1A210000, 100 MHz). Adresses et fréquences ajoutées à `board/ensemble_e7.yaml` (type `counter`, sources : pack Alif 2.2.1 `soc.h` L3199/3200/3210/3211, `sys_utils.c`). |
| 4 | Lecture en boucle du registre 0x7C de l'UART4 | l'API console Alif attend `UART_USR.TFNF` (bit 1) avant d'écrire THR ; le NS16550 de Renode ne modélise pas les registres DesignWare | Stub « uartdw » sur 0x40…0xFF de chaque UART : `USR (0x7C) = 0x06` (TFNF | TFE), TFL/RFL = 0, SRR (0x88), DLF (0xC0), STET (0xA0)… = dernière valeur écrite ; journalisés. **La réception UART n'est pas modélisée** (RFNE = 0). |
| 5 | `'PythonPeripheral' object has no attribute 'WarningLog'` ; `Non-ASCII character … no encoding declared` | scripts Python Renode = **IronPython 2.7** ; le code `exec`é perd les méthodes d'extension de `self` | Un fichier `.py` généré par bloc (`stubs._script_file`), en-tête `# -*- coding: utf-8 -*-`, pas d'`exec`. |

Accès restant non modélisés mais **sans conséquence constatée** pour `hello` (avertissements Renode, journalisés
dans `build/sim/*/renode.log`) : écriture `0xE001E004` (registre propre au M55, valeur 0x2E5) et lecture `0xE001E000`,
opérations de cache (`DCacheCleanBySetWay`, `ICacheInvalidateAll`, « instruction cache not supported »),
`nvic` offset 0x8, et les accès aux blocs CLKCTL/CGU/PINMUX/AON (registres « fichier » : lecture = dernière valeur
écrite ; **aucun bit ready/locked n'a été nécessaire** jusqu'ici, la table `stubs.READY` est vide).

Faits sur les ELF du SDK (utiles au chargeur) : vecteurs et `Reset_Handler` en MRAM (`.startup.at_mram` à 0x80200000
pour HP) ; code lié en **ITCM à 0x0** avec LMA en MRAM, `.data` en DTCM 0x20000000, copiés par `Reset_Handler_C`
via `.copy.table`. Le chargeur charge donc par **adresse de chargement** (`LoadELF … useVirtualAddress=false`) ;
cela confirme que le SDK compte sur une ITCM locale à 0x0.

## 3. MVE

Programme `mve_hp.c` (`-mcpu=cortex-m55 -mfloat-abi=hard -O2`, `Tag_MVE_arch: MVE Integer and FP`) :
produit scalaire int8 `vmladavaq_p_s8` sur 100 éléments (prédication de queue `vctp8q` + `vldrbq_z_s8`),
produit scalaire flottant `vfmaq_m_f32` / `vctp32q`, `vqaddq_s16` (saturation), `vgetq_lane_*`. Le désassemblage
contient en plus des `dls`/`le`, `dlstp`/`letp` (boucles à faible surcoût, y compris prédiquées) et `vpst` produits par
le compilateur : tous exécutés correctement, résultats **identiques au calcul scalaire** (`0x61ee` pour le dot int8).
**Non couvert** : le reste du jeu MVE (FP16, gather/scatter, complexe, histogrammes…), les comportements
sur fautes en milieu de séquence, et tout timing (voir `FIDELITE.md`). Le jeu d'instructions MVE est donc validé
sur un échantillon représentatif des noyaux CMSIS-NN/DSP, pas exhaustivement.

## 4. Multi-CPU

`dual` : HP (UART4) et HE (LPUART) tournent en même temps, chacun avec ses VTOR/SP/PC :
- **TCM locales** : les deux écrivent une marque différente à `0x20000000` ; chacun relit la sienne intacte
  (même adresse, contenu propre au cœur). Le SDK HE/HP dépend aussi de l'ITCM locale à 0x0.
- **Mémoire partagée** : drapeaux en SRAM0 vus par les deux cœurs.
- **IRQ vers deux NVIC** : HP lève la ligne THRE de l'UART7 (IRQ 131) ; les handlers HP **et** HE s'exécutent (une fois chacun).
- Fréquences : `PerformanceInMips` n'est pas lié à la fréquence par défaut (100 MIPS pour les deux) ;
  `sim.Session` applique 400 / 160 (hypothèse CPI = 1, cf. INTERFACES.md § Sample).

## 5. Verdict

**GO** pour l'approche Renode :
- l'init du SDK Alif passe avec peu de stubs (compteur, 2 registres DW UART, regfile) ;
- MVE, FPU, multi-cœur, IRQ, TCM locales fonctionnent ;
- le pilotage headless par monitor + sockets UART est fiable (`sim.Session`, `sim.main`, 140 tests verts).

Risques à suivre (tous documentés) : (1) contournement MPU (blocage 1-2), à lever si une version ultérieure de Renode
corrige tlib ; (2) temps non exact au cycle (statistique de charge seulement) ; (3) MHU, clocks « ready » et NPU non
faits (cycle suivant) ; (4) Renode 1.17.0 n'a pas `alif_ae822fa0e5597bs0.repl` : nous n'en dépendons pas.

## Palier de boot (~5 s) — mesures du cycle 4

`hello_hp.elf`, `Session` complète. Répartition (s, réel) : spawn Renode + monitor + `mach create` + plateforme + stubs + UART ≈ **2,8** (dont `LoadPlatformDescription` ≈ 0,6) ; `load_elf` ≈ 0,15 ; `start` ≈ 0,2 ; **start → 1er octet UART ≈ 5,0** ; total ≈ 8,2.

Le palier est **dans l'exécution après `start`** : le CPU HP exécute exactement 40 000 instructions (1 quantum à 400 MHz, boucle de copie `.data` de `Reset_Handler_C`), puis Renode reste **inactif ~4,5 s (0 % CPU, temps virtuel figé)** avant de reprendre. Causes **écartées par mesure** : IronPython (la lecture d'échauffement ne change rien : ouverture et palier identiques avec/sans), terminaux UART (palier identique sans), cœur HE halted (identique en double chargement), `SetGlobalSerialExecution`, `SetSeed`. `SetGlobalQuantum` (0,5–1 ms) aggrave (boot > 30 s) : à ne pas utiliser. **Cause racine non identifiée** (attente interne de Renode à la 1re synchronisation de quantum, juste après le 1er accès au stub Python `clkctl_per_slv`). Aucun correctif sûr ; `run_until` sonde déjà à 0,05 s, aucun `sleep` fixe en cause. Piste : retirer les stubs Python un par un, ou tester une autre version de Renode.
