# Spike écran (CDC200 → MIPI DSI → panneau) — agent 04

Statut : **étapes A et B faites** (cycle 9) : stub CDC200 + DSI, `Session.read_framebuffer()`, test vert sur le vrai
firmware `display_hp.elf` (§5). Verdict : **GO**. Reste hors spike : routes serveur et vue dashboard (agents 05/06),
conversion PNG côté hôte, vérification sur carte.

Source unique de cette page : pack local `tools/packs/AlifSemiconductor/Ensemble/2.2.1/` (DFP Alif 2.2.1).
Les adresses ci-dessous sont **relevées pour le spike** ; la source de vérité reste `board/ensemble_e7.yaml`
(section `display`, sourcée par l'agent 01). Le stub de l'étape B devra lire les adresses dans le YAML.

## 1. Carte des registres

### 1.1 CDC200 (contrôleur d'affichage)

Base `CDC_BASE = 0x49031000` (`Device/soc/AE722F80F55D5/include/rtss_hp/soc.h:3322`), taille 0x300
(`CDC_Type`, ibid. l. 2083). Pilote bas niveau : `drivers/source/cdc.c`, `drivers/include/cdc.h` ;
pilote CMSIS : `Alif_CMSIS/Source/Driver_CDC200.c`.

Registres globaux (offset depuis `CDC_BASE`) :

| Off. | Nom | Accès | Encodage (cdc.c) | À émuler |
|------|-----|-------|------------------|----------|
| 0x00 | CDC_HW_VER | RO | version | renvoyer une valeur non nulle fixe |
| 0x04 | CDC_LCNT | RO | nb de calques | **2** (`CDC_LAYER_CFG[2]`) |
| 0x08 | CDC_SYNC_SIZE_CFG | RW | `(hsync-1)<<16 \| (vsync-1)` (l. 33) | mémoriser |
| 0x0C | CDC_BP_CFG | RW | `hbp<<16 \| vbp` + sync (cumulé, l. 38) | mémoriser |
| 0x10 | CDC_ACTW_CFG | RW | `hactive<<16 \| vactive` + bp (cumulé, l. 44) | **taille active** = ACTW − BP |
| 0x14 | CDC_TOTALW_CFG | RW | `hfp<<16 \| vfp` + actw (cumulé, l. 50) | mémoriser |
| 0x18 | CDC_GLB_CTRL | RW | bit0 `EN` ; bits 28-31 polarités (cdc.h l. 52-56) | **activation globale** |
| 0x24 | CDC_SRCTRL | RW | `1 << sh_rld` (0 = immédiat, 1 = VBlank) (l. 65) | déclencheur de « nouvelle image » |
| 0x2C | CDC_BACKGND_COLOR | RW | `R<<16 \| G<<8 \| B` (l. 59) | couleur hors calques |
| 0x34/38/3C | IRQ_MASK0 / IRQ_STATUS0 / IRQ_CLEAR0 | RW/RO/WO | | status = 0 (pas d'IRQ ligne en v1) |
| 0x40 | CDC_LINE_IRQ_POS | RW | | mémoriser |
| 0x44/48 | POS_STAT / SYNC_BLANK_STAT | RO | | 0 (non lus en boucle) |

Calques : `CDC_LAYER_CFG[n]` à `0x100 + n*0x100` (n = 0, 1), offsets relatifs au calque :

| Off. | Nom | Encodage (cdc.c `cdc_set_layer_info`, l. 149-216) | À émuler |
|------|-----|---------------------------------------------------|----------|
| 0x08 | CDC_L_REL_CTRL | bit2 `SH_MASK` ; `1<<sh_rld` = rechargement | déclencheur |
| 0x0C | CDC_L_CTRL | bit0 `LAYER_EN` (cdc.h l. 61) | **activation calque** |
| 0x10 | CDC_L_WIN_HPOS | `h_stop<<16 \| h_start` | **fenêtre** |
| 0x14 | CDC_L_WIN_VPOS | `v_stop<<16 \| v_start` | **fenêtre** |
| 0x1C | CDC_L_PIX_FORMAT | enum `CDC_PIXEL_FORMAT` : 0 ARGB8888, 1 RGB888, **2 RGB565**, 3 RGBA8888, 4 AL44, 5 AL8, 6 ARGB1555, 7 ARGB4444 (cdc.h l. 109) | **format** |
| 0x20 | CDC_L_CONST_ALPHA | alpha constant | mémoriser |
| 0x24 | CDC_L_DFLT_COLOR | couleur par défaut | mémoriser |
| 0x28 | CDC_L_BLEND_CFG | facteurs F1/F2 | mémoriser (mélange non émulé en v1) |
| 0x34 | CDC_L_CFB_ADDR | adresse du framebuffer (bus système) | **framebuffer** |
| 0x38 | CDC_L_CFB_LENGTH | `pitch_octets<<16 \| (longueur_ligne_octets + BUS_WIDTH)` (l. 157-176) | **pitch** = bits 31:16 |
| 0x3C | CDC_L_CFB_LINES | nombre de lignes | **hauteur** |
| 0x50 | CDC_L_CLUT_WRACC | écriture CLUT | ignorer (formats CLUT non utilisés) |

**Boucles d'attente du pilote CDC200 : aucune.** `grep while` sur `drivers/source/cdc.c` et
`Alif_CMSIS/Source/Driver_CDC200.c` ne renvoie rien : un regfile qui mémorise les écritures suffit pour
que le pilote ne bloque pas.

### 1.2 MIPI DSI et D-PHY (registres sondés en attente seulement)

Base `DSI_BASE = 0x49032000` (`soc.h:3323`). Boucles bloquantes dans
`Alif_CMSIS/Source/DPHY_DSI.c` (`DSI_DPHY_Init`, l. 388-407), avec garde de 1 000 000 × 1 µs
(le pilote rend `ARM_DRIVER_ERROR` au lieu de bloquer, mais l'init échoue) :

| Registre | Off. | Bits attendus (`drivers/include/dsi.h` l. 175-182) | Réponse du stub |
|----------|------|-----------------------------------------------------|-----------------|
| DSI_PHY_STATUS | 0xB0 | bit0 `PHY_LOCK`, bit2 `STOPSTATECLKLANE`, bit4 `STOPSTATELANE_0`, bit7 `STOPSTATELANE_1` | OR fixe `0x95` en lecture |

`DSI_CMD_PKT_STATUS` (0x74) n'est lu nulle part dans le pack (`grep` sur `dsi.c`, `dsi.h`,
`Alif_CMSIS/Source/*.c`) : l'envoi DCS (`dsi.c` l. 66, boucle sur la longueur du message, pas sur un
statut) ne bloque pas. L'interface de test du D-PHY est **dans la fenêtre DSI** (`PHY_DSI_TEST_CTRL1_BASE =
DSI_BASE + 0xB8`, `Alif_CMSIS/Source/dphy.h:108`) et n'a pas de boucle d'attente (les seules boucles de
`DPHY_*.c` sont DSI l. 388/401 et CSI l. 357/499). **Un seul regfile de 0x1000 à `DSI_BASE`, avec
`READY = {0xB0: 0x95}`, devrait suffire** (à confirmer à l'étape B en exécutant
`Boards/Templates/Baremetal/demo_cdc200.c`). `sys_busy_loop_us` dépend du compteur déjà stubbé.

Panneau : piloté uniquement par commandes DCS via DSI (`components/Source/ILI9806E_LCD_panel.c`, pas de
`while` ni de lecture DCS) ; aucun registre propre sur le bus. Rien à émuler au-delà du DSI.

### 1.3 Résolution et format (pour l'agent 01, à trancher dans le YAML)

`Boards/AppKit-e7-aiml/Examples/M55_H*/Blinky/RTE/Device/*/RTE_Device.h` contient plusieurs panneaux :
480×800 (l. 1103/1123, ILI9806E), 480×854 (l. 1147/1167) et 800×480 (l. 1401/1421). Format par défaut
`RTE_CDC200_PIXEL_FORMAT 2` = RGB565 (l. 1325). Les mesures ci-dessous prennent 480×800×2 = 768 000 octets.

## 2. Mesures de capture d'un framebuffer (768 000 octets)

Banc : `py -3.14 e7twin/renode/spike/fb_bench.py` : session `hello_hp.elf` démarrée (HP en WFI), motif
aléatoire de 768 000 octets chargé en SRAM0 (0x02000000), chaque voie vérifiée octet par octet contre le motif.
Machine de dev Windows 11, Renode 1.17.0.

| Méthode | ms / image (médiane) | img/s max | Remarque |
|---------|---------------------:|----------:|----------|
| `sysbus ReadBytes` blocs 4 Ko (= `Session.read_mem`) | 16 464 | 0,06 | 188 commandes, texte hex ~6 o/octet |
| `sysbus ReadBytes` blocs 64 Ko | 15 898 | 0,06 | le coût est le formatage/parsing du texte, pas le nombre d'aller-retours |
| `sysbus.sram0 DumpBinary` vers fichier | — | — | **non appelable** depuis le monitor 1.17 : `SequencedFilePath` refusé (« Parameters did not match the signature ») avec `@chemin` comme avec `"chemin"` |
| **`python` monitor → fichier binaire** | **4,9** (min 4,2, max 11,9 ; n = 10) | ~200 | `System.IO.File.WriteAllBytes(<abs>, self.Machine.SystemBus.ReadBytes(addr, n))`, puis lecture du fichier par l'hôte ; contenu identique au motif |
| `python` monitor, lecture du bus seule | 0,9 | ~1 100 | borne basse (lecture Renode + aller-retour telnet, sans fichier) |

Côté hôte, conversion RGB565 → PNG en Python pur (table 65 536 entrées + `zlib` + `struct`, sans
dépendance) : 25 ms pour la conversion, **32 ms** au total avec zlib niveau 1 (39 ms au niveau 6), sur
des données aléatoires (pire cas pour la taille, ~1,2 Mo ; une IHM réelle compresse bien mieux).

Pièges relevés :
- le répertoire courant de l'interpréteur `python` du monitor est `tools/renode-1.17.0/`, pas le dépôt : chemin
  **absolu** obligatoire. L'espace de `C:/Dev/E7 Twin` ne gêne pas, puisqu'il est dans une chaîne Python et pas
  dans un jeton `@chemin` ;
- la capture est faite **émulation en marche** : pas atomique vis-à-vis des écritures du firmware (déchirure
  possible, comme sur une vraie carte sans double tampon). Pour une image cohérente : `pause` → capture → `start`
  (non mesuré).

Commandes : `py -3.14 e7twin/renode/spike/fb_bench.py --skip-4k --repeat 10` (64k, python_file,
python_busread) ; la ligne 4 Ko vient du run complet sans `--skip-4k` (`--repeat` plafonné à 2 pour ReadBytes).

## 3. Recommandation (étape A)

**Verdict partiel : GO sur la capture.** La voie `python` du monitor vers un fichier binaire est environ
3 000 fois plus rapide que `ReadBytes`, exacte et sans dépendance. C'est le coût hôte de la conversion PNG
qui fixe la cadence, pas Renode.

- **Méthode** : `Session.read_framebuffer()`, qui lit d'abord la config du calque (`CFB_ADDR`, `CFB_LENGTH`
  pour le pitch, `CFB_LINES`, `WIN_HPOS/VPOS`, `PIX_FORMAT`, `L_CTRL.EN`, `GLB_CTRL.EN`) depuis le stub CDC200
  (lectures `sysbus ReadDoubleWord`, quelques ms), puis copie `pitch × lignes` octets par la voie `python` →
  fichier dans `Session._workdir`. Elle renvoie (largeur, hauteur, format, octets bruts). La conversion PNG
  reste côté hôte (serveur, agent 05/06).
- **Cadence réaliste** : **5 img/s** par défaut, environ 40 ms de travail hôte par image, soit ~20 % d'un
  cœur hôte. 10 img/s possible. Au-delà, envoyer le RGB565 brut au navigateur (canvas) plutôt qu'un PNG, ou
  n'envoyer que sur changement (hash de l'image).
- **Option plus tard** : un stub CDC200 qui écrit lui-même le framebuffer dans un fichier ou un socket quand le
  firmware recharge les registres d'ombre (`SRCTRL` / `L_REL_CTRL`). Capture alignée sur les images du
  firmware, mais plus complexe ; inutile tant que 5 img/s suffisent.
- **Étape B** : stub CDC200 (regfile + `HW_VER`/`LCNT`), regfile DSI (`READY {0xB0: 0x95}`), adresses lues
  dans `board/ensemble_e7.yaml` section `display` (agent 01), firmware mire RGB565, PNG
  `build/spike/display.png`, test pytest.

## 4. Limites de fidélité (connues dès l'étape A)

- Pas de timing d'affichage : ni pixel clock, ni porches, ni VSYNC/IRQ ligne, ni débit DSI ou PHY. L'image
  capturée est l'état de la mémoire à l'instant de la capture, pas une image balayée.
- Mélange de calques (alpha, color key, CLUT) et couleur de fond : non émulés en v1 ; un seul calque affiché.
- DSI et panneau : écritures acceptées sans effet. Init DCS du panneau non vérifiée, rotation et
  orientation du panneau non modélisées.
- Bande passante mémoire du CDC (lecture continue de la SRAM qui ralentit les cœurs sur la vraie carte) : non
  modélisée.
- Rien n'est vérifié sur carte réelle.

## 5. Étape B — stub et capture (cycle 9)

**Réalisé.** `e7twin/renode/stubs.py` génère deux regfiles Python (`cdc200`, `dsi`) à partir de `board.display`
(bases, taille, nombre de calques, offset de `DSI_PHY_STATUS` : tout vient du YAML ; `hw_ver`, `lcnt` et
`dsi.registers.phy_status` y ont été ajoutés). `e7_stub.py` gagne le paramètre `FIXED` (registres RO constants).
`Session.read_framebuffer(coherent=True) -> dict | None` : `None` si `GLB_CTRL.EN` ou aucun calque actif ;
sinon `{width, height, format, pitch, address, data}` (largeur = pitch / octets par pixel, hauteur = `CFB_LINES`,
copie par la commande `python` du monitor). Test : `tests/test_display.py`.

**Résultats sur `display_hp.elf` (démo CDC200 du SDK, cœur HP).** Aucun blocage au boot : le pilote CDC200 et
`DSI_DPHY_Init` passent avec les seuls regfiles (seuls accès DSI observés : lectures 0x14 et 0xB0 ; aucune
trace du PC nécessaire). Le firmware programme le calque 0 (480×800, RGB565, pitch 960, adresse `0x02000000`,
SRAM0). Capture en pause : 33 ms (registres + copie 768 000 octets + relecture).

**Piège découvert : cohérence de la capture.** Pendant un `__WFI`, Renode avance le temps virtuel presque sans
temps réel ; le temps réel se passe donc surtout dans l'effacement/redessin de la barre. Capture en marche
(non atomique) : barre absente dans 2 captures sur 8. D'où `coherent=True` par défaut (pause → copie → reprise) ;
même ainsi l'image peut tomber en plein redessin (état mémoire réel à cet instant, comme sur carte sans double
tampon). Le test reprend donc jusqu'à 12 captures jusqu'à voir la barre (3 runs : verts).

**Limites de fidélité (étape B)** : `CDC_HW_VER` inventé ; shadow reload ignoré (la capture lit les registres en
direct) ; largeur déduite du pitch, fenêtre du calque ignorée ; calque 0 seul ; pas de PNG produit par la
session (conversion côté hôte, §2) ; aucune vérification sur carte.

## 6. Vitesse de simulation (cycle 11, mesure)

Banc : `py -3.14 e7twin/renode/spike/speed_bench.py [--only a,b,...]` ; résultats bruts ajoutés au fur et à mesure à
`build/spike/speed_results.jsonl`. Chronomètre lancé à la 1re ligne UART (`DISPLAY frame 1`), hors palier de boot.
Rapport = temps simulé / temps réel (1,0 = temps réel). Fenêtre réelle : 8 s. Renode orphelins au lancement : voir §6.3.

### 6.1 Tableau

| Cfg | Configuration | Réel (s) | Simulé (s) | Ratio | Appels / médiane |
|-----|---------------|---------:|-----------:|------:|------------------|
| ref | `load_loop_hp` (code en ITCM), Session seule | 8,01 | 8,014 | **1,00** | - |
| a | `display_hp`, Session seule | 8,01 | 18,85 | **2,35** | - |
| b | a + `read_framebuffer(coherent=True)` 5 Hz | 8,01 | 17,86 | 2,23 | 40 appels, médiane 134 ms |
| c | a + `read_framebuffer(coherent=False)` 5 Hz | 8,02 | 17,85 | 2,23 | 40 appels, médiane 27 ms |
| d | `SimSource` (période 0,25 s), sans profiler | 8,04 | 17,05 | 2,12 | poll 4,0 Hz |
| dp | `SimSource` avec profiler | 8,02 | 18,33 | 2,29 | poll 4,0 Hz |
| df | d + `framebuffer()` 5 Hz | 8,45 | 17,91 | 2,12 | 24 appels, médiane 143 ms ; poll 3,45 Hz |
| py | 1er appel `python` du monitor | - | - | - | 5,5 ms (2e : 3,0 ms ; `ReadDoubleWord` : 19 ms, ms de connexion) |

**Lecture de la 1re série (code d'avant le cycle 11).** Mesuré APRÈS « DISPLAY frame 1 », là où le firmware dort en
`__WFI` : ni la capture, ni `SimSource`, ni le profiler, ni le 1er appel `python` ne ralentissent (ratio 2,1 à 2,3 :
Renode rattrape son retard accumulé pendant le boot). **La lenteur est AVANT cette ligne**, pendant le boot.

Boot de `display_hp` jusqu'à `DISPLAY frame 1` (essais `speed_bench`/expériences ad hoc, un essai chacun) :

| Essai | Temps réel | Temps virtuel | Remarque |
|-------|-----------:|--------------:|----------|
| état d'avant (ni patch ni option) | 16,9 s | 1,44 s | ~0,024 s virtuelle par s réelle pendant la phase active |
| `sys_busy_loop_us` court-circuité | 6,6 s | 0,37 s | reste le palier de ~5 s |
| + `SetAdvanceImmediately true` | **0,3 s** | 0,47 s | palier supprimé ; ensuite ratio 36 en WFI |
| `SetAdvanceImmediately` seul | 10,9 s | 1,30 s | |

Après correction (nouveaux défauts de `Session`, `skip_busy_loops=True`) : a, b, c, d, dp, df, ref, ratio **1,00** (temps
réel, pas de retard) ; boot 8 s (dont le palier). Avec `advance_immediately=True` : boot 0,4 s, ratio 39 (a, d), 35 (b),
`load_loop_hp` 2,4.

| Cfg après correction | Réel (s) | Simulé (s) | Ratio | Appels / médiane |
|----------------------|---------:|-----------:|------:|------------------|
| a | 8,01 | 8,17 | 1,02 | - |
| b (coherent=True) | 8,01 | 7,71 | 0,96 | 40 appels, 140 ms |
| d / dp / df | 8,16 / 8,01 / 8,22 | 8,16 / 8,01 / 8,14 | 1,00 / 1,00 / 0,99 | df : 24 appels, 142 ms |
| ref (`load_loop_hp`) | 8,01 | 8,01 | 1,00 | - |
| a + advance_immediately | 8,01 | 325,9 | 40,7 | boot 0,4 s |
| b + advance_immediately | 8,02 | 280,3 | 34,9 | 139 ms |
| d + advance_immediately | 8,01 | 315,5 | 39,4 | - |
| ref + advance_immediately | 8,01 | 19,1 | 2,39 | boot 0,3 s |

### 6.2 Analyse et corrections recommandées

**Cause principale (mesurée).** Pendant le boot, le PC reste sur `sys_busy_loop_us` (`sys_utils.c:118`, pack Alif 2.2.1),
relevé sur 12 échantillons de PC sur 16 s. Cette fonction scrute `S32K_CNTRead->CNTCVL` (compteur 32 kHz) ; chaque lecture
est un appel à un **stub Python** (`e7_stub.py`, KIND counter), et un tick de 30 µs correspond à ~2 400 itérations à
400 MIPS : 9 MIPS réels (contre 90 à 300 MIPS pour une boucle de registres ou de stores SRAM0, mesurés avec de petits
firmwares jetables). Le boot de `display_hp` (≈ 0,4 s virtuelles de délais du pilote CDC/DSI/panneau) coûtait 11 s.
Le stub n'est pas en cause en régime établi (WFI).
**Cause secondaire** : le palier de boot de ~5 s (temps virtuel figé à 0,100 s, 40 000 instructions = un quantum)
disparaît avec `SetAdvanceImmediately true` : c'est l'attente de synchronisation du temps virtuel sur le temps réel de Renode.
Cela réouvre la piste de « Palier de boot » de `SPIKE_RENODE.md` (cause : synchronisation temps réel, pas IronPython).

**Corrections, par gain attendu.**
1. **04, fait (`sim.py`, mesuré)** : `Session(skip_busy_loops=True)` par défaut, `sys_busy_loop_us` remplacée par
   `movs r0,#0 ; bx lr` (technique de `MPU_Setup`). Boot 16,9 → 6,6 s, ratio post-boot 1,0 (plus de retard). Fidélité :
   les délais du SDK ne durent plus en temps virtuel (à documenter dans `FIDELITE.md` si adopté).
2. **04 (option livrée, défaut OFF), décision orchestrateur** : `Session(advance_immediately=True)` : boot 0,4 s au lieu de
   6,6 s ; en contrepartie la simulation court à 35-40x le temps réel pendant les WFI (la durée virtuelle par seconde
   réelle n'est plus 1:1). **05** devra adapter `SimSource` si on l'active (fenêtres de métriques de 0,25 s réelles =
   ~10 s virtuelles, UART/événements en rafale) ; à valider avec le dashboard. Gain maximal, effet de bord de pacing.
3. **04** : capture `coherent=True` = ~110 ms de pause/reprise par appel (140 ms contre 27 ms sans pause, ratio 0,96 à 5 Hz).
   Piste : pause/reprise uniquement quand l'image a changé (hash du dernier `DISPLAY` ou écriture dans le framebuffer),
   ou 2 Hz par défaut. Gain faible (4 % de temps virtuel).
4. **05** : `SimSource.framebuffer()` appelle `read_framebuffer()` sans paramètre (cohérent) sous `self._lock` : une capture
   de 140 ms bloque le thread de poll (poll 3,45 Hz au lieu de 4 dans la 1re série). Passer `coherent=False` (27 ms) si le
   tearing est acceptable, ou capturer hors verrou. Gain faible.
5. **04** : stub compteur 32 kHz : si d'autres firmwares scrutent le compteur sans `sys_busy_loop_us`, rendre la
   lecture moins coûteuse (non mesuré, pas de cas connu).

### 6.3 Processus Renode au démarrage de la tâche

`tasklist` : aucun `renode.exe` ne tournait (les PID 18828 et 11432 vus au cycle 9 ont disparu).
