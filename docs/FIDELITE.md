# Fidélité de la simulation Renode (agent 04)

Tout ce qui est **stubbé, approximé ou absent** par rapport à la carte réelle (AK-E7-AIML, AE722F80F55D5).
Légende : **V** vérifié en émulation · **S** stub · **A** approximation · **X** absent.
Règle : un résultat « OK en simulation » ne prouve que ce qui est en **V**.

## Cœurs et exécution
| Élément | État | Détail |
|---|---|---|
| Cortex-A32 ×2 | **X** | Pas de modèle dans Renode ; inertes en v1. Les images A32 d'un ATOC sont ignorées (`parse_atoc`). |
| M55-HP / M55-HE | **V** | `cortex-m55` de tlib. Instructions MVE/FPU testées sur un échantillon (SPIKE_RENODE § 3). |
| Temps / performance | **A** | Pas de simulation cycle-exacte. `PerformanceInMips` = fréquence en MHz (CPI = 1). Pas de pipeline, caches, wait states MRAM/XIP, contention de bus. Le temps virtuel avance par quantum de 100 µs ; une boucle active fait courir le temps virtuel plus vite que le temps réel. Les mesures de charge/MIPS seront des estimations. |
| Caches I/D | **X** | « not supported » (Renode) ; opérations de maintenance sans effet. Aucun problème de cohérence cache/DMA ne sera visible. |
| MPU | **A** | Armé par le loader pour rendre MRAM/XIP exécutables (tlib interdit ≥ 0x80000000 sans MPU). `MPU_Setup()` du SDK est **court-circuité** (sa 1re instruction, en MRAM simulée, est remplacée par `bx lr`) : ses attributs/XN ne sont pas appliqués ; une faute d'accès que le MPU du SDK aurait levée sur la carte **ne sera pas vue**. |
| TrustZone / IDAU | **X** | Désactivés dans le modèle tlib du M55 (cohérent : le M55 n'a pas TrustZone). |
| Registre impl.-defined M55 (0xE001E000…) | **X** | Écritures/lectures journalisées, sans effet (ITCM/DTCM control, MSCR…). |
| Reset | **A** | `Session.reset()` = `machine Reset` + rechargement des images. Pas de reset par watchdog / SE. |

## Mémoire
| Élément | État | Détail |
|---|---|---|
| TCM locales HP/HE (0x0, 0x20000000) | **V** | Même adresse, contenu propre au cœur, alias globaux (SRAM2-5). |
| SRAM0/1/6-9, MRAM, OSPI XIP | **A** | Mémoires RAM simples. MRAM accessible en écriture (la vraie est en lecture seule/programmée par SETOOLS) ; XIP OSPI = RAM, sans flash ni latence. Aucune protection, ECC, ni retention. |
| Chargement ATOC | **A** | Segments aux adresses de chargement ; pas de vérification de signature ni de la table ATOC réelle écrite par SETOOLS ; `.bin` chargé à `mramAddress`/`loadAddress`. |
| Accès non mappé | **A** (détection heuristique) | Renode ne lève **pas** de BusFault : il journalise « non existing peripheral », renvoie 0 en lecture, perd l'écriture. `Session` lit ce journal (incrémentalement) : `unmapped_events()` renvoie tous les accès (timeline `unmapped`) ; `faults()` en remonte un comme faute `unmapped` **seulement s'il est hors des espaces où des registres existent sans être modélisés** : zone système ARM (≥ 0xE0000000), région « Peripheral » du plan mémoire Armv8-M (0x40000000-0x5FFFFFFF), blocs de 16 Mio contenant un périphérique du modèle (ex. 0x1A…). Vérifié : le débordement de pile de `stack_overflow` (premier accès sous la DTCM, 0x1FFFFEFC) donne `FAULT`, `e7twin run` sort en 1. **Limites** : (1) un accès à un vrai périphérique de la carte situé hors de ces espaces et non modélisé serait classé faute à tort (aucun cas vu : `hello` ne touche que le PPB et 0x1A…) ; (2) un accès non mappé DANS un espace périphérique n'est jamais une faute (la carte pourrait lever un BusFault) ; (3) pas de CFSR/BFAR/PC précis : le fault est constaté par le log, après coup, le firmware continue de tourner jusqu'à l'arrêt ; (4) l'exécution hors mémoire reste un `abort` Renode (arrêt de la machine). |

## Périphériques
| Élément | État | Détail |
|---|---|---|
| NVIC, SysTick, DWT | **V** (Renode) | Un par cœur. SysTick à la fréquence du cœur. DWT : compteur de cycles de Renode (= instructions), pas de cycles réels. |
| UART0-7, LPUART | **A** | NS16550 de Renode (0x00-0x3F, stride 4) + stub DW (0x40-0xFF). Pas de baud rate, FIFO, DMA, ni **réception** (RFNE toujours 0). TX instantané. La console du SDK (UART4) fonctionne ; le LPUART n'est utilisé que par les firmwares du spike. |
| Compteurs S32K (32 768 Hz), REFCLK (100 MHz) | **S** | `CNTCV` = temps virtuel × fréquence, que `CNTCR.EN` soit levé ou non ; `CNTSR` reflète `CNTCR.EN`. Pas d'IRQ de comparaison (CNTBASE0/1). |
| CLKCTL_SYS, CGU, PINMUX, AON, VBAT, ANA, LPGPIO_CTRL, M55HE_CFG, CLKCTL_PER_* | **S** | « Fichier de registres » : lecture = dernière valeur écrite. **Aucun** comportement (PLL lock, horloge prête, mux pins). Premier accès à chaque offset journalisé (WARNING). Aucun bit ready/locked n'a été nécessaire pour `hello` ; la table `stubs.READY` est prête à en recevoir. Le code qui attend un bit de statut que le matériel lèverait seul bouclera. |
| M55HP_CFG (fenêtre locale 0x400F0000) | **X** | Non modélisé (hors `sysctrl` global). |
| MHU (24 fenêtres, 20 liens) | **S** (fonctionnel HP<->HE) | Arm MHUv2 d'après le pilote du pack Alif 2.2.1 : `drivers/include/mhu_driver.h` L67-147 (`MHU_sender/receiver_frame_register_t`, canaux de stride 0x20, trame à 0xF80), `drivers/source/mhu_sender.c` L100-300 (ACCESS_REQUEST/READY, INT_ST/CLR/EN, CH_SET, CH_INT_*) et `mhu_receiver.c` L60-120 (CH_INT_ST0, CH_ST, CH_CLR). Un périphérique Python **par lien**, enregistré aux deux fenêtres locales (TX sur un cœur, RX sur l'autre) ; `CH_SET` -> `CH_ST` du RX -> IRQ RX (si `INT_EN.CHCOMB`) ; `CH_CLR` jusqu'à 0 -> acquittement -> IRQ TX (CHCOMB). Validé par `mhu_pingpong` (100 allers-retours, 400 événements). **Approximations** : ACCESS_READY = ACCESS_REQUEST (le récepteur accorde toujours, donc `RECEIVER_BUSY` n'arrive jamais) ; NR2R/R2NR modélisés, mais pas de masquage fin (CH_MSK_*) testé ; registres d'identification (IIDR/PID/CID) = 0 ; **liens vers les A32 et le SE : une seule extrémité simulée** (un message envoyé n'est jamais acquitté, journalisé avec `dst=none`) ; aucune latence. Les messages sont journalisés (`MHU_EVT`) et exposés par `Session.mhu_events()`. |
| Ethos-U55 (HP 256 MAC, HE 128 MAC) | **X** | Aucun modèle ; une inférence NPU ne peut pas s'exécuter. L'estimation passe par Vela (agent 08), pas par Renode. |
| Services SE (Secure Enclave), SERVICES_* | **X** | Pas de SE : les appels de services du SDK ne reçoivent pas de réponse. `hello` n'en dépend pas. |
| GPIO, I2C, SPI, OSPI contrôleur, DMA, caméra/écran, Ethernet, USB, timers (UTIMER/LPTIMER), WDT, RTC | **X** | Non modélisés (accès journalisés, lecture 0). |
| Consommation, horloges réelles, DVFS | **X** | — |

## Mesures (`Session`)
| Mesure | État | Détail |
|---|---|---|
| `cpu_stats.instructions` | **A** | `ExecutedInstructions` de Renode : instructions exécutées (pas de cycles) ; ne croît pas en WFI. MIPS = instructions / temps virtuel, avec CPI = 1 supposé. |
| `cpu_stats.halted_or_wfi` | **A** | Heuristique : cœur `IsHalted`, ou PC sur/juste après un `WFI`/`WFE` (Renode 1.17 n'expose pas l'état de veille). |
| `stack_watermark` | **A** | Pile peinte 0xDEADBEEF de `__StackLimit` au SP initial (symboles de l'ELF ; absents des firmwares du spike -> `None`). Mesure la plus haute profondeur atteinte, y compris les piles d'exception ; un firmware qui écrit 0xDEADBEEF dans sa pile fausse la mesure. |
| `faults` | **V** | HardFault via CFSR/HFSR (vérifié : `udf` -> CFSR 0x10000, HFSR 0x40000000), lockup / abort Renode via le log. Une faute interceptée puis « réparée » par le firmware reste visible tant que CFSR n'est pas effacé. |
| `virtual_time` | **A** | Temps virtuel Renode depuis le dé-halt (la machine tourne à vide pendant l'armement du MPU : ce temps-là est exclu). Avance au quantum de 100 µs. |

## Robustesse du pilotage (cycle 4)
- **Cause des « connexion au monitor perdue »** (reproduite : 4 sessions parallèles, 7 sessions sur 12 en échec) :
  Renode réécrit tout l'historique des commandes dans `%APPDATA%
enode\history`, **partagé entre instances** ; deux Renode
  concurrents entrent en collision (IOException) et l'un meurt. Correctif : `--config` propre à chaque session
  (`history-path` dans le dossier de travail). Avec le correctif : 12/12 puis 6/6 sessions OK, 0 processus orphelin ; 3 pytest `test_sim.py` simultanés : 3 × 21 passed.
- Ports : jamais deux fois le même dans un processus ; nouvel essai (3 fois) du démarrage du monitor si le port est
  pris par un autre processus, nouvel essai (5 fois) de la création de chaque terminal UART ; le prompt de Renode est
  attendu avant toute commande. **Seul le démarrage est rejoué** : une commande qui échoue ensuite n'est jamais rejouée,
  un `expect` absent donne toujours 1.
- Nettoyage : `close()` arrête Renode (`quit`, puis kill) ; `atexit` tue tout Renode lancé par le processus si `close()` n'a pas eu lieu.
- Preuve : `e7twin/renode/spike/stress_sessions.py -n 3 -k 2` ; `--shared-history` rétablit le bug d'origine.

## Performance connue
- Le boot du `hello` SDK prend ~8,2 s réelles (mesures 04 : ~2,8 s de démarrage Renode et plateforme, ~0,15 s de `load_elf`,
  ~0,2 s de `start`, ~5 s de `start` au premier octet UART dont ~4,5 s d'inactivité à **temps virtuel figé**). Détail des mesures,
  causes écartées et piste ouverte : `docs/SPIKE_RENODE.md`, section « Palier de boot ». **Cause non identifiée.** Conséquence : pendant ce palier `Session.virtual_time()` ne bouge pas (un échantillonneur
  doit tolérer `dt = 0`) et la rafale d'instructions du boot arrive d'un coup ; les tests qui attendent une charge > 0 pendant
  le boot sont fragiles (cas de `tests/test_simsource.py::test_real_hello_hp`).

## Outillage de simulation
- Renode **1.17.0** (IronPython 2.7 pour les stubs ; .NET 8 embarqué). Mise à jour de Renode = revalider (MPU, `LoadELF`).
- `t_virtual_s` des `UartLine` : dernier temps virtuel échantillonné (interrogé à la demande), **pas** l'instant d'émission exact.
- Attribution d'une ligne UART à un cœur : heuristique (LPUART → HE ; sinon HP si HP démarré, sinon le seul cœur démarré). Si HP et HE écrivent sur UART4 (comme le SDK), la sortie est entrelacée comme sur la carte, et attribuée à HP.
- Les blocs stubbés sont générés depuis `board/ensemble_e7.yaml` (`e7twin/renode/stubs.py`) : pas de constante dupliquée.
