# NPU : estimation Ethos-U55 avec Vela (agent 08)

Question traitée : « mon modèle `.tflite` tient-il (arena SRAM, poids) et tourne-t-il assez vite sur les deux
Ethos-U55 de l'E7 ? ». Code : `e7twin/npu.py`. Tests : `tests/test_npu.py`. Exemple : `examples/ml/`.

## Limites (à lire d'abord)

- **Estimation statique, pas une exécution.** Vela applique un modèle de performance du bloc Ethos-U55 et de ses
  ports AXI. **Renode n'émule pas l'Ethos-U55** : rien de ce qui est ici n'a été exécuté ni mesuré sur la carte.
- Les paramètres d'interconnexion de l'E7 (latences, débit MRAM, profondeur de files AXI) **ne sont pas sourcés** :
  ce sont des hypothèses (liste ci-dessous, aussi dans chaque rapport JSON, champ `assumptions`).
- Seuls les opérateurs offloadés sont chronométrés. Les opérateurs restant sur CPU (via TFLM/CMSIS-NN) sont
  **listés** (`cpu_op_types`) mais leur temps n'est pas estimé.
- Aucune contention (A32, autre cœur, DMA) sur la SRAM ou la MRAM.
- L'arena mesurée est celle du modèle seul ; la pile, le tas et les données applicatives s'y ajoutent.

## Environnement

- `ethos-u-vela` 5.2.0 installé avec `py -3.14 -m pip install ethos-u-vela`. **Pas de wheel Python 3.14 pour ses
  dépendances au moment de l'essai** (numpy compilé depuis les sources, ~10 min la première fois), mais l'installation
  a abouti : aucun venv dédié n'a été nécessaire.
- Ordre de recherche de l'interpréteur Vela (`npu.vela_python()`) : variable `E7TWIN_VELA_PYTHON`, venv
  `tools/vela-venv` s'il existe, sinon l'interpréteur courant. Vela est toujours lancé en sous-processus
  (`python -I -m ethosu.vela …`, dossier de travail neutre), jamais importé : si un jour 3.14 pose problème, il suffit
  de créer `tools/vela-venv` avec un Python 3.12 portable et de `pip install ethos-u-vela` dedans.
- Appeler `python -m ethosu.vela` (et non `ethosu.vela.vela`, qui n'a pas de point d'entrée `__main__`).

## Configurations Vela

`npu.vela_ini()` génère le `vela.ini` à partir de `board/ensemble_e7.yaml` (copie de référence : `tests/fixtures/npu/vela_e7.ini`,
vérifiée par un test).

| | NPU HP | NPU HE |
|---|---|---|
| `--accelerator-config` | `ethos-u55-256` | `ethos-u55-128` |
| Cœur associé | M55-HP | M55-HE |
| `core_clock` (horloge NPU) | 400 MHz (`freq_hz` du HP ; ACLK 400 MHz **V**, E7_FAITS) | 160 MHz (`freq_hz` du HE) |
| Système | `E7_U55_256_MRAM` | `E7_U55_128_MRAM` |
| Mode mémoire | `E7_Shared_Sram` : poids en lecture seule (Axi1), arena + E/S en SRAM (Axi0) | idem |
| Optimisation | `--optimise Performance` | idem |

- **Port SRAM (Axi0)** : 64 bits (R), `clock_scale=1.0`, burst 32, latence lecture/écriture 32, 4 accès simultanés.
  Ce sont les valeurs de la config Vela `Ethos_U55_High_End_Embedded` (hypothèse, source : `vela.ini` fourni avec Vela 5.2.0).
- **Port poids MRAM (Axi1, `OnChipFlash`)** : `clock_scale=0.25` (≈ 0,8 Go/s à 400 MHz), burst 128, latence 64, 2 accès.
  **Hypothèse non sourcée**, à mesurer sur la carte. Variante poids en OSPI : `npu.run(model, weights="ospi")`
  (`OffChipFlash`, `clock_scale=0.0625`, latence 128 : hypothèse).
- Horloge HE : la page série donne 46 GOPS pour l'U55-128, ce qui correspond plutôt à ~180 MHz qu'à 160 MHz (R).
  On prend 160 MHz (fréquence du cœur dans le modèle de carte) tant que le HRM ne tranche pas.
- Budget SRAM d'un cœur (`sram_budget_bytes`) : plus grande région `sram` visible du cœur dans le modèle de carte
  (SRAM0, 4 Mo, V : mem_regions L57/L61). L'arena doit être contiguë, donc on ne somme pas les régions. Les TCM ne sont pas comptées.

Sources : `ethosu/config_files/Arm/vela.ini` (paquet `ethos-u-vela` 5.2.0), `docs/E7_FAITS.md`, `board/ensemble_e7.yaml`,
dépôt Vela https://gitlab.arm.com/artificial-intelligence/ethos-u/ethos-u-vela, flux de référence Alif
https://github.com/alifsemi/alif_ml-embedded-evaluation-kit (non lu : ses configs Vela pourraient affiner les
hypothèses ci-dessus).

## Utilisation

```
py -3.14 -c "from e7twin import npu; raise SystemExit(npu.main('examples/ml/ds_cnn_s_quantized.tflite'))"
```

`npu.main(model, npu=None, out_dir="reports") -> int` : **0** tout tient ; **1** l'arena dépasse la SRAM d'un cœur ;
**2** vela ou modèle absent, NPU inconnu, échec de Vela. Écrit `reports/<modèle>.npu.json`.
`npu.run(model, npu=None, weights="mram") -> NpuReport` (`to_dict()`), lève `NpuError`.

## Résultat sur l'exemple (DS-CNN Small int8, 2,66 M MACs)

| | U55-256 (HP, 400 MHz) | U55-128 (HE, 160 MHz) |
|---|---|---|
| Cycles NPU (total) | 53 660 | 86 457 |
| Temps estimé | 0,134 ms | 0,540 ms |
| Arena SRAM | 21,7 KiB | 21,7 KiB |
| Poids encodés (MRAM) | 23,6 KiB | 23,6 KiB |
| Opérateurs hors NPU | aucun | aucun |

Estimation, voir les limites ci-dessus.

## JSON : forme pour `GET /api/npu/{model}` (agents 05/06)

`{model}` = nom de fichier ou stem ; la route lit `reports/<stem>.npu.json` (ou appelle `npu.run`). Contenu :

```json
{
  "model": "ds_cnn_s_quantized.tflite", "model_bytes": 47616, "model_sha256": "…", "vela_version": "5.2.0",
  "weights_region": "mram", "ok": true, "fits": true,
  "results": {
    "u55_256": {
      "npu": "u55_256", "accelerator": "ethos-u55-256", "core": "m55_hp", "ok": true, "error": null,
      "clock_hz": 400000000, "cycles_total": 53660, "cycles_npu": 53614,
      "inference_time_s": 0.00013415, "inferences_per_s": 7454.3, "macs": 2664792,
      "sram_bytes": 22208, "sram_budget_bytes": 4194304, "fits": true,
      "weights_bytes": 24176, "weights_original_bytes": 22016, "weights_region": "mram",
      "npu_operators": 39, "cpu_operators": 0, "cpu_op_types": {}, "vela_warnings": []
    },
    "u55_128": { "…": "même forme, core m55_he" }
  },
  "assumptions": ["…"], "fidelity": "Estimation statique Vela …"
}
```

À afficher tel quel dans l'onglet NPU : `fidelity` en bandeau permanent, `assumptions` dans un détail dépliable,
`fits=false` en rouge. Un résultat peut avoir `ok=false` avec `error` renseigné (l'autre NPU reste exploitable).

## Reste à faire

- Brancher `check` : bloquer si `fits` est faux pour le cœur du firmware (pas encore relié : ne touche pas à `check.py`).
- Affiner le port MRAM/OSPI avec le HRM ou une mesure sur la carte.
- Détail par couche (`--verbose-performance`) pour le dashboard.
- Mode carte réelle (`board_link.py`, phase 6).
