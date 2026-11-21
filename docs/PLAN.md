# E7 Twin : simulateur et monitoring pour l'Alif Ensemble E7 AppKit

## Contexte
L'équipe n'a qu'**une seule** carte Alif Ensemble E7 AI/ML AppKit (AK-E7-AIML, SoC AE722F80F55D5). Elle veut un outil sur PC qui permette :
1. de vérifier que le code **compile pour la vraie cible** et **tient dans ses mémoires** ;
2. d'**exécuter le vrai binaire** avant de le flasher, pour voir s'il fonctionne ;
3. de **monitorer** l'usage des ressources : 4 cœurs (2× A32, M55-HP 400 MHz, M55-HE 160 MHz), RAM et MRAM par région, NPU ;
4. de produire une **garantie d'exploitabilité** : un rapport pass/fail et des images prêtes pour SETOOLS.

Choix validés : émulation réelle du binaire (Renode), dashboard web local, cibles logicielles encore ouvertes. On démarre donc sur les M55 en bare-metal/RTOS avec le SDK Alif, en restant extensible.

Limite à annoncer clairement : aucun simulateur ne garantit le fonctionnement à 100 %. Le timing n'est pas au cycle près, et certains périphériques (caméra, MIPI, horloges) ne sont pas modélisés. L'outil garantit quatre choses : binaire compilé pour la bonne cible, placement mémoire valide, démarrage, et logique fonctionnelle validée en émulation. La carte reste le test final, et l'outil réduit les allers-retours sur elle.

## Faits techniques vérifiés (sources Alif / Renode sur GitHub)
- **Mémoire E7**, d'après `alif_ensemble-cmsis-dfp/Device/soc/AE722F80F55D5/config/app_mem_regions.h` :
  - ITCM local 0x00000000 : 256 Ko pour HP et pour HE.
  - DTCM local 0x20000000 : 1 Mo pour HP, 256 Ko pour HE.
  - Alias globaux des TCM : SRAM2 0x50000000, SRAM3 0x50800000, SRAM4 0x58000000, SRAM5 0x58800000.
  - SRAM0 0x02000000 (4 Mo), SRAM1 0x08000000 (2,5 Mo).
  - MRAM 0x80000000 (5,5 Mo), découpée en HE app 0x80000000, HP app 0x80200000 et user 0x80400000.
  - OSPI XIP 0xC0000000.
- UART0..7 de 0x49018000 à 0x4901F000, LPUART 0x43008000. Ce sont des registres de type 16550.
- MHU inter-cœurs de 0x40000000 à 0x400B0000 ; IRQ 33-44 côté HP.
- Toolchain : CMSIS-Toolbox 2.12 (`csolution`/`cbuild`), pack `AlifSemiconductor::Ensemble@2.2.1`, GCC par défaut (armclang en option). Le modèle à suivre est `alif_vscode-template`.
- Packaging : fichiers JSON ATOC (`.alif/M55_HP_cfg.json`…) avec les champs `binary`, `mramAddress`, `loadAddress`, `cpu_id` (M55_HP, M55_HE, A32_0), `flags` (`load`, `boot`) et `signed`.
- **Renode 1.17.0** (Windows portable, runtime .NET 8.0.10 embarqué : aucun .NET à installer) apporte deux choses :
  - un modèle Cortex-M55 avec FPv5 et une large part de Helium/MVE ;
  - le multi-CPU, un monitor telnet, un profiler (Perfetto/speedscope) et des hooks mémoire.
- Le fichier `platforms/cpus/alif_ae822fa0e5597bs0.repl` (cœur RTSS-HE de l'**E8**, UART NS16550) n'est **pas** dans Renode 1.17.0 : il n'existe que sur `master` (commit `c38ac3b`). Il ne sert que de modèle de syntaxe ; notre `.repl` est généré par `board.py`.
- Les ELF du SDK Alif sont **stockés en MRAM et liés en ITCM/DTCM** : le chargeur charge par **LMA**. La table des vecteurs est en MRAM (0x80200000 pour HP, 0x80000000 pour HE).
- Renode ne propose pas de Cortex-A32. Les A32 seront donc représentés comme ressources sans exécution en v1.

## Architecture
Le projet est créé dans `C:\Dev\E7 Twin\`, en Python 3.12+. C'est un paquet `e7twin` avec une CLI `typer`.

```
board/ensemble_e7.yaml        # source de vérité : cœurs, fréquences, régions mémoire, périphériques
e7twin/board.py               # charge le YAML et génère le .repl Renode et les limites mémoire
e7twin/toolchain.py           # localise ou installe arm-none-eabi-gcc, cbuild, le pack Alif et Renode
e7twin/build.py               # `e7twin build` : wrap de cbuild (csolution) ou de CMake, flags M55 imposés
e7twin/check.py               # `e7twin check` : barrière de compatibilité (cf. ci-dessous)
e7twin/package.py             # .elf -> .bin + JSON ATOC prêt pour SETOOLS (app-gen-toc / app-write-mram)
e7twin/renode/                # e7.repl généré, e7.resc, stubs périphériques (MHU, CLKCTL, PINMUX) en Python
e7twin/sim.py                 # pilote Renode headless (telnet) : load ELF par cœur, start/pause/step, sockets UART
e7twin/metrics.py             # collecte : instructions/s, temps actif vs WFI, watermark de pile/tas, accès MHU
e7twin/server.py              # FastAPI + WebSocket : diffuse les métriques et pilote la simu
web/                          # dashboard (HTML/JS vanilla + uPlot)
examples/                     # firmwares de référence (doubles de tests)
tests/                        # pytest
```

### 1. Modèle de carte (`board/ensemble_e7.yaml`)
Ce fichier décrit les 4 cœurs, les 2 Ethos-U55 (256 et 128 MAC) et toutes les régions mémoire ci-dessus avec leur visibilité par cœur (ITCM et DTCM locaux privés). Le `.repl` Renode, les limites du checker et la vue « carte » du dashboard sont tous générés depuis ce fichier, ce qui garantit une seule source de vérité.

### 2. Build (`e7twin build`)
- Il exécute `cbuild` sur un `.csolution.yml` qui suit `alif_vscode-template`, contexte réel `hello.debug+DevKit-E7-HP` ou `hello.debug+DevKit-E7-HE` (`<projet>.<build>+<cible>`, cibles de `alif.csolution.yml`). Le printf de l'exemple Alif passe par **UART4 à 115200 bauds**, sur HP comme sur HE. Un projet CMake simple reste possible grâce à un toolchain file fourni (`-mcpu=cortex-m55 -mfloat-abi=hard`, MVE, `-fstack-usage`, `-Wl,-Map,--print-memory-usage`).
- Il garantit que le compilateur, les flags et le pack Alif sont **identiques** à ceux utilisés pour flasher la vraie carte.

### 3. Barrière de compatibilité (`e7twin check`), au cœur de la « garantie »
Elle s'appuie sur une analyse ELF avec `pyelftools`, sans exécution :
- Attributs ARM (`Tag_CPU_arch` v8.1-M, MVE, FP double précision). Le checker rejette un ELF compilé pour un autre CPU.
- Chaque segment LOAD doit se trouver dans une région valide **pour ce cœur** : pas de DTCM HE plein dans une image HP, et pas de chevauchement avec les fenêtres MRAM des autres cœurs.
- Le taux d'occupation de chaque région est comparé à sa capacité : ITCM, DTCM, SRAM0/1, slot MRAM de 2 Mo.
- Le pire cas de pile est calculé à partir des `.su` et du graphe d'appels (désassemblage `objdump`), puis comparé à la taille de pile réservée.
- La table des vecteurs doit être à l'adresse de chargement et le `Reset_Handler` doit être présent.
- La config ATOC est cohérente : `mramAddress` correspond à l'ELF, les images HP et HE ne se chevauchent pas, et la taille du `.bin` reste sous le slot.
- Le résultat est un rapport JSON/HTML pass/warn/fail, avec un code de sortie non nul en cas d'échec (utilisable en CI).

### 4. Simulateur (`e7twin run`)
- La plateforme `e7.repl` est générée en partant du `.repl` Alif de Renode. Elle contient :
  - `cpu_hp` et `cpu_he` (cortex-m55), avec leur ITCM et DTCM enregistrés **par CPU** (`BusPointRegistration { cpu: ... }`) et leurs alias globaux ;
  - SRAM0/1 et la MRAM partagées ;
  - les UART NS16550 ;
  - les SysTick et NVIC propres à chaque cœur.
- Les périphériques manquants sont fournis par des **stubs Python Renode** :
  - un MHU fonctionnel (doorbell et interruption vers l'autre cœur) ;
  - des stubs pour les registres d'horloge, de PINMUX et de power, qui renvoient « prêt » pour que le code d'init du SDK ne bloque pas ;
  - un accès à une adresse non mappée est journalisé et remonté comme **erreur** dans le rapport.
- Le démarrage imite l'ATOC : chaque image est chargée à son `mramAddress` ou `loadAddress`, puis les cœurs marqués `boot` démarrent. Le Secure Enclave est simulé par ce chargeur.
- La simulation tourne en headless avec `renode --disable-gui -P <port>`. `sim.py` la pilote par le monitor telnet, et chaque UART est exposée sur un socket TCP (`CreateServerSocketTerminal`).

### 5. Métriques (`metrics.py`)
- **Charge par cœur** : instructions exécutées par fenêtre de temps virtuel, plus la part de temps en WFI/halted. Le taux d'utilisation est ramené à la fréquence réelle du cœur (400 ou 160 MHz).
- **Profil** : `cpu EnableProfiler CollapsedStack/Perfetto` donne les fonctions chaudes et un flamegraph ; la trace Perfetto est téléchargeable.
- **Mémoire** :
  - occupation statique tirée de l'ELF ;
  - watermark dynamique de pile par *stack painting*, avec un motif écrit au chargement puis relu périodiquement via `sysbus ReadDoubleWord` ;
  - fin du tas lue via le symbole `__HeapLimit` ou `_sbrk`.
- **Inter-cœurs** : hooks sur les registres MHU, qui alimentent une timeline des messages HP↔HE.
- **NPU** : estimation hors exécution avec **Vela** (`ethos-u-vela`), qui donne cycles, SRAM et part d'opérations offloadées pour un `.tflite` sur U55-256 et U55-128.
- **A32** : affichés comme ressources sans charge en v1.

### 6. Dashboard web (`e7twin serve`, ouvert sur http://localhost:8000)
- **Vue carte** : schéma du SoC, avec les cœurs colorés selon leur charge et les mémoires selon leur remplissage.
- **Onglet par cœur** : courbe de charge, MIPS, fonctions chaudes, console UART en direct.
- **Mémoire** : barres par région (statique et watermark), avec alerte au-delà de 85 %.
- **Timeline** des messages MHU et des événements (faults, accès invalides).
- **Compatibilité** : le rapport de `check`, avec ses verdicts pass/warn/fail.
- **NPU** : les estimations Vela.
- **Contrôles** : charger des ELF, start/pause/reset, régler la vitesse.

### 7. Packaging (`e7twin package`)
La commande produit `.bin` et JSON ATOC, sur le même modèle que `.alif/M55_HP_HE_mram_cfg.json`, ainsi que la ligne de commande SETOOLS à lancer sur le PC relié à la carte. L'outil ne flashe **jamais** la carte lui-même dans cette version.

## Mise en place du projet (étape 0, avant le code)

### Dépôt `C:\Dev\E7 Twin`
- Le dépôt contient au départ :
  - `README.md` : but, limites et installation ;
  - `CLAUDE.md` : règles du projet (aucun commit sans demande, jamais de Co-Authored-By, carte unique donc jamais de flash automatique, plus les commandes de build et de test) ;
  - `.claude/settings.json` : `git push` refusé par défaut ;
  - `.gitignore` (Python, build, `*.elf`, `*.bin`, traces) ;
  - `docs/PLAN.md` (ce plan) et `docs/E7_FAITS.md` (mémoire, UART et MHU avec leurs URLs sources) ;
  - `docs/agents/<agent>.md` : une fiche de rôle par agent ;
  - l'arborescence vide de l'architecture ci-dessus, avec des `.gitkeep`.
- Les fiches `docs/agents/*.md` sont aussi copiées dans `.claude/agents/`, ce qui les rend utilisables comme sous-agents Claude Code.

### Projet cowork ClaudeDesk : `Documents\ClaudeDesk\E7 Twin\`
- `_dossier.json` du projet : `kind: project`, `cwd: C:\Dev\E7 Twin`, `icon: code`. Ses `notes` résument le projet, les règles et l'emplacement du plan.
- Chaque **thème est un agent**. Son `_dossier.json` porte le rôle dans `notes` : périmètre, fichiers possédés, interfaces avec les autres agents et définition de « terminé ». ClaudeDesk injecte ces notes dans le prompt système de toutes les conversations du thème.

| Thème (dossier) | Rôle de l'agent | Fichiers possédés |
|---|---|---|
| `00 Coordination` | Architecte : plan, interfaces, revue, roadmap | `docs/`, `CLAUDE.md` |
| `01 Modèle de carte` | Faits E7 (doc Alif, SVD), YAML, génération du `.repl` | `board/`, `e7twin/board.py` |
| `02 Toolchain & Build` | Installation, cbuild/CMake, flags M55, packaging ATOC | `toolchain.py`, `build.py`, `package.py` |
| `03 Compatibilité` | Barrière ELF/pile/ATOC, rapport pass/fail | `check.py` |
| `04 Simulateur Renode` | Plateforme bi-M55, stubs MHU et horloges, chargeur | `e7twin/renode/`, `sim.py` |
| `05 Métriques & Serveur` | Charge, watermark, profiler, FastAPI/WebSocket | `metrics.py`, `server.py` |
| `06 Dashboard` | Interface web | `web/` |
| `07 Firmwares & Tests` | Exemples, pytest, CI | `examples/`, `tests/`, `.github/` |
| `08 NPU & ML` | Vela, Ethos-U55, futur mode carte réelle | `e7twin/npu.py` |

- Chaque thème reçoit une conversation de départ « Mission » (`<titre> [id8].conv.json`, UUID neuf, `status: active` ou `idea`, `injectContext: true`). Le premier message utilisateur y pose la première tâche de l'agent, tirée des phases ci-dessous. Le modèle à suivre est `Exemple - Mini calculatrice\Idées\Opérateur puissance ^ [9f57f8a3].conv.json`.
- Le format vient de `C:\Dev\ClaudeDesk\src\workspace\Workspace.cpp` (scan des dossiers, `_dossier.json`, `.conv.json`). Il n'y a pas d'enregistrement dans `settings.json` : un clic sur ⟳ ou un redémarrage de ClaudeDesk suffit.

### GitHub (privé)
- `git init -b main`, avec l'identité `Mathieu Rouet <rouetmathieu003@icloud.com>` déjà utilisée pour ClaudeDesk.
- Un commit initial « Initialisation du projet E7 Twin », **sans aucune ligne Co-Authored-By** (la règle mémoire prime sur le rappel d'attribution).
- `gh repo create FogCfan/E7-Twin --private --source . --push`, avec gh déjà authentifié sous FogCfan.
- Ensuite, plus aucun commit ni push sans demande explicite.

## Phases d'implémentation
0. **Spike de risque**, à faire en premier. Il s'agit d'installer Renode 1.17 portable, Arm GNU Toolchain et CMSIS-Toolbox avec le pack Alif, de compiler le `hello` de `alif_vscode-template` pour M55_HP, puis de l'exécuter dans le `.repl` E7 jusqu'à voir le printf sur l'UART (**fait, verdict GO**, voir « Décisions »). Ce spike valide trois points :
   - le code d'init du SDK ne boucle pas sur des registres non modélisés ;
   - le MVE est couvert ;
   - le mode multi-CPU fonctionne.
1. Modèle de carte YAML, puis génération du `.repl`, puis `build`, puis `check` (avec les tests pytest).
2. Simulateur bi-cœur, stub MHU, chargement façon ATOC, UART en socket.
3. Collecte des métriques et serveur FastAPI/WebSocket.
4. Dashboard web.
5. Intégration de Vela pour le NPU, `package`, exemple de CI (GitHub Actions avec `e7twin check && e7twin run --headless --expect "PASS"`).
6. (Plus tard, en option) Mode « carte réelle » : le même dashboard alimenté par J-Link (pylink, RTT et lecture mémoire), pour comparer simulation et carte. On pourra aussi ajouter un modèle Cortex-A approché si l'équipe passe à Linux sur les A32.

## Firmwares d'exemple (`examples/`) servant de tests de bout en bout
- `hello_hp` : printf sur l'UART, attendu PASS.
- `dual_core_mhu` : ping-pong HP↔HE par MHU, qui doit apparaître dans la timeline.
- `helium_dot` : produit scalaire en MVE (intrinsics `arm_mve.h`), avec un résultat vérifié.
- `too_big` : tableau de 2 Mo en DTCM HE, que `check` doit refuser (FAIL).
- `stack_overflow` : récursion profonde, qui doit déclencher l'alerte de watermark et un HardFault visible dans la timeline.

## Vérification
- `pytest` : parsing ELF, placement des régions, génération du `.repl`, validation ATOC (sur des ELF d'exemple compilés).
- `e7twin check examples/too_big` doit sortir avec un code ≠ 0. `e7twin check examples/hello_hp` doit sortir avec 0.
- `e7twin run examples/dual_core_mhu --headless --expect "PONG 100"` doit sortir avec 0.
- `e7twin serve`, puis ouverture du dashboard dans Chrome pour vérifier :
  - les courbes de charge HP et HE évoluent ;
  - les barres mémoire correspondent au `.map` ;
  - la console UART défile ;
  - les messages MHU apparaissent.
- Enfin, flasher `hello_hp` packagé avec SETOOLS sur la vraie carte, et comparer la sortie UART avec celle de la simulation (validation croisée sim↔carte).

## Prérequis à installer (aucun présent aujourd'hui sauf Python, CMake et Ninja)
- Arm GNU Toolchain 14.x (`arm-none-eabi-gcc`)
- CMSIS-Toolbox 2.12, avec le pack Alif Ensemble 2.2.1
- Renode 1.17.0 portable Windows (.NET 8.0.10 embarqué, rien d'autre à installer)
- `pip install pyelftools fastapi uvicorn typer ethos-u-vela`
- SETOOLS (compte Alif requis), uniquement sur le poste qui flashe la carte

## Décisions

Journal des décisions d'architecture (agent 00). Format : date — décision — raison.

- **2026-11-20 — Python 3.14 accepté.** Le minimum reste 3.12 (`requires-python >=3.12`). Le poste
  de dev a deux Python 3.14 : celui de MSYS2 (`C:\msys64\ucrt64\bin\python.exe`, premier dans le
  PATH, **sans pip**) et le Python officiel (`py -3.14`). Installer et lancer avec `py -3.14 -m pip …`
  / `py -3.14 -m pytest`.
- **2026-11-20 — Contrat `board` figé** (`Region`, `Core`, `AtocSlot`, `Peripheral`, `Board`,
  `load()`, `generate_repl()`), détaillé dans `docs/INTERFACES.md`. `board.load()` cherche le YAML
  dans l'ordre : argument, `E7TWIN_BOARD`, `<dépôt>/board/`, puis la copie empaquetée
  `e7twin/_board/` (wheel). `board/` reste à la racine : c'est la source de vérité unique.
- **2026-11-20 — CLI à imports paresseux.** `e7twin/cli.py` ne contient aucune logique métier et
  importe chaque module au moment de la commande ; un module absent ou cassé donne un message
  propre et le code 2, sans empêcher les autres commandes. Codes : 0 succès, 1 échec métier,
  2 non implémenté.
- **2026-11-20 — Build backend hatchling.** Le paquet inclut `e7twin/renode/*` ; `board/` est
  copié dans la wheel via `force-include`.
- **2026-11-21 — Licences des packs CMSIS acceptées** par l'humain ; les packs (Ensemble 2.2.1, CMSIS 6.1.0, CMSIS-Compiler 2.1.0) sont installés.
- **2026-11-21 — GCC 14.3.1 conservé** (Arm GNU Toolchain 14.3.rel1), et non le 13.x du template Alif.
- **2026-11-21 — Renode : GO après le spike (phase 0).** Firmwares bare-metal et `hello` du SDK Alif affichent « Hello World! » sur UART4 dans Renode 1.17.0 (vérifié par l'orchestrateur). Renode embarque .NET 8.0.10 ; le `.repl` Alif E8 n'est pas dans la 1.17.0.
- **2026-11-21 — Chargement par LMA.** Les ELF du SDK sont stockés en MRAM et liés en ITCM/DTCM ; le chargeur charge par LMA.
