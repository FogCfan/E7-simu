# E7 Twin — consignes pour Claude

Simulateur + monitoring de la carte Alif Ensemble E7 AppKit. Plan de référence : `docs/PLAN.md`. Faits matériels (adresses, tailles, sources) : `docs/E7_FAITS.md` — ne jamais inventer une adresse ou une taille : la sourcer (DFP Alif, HRM, SVD) et l'ajouter à ce fichier.

## Règles

- **Aucun commit / push / PR sans demande explicite.** Quand c'est demandé : l'utilisateur est seul auteur, **jamais de ligne `Co-Authored-By`** ni de mention « Generated with Claude Code ».
- **Une seule carte réelle** : l'outil ne flashe jamais la carte automatiquement. `e7twin package` donne la commande SETOOLS, l'humain la lance.
- Source de vérité de la carte : `board/ensemble_e7.yaml`. Le `.repl` Renode, les limites du checker et la vue du dashboard en sont **générés** — ne pas dupliquer de constantes ailleurs.
- Chaque agent (voir `docs/agents/`) possède ses fichiers ; modifier ceux d'un autre agent seulement si nécessaire, et le signaler dans la réponse.
- Être honnête sur la fidélité : distinguer ce qui est vérifié en émulation de ce qui ne l'est pas.

## Stack

Python 3.12+, paquet `e7twin` (CLI typer), pyelftools, FastAPI + WebSocket, dashboard HTML/JS vanilla. Simulation : Renode 1.17 piloté en headless (monitor telnet). Firmware : CMSIS-Toolbox (`cbuild`) + pack Alif, Arm GNU Toolchain.

## Commandes

```
pip install -e .[dev]
pytest
e7twin check <projet|elf>
e7twin run <elf...> --headless
e7twin serve
```
