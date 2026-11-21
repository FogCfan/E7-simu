# E7 Twin — feuille de route

Tenue par l'agent 00. Une phase est **terminée** quand : les tests passent, `docs/INTERFACES.md` est
à jour, la démo de la phase se rejoue en **une seule commande**, et cette page est mise à jour.

Légende d'état : ⬜ à faire · 🟨 en cours · ✅ terminé.

| # | Phase | Agents | Livrable | Démo en une commande | État |
|---|---|---|---|---|---|
| — | Socle du dépôt | 00 | `pyproject.toml`, CLI squelette, `INTERFACES.md`, cette roadmap | `e7twin --help` | ✅ (cycle 1) ; CLI branchée sur toutes les commandes (build, check, run, serve, package, npu, doctor) ; `npu` dépend de `npu.py` (08) |
| **0** | **Spike de risque Renode** (go/no-go) | 02, 04 (+01 pour le `.repl`) | Outils installés (`install_tools.ps1`), `hello` de `alif_vscode-template` compilé pour M55_HP/HE, exécuté dans Renode jusqu'au printf ; MVE et bi-CPU testés ; `docs/SPIKE_RENODE.md` avec verdict | `e7twin doctor` puis `py -3.14 e7twin/renode/run_spike.py` *(à confirmer par 04)* | ✅ **GO** (2026-11-21) : bare-metal et `hello` du SDK Alif affichent « Hello World! » sur UART4 dans Renode 1.17.0 |
| 1 | Modèle de carte, build, check | 01, 02, 03, 07 | `board/ensemble_e7.yaml` + `board.py` (`load`, `generate_repl`), `build.py`, `check.py` (règles 1, 2, 3, 5), fixtures ELF, `examples/hello_hp`, `examples/too_big` | `e7twin check build/spike/hello_hp.elf --core m55_hp` (code 0) ; à terme `e7twin check examples/too_big` (code ≠ 0) | ✅ board, build, check (règles 1 à 6, pile via `.su`), packaging des ELF du SDK ; exemples et fixtures (07) |
| 2 | Simulateur bi-cœur | 04, 01, 07 | `.repl` bi-M55 généré, stubs MHU/CLKCTL/PINMUX/power, chargeur façon ATOC, UART en socket, `sim.Session`, `docs/FIDELITE.md`, `examples/dual_core_mhu` | `e7twin run --hp hp.elf --he he.elf --headless --expect "PONG 100"` | ✅ stub MHU, `cpu_stats`, watermark de pile, détection de HardFault, fault sur accès non mappé (`unmapped_events`, `Fault.kind="unmapped"`), `sim.main` ; la faute sur accès non mappé est livrée |
| 3 | Métriques et serveur | 05 | `metrics.Sample`, `server.py` (REST + `/ws`), mode `--fake`, puis branchement sur `sim` (charge, watermark, MHU, profil) | `e7twin serve --fake` puis `curl http://127.0.0.1:8000/api/board` | ✅ serveur réel via `SimSource` (4 Hz, `close()`, `status()`, routes `/api/check`, `/api/uart`, 409 sur start/pause/reset) ; **profiler branché** (instructions par fonction, sans Perfetto) |
| 4 | Dashboard web | 06 | `web/` : vue carte, charge par cœur, mémoire par région, UART, timeline MHU/faults, rapport check, contrôles | `e7twin serve --fake` → http://127.0.0.1:8000 | ✅ dashboard `web/` (agent 06) sur le serveur factice et `SimSource` ; **vue NPU à venir** |
| 5 | NPU, packaging, CI | 08, 02, 07 | `npu.py` (Vela U55-256/128, `docs/NPU.md`), `package.py` (.bin + ATOC + commande SETOOLS affichée), workflow GitHub Actions | `e7twin package build/hello_hp.elf --core m55_hp` ; `e7twin npu examples/ml/kws.tflite` | 🟨 `npu.run`/`npu.main` ✅, `package` ✅ (branché CLI) ; CI `ci.yml` **écrite mais jamais exécutée** ; `check` ne bloque pas encore sur l'arena NPU |
| 6 | *(optionnel)* Mode carte réelle | 08, 05, 06 | `board_link.py` (pylink, RTT, lecture mémoire seule — **jamais de flash**), comparaison sim ↔ carte dans le même dashboard | `e7twin serve --board jlink` *(à définir)* | ⬜ non commencée (`board_link.py`, pylink, RTT, lecture seule) |

## Dépendances et ordre

1. 00 + 02 démarrent (socle, outils).
2. 04 (spike, **go/no-go**) + 01 (modèle de carte) — tout le reste de la simulation dépend du verdict.
3. 03 (check) et 05 (serveur factice) peuvent avancer sans Renode.
4. 06 (dashboard, sur `serve --fake`) et 07 (tests, CI).
5. 08 (NPU) en dernier, indépendant de Renode.

## Si le spike est « no-go »

Le check statique (phase 1), le packaging et le NPU restent utiles sans émulation. On réévaluera
alors : stubs plus profonds, ou exécution limitée (M55 seul sans init SDK complète). Décision à
consigner dans `docs/PLAN.md` § Décisions.

## Limites ouvertes (à la date de ce cycle)

- **MPU du SDK contourné** : `MPU_Setup()` est court-circuité et le MPU est armé par le loader ; une faute
  d'accès que le MPU du SDK aurait levée sur la carte n'est pas vue (voir `docs/FIDELITE.md`, ligne MPU).
- **Palier de boot d'environ 5 s** d'inactivité en temps réel : temps virtuel figé après le premier quantum de 40 000 instructions. Pistes écartées dans `docs/SPIKE_RENODE.md` (« Palier de boot ») ; cause **non trouvée**.
- **Profiler branché** (`CollapsedStack`, instructions par fonction) ; pas de trace Perfetto ; une activation tardive ne voit pas le boot.
- **WARN de pile sur les ELF du SDK** : maintenu, voulu. Il vient d'appels indirects réels (callbacks de pilotes, `exit` de newlib).
- **Vue NPU du dashboard** : présente (`web/`, alimentée par `/api/npu` et `/api/npu/{stem}`), **sans vérification visuelle dans un navigateur**.
- **Arena NPU dans `check` : opt-in** : `e7twin check <elf> --npu-report reports/<modèle>.npu.json`
  ajoute la règle `npu` (FAIL si le pic d'arena dépasse le budget SRAM du cœur, SKIP sans rapport ;
  rapport illisible = code 2). Sans `--npu-report`, `check` ne regarde pas le NPU.
- **Validation croisée sur la carte à faire par l'humain** : comparer la sortie UART de la simulation et
  de la carte réelle (`package` + SETOOLS, lancés à la main). Rien n'a été flashé par l'outil.
- **CI écrite, jamais exécutée** (`.github/workflows/ci.yml`).
