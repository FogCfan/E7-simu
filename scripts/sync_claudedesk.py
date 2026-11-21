"""Synchronise les agents d'E7 Twin vers Claude Code et ClaudeDesk.

Source unique : docs/agents/*.md (frontmatter name/description + corps = rôle).

- Copie chaque fiche dans .claude/agents/ (sous-agents Claude Code).
- Crée / met à jour le projet cowork ClaudeDesk « E7 Twin » :
    <workspace>/E7 Twin/_dossier.json             (projet, cwd = racine du dépôt)
    <workspace>/E7 Twin/<NN Agent>/_dossier.json  (thème = agent, notes = rôle)
    <workspace>/E7 Twin/<NN Agent>/Mission [xxxxxxxx].conv.json
  Les notes des thèmes sont réécrites à chaque synchro ; les conversations
  existantes ne sont jamais touchées (la « Mission » n'est créée que si le
  thème n'a encore aucune conversation).

Usage : python scripts/sync_claudedesk.py [--workspace <racine ClaudeDesk>]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO / "docs" / "agents"
PROJECT_NAME = "E7 Twin"

PROJECT_NOTES = """E7 Twin : simulateur + monitoring de la carte Alif Ensemble E7 AppKit (AK-E7-AIML, SoC AE722F80F55D5 : 2x Cortex-A32, M55-HP 400 MHz, M55-HE 160 MHz, 2x Ethos-U55). L'équipe n'a qu'une seule carte : l'outil doit permettre de compiler pour la vraie cible, vérifier l'exploitabilité (placement mémoire, pile, ATOC), exécuter les vrais binaires en émulation (Renode) et monitorer cœurs / RAM dans un dashboard web.

Plan : docs/PLAN.md — faits matériels sourcés : docs/E7_FAITS.md — règles : CLAUDE.md.
Chaque thème de ce projet est un AGENT (rôle dans ses notes, fiche complète dans docs/agents/). Rester dans son périmètre ; signaler toute modification des fichiers d'un autre agent.
Ordre de démarrage : 00 Coordination + 02 Toolchain → 04 Simulateur (spike Renode, go/no-go) + 01 Modèle de carte → 03 Compatibilité, 05 Métriques (serveur factice) → 06 Dashboard, 07 Tests → 08 NPU.

Règles absolues : aucun commit/push sans demande explicite, jamais de Co-Authored-By (l'utilisateur est seul auteur) ; jamais de flash automatique de la carte."""

DEFAULT_CONFIG = {
    "addDirs": [], "allowedTools": [], "chrome": False, "disallowedTools": [],
    "effort": "", "enabledTools": [], "extraArgs": "", "fallbackModel": "",
    "maxBudgetUsd": 0.0, "model": "", "outputStyle": "", "permissionMode": "default",
    "systemPrompt": "", "systemPromptMode": 0,
}
EMPTY_STATS = {
    "apiSeconds": 0.0, "cacheRead": 0, "cacheWrite": 0, "contextTokens": 0,
    "contextWindow": 0, "costUsd": 0.0, "inputTokens": 0, "lastModel": "",
    "outputTokens": 0, "turnHistory": [], "turns": 0,
}


def parse_agent(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise SystemExit(f"{path.name} : frontmatter manquant")
    front, body = m.group(1), m.group(2).strip()
    meta = dict(re.findall(r"^(\w+):\s*(.+)$", front, re.M))
    title = re.search(r"^# Agent (\d+) — (.+)$", body, re.M)
    if not title:
        raise SystemExit(f"{path.name} : titre « # Agent NN — Nom » manquant")
    mission = re.search(r"^## Première mission[^\n]*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return {
        "file": path,
        "name": meta["name"],
        "description": meta["description"],
        "theme": f"{title.group(1)} {title.group(2).strip()}",
        "body": body,
        "mission": mission.group(1).strip() if mission else "",
    }


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def update_dossier(folder: Path, kind: str, cwd: str, notes: str, icon: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "_dossier.json"
    data = {"cwd": "", "icon": "", "kind": kind, "notes": "", "recap": "", "recapTime": 0}
    if f.exists():
        data.update(json.loads(f.read_text(encoding="utf-8")))
    data.update({"kind": kind, "cwd": cwd, "notes": notes, "icon": icon})
    write_json(f, data)


def create_mission(theme_dir: Path, agent: dict) -> bool:
    if any(theme_dir.glob("*.conv.json")):
        return False
    now = int(time.time())
    cid = str(uuid.uuid4())
    intro = (
        f"Conversation de départ de l'agent « {agent['theme']} ». Ton rôle complet est dans les notes "
        f"de ce dossier (et dans {agent['file'].relative_to(REPO).as_posix()}).\n\n"
        f"Première mission :\n{agent['mission']}\n\n"
        "Pour lancer l'agent, écrivez par exemple : « Lis CLAUDE.md et docs/PLAN.md, puis attaque ta première mission. »"
    )
    conv = {
        "claudeSessionId": "",
        "config": dict(DEFAULT_CONFIG),
        "created": now,
        "cwd": str(REPO),
        "entries": [{"kind": "system", "text": intro, "time": now}],
        "id": cid,
        "injectContext": True,
        "pinned": True,
        "recap": "",
        "recapTime": 0,
        "stats": dict(EMPTY_STATS),
        "status": "active",
        "title": "Mission",
        "updated": now,
        "version": 1,
    }
    write_json(theme_dir / f"Mission [{cid[:8]}].conv.json", conv)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, default=Path.home() / "Documents" / "ClaudeDesk")
    args = ap.parse_args()

    agents = [parse_agent(p) for p in sorted(AGENTS_DIR.glob("*.md"))]

    # 1. Sous-agents Claude Code
    cc_dir = REPO / ".claude" / "agents"
    cc_dir.mkdir(parents=True, exist_ok=True)
    for a in agents:
        shutil.copyfile(a["file"], cc_dir / a["file"].name)

    # 2. Projet cowork ClaudeDesk
    project = args.workspace / PROJECT_NAME
    update_dossier(project, "project", str(REPO), PROJECT_NOTES, "code")
    for a in agents:
        theme_dir = project / a["theme"]
        notes = a["body"] + "\n\n(Fiche synchronisée depuis le dépôt : modifier docs/agents/ puis relancer scripts/sync_claudedesk.py.)"
        update_dossier(theme_dir, "folder", "", notes, "agent")
        created = create_mission(theme_dir, a)
        print(f"{a['theme']:<28} -> {'mission créée' if created else 'notes mises à jour'}")
    print(f"\nProjet ClaudeDesk : {project}\nSous-agents Claude Code : {cc_dir}")


if __name__ == "__main__":
    main()
