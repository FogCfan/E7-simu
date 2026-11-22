# Guide d'installation E7 Twin

Statut de vérification : **Windows 11 = utilisé au quotidien. macOS et Linux = jamais testés** (voir `docs/MACOS.md`).
Python **3.12 ou plus** obligatoire (pas 3.9, pas 3.10).

## Ce qu'il faut selon l'usage

| Usage | Outils nécessaires |
|---|---|
| Dashboard avec données factices | Python 3.12+ seulement (`serve --fake`) |
| Compiler les exemples / `check` | + GCC Arm 14.3, CMSIS-Toolbox, packs Alif/CMSIS |
| Simuler (Renode) | + Renode 1.17.0 |

## Windows 11

```powershell
py -3.14 -m pip install -e ".[dev]"          # ou py -3.12
powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1 -AcceptPackLicenses
py -3.14 examples\build_all.py
E7Twin.cmd                                   # double-clic possible
```

Sans `-AcceptPackLicenses`, le script extrait les licences des packs puis s'arrête : lisez-les, puis relancez avec l'option.
`e7twin.exe` n'est pas dans le PATH : utiliser `py -3.14 -m e7twin.cli ...`.

## macOS (Apple Silicon recommandé)

```bash
brew install python@3.12                      # si python3.12 absent
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"             # guillemets obligatoires sous zsh
bash scripts/install_tools.sh --accept-pack-licenses
python examples/build_all.py
chmod +x E7Twin.command && ./E7Twin.command
```

**Renode** (pas de cask Homebrew) : release 1.17.0, `.dmg` arm64 uniquement (pas de paquet Intel).
```bash
curl -L -o renode.dmg https://github.com/renode/renode/releases/download/v1.17.0/renode-1.17.0.osx-arm64-portable.dmg
hdiutil attach renode.dmg && ls /Volumes/*enode*     # copier le contenu où vous voulez
xattr -dr com.apple.quarantine <dossier_copié>
export E7TWIN_RENODE=<chemin du binaire renode>
```
Mac Intel : pas de Renode 1.17.0 natif ; compilation et `check` restent possibles, pas la simulation.
Non vérifié : URL/SHA macOS de `install_tools.sh`, branchement de `E7TWIN_RENODE` dans `sim.py`.

## Linux (x86_64)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
bash scripts/install_tools.sh --accept-pack-licenses
python examples/build_all.py
python -m e7twin.cli serve
```
Renode : `renode-1.17.0.linux-portable.tar.gz` (ou `.deb`) depuis https://github.com/renode/renode/releases/tag/v1.17.0, puis `E7TWIN_RENODE=<binaire>` si hors PATH. Linux arm64 : archive `linux-arm64-portable` existe, non géré par le script.

## Dépannage

| Message | Cause / remède |
|---|---|
| `zsh: no matches found: .[dev]` | Mettre `".[dev]"` entre guillemets |
| Python 3.9 demandé / erreur de version | `python3` pointe vers une vieille version (conda `(base)` ?) : utiliser `python3.12 -m venv` |
| `externally-managed-environment` | Utiliser un venv (ci-dessus) |
| « outil manquant » packs Alif/CMSIS | `install_tools` arrêté avant les packs : relancer avec `--accept-pack-licenses` |
| « outil manquant » Renode | Installer Renode 1.17.0 et/ou `E7TWIN_RENODE` ; inutile pour compiler |
| Outils ailleurs que `tools/` | `E7TWIN_TOOLS_DIR=<dossier>` |

L'outil ne flashe jamais la carte : `e7twin package` affiche la commande SETOOLS, à lancer à la main.
