# Portage macOS (Intel et Apple Silicon) : audit statique

Audit du 2026-11-22, **sans exécution sur Mac** (aucun Mac disponible) et sans modification de code : tout ce qui suit vient de recherches `Grep` et de lectures ciblées. Les numéros de ligne sont ceux du dépôt à cette date.

## 1. Disponibilité des outils sur macOS

Aucune de ces informations n'est vérifiée par la documentation locale : `docs/BUILD.md` et `install_tools.ps1` ne parlent que de Windows. Elles viennent de mes connaissances et sont à confirmer avant de coder l'installeur.

| Outil | darwin-arm64 | darwin-x86_64 | Statut | Réserve |
|---|---|---|---|---|
| Arm GNU Toolchain 14.3.rel1 | oui, archive `arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi.tar.xz` | oui, `...-darwin-x86_64-arm-none-eabi.tar.xz` | **connaissance, probable** | Noms exacts et SHA-256 à relever sur developer.arm.com. |
| CMSIS-Toolbox 2.12.0 | oui, `cmsis-toolbox-darwin-arm64.tar.gz` | oui, `cmsis-toolbox-darwin-amd64.tar.gz` | **connaissance, probable** | Noms d'assets et checksums à relire dans `cmsis-toolbox-checksums.txt` de la release. |
| Renode 1.17.0 | **incertain** | **incertain** | **connaissance, à vérifier en premier** | Renode publie des paquets macOS (`.dmg`, formule Homebrew), mais je ne sais pas confirmer que la 1.17.0 existe en natif arm64 en plus de x86_64 ni comment est packagé le runtime .NET. Sans build arm64 natif, il faudrait Rosetta 2. C'est le point le plus risqué du portage. |
| Packs Alif / ARM (cpackget) | indépendants de l'OS | idem | connaissance | Les packs sont des archives de sources. |
| Python 3.12+, pyelftools, FastAPI, uvicorn, typer, pyyaml | oui | oui | connaissance | Dépendances pures Python ou avec roues macOS ; `pyproject.toml` n'a aucune dépendance propre à Windows. |

Sous macOS, les binaires téléchargés portent l'attribut de quarantaine (Gatekeeper). Il faudra faire `xattr -dr com.apple.quarantine tools/` après extraction, sinon `arm-none-eabi-gcc`, `cbuild` et `renode` sont bloqués au premier lancement. Ce point vient de mes connaissances.

## 2. Problèmes relevés

### Bloquants

| Problème | Fichier:ligne | Agent propriétaire | Correction proposée | Effort |
|---|---|---|---|---|
| **TRAITÉ (cycle 19)** — `scripts/install_tools.sh` écrit (URL/SHA darwin et linux marqués « à confirmer », jamais exécuté). L'installation des outils est écrite uniquement pour Windows (PowerShell). Les URL sont des zip Windows (`mingw-w64`, `windows-amd64`, `windows-portable`). Elle utilise `System32\tar.exe`, `renode.exe` et `$env:SystemRoot`. | `scripts/install_tools.ps1:43,50,57,59,77` | 02 | Écrire `scripts/install_tools.py` (ou `install_tools.sh`) multiplateforme : URL et SHA-256 choisis selon `sys.platform` et `platform.machine()`, extraction `tar`/`zipfile`, puis `xattr -dr com.apple.quarantine`. Garder le `.ps1` comme enveloppe sous Windows. | L |
| **TRAITÉ (cycle 19)** — `toolchain.renode_binary()`, `.exe` seulement sous Windows, repli `/Applications/Renode.app/Contents/MacOS/` (nom du binaire à confirmer sur un Mac), `E7TWIN_RENODE`. Reste à brancher dans `sim.py` (agent 04). Renode : l'emplacement attendu est `renode-*/renode` et un `.exe` est ajouté sous Windows seulement. Sur Mac, Renode est livré en `.dmg` / `Renode.app` (exécutable dans `Contents/MacOS/`), ou installé hors de `tools/`. | `e7twin/toolchain.py:60,98-99,113` | 02 (utilisé par 04) | Ajouter dans `_find_in_tools` les variantes macOS (`renode-*/Renode.app/Contents/MacOS/renode`, `renode-*/renode`) et lire `E7TWIN_RENODE` comme repli. Valider la disposition sur un vrai Mac. | M |
| La compilation des exemples (`build_all.ps1`) est pilotée par PowerShell. La fixture de tests la lance par `powershell`/`pwsh`, donc les tests qui dépendent des ELF d'exemples sont sautés sur Mac, ou échouent si `pwsh` est installé et que le script suppose Windows. | `tests/conftest.py:44-50,79-81`, `examples/build_all.ps1` | 07 (avec 02) | Porter `build_all.ps1` en Python (`e7twin.build.main` sur chaque exemple) et faire appeler ce module par la fixture. Cela retire aussi la dépendance à PowerShell côté tests. | M |
| Aucune vérification que le moniteur Renode et le lancement `--disable-gui --plain -P <port>` fonctionnent avec le paquet macOS (Mono ou .NET selon la build). | `e7twin/sim.py:280-286` | 04 | À tester sur un Mac. Prévoir un contournement via `E7TWIN_RENODE` et un message d'erreur clair si le binaire n'est pas exécutable (Gatekeeper). | M (non testable ici) |

### Gênants

| Problème | Fichier:ligne | Agent propriétaire | Correction proposée | Effort |
|---|---|---|---|---|
| **TRAITÉ (cycle 19)** — `install_hint()` / `license_hint()` selon l'OS. Les messages d'aide pointent vers `powershell ... install_tools.ps1`. | `e7twin/toolchain.py:41,44` | 02 | Choisir le message selon `_IS_WINDOWS` (commande `python scripts/install_tools.py` ailleurs). Les tests `test_cli.py:151-156` et `test_toolchain.py:96` vérifient la présence de `install_tools.ps1` dans le texte : à adapter avec le changement. | S |
| Pas de lanceur macOS. `E7Twin.cmd` appelle `py -3.14` (lanceur `py`, propre à Windows) et PowerShell pour ouvrir le navigateur. | `E7Twin.cmd:1-7`, `README.md:55-56` | 00 | Ajouter `E7Twin.command` (exécutable, `python3 -m e7twin.cli serve`, ouverture avec `open http://127.0.0.1:8000` quand le port répond) et documenter `python3` à la place de `py -3.14`. | S |
| La CI n'a ni job macOS ni test de l'installeur hors Windows. Le job `toolchain` est Windows seulement. | `.github/workflows/ci.yml:14` (matrice `os`), `:27-` | 07 | Ajouter `macos-13` (Intel) et `macos-latest` (arm64) à la matrice `tests`. Plus tard, un job `toolchain` macOS quand l'installeur existe. | S |
| Dossier des packs CMSIS par défaut : sous Mac, le code retombe sur `~/.cache/arm/packs`. Je crois que c'est aussi la valeur de cpackget sur Mac, mais je ne l'ai pas vérifié. En pratique `tools/packs` ou `CMSIS_PACK_ROOT` priment. | `e7twin/toolchain.py:139-154` | 02 | Confirmer le défaut de cpackget sur macOS ; sinon forcer `CMSIS_PACK_ROOT=tools/packs` dans `env()` (déjà le cas quand `tools/packs` est initialisé). | S |
| Les processus Renode sont tués par `kill()` (SIGKILL) et par `atexit`. Pas de groupe de processus : si l'on tue le serveur avec SIGKILL, ou si `uvicorn` sort sans `atexit`, un `renode` orphelin peut subsister. Même risque sous Windows, mais le commentaire de test parle de `renode.exe`. | `e7twin/sim.py:240,245,360-363` | 04 | `start_new_session=True` sur POSIX et arrêt du groupe (`os.killpg`) en repli. Pas de signal à traiter ailleurs. | S |
| Les scripts de build (`build_spike.ps1`) utilisent `.exe` et `\`. | `scripts/build_spike.ps1:23-24,37,62` | 02 | Remplacer par un appel de `python -m e7twin.build` (voir l'élément « build_all »), ou le déclarer Windows seulement. | S |
| Le code d'accès par chemin absolu `C:\` dans la documentation et les commentaires peut tromper : `docs/` et `sim.py:15` citent `C:\\Dev\\E7 Twin`. | `e7twin/sim.py:15` | 04 | Reformuler en « chemin avec espace ». Aucun effet fonctionnel. | S |

### Cosmétiques

| Problème | Fichier:ligne | Agent propriétaire | Correction proposée | Effort |
|---|---|---|---|---|
| Reconfiguration de l'encodage console pour cp1252 : inoffensive sous macOS (UTF-8 déjà). | `e7twin/board.py:681`, `e7twin/build.py:278`, `e7twin/cli.py` | 01 / 02 / 00 | Aucune ; garder. | — |
| Commentaires et noms (`%APPDATA%`, `renode.exe`) propres à Windows. | `e7twin/sim.py:277`, `tests/test_sim.py:312` | 04 / 07 | Reformuler. Sous Mac l'historique Renode est ailleurs, mais le correctif (`--config` + `history-path`) reste valable. | S |
| Test de chemin Windows (`C:/Windows/notepad.exe`) : toujours refusé sur Mac, donc aucun effet. | `tests/test_server.py:244` | 07 | Aucune. | — |
| `py -3.14` cité dans les notes et la doc ; sous Mac c'est `python3`. | `README.md`, `docs/BUILD.md`, `CLAUDE.md` | 00 | Ajouter la variante macOS dans la doc. | S |

### Points vérifiés sans problème

- `e7twin/server.py` et `e7twin/cli.py` n'ouvrent pas le navigateur eux-mêmes (c'est `E7Twin.cmd` qui le fait) et n'ont pas de code propre à Windows ; l'écoute se fait sur `127.0.0.1`.
- `e7twin/sim.py` : sockets TCP sur `127.0.0.1` (`:296,375,968`), pas de socket Unix ni de signal ; les chemins donnés à Renode passent par `as_posix()` et `_rel()` (`:536-543`), qui contournent déjà le problème des espaces.
- `e7twin/build.py` : seul code propre à Windows = avertissement MAX_PATH (`:110`, limité à `win`) ; la recherche des outils passe par `toolchain.find`.
- `pyproject.toml` : aucune dépendance propre à Windows.
- Le système de fichiers macOS est insensible à la casse par défaut, comme Windows : pas de nouveau risque sur les noms de fichiers.

## 3. Corrections à faire, par agent

1. **02 Toolchain & Build** : installeur multiplateforme (`install_tools.py`, versions, URL et SHA-256 darwin, `xattr`) ; disposition Renode macOS et variable `E7TWIN_RENODE` dans `toolchain.py` ; messages d'aide selon la plateforme ; défaut des packs CMSIS ; remplacer `build_spike.ps1` par du Python.
2. **07 Tests** : porter `build_all.ps1` en Python et adapter `tests/conftest.py` ; ajouter `macos-13` et `macos-latest` à la CI ; adapter les tests qui lisent `install_tools.ps1` dans les messages.
3. **04 Simulateur** : valider sur un Mac le lancement de Renode et le monitor ; `start_new_session` / `killpg` ; reformuler les commentaires propres à Windows.
4. **00 Coordination** : `E7Twin.command` ; README et `CLAUDE.md` avec `python3` ; décision sur le niveau de support (voir ci-dessous).

## 4. À décider ou vérifier avant de commencer

- Confirmer l'existence de Renode 1.17.0 en natif arm64 sur macOS (page de release). Si seul x86_64 existe, l'Apple Silicon passera par Rosetta 2, avec une performance de simulation à mesurer.
- Les trois points d'outils du § 1 sont des connaissances, pas des vérifications : relever les URL et empreintes avant d'écrire l'installeur.
- Le plus gros du travail est l'installeur (L) ; tout le reste est S ou M. Sans Mac, l'ensemble ne pourra être validé que par la CI macOS et par un collègue disposant d'un Mac.

## 5. Traité (cycle 19)

- `examples/build_all.py` (multiplateforme, remplace l'appel PowerShell dans `tests/conftest.py`) et job CI `macos-latest` (tests sans Renode ni toolchain, sautés proprement) ; non exécuté sur GitHub ni sur un Mac.

## 5. État après le cycle 19 (agent 02)

- Traités : installeur POSIX (écrit, **non exécuté**), localisation de Renode multiplateforme (`toolchain.renode_binary(tdir=None) -> Path | None`), messages d'aide par OS, tests avec `sys.platform` simulé.
- Restent ouverts : branchement de `renode_binary()` dans `sim.py` (04), validation du lancement de Renode sur un Mac (04), `build_all.ps1` / `conftest.py` (07), CI macOS (07), `E7Twin.command` (00), URL/SHA-256 darwin et linux de `install_tools.sh` à confirmer.
