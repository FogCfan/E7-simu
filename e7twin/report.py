"""Rendu des rapports de compatibilité : JSON et HTML autonome (agent 03).

Aucune dépendance : le HTML est un fichier unique (CSS en ligne, pas de JavaScript ni de ressource
externe). Les fonctions acceptent un ``Report`` (objet exposant ``to_dict()``) ou son dict.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

__all__ = ["to_html", "to_json", "write"]

_COLORS = {"PASS": "#1a7f37", "WARN": "#9a6700", "FAIL": "#cf222e", "SKIP": "#6e7781"}

_CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem;color:#1f2328}
h1{font-size:1.4rem;margin-bottom:.2rem} h2{font-size:1.1rem;margin-top:2rem}
.meta{color:#57606a;margin:0 0 1rem}
.badge{display:inline-block;padding:.05rem .55rem;border-radius:1rem;color:#fff;font-weight:600;font-size:.85rem}
table{border-collapse:collapse;width:100%} th,td{border-bottom:1px solid #d0d7de;padding:.4rem .5rem;text-align:left;vertical-align:top}
th{background:#f6f8fa} td.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{background:#eaeef2;border-radius:3px;height:.7rem;min-width:8rem;overflow:hidden}
.bar>span{display:block;height:100%}
code{font-family:ui-monospace,monospace;font-size:.9em}
"""


def _d(report: Any) -> dict:
    return report if isinstance(report, dict) else report.to_dict()


def to_json(report: Any) -> str:
    return json.dumps(_d(report), indent=2, ensure_ascii=False) + "\n"


def _badge(verdict: str) -> str:
    color = _COLORS.get(verdict, "#6e7781")
    return f'<span class="badge" style="background:{color}">{escape(verdict)}</span>'


def _cell(v: Any) -> str:
    return "" if v is None else escape(str(v))


def to_html(report: Any) -> str:
    d = _d(report)
    rows = "\n".join(
        f"<tr><td>{_badge(r['verdict'])}</td><td><strong>{escape(r['title'])}</strong>"
        f"<br><code>{escape(r['id'])}</code></td><td>{escape(r['message'])}</td>"
        f"<td>{_cell(r.get('measured'))}</td><td>{_cell(r.get('limit'))}</td></tr>"
        for r in d["rules"]
    )
    usage_rows = []
    for name, u in d.get("regions_usage", {}).items():
        if not u["used_bytes"]:
            continue
        pct = u["pct"]
        color = _COLORS["FAIL"] if pct > 100 else _COLORS["WARN"] if pct > 85 else _COLORS["PASS"]
        usage_rows.append(
            f"<tr><td><code>{escape(name)}</code></td><td class=\"num\">{u['used_bytes']}</td>"
            f"<td class=\"num\">{u.get('reserved_bytes', 0)}</td>"
            f"<td class=\"num\">{u['size_bytes']}</td><td class=\"num\">{pct:.1f} %</td>"
            f"<td><div class=\"bar\"><span style=\"width:{min(pct, 100):.1f}%;background:{color}\"></span></div></td></tr>"
        )
    usage = "\n".join(usage_rows) or '<tr><td colspan="6">aucune région occupée</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>E7 Twin — compatibilité {escape(Path(d['image']).name)}</title>
<style>{_CSS}</style></head><body>
<h1>Compatibilité E7 : {_badge(d['verdict'])}</h1>
<p class="meta">Image : <code>{escape(d['image'])}</code> · cœur : <code>{escape(d['core'])}</code><br>
Analyse statique de l'ELF (aucune exécution) ; SKIP = règle non évaluée.</p>
<h2>Règles</h2>
<table><tr><th>Verdict</th><th>Règle</th><th>Explication</th><th>Mesuré</th><th>Limite</th></tr>
{rows}
</table>
<h2>Occupation des régions</h2>
<p class="meta">Pour la MRAM / XIP, la taille de référence est le slot ATOC du cœur.</p>
<table><tr><th>Région</th><th>Utilisé (o)</th><th>dont pile/tas (o)</th><th>Taille (o)</th><th>Taux</th><th></th></tr>
{usage}
</table>
</body></html>
"""


def write(report: Any, out_dir: str | Path = "reports", name: str | None = None) -> tuple[Path, Path]:
    """Écrit ``<out_dir>/<nom>.check.json`` et ``.html`` ; ``nom`` = nom de l'ELF sans extension."""
    d = _d(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = name or Path(d["image"]).stem
    jp, hp = out / f"{stem}.check.json", out / f"{stem}.check.html"
    jp.write_text(to_json(d), encoding="utf-8", newline="\n")
    hp.write_text(to_html(d), encoding="utf-8", newline="\n")
    return jp, hp
