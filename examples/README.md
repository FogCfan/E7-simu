# Firmwares de référence (agent 07)

Solution csolution copiée et adaptée de `tools/alif_vscode-template` (commit `ebbf1513`) : mêmes packs
(`AlifSemiconductor::Ensemble@2.2.1`, `ARM::CMSIS@6.1.0`, `ARM::CMSIS-Compiler@2.1.0`), même board DevKit-E7
(même SoC que l'AppKit), mêmes layers et même `cdefault.yml`. Aucun lien vers `tools/`.

| Projet | Cœur | Attendu |
|---|---|---|
| `hello_hp` | HP | `check` sans FAIL ; UART4 : « Hello E7 HP » |
| `hello_he` | HE | `check` sans FAIL ; UART4 : « Hello E7 HE » |
| `helium_dot` | HP | produit scalaire int8 MVE = scalaire ; UART4 : « MVE OK » |
| `too_big` | HE | **`check` FAIL** : tableau de 2 Mo en DTCM. Compile parce que son `app_mem_regions.h` déclare volontairement une DTCM de 4 Mo. Ne jamais flasher. |
| `stack_overflow` | HP | récursion infinie ; fault attendu sur carte (voir `tests/test_examples.py` pour ce que Renode montre) |

```powershell
powershell -ExecutionPolicy Bypass -File examples\build_all.ps1            # les 5
powershell -ExecutionPolicy Bypass -File examples\build_all.ps1 -Only too_big
```

Sorties : `build/examples/<nom>.{elf,map,su}`. `-fstack-usage` est actif dans chaque projet. Le `.su` fusionne
ceux de l'application et du SDK : donner un dossier par projet à `check --su-dir`, car `build/examples/` les mélange.

`dual_core_mhu` viendra au cycle suivant.
