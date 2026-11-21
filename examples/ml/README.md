# Modèles ML d'exemple (agent 08)

| Fichier | Contenu |
|---|---|
| `ds_cnn_s_quantized.tflite` | DS-CNN Small int8 (keyword spotting, 12 mots, entrée MFCC `[1,490]` int8), 47 616 octets |
| `README_model.md` | fiche du modèle, copie de la fiche amont |
| `LICENSE_ML-zoo.txt` | licence Apache-2.0 du dépôt ML-zoo |

- **Source** : Arm ML-zoo, `models/keyword_spotting/ds_cnn_small/model_package_tf/model_archive/TFLite/tflite_int8/ds_cnn_s_quantized.tflite`
  (https://github.com/Arm-Examples/ML-zoo, anciennement `ARM-software/ML-zoo`). Le fichier est stocké en Git LFS : le
  télécharger par `https://media.githubusercontent.com/media/Arm-Examples/ML-zoo/master/<chemin>` (l'URL `raw` ne donne qu'un pointeur).
- **Licence** : Apache-2.0 (modèle et dépôt).
- **sha256** : `0f235d3602308653fcdb50d73462080dec73f21473b4856f05f307d0eb24ca1a`
- Ce modèle est celui utilisé par l'Arm/Alif ML embedded evaluation kit pour le keyword spotting.

```powershell
py -3.14 -m e7twin.cli npu examples/ml/ds_cnn_s_quantized.tflite     # quand la commande est branchée dans cli.py
py -3.14 -c "from e7twin import npu; raise SystemExit(npu.main('examples/ml/ds_cnn_s_quantized.tflite'))"
```

Le rapport JSON est écrit dans `reports/ds_cnn_s_quantized.npu.json`.
