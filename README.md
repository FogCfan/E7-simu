# E7 Twin

Jumeau numérique de la carte **Alif Ensemble E7 AI/ML AppKit** (AK-E7-AIML, SoC AE722F80F55D5) : simulateur + monitoring pour développer sans monopoliser l'unique carte de l'équipe.

## Ce que fait l'outil

| Commande | Rôle |
|---|---|
| `e7twin build` | Compile pour la vraie cible (CMSIS-Toolbox + pack Alif Ensemble, GCC, flags Cortex-M55) |
| `e7twin check` | Barrière de compatibilité : architecture ELF, placement mémoire par cœur, taux d'occupation, pile pire cas, cohérence ATOC → rapport pass/warn/fail |
| `e7twin run` | Exécute les vrais binaires `.elf` sur une E7 émulée (Renode : M55-HP + M55-HE, mémoire Alif, UART, MHU) |
| `e7twin serve` | Dashboard web local (http://localhost:8000) : charge par cœur, RAM par région, consoles UART, timeline MHU, NPU |
| `e7twin package` | Produit `.bin` + JSON ATOC prêts pour SETOOLS (le flash reste manuel) |
| `e7twin npu` | Estimation Vela sur les deux Ethos-U55 (cycles, SRAM, opérateurs hors NPU) |
| `e7twin doctor` | Diagnostic des outils installés (toolchain, CMSIS-Toolbox, pack Alif, Renode) |

## Installation (poste de dev)

```
py -3.14 -m pip install -e ".[dev]"
py -3.14 -m pytest
e7twin --help
```

Sous Windows, `python` pointe vers le Python de MSYS2 (`C:\msys64\ucrt64\bin`), qui n'a **pas pip** :
utiliser le lanceur `py -3.14` pour tout (`py -3.14 -m pip …`, `py -3.14 -m pytest`).
`pip` installe `e7twin.exe` dans le dossier `Scripts` du Python officiel
(`%LOCALAPPDATA%\Python\pythoncore-3.14-64\Scripts`), qui n'est pas dans le PATH par défaut :
l'ajouter au PATH pour taper `e7twin`, ou lancer `py -3.14 -m e7twin.cli <commande>`.

## Parcours complet

```
e7twin doctor                                   # 1. outils trouvés ? (gcc, cbuild, packs, Renode)
e7twin build <projet.csolution.yml> --core m55_hp   # 2. compile pour la vraie cible
e7twin check build/<projet>/hello_hp.elf --core m55_hp   # 3. barrière : PASS/WARN/FAIL (code 1 si FAIL)
e7twin run --hp hp.elf --he he.elf --headless --expect "PONG 100"   # 4. exécute les vrais ELF dans Renode
e7twin serve --hp hp.elf --he he.elf            # 5. dashboard http://127.0.0.1:8000 (--fake : sans Renode)
e7twin package hp.elf --core m55_hp [--with he.elf]   # 6. .bin + JSON ATOC + commande SETOOLS affichée
e7twin npu model.tflite                         # (en plus) estimation Vela U55-256 / U55-128
```

- Les codes de sortie sont exploitables en CI : 0 succès, 1 échec métier (check FAIL, texte attendu absent),
  2 erreur d'environnement ou fonction pas encore livrée.
- `check --su-dir <dossier>` donne les `.su` (`-fstack-usage`) pour la règle de pile ; sans eux, la règle est
  `SKIP` (et le dit).
- `package` ne flashe **jamais** : il affiche les commandes SETOOLS (`app-gen-toc`, `app-write-mram`) que
  l'humain lance sur le PC relié à la carte. Le package refuse (et supprime ses fichiers) si `check` rend FAIL.
- `serve` sans `--fake` démarre sans image chargée ; la page (ou l'API `/api/sim/load`) charge un ELF et pilote la simulation Renode (`SimSource`). `--fake` sert de démonstration sans Renode.

## Interface graphique (sans terminal)

Pour les collègues qui n'utilisent pas de terminal :

1. Installation unique : `py -3.14 -m pip install -e ".[dev]"`, puis les outils via `scripts/install_tools.ps1` (voir [`docs/BUILD.md`](docs/BUILD.md)).
2. Double-clic sur `E7Twin.cmd` à la racine : il lance `py -3.14 -m e7twin.cli serve` et ouvre http://127.0.0.1:8000 dès que le port écoute.
3. La page a quatre onglets :
   - **Exemples** : ELF de `build/examples`, dont `load_loop_hp` et `load_loop_he`, qui animent les jauges de charge des cœurs (voir [`examples/README.md`](examples/README.md)) ;
   - **Importer** : envoi d'un ELF, ou d'un `main.c` compilé côté serveur pour HP ou HE ;
   - **Vérification** : lance `e7twin check` et donne un lien vers le rapport HTML ;
   - **Simulation** : chargement dans Renode, démarrage, pause, reset, métriques en direct.
4. Une bascule FR/EN est disponible dans la page.

Tout tourne en émulation Renode : rien n'est vérifié sur la carte réelle.

Contrats entre modules : [`docs/INTERFACES.md`](docs/INTERFACES.md) · feuille de route : [`docs/ROADMAP.md`](docs/ROADMAP.md).

## macOS (Apple Silicon : vérifié)

Vérifié sur un Mac Apple Silicon (2026-11-25 et 2026-11-27) : installation des outils, compilation de tous les exemples, Renode en natif arm64 et suite de tests. Mac Intel : non vérifié, et ni Arm (GCC 14.3) ni Renode (1.17.0) n'y publient d'archive (voir [`docs/MACOS.md`](docs/MACOS.md)).

```bash
python3 -m venv .venv && source .venv/bin/activate   # le python3 de Homebrew refuse « pip install » hors venv (PEP 668)
pip install -e ".[dev]" ninja             # ninja : requis par cbuild, absent de l'installeur
bash scripts/install_tools.sh             # GCC 14.3, CMSIS-Toolbox 2.12.0, Renode 1.17.0 (.dmg), template ; s'arrête sur les licences des packs
bash scripts/install_tools.sh --accept-pack-licenses   # après lecture des licences (tools/packs/.Download/)
e7twin doctor
```

- CMake ≥ 3.27 dans le `PATH` (par exemple `brew install cmake`).
- Lanceur : `E7Twin.command` à la racine (pendant de `E7Twin.cmd`), qui prend `.venv/bin/python` s'il existe. Une fois : `chmod +x E7Twin.command`.
- Détails et points encore ouverts : [`docs/MACOS.md`](docs/MACOS.md).

## La carte modélisée

- 2× Cortex-A32 @ 800 MHz (représentés, non exécutés en v1)
- Cortex-M55 **HP** @ 400 MHz — ITCM 256 Ko, DTCM 1 Mo
- Cortex-M55 **HE** @ 160 MHz — ITCM 256 Ko, DTCM 256 Ko
- 2× Ethos-U55 (256 et 128 MAC/cycle) — estimés via Vela
- SRAM0 4 Mo, SRAM1 2,5 Mo, MRAM 5,5 Mo, OSPI XIP

Détails et sources : [`docs/E7_FAITS.md`](docs/E7_FAITS.md).

## Limites (à lire)

Un simulateur ne remplace pas la carte. E7 Twin garantit que le binaire est **compilé pour la bonne cible**, **tient en mémoire**, **démarre** et que sa **logique fonctionne** en émulation. Le timing n'est pas au cycle près et certains périphériques (caméra, MIPI, display, horloges réelles) ne sont pas modélisés : la validation finale se fait sur la carte.

## Prérequis

- Python 3.12+
- Arm GNU Toolchain 14.x (`arm-none-eabi-gcc`)
- CMSIS-Toolbox 2.12 + pack `AlifSemiconductor::Ensemble@2.2.1`
- Renode 1.17.0 (Windows portable ; .NET 8.0.10 embarqué, rien à installer)
- SETOOLS (compte Alif) uniquement sur le poste qui flashe la carte

## Organisation

Plan complet : [`docs/PLAN.md`](docs/PLAN.md). Le travail est découpé en agents (un par domaine), décrits dans [`docs/agents/`](docs/agents/).
