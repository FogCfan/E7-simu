# Build : chaîne de compilation E7 Twin (agent 02)

Ce document décrit comment installer les outils et compiler pour la vraie carte (AK-E7-AIML, SoC AE722F80F55D5).
Il fournit les commandes exactes, les versions épinglées et les pièges rencontrés.
Le code de référence se trouve dans `scripts/install_tools.ps1`, `scripts/build_spike.ps1` et `e7twin/toolchain.py`.

> **État (2026-11-21)** : outils et packs installés (licences acceptées par l'humain), `e7twin doctor` sans manque.
> `hello` compile pour HP et HE : `build/spike/hello_{hp,he}.{elf,map,readelf.txt,size.txt}`.

## 1. Versions épinglées

| Outil | Version | Source | SHA-256 de l'archive |
|---|---|---|---|
| Arm GNU Toolchain (arm-none-eabi, mingw-w64 x86_64) | **14.3.rel1** (`gcc -dumpfullversion` = 14.3.1) | developer.arm.com `…/14.3.rel1/binrel/arm-gnu-toolchain-14.3.rel1-mingw-w64-x86_64-arm-none-eabi.zip` | `864c0c88…74f60986` (publié par Arm, `.sha256asc`) |
| CMSIS-Toolbox (cbuild, csolution, cpackget 2.1.9, cbuild2cmake 0.9.9) | **2.12.0** | GitHub Open-CMSIS-Pack/cmsis-toolbox, `cmsis-toolbox-windows-amd64.zip` | `07093299…46fdb42c` (`cmsis-toolbox-checksums.txt`) |
| Renode portable Windows | **1.17.0** (build `1.17.0+20260906gitf1dd1b4af`) | GitHub renode/renode, `renode-1.17.0.windows-portable.zip` | `18bcf145…2b9db` (calculé au 1er téléchargement, aucun checksum publié) |
| Pack `AlifSemiconductor::Ensemble` | **2.2.1** | index keil.com via cpackget | — |
| Pack `ARM::CMSIS` | **6.1.0** | idem | — |
| Pack `ARM::CMSIS-Compiler` | **2.1.0** | idem (requis par `hello`, cf. `alif.cbuild-pack.yml`) | — |
| alif_vscode-template | commit **`ebbf1513`** (2026-10-19) | https://github.com/alifsemi/alif_vscode-template | — |
| CMake / Ninja | 4.3.3 / 1.13.2 (msys64 ucrt64, **non épinglés**, pris dans le PATH) | — | — |

Les SHA-256 complets figurent dans `scripts/install_tools.ps1`, qui les vérifie à chaque téléchargement.

**.NET** : Renode 1.17.0 portable Windows est **self-contained**. Il embarque `coreclr.dll`, `hostfxr.dll`, et son
`Renode.runtimeconfig.json` déclare `includedFrameworks: Microsoft.NETCore.App 8.0.10` (`tfm: net8.0`).
**Aucun .NET système n'est requis**, ni .NET 10 ni .NET 8. `docs/PLAN.md` et `docs/E7_FAITS.md` mentionnent « .NET 10 » à tort
(voir § 7).

## 2. Installation

```powershell
# Depuis C:\Dev\E7 Twin
powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1                       # 1re passe
powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1 -AcceptPackLicenses   # après lecture des licences
```

Le script est idempotent : chaque étape est sautée si elle est déjà faite. Tout va dans `tools/`, ignoré par git.

```
tools/
  _dl/                          archives téléchargées (vérifiées par SHA-256)
  arm-gnu-toolchain-14.3.rel1/  bin/arm-none-eabi-*.exe
  cmsis-toolbox-2.12.0/         bin/{cbuild,csolution,cpackget,cbuild2cmake}.exe, etc/
  renode-1.17.0/                renode.exe (+ runtime .NET 8.0.10 embarqué), platforms/
  packs/                        CMSIS_PACK_ROOT (cpackget init https://www.keil.com/pack/index.pidx)
  alif_vscode-template/         clone au commit épinglé (lecture seule pour nous)
```

Options : `-SkipGcc`, `-SkipCmsisToolbox`, `-SkipRenode`, `-SkipPacks`, `-SkipTemplate`, `-ToolsDir <dossier>`.

### Licences des packs : accord humain requis

`cpackget add` affiche la licence embarquée de chaque pack et attend `[A]ccept [D]ecline [E]xtract`.
Sans `-AcceptPackLicenses`, le script n'installe aucun pack. Il extrait les textes de licence, les liste, puis
s'arrête avec le code **2**. Fichiers à lire :

| Pack | Licence | Fichier extrait |
|---|---|---|
| ARM::CMSIS@6.1.0 | Apache-2.0 | `tools/packs/.Download/ARM.CMSIS.6.1.0.pack.LICENSE` |
| ARM::CMSIS-Compiler@2.1.0 | Apache-2.0 | `tools/packs/.Download/ARM.CMSIS-Compiler.2.1.0.pack.LICENSE` |
| AlifSemiconductor::Ensemble@2.2.1 | **Alif Semiconductor Software License Agreement** (propriétaire, droit californien, rév. 10/2022) | `tools/packs/.Download/AlifSemiconductor.Ensemble.2.2.1.pack.License.txt` |

`-AcceptPackLicenses` passe `--agree-embedded-license` à cpackget. Cela vaut acceptation : c'est à l'humain d'en décider.
Les archives `.pack` sont déjà en cache dans `tools/packs/.Download`, l'installation prendra donc quelques secondes.

### Vérification

```powershell
python -m e7twin.toolchain              # tableau lisible
python -m e7twin.toolchain --json       # doctor() en JSON
python -c "from e7twin import toolchain; print(toolchain.doctor())"
e7twin doctor                           # via la CLI (agent 00), code 1 si un outil requis manque
```

État constaté le 2026-11-20 : arm-none-eabi-gcc 14.3.1, cbuild 2.12.0, csolution 2.12.0, cpackget 2.1.9, cmake 4.3.3,
ninja 1.13.2, renode 1.17.0 et .NET embarqué 8.0.10 sont **OK**. Les 3 packs sont **manquants**, en attente de la licence.

## 3. Environnement (`e7twin.toolchain.env()`)

| Variable | Valeur | Rôle |
|---|---|---|
| `PATH` | `tools/cmsis-toolbox-2.12.0/bin`, `tools/arm-gnu-toolchain-14.3.rel1/bin`, `tools/renode-1.17.0` placés en tête | outils épinglés prioritaires |
| `CMSIS_PACK_ROOT` | `tools/packs` s'il est initialisé, sinon `$CMSIS_PACK_ROOT`, sinon `%LOCALAPPDATA%\Arm\Packs` | packs |
| `CMSIS_COMPILER_ROOT` | `tools/cmsis-toolbox-2.12.0/etc` | fichiers `*.cmake` de CMSIS-Toolbox |
| `GCC_TOOLCHAIN_14_3_1` | `tools/arm-gnu-toolchain-14.3.rel1/bin` | enregistre le compilateur : `cbuild list toolchains` → `GCC@14.3.1` |

Recherche des outils : d'abord `tools/` (`E7TWIN_TOOLS_DIR` peut le déplacer), puis le `PATH`. Si plusieurs versions
sont présentes dans `tools/`, la plus récente l'emporte.

## 4. Compiler `hello` (spike phase 0)

Contextes définis par le template (`csolution list contexts alif.csolution.yml`, vérifié) :
`hello.debug+DevKit-E7-HP` et `hello.debug+DevKit-E7-HE`, plus `release` et les variantes `blinky` / `hello_rtt`.
Les target-types s'appellent `DevKit-E7-HP` / `DevKit-E7-HE` et non `M55_HP` / `M55_HE`. Le device est
`Alif Semiconductor::AE722F80F55D5LS:M55_HP|M55_HE` et la board `Alif Semiconductor::DevKit-E7`.

Commande tout-en-un, qui produit `build/spike/hello_{hp,he}.{elf,map,readelf.txt}` :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_spike.ps1
```

Commande exacte exécutée par le script, avec l'environnement du § 3 et depuis `tools/alif_vscode-template` :

```powershell
cbuild alif.csolution.yml --context hello.debug+DevKit-E7-HP --frozen-packs -O "C:\Dev\E7 Twin\build\alif_template"
cbuild alif.csolution.yml --context hello.debug+DevKit-E7-HE --frozen-packs -O "C:\Dev\E7 Twin\build\alif_template"
```

- `--frozen-packs` : les versions de packs de `alif.cbuild-pack.yml` sont imposées, et le build échoue si elles changent.
- `-O` : `out/` et `tmp/` sont écrits sous `build/alif_template` au lieu du clone. Le script vérifie ensuite par
  `git status` que le clone n'a pas été modifié. `RTE/_debug_DevKit-E7-*/RTE_Components.h` est versionné dans le template.
- Flags imposés par le template (`cdefault.yml`, GCC) : `-std=c99 -fdata-sections` ; link `-z noexecstack --specs=nosys.specs
  --entry=Reset_Handler -Wl,-Map=linker.map,--cref,-print-memory-usage,--gc-sections,--no-warn-rwx-segments` ;
  libs `-lm -lc -lgcc`. CPU, FPU et MVE viennent du device dans le pack (`Dfpu=DP_FPU Dmve=FP_MVE`).

Résultat (GCC 14.3.1, CMake 4.3.3 msys64, `Build summary: 1 succeeded` pour chaque cœur, ~2 s) :

| | M55_HP | M55_HE |
|---|---|---|
| ITCM utilisée | 33 664 o / 256 Ko (12,84 %) | 34 032 o / 256 Ko (12,98 %) |
| DTCM utilisée | 1 Mo / 1 Mo (**100 %**, voir piège 9) | 256 Ko / 256 Ko (**100 %**) |
| MRAM utilisée | 42 176 o / 2 Mo (2,01 %) | 42 608 o / 2 Mo (2,03 %) |
| SRAM0 / SRAM1 / MRAM_USER | 0 | 0 |

### UART du `printf` (pour l'agent 01)

Sources lues **V** dans le template au commit `ebbf1513` :
`hello/RTE/Services/AE722F80F55D5LS_M55_{HP,HE}/retarget_config.h` et `hello/RTE/Device/AE722F80F55D5LS_M55_{HP,HE}/RTE_Device.h`.

| Paramètre | M55_HP | M55_HE |
|---|---|---|
| UART console (`PRINTF_UART_CONSOLE`) | **UART4** | **UART4** (identique) |
| Baud rate (`PRINTF_UART_CONSOLE_BAUD_RATE`) | **115200** | 115200 |
| Mode | bloquant / polling (`RTE_UART4_BLOCKING_MODE_ENABLE 1`), DMA off | idem |
| Horloge source (`RTE_UART4_CLK_SOURCE`) | 1 = CLK_100MHz | idem |
| Broches | RX P12_1 / TX P12_2, `PINMUX_ALTERNATE_FUNCTION_2` | idem |

L'adresse de base d'UART4 se déduit de `E7_FAITS.md` (UART0 à 7 au pas de 0x1000, **R**). L'IRQ est 128 (**V**).
Ces valeurs relèvent de l'agent 01 : elles ne sont pas dupliquées ici.
⚠ Les deux `hello` écrivent sur **la même UART4**. Pour un scénario bi-cœur, la sortie sera entrelacée, sur la
carte comme en simulation.

### `arm-none-eabi-readelf -h -l -A` et `arm-none-eabi-size -A`

Sorties complètes : `build/spike/hello_{hp,he}.readelf.txt` et `.size.txt`.

**En-tête ELF** (identique HP/HE, sauf le point d'entrée) : ELF32 little-endian, `Machine: ARM`, `Type: EXEC`,
`Flags: 0x5000400, Version5 EABI, hard-float ABI`.

| | Point d'entrée |
|---|---|
| HP | `0x802007c1` (Reset_Handler en MRAM, slot HP, bit Thumb à 1) |
| HE | `0x800007c1` (Reset_Handler en MRAM, slot HE) |

**Attributs CPU** (identiques) : `Tag_CPU_name: "cortex-m55"`, `Tag_CPU_arch: v8.1-M.mainline`, profil Microcontroller,
`Tag_FP_arch: FPv5/FP-D16 for ARMv8`, `Tag_ABI_VFP_args: VFP registers` (hard float), `Tag_DSP_extension: Allowed`,
`Tag_MVE_arch: MVE Integer and FP`. `FP-D16` désigne les 16 registres
double (D0-D15) du FPv5 : la double précision est bien disponible (`Dfpu=DP_FPU`).

**Segments LOAD, HP** (VirtAddr / PhysAddr = adresse de chargement) :

| Segment | VirtAddr | PhysAddr | FileSiz | MemSiz | Flags | Sections |
|---|---|---|---|---|---|---|
| 0 | 0x20000000 | 0x20000000 | 0x114 | 0xc000 | RW | `.bss .nonsecure0_region .__app_heap .heap` |
| 1 | 0x200fe000 | 0x200fe000 | 0 | 0x2000 | RW | `.stack` |
| 2 | 0x80200000 | 0x80200000 | 0x1220 | 0x1220 | RWE | `.startup.at_mram .copy.table .zero.table` |
| 3 | **0x00000000** | **0x80201220** | 0x8380 | 0x8380 | R E | `.code.at_itcm` |
| 4 | **0x20000000** | **0x802095a0** | 0xe00 | 0xe00 | RW | `.data.at_dtcm` |
| 5 | 0x8020a3a0 | 0x8020a3a0 | 0x120 | 0x120 | RWE | `.readonly.at_mram` |

**Segments LOAD, HE** : même structure. Seuls les chiffres changent : stack à `0x2003e000` (fin de la DTCM de 256 Ko),
startup en `0x80000000`, ITCM stocké en `0x80001220` (0x84f0), DATA stocké en `0x80009710` (0xe40), readonly en `0x8000a550`.

**Tailles de sections (`size -A`)**, en octets :

| Section | HP | HE |
|---|---|---|
| `.startup.at_mram` | 4 592 | 4 592 |
| `.code.at_itcm` (VMA 0x0) | 33 664 | 34 032 |
| `.data.at_dtcm` | 3 584 | 3 648 |
| `.bss` | 1 592 | 1 592 |
| `.nonsecure0_region` | 11 208 | 11 144 |
| `.__app_heap` / `.heap` | 16 384 / 16 384 | 16 384 / 16 384 |
| `.stack` | 8 192 | 8 192 |
| `.readonly.at_mram` | 288 | 288 |

**Conséquences pour les agents 03 (check) et 04 (sim)** :
- Le code est **stocké en MRAM mais lié (VMA) en ITCM à 0x0** (idem pour `.data` en DTCM à 0x20000000). Le *startup*,
  placé en MRAM, le recopie au démarrage via `.copy.table`. Un chargeur Renode qui charge par VirtAddr écraserait la
  mauvaise région. Il faut charger par **PhysAddr (LMA)**, puis laisser le startup s'exécuter. Pour `check`,
  l'occupation ITCM se lit sur la VMA, et celle de la MRAM sur la LMA.
- La **table des vecteurs est en MRAM** : `__Vectors` = `0x80200000` (HP), SP initial `0x20100000` (fin de DTCM HP),
  reset = `0x802007c1` (`Reset_Handler` à `0x802007c0`, `.copy.table` à `0x802011f0`, `.zero.table` à `0x80201210`).
  Le démarrage réel passe par le Secure Enclave, qui lit l'ATOC puis lance le cœur. Le chargeur simulé doit
  l'imiter : `mramAddress` 0x80200000 pour HP et 0x80000000 pour HE (cohérents avec E7_FAITS.md), VTOR sur cette adresse.
- Le segment 0 déclare `MemSiz 0xc000` pour `FileSiz 0x114` : c'est du `.bss` (non chargé, à zéro-initialiser).

## 5. `e7twin build` et `e7twin package` (cycle 2)

### `build.main(target, core=None, build_type="debug", out_dir="build") -> int`

Codes de sortie : **0** succès, **1** échec de compilation, **2** environnement absent ou usage incorrect.

```powershell
# Plusieurs projets dans le csolution : suffixe #projet. Cœur par défaut : m55_hp.
py -3.14 -c "from e7twin import build; build.main('tools/alif_vscode-template/alif.csolution.yml#hello', 'm55_hp', 'debug', 'build/sdk')"
```

| Cible (`target`) | Ce qui est lancé |
|---|---|
| `x.csolution.yml[#projet]`, dossier de csolution, ou dossier d'un `*.cproject.yml` (on remonte au csolution) | `cbuild <sol> --context <projet>.<build>+<target-type> -O <out>/<sol>/<projet>/<cœur> [--frozen-packs]` |
| dossier avec `CMakeLists.txt` | `cmake -G Ninja -S … -B <out>/<nom>/<cœur>/tmp -DCMAKE_TOOLCHAIN_FILE=cmake/e7-m55.toolchain.cmake` puis `cmake --build` |

- **Target-type lu dans le csolution** : le `target-types[].device` qui finit par `:M55_HP` / `:M55_HE` **et** contient
  `board.info['cmsis_device']` (`AE722F80F55D5LS`, donc jamais un target E8 ou E1C). Ambigu ou absent : code 2.
- `--frozen-packs` n'est passé que si `<sol>.cbuild-pack.yml` existe.
- **Un dossier de sortie par cœur** (piège 10) : `<out_dir>/<solution>/<projet>/<cœur>/{out,tmp}`.
- Environnement identique à `toolchain.env()`. Les outils sont lancés par chemin complet, parce que `CreateProcess`
  cherche l'exécutable dans le PATH du parent, pas dans l'`env` passé.
- Sortie : chemin de l'ELF, tableau `--print-memory-usage` du linker, `.map` copié à côté de l'ELF (`<nom>.map`).

**Mécanisme `-fstack-usage`** (actif dans les deux cas) :

- *CMake* : `cmake/e7-m55.toolchain.cmake` le met dans `CMAKE_C_FLAGS_INIT`, avec `-mcpu=cortex-m55 -mthumb -mfloat-abi=hard
  -mfpu=auto`, `-ffunction-sections -fdata-sections`, `--specs=nosys.specs`, `-Wl,--gc-sections`,
  `-Wl,-Map=<build>/firmware.map,--cref,--print-memory-usage`, et le suffixe `.elf`. Le MVE et le FPU DP sont implicites avec
  `-mcpu=cortex-m55` (vérifié : `Tag_MVE_arch: MVE Integer and FP`, `v8.1-M.mainline`, `VFP registers`). Le script d'édition de
  liens (placement mémoire) reste à la charge du projet.
- *cbuild* : **la variable d'environnement `CFLAGS=-fstack-usage`** (et `CXXFLAGS`). CMake l'utilise pour initialiser
  `CMAKE_C_FLAGS` à la première configuration, donc le template n'est pas touché et aucun fichier de couche n'est nécessaire.
  Vérifié : 36 `.su` pour `hello`. Le flag ne change pas le code généré.
- Les `.su` (écrits à côté des `.o`, dans `tmp/`) sont copiés dans **`<dossier de l'ELF>/su/`** avec un préfixe d'index
  (`0007_main.c.su`) pour éviter les collisions. Les `.su` de sondage `CompilerId` de CMake sont ignorés.
  `check` les trouve seul (il cherche récursivement à côté de l'ELF).

### `package.main(elf, core=None, out_dir="build/package", atoc_with=None) -> int`

Codes : **0** succès, **1** verdict FAIL de `check` (rien n'est conservé), **2** erreur (ELF illisible, cœur invalide, objcopy absent…).

```powershell
py -3.14 -c "from e7twin import package; package.main('hp/hello.elf', None, 'build/package', ['he/hello.elf'])"
```

1. **`.bin`** : `arm-none-eabi-objcopy -O binary -j <sections…>`. Sont gardées les sections dont la **LMA** est dans le slot MRAM du
   cœur (`board.slot(core,'mram')`), soit `.startup`, `.copy.table`, `.zero.table`, **`.code.at_itcm` et `.data.at_dtcm`** (liés en
   TCM mais stockés en MRAM) et `.readonly`. Les segments à LMA en RAM (`.bss`, pile, tas) sont exclus. Le `.bin` couvre de la LMA minimale
   à la LMA maximale (42 176 o pour HP, 42 608 o pour HE), ce qui correspond exactement à ce que `check` mesure. Le contenu a été comparé
   octet par octet à une reconstruction Python des segments (identique), et la taille est vérifiée à chaque fois.
   Le cœur est déduit du slot si `core=None`.
2. **JSON ATOC** `M55_HP_mram_cfg.json` ou `M55_HP_HE_mram_cfg.json` : `DEVICE` (recopié du template) + `HP_APP` / `HE_APP`
   (`mramAddress` = début du slot lu dans `board.load()`, `cpu_id`, `flags:["boot"]`, `signed:false`, `binary`). Les stubs A32/HE du
   `M55_HP_HE_mram_cfg.json` du template sont volontairement omis, car seuls les M55 sont utilisés (comme `M55_HP_cfg.json`, le flux
   « Program with Security Toolkit » du template).
3. **`check.main`** sur chaque image avec ce JSON : un FAIL supprime `.bin` et JSON, et le code est 1.
4. **Affichage** (jamais d'exécution) : `cp` des `.bin` vers `build/images/` et du JSON vers `build/config/` de SETOOLS, puis
   `./app-gen-toc -f build/config/<json>` et `./app-write-mram -p`, comme les tâches du template (`.vscode/tasks.json`).
   Une étape 0 rappelle de sélectionner explicitement l'E7 dans SETOOLS (défaut E8 depuis la 1.107).
   **Non vérifié** : le nom exact de l'outil de sélection (`tools-config`), à confirmer dans le SWRM Alif (ASRM0002). Le module n'appelle que `objcopy`.
   Un test remplace `subprocess.run/Popen`, `os.system` et `os.exec*` par un garde qui échoue sur toute commande SETOOLS.

### Résultat sur `hello` (HP + HE, GCC 14.3.1)

```
M55_HP_hello.bin  42176 o @ 0x80200000 (slot hp_app_mram)      M55_HE_hello.bin  42608 o @ 0x80000000 (slot he_app_mram)
check m55_hp : arch PASS, placement PASS, usage PASS, stack WARN, boot PASS, atoc PASS
check m55_he : idem (atoc PASS : HE_APP adresse = début de l'image, dans son slot, sans chevauchement avec HP_APP)
```

La règle `stack` est **évaluée** : pile pire cas 392 o sur 8 192 o. Elle est en WARN parce que c'est une borne inférieure :
des handlers d'IRQ appellent par `blx rN` (UART/GPIO/MHU), ce que le graphe d'appels ne peut pas suivre.

## 6. Tests

```powershell
py -3.14 -m pytest tests/test_toolchain.py tests/test_build.py tests/test_package.py
```

- `test_toolchain.py` et les tests non marqués de `test_build.py` / `test_package.py` n'ont besoin d'aucun outil (faux outils).
- Les tests `@pytest.mark.toolchain` utilisent les vrais outils et sont sautés s'ils manquent : build CMake réel, build du `hello` du SDK,
  package HP+HE avec `check` (règle `atoc` PASS, règle `stack` non SKIP). Ils utilisent un **dossier temporaire court** (voir piège 16).
- Ces trois fichiers relèvent de l'agent 07 (signalé).

## 7. Pièges rencontrés

1. **Licences des packs** : `cpackget add` demande `[A]ccept [D]ecline`. En non-interactif, la réponse vaut
   *Decline*, pourtant cpackget **sort avec le code 0**. Le script vérifie donc la présence de `<pack root>/<vendor>/<pack>/<version>`.
   Le nom du fichier de licence extrait (`-x`) varie : `.LICENSE` pour ARM, `.License.txt` pour Alif. Le script le lit dans la sortie de cpackget.
2. **Renode 1.17.0 n'exige pas .NET 10** : le zip portable Windows embarque .NET 8.0.10. La mention « .NET 10 »
   de `docs/PLAN.md` et `docs/E7_FAITS.md` est à corriger (fichiers de l'agent 00).
3. **`platforms/cpus/alif_ae822fa0e5597bs0.repl` est absent de Renode 1.17.0** (ni dans le zip, ni au tag `v1.17.0`).
   Il n'existe que sur la branche `master` de renode/renode. L'agent 04 devra le récupérer à un commit épinglé,
   au lieu de s'appuyer sur `tools/renode-1.17.0/platforms`.
4. **Fidélité du compilateur** : le template demande `arm-none-eabi-gcc ^13.2.1-0` dans `vcpkg-configuration.json`,
   via Arm Environment Manager, soit une **13.x** dans le flux VS Code standard. La fiche agent 02 impose 14.x, et nous utilisons 14.3.1.
   Pour garantir « même compilateur que pour la carte », l'équipe doit **compiler aussi les binaires flashés avec 14.3.1**
   (via `e7twin build`), ou bien on s'aligne sur 13.3.rel1. C'est une décision pour l'agent 00.
5. **PowerShell 5.1** : `$PSScriptRoot` est vide dans les valeurs par défaut de `param()`, il faut le calculer dans le corps.
   De plus, les guillemets doubles des arguments passés à un exécutable natif sont supprimés (`python -c "…"` casse) : d'où
   `python -m e7twin.toolchain --env-json` dans `build_spike.ps1`.
6. **Deux Python sur le poste** : `python` dans le PATH est celui de msys64 ucrt64 (3.14.5, **sans pytest**).
   `py` est le Python 3.14.6 de python.org, avec pytest 9.2. Il faut utiliser `py -m pytest`.
7. **CMake 4.3** (msys64) n'est pas la version testée par Arm (le template demande `^3.28.4`). Risque de compatibilité
   avec `cbuild2cmake` à vérifier au premier vrai build. En cas d'échec, épingler CMake 3.31 dans `tools/`.
8. `tar.exe` de Windows (bsdtar) est utilisé pour extraire les zip : `Expand-Archive` est beaucoup plus lent sur les
   ~290 Mo de la toolchain Arm.
9. **« DTCM 100 % » est un artefact du script de lien, pas un dépassement** : `linker_gnu_tcm.ld.src` (pack Alif) place
   `.stack` en **fin** de DTCM et l'étend jusqu'à la limite de la région (HP : 0x200fe000 + 0x2000 = 1 Mo ; HE :
   0x2003e000 + 0x2000 = 256 Ko). L'agent 03 ne doit pas interpréter ce 100 % comme un échec : il doit séparer
   `.stack` / `.heap` du reste. Le `.stack` ne pèse que 8 Ko, et les heaps 2 × 16 Ko.
10. **Les deux contextes ne peuvent pas partager le même `-O`** : `tmp/1` (cache CMake) est lié au premier contexte, et le
    second échoue avec « The source … does not match the source … used to generate cache ». `build_spike.ps1`
    utilise donc `build/alif_template/DevKit-E7-HP` et `…-HE`. cbuild affiche aussi
    `failed to delete: './C:/Dev/E7 Twin/…/tmp'` quand `-O` est absolu (avertissement bénin).
11. **Le `.map` est `tmp/1/linker.map`** (`-Wl,-Map=linker.map` de `cdefault.yml`, relatif au dossier CMake) : aucun `hello` dans son chemin.
12. **CMake 4.3.3 fonctionne avec cbuild 2.12.0** (piège 7 levé) : deux builds propres, sans erreur ni avertissement de politique.
13. **GCC 14.3.1 remplace le 13.x du template sans problème** : `cbuild list toolchains` donne `GCC@14.3.1` grâce à
    `GCC_TOOLCHAIN_14_3_1`, les attributs ELF sont corrects, et il n'y a aucun avertissement bloquant. La décision de l'humain est de garder 14.3.1.
14. **cbuild réécrit `hello/RTE/_debug_DevKit-E7-*/RTE_Components.h` du template** (LF → CRLF avec autocrlf, `git status` le
    montre modifié). Le contenu est identique (`git diff --ignore-space-at-eol` vide). Le clone n'est donc **pas** modifié en
    substance, et le script ne signale que les vraies différences.
15. **Sortie de `readelf` en UTF-8 avec BOM** : `Out-File -Encoding utf8` de PowerShell 5.1 ajoute un BOM. Les parseurs doivent lire en `utf-8-sig`.
16. **MAX_PATH (260) casse `-fstack-usage`** : sous Windows, gcc échoue avec `fatal error: cannot open …/x.c.su for writing: No such file or directory`
    quand le chemin absolu du `.su` dépasse 260 caractères (les `.o` passent, le `.su` non). Cela arrive avec le répertoire temporaire de pytest.
    `build` affiche un avertissement au-delà de 110 caractères pour le dossier de sortie. Il faut utiliser un `out_dir` court.
17. **`CMAKE_EXECUTABLE_SUFFIX` est ignoré avec `CMAKE_SYSTEM_NAME Generic`** : il faut `CMAKE_EXECUTABLE_SUFFIX_C/CXX/ASM` pour obtenir `fw.elf`.
18. **Le PATH exporté dans Git Bash masque `/usr/bin`** si on le remplace au lieu de le préfixer (plus de `rm`, `find`…) : toujours préfixer.
19. **Tests Renode instables sous charge** : `connexion au monitor perdue [WinError 10054]` apparaît de façon aléatoire dans `tests/test_sim.py`
    et `tests/test_examples.py` (agents 04/07). Ce n'est pas lié à build/package.
