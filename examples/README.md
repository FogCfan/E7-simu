# Firmwares de référence (agent 07)

Solution csolution copiée et adaptée de `tools/alif_vscode-template` (commit `ebbf1513`) : mêmes packs
(`AlifSemiconductor::Ensemble@2.2.1`, `ARM::CMSIS@6.1.0`, `ARM::CMSIS-Compiler@2.1.0`), même board DevKit-E7
(même SoC que l'AppKit), mêmes layers et même `cdefault.yml`. Aucun lien vers `tools/`.

| Projet | Cœur | Attendu |
|---|---|---|
| `hello_hp` | HP | `check` sans FAIL ; UART4 : « Hello E7 HP » |
| `hello_he` | HE | `check` sans FAIL ; UART4 : « Hello E7 HE » |
| `parity_hp` | HP | **test de parité carte / simu** : 10 contrôles déterministes (CRC32, U64, flottants, RAM, tas, horloge, SysTick) affichés à l’écran et sur UART4 ; ligne `SIG xxxxxxxx` identique carte et simu = même comportement. Lignes `I` (CPUID, adresses) hors signature. Vérifié sur carte (AppKit E7, 2026-11-25) : `SIG E1C0E658  10/10 OK`, écran compris. |
| `helium_dot` | HP | produit scalaire int8 MVE = scalaire ; UART4 : « MVE OK » |
| `too_big` | HE | **`check` FAIL** : tableau de 2 Mo en DTCM. Compile parce que son `app_mem_regions.h` déclare volontairement une DTCM de 4 Mo. Ne jamais flasher. |
| `stack_overflow` | HP | récursion infinie ; fault attendu sur carte (voir `tests/test_examples.py` pour ce que Renode montre) |
| `dual_core_mhu_hp` / `dual_core_mhu_he` | HP + HE | ping-pong MHU ×100 ; UART4 : « PONG 100 » |
| `display_hp` | HP | CDC200 → DSI → panneau ILI9806E 480×800 RGB565 ; barre blanche qui descend de 40 lignes toutes les 500 ms ; UART4 : « DISPLAY frame <n> » |
| `load_loop_hp` / `load_loop_he` | HP + HE | boucle infinie, 1 tour = 1 s simulée ; calcul 10→90 % puis WFI ; UART4 : « HP tick <n> load <pct>% » |
| `dual_core_mhu_hp` / `dual_core_mhu_he` | HP + HE | ping-pong MHU ×100 ; UART4 : « PONG 100 » |
| `display_hp` | HP | CDC200 → DSI → panneau ILI9806E 480×800 RGB565 ; barre blanche qui descend de 40 lignes toutes les 500 ms ; UART4 : « DISPLAY frame <n> » |
| `load_loop_hp` / `load_loop_he` | HP + HE | boucle infinie, 1 tour = 1 s simulée ; calcul 10→90 % puis WFI ; UART4 : « HP tick <n> load <pct>% » |

```powershell
powershell -ExecutionPolicy Bypass -File examples\build_all.ps1            # les 5
powershell -ExecutionPolicy Bypass -File examples\build_all.ps1 -Only too_big
```

Sorties : `build/examples/<nom>.{elf,map,su}`. `-fstack-usage` est actif dans chaque projet. Le `.su` fusionne
ceux de l'application et du SDK : donner un dossier par projet à `check --su-dir`, car `build/examples/` les mélange.



Descriptions lisibles par l'interface (une ligne par exemple, `nom — description — cœur(s)`) :

- display_hp — affichage CDC200/DSI 480×800 RGB565, barre animée toutes les 500 ms, UART « DISPLAY frame <n> » — HP
- load_loop_hp — charge cyclique 10→90 % sur 1 s simulée, veille WFI entre les tours — HP
- load_loop_he — charge cyclique 10→90 % (déphasée) sur 1 s simulée, veille WFI entre les tours — HE

Descriptions lisibles par l'interface (`nom — description — cœur(s)`) :

- display_hp — affichage CDC200/DSI 480×800 RGB565, barre animée toutes les 500 ms, UART « DISPLAY frame <n> » — HP
- load_loop_hp — charge cyclique 10→90 % sur 1 s simulée, veille WFI entre les tours — HP
- load_loop_he — charge cyclique 10→90 % (déphasée) sur 1 s simulée, veille WFI entre les tours — HE

## Tester la parité sur la vraie carte

`parity_hp` sert à comparer la carte et le simulateur. L'outil ne flashe jamais : l'humain lance la commande.

1. `py -3.14 -m e7twin.cli package build/examples/parity_hp.elf` affiche la commande SETOOLS ; la lancer soi-même.
   Sur l'AppKit E7 (SE rev B4), ajouter le débit et le port du SE : `./app-write-mram -p -b 55000 -c <port>`.
2. Lire l'écran (CDC200) jusqu'à `PARITY: done`. **Sur l'AppKit, l'UART4 ne sort pas sur l'USB** (le port série de la
   J-Link est relié à SEUART ou à UART2 selon les cavaliers J15) : l'écran est la sortie lisible sans matériel en plus.
   Sans écran, la J-Link lit le résultat pendant que le programme tourne : `g_sig`, `g_tests`, `g_pass` (adresses par
   `arm-none-eabi-nm`), et le framebuffer `lcd_image` (`savebin`, 480×800 RGB565).
3. Comparer la ligne `SIG` à la référence simulée : **`SIG E1C0E658  10/10 OK`** (identique sur 2 exécutions Renode).
   Les 10 lignes `T` doivent aussi toutes afficher `PASS`.
   **Vérifié sur la carte** (AppKit E7, SE v1.100.0, 2026-11-25) : même signature, mêmes 10 lignes `T`, écran affiché.
4. Peuvent légitimement différer : les lignes `I` (CPUID, adresses `SRAMBUF`/`ADDR_VAR`), hors signature. Sur la carte,
   CPUID = `411FD220` (Cortex-M55 r1p0) ; Renode donne `411FD221` (r1p1).
5. L'écran ne s'allume sur la carte que parce que le programme demande d'abord au Secure Enclave l'alimentation de la
   D-PHY MIPI et ses horloges (`se_power_display()`). Sans ces appels, `Driver_CDC200.Initialize` rend -1 sur la carte,
   alors que la simulation passe : voir la ligne Écran de [`docs/FIDELITE.md`](../docs/FIDELITE.md). Tout nouveau
   programme qui utilise l'écran doit reprendre cette séquence. La configuration RTE de la dalle (ILI9806E, variante
   E43GB_MW405) vient du DevKit-E7 et convient à l'AppKit (vérifié).
