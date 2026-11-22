# E7 Twin — contrats entre modules

Ce fichier est tenu par l'agent 00 (Coordination). Un contrat ne change pas sans mise à jour ici
et sans prévenir les agents consommateurs. Ce qui est marqué *(précision 00)* complète un contrat
donné à un agent sans le contredire ; si l'implémentation diverge, on aligne ce fichier après revue.

## Conventions communes

- **Identifiants de cœur** : `a32_0`, `a32_1`, `m55_hp`, `m55_he`. Les commandes d'exécution ne
  ciblent que `m55_hp` et `m55_he` (A32 inertes en v1).
- **Adresses et tailles** : `int` en Python, en octets. En JSON, ce sont des **entiers** (32 bits,
  sans risque pour JavaScript) ; l'affichage hexadécimal relève du dashboard.
- **Fins de plage exclusives** : `end = base + size`.
- **Sérialisation** : tout objet échangé expose `to_dict()` qui renvoie uniquement des types JSON
  (dict, list, str, int, float, bool, None). Clés en `snake_case`.
- **Verdicts** : `"PASS" | "WARN" | "FAIL"`, ordre de gravité PASS < WARN < FAIL.
- **Aucune constante matérielle hors de `board/ensemble_e7.yaml`** : les autres modules passent par
  `board.load()`.

## `e7twin.board` (agent 01)

Contrat repris tel quel de la mission de l'agent 01.

```python
@dataclass(frozen=True)
class Region:
    name: str
    base: int
    size: int
    kind: Literal["tcm", "sram", "mram", "xip"]
    visible_to: tuple[str, ...]      # ids de cœurs qui voient la région à cette adresse
    local: bool                      # True = TCM locale (même adresse, contenu propre à chaque cœur)
    alias_of: str | None             # nom de la région dont celle-ci est l'alias global, sinon None
    @property
    def end(self) -> int: ...        # base + size (exclusive)

@dataclass(frozen=True)
class Core:
    id: Literal["a32_0", "a32_1", "m55_hp", "m55_he"]
    type: str                        # ex. "cortex-m55", "cortex-a32"
    freq_hz: int
    fpu: ...                         # forme choisie par l'agent 01 (ex. "dp" / None)
    mve: bool
    npu: str | None                  # nom de l'Ethos-U55 associé, sinon None
    executable: bool                 # False pour les A32 en v1

@dataclass(frozen=True)
class AtocSlot:
    core: str
    name: str
    base: int
    size: int
    kind: Literal["mram", "ospi"]

@dataclass(frozen=True)
class Peripheral:
    name: str
    type: str
    base: int
    size: int
    irq: dict[str, int]              # id de cœur -> n° d'IRQ NVIC sur ce cœur, ex. {"m55_hp": 128}
    # champs optionnels (livrés par l'agent 01, tous avec valeur par défaut) :
    visible_to: tuple[str, ...] = () # cœurs qui voient le bloc (() = non précisé)
    local: bool = False              # fenêtre privée du cœur (même adresse, bloc différent)
    attrs: dict[str, Any] = {}       # clés YAML hors schéma (link, role, reg_width…)
    source: str = ""                 # clé de la table `sources` du YAML

@dataclass
class Board:
    cores: ...                       # collection de Core
    memories: ...                    # collection de Region
    atoc_slots: ...                  # collection de AtocSlot
    peripherals: ...                 # collection de Peripheral
    # champs optionnels (défaut vide), issus des sections `layout`, `board` et `sources` du YAML :
    layout: dict[str, Any] = {}      # disposition du schéma « carte » (dashboard)
    info: dict[str, Any] = {}        # nom, kit, SoC…
    sources: dict[str, str] = {}     # clé -> URL des sources des faits matériels

    def core(self, id: str) -> Core: ...
    def regions_for(self, core_id: str) -> list[Region]: ...
    def region_at(self, addr: int, core_id: str) -> Region | None: ...
    def slot(self, core_id: str, kind: str = "mram") -> AtocSlot: ...
    def to_dict(self) -> dict: ...

def load(path: str | Path | None = None) -> Board: ...
def generate_repl(board: Board) -> str: ...
```

*(précisions 00)*
- `core(id)` lève `KeyError` pour un id inconnu ; `slot()` lève `KeyError` si le cœur n'a pas de
  slot de ce type. `region_at()` renvoie `None` hors de toute région visible (adresse non mappée
  pour ce cœur) ; c'est ce qu'utilisent check (règle placement) et sim (accès non mappé).
- `regions_for(core_id)` renvoie les régions dont `visible_to` contient `core_id`, triées par `base`.
  Une TCM locale apparaît pour son seul cœur ; son alias global (`alias_of` renseigné) pour tous.
- **Résolution de `load(path=None)`**, dans l'ordre :
  1. `path` s'il est fourni ;
  2. variable d'environnement `E7TWIN_BOARD` ;
  3. `<racine du dépôt>/board/ensemble_e7.yaml`, soit `Path(__file__).resolve().parents[1] / "board"`
     (cas du mode éditable `pip install -e`, le cas normal de l'équipe) ;
  4. ressource empaquetée `e7twin/_board/ensemble_e7.yaml` (copiée dans la wheel par
     `pyproject.toml`, `force-include`), via `importlib.resources`.
  `load()` lève `FileNotFoundError` (chemins essayés listés) si aucun fichier n'est trouvé, et
  `BoardError` (sous-classe de `ValueError`) si le YAML est invalide ou incohérent (champ
  manquant, source absente de `sources`, régions qui se chevauchent…).
- `generate_repl()` est pur (pas d'écriture disque) ; l'écriture de `e7twin/renode/e7.repl` est faite
  par l'appelant.

## `e7twin.check` (agent 03)

```python
@dataclass
class RuleResult:
    id: str                          # "arch" | "placement" | "usage" | "stack" | "boot" | "atoc"
    title: str
    verdict: Literal["PASS", "WARN", "FAIL", "SKIP"]   # SKIP : règle non évaluée (voir ci-dessous)
    message: str                     # explication lisible
    measured: int | float | str | None
    limit: int | float | str | None

@dataclass
class RegionUsage:                   # (précision 00)
    used_bytes: int                  # données + réserve pile/tas
    size_bytes: int                  # taille de la région ; pour mram et xip : taille du SLOT ATOC du cœur
    pct: float                       # used_bytes / size_bytes, 0..100+, WARN > 85, FAIL > 100
    reserved_bytes: int = 0          # dont réserve pile/tas (extension 03)
    # propriétés dérivées (sérialisées par to_dict) : data_bytes = used_bytes - reserved_bytes ;
    # data_pct = data_bytes / size_bytes. `pct` (avec réserve) est celui comparé à 85 % / 100 %.

@dataclass
class Report:
    image: str                       # chemin de l'ELF analysé
    core: str                        # "m55_hp" | "m55_he"
    verdict: Literal["PASS", "WARN", "FAIL"]   # pire verdict des règles ; SKIP est ignoré
    rules: list[RuleResult]
    regions_usage: dict[str, RegionUsage]      # clé = Region.name
    def to_dict(self) -> dict: ...

def run(elf_path: str | Path, core: str, atoc: str | Path | None = None,
        su_dir: str | Path | None = None, npu_report: str | None = None) -> Report: ...

def main(target: str | Path, core: str, atoc: str | Path | None = None,
         out_dir: str | Path = "reports", su_dir: str | Path | None = None,
         npu_report: str | None = None) -> int: ...   # point d'entrée CLI
```

- **`SKIP`** : verdict d'une règle non évaluée (ex. règles `stack` et `atoc` tant qu'elles ne sont pas
  implémentées, ou `atoc` sans `--atoc`). Il ne compte **pas** dans le verdict global : un rapport
  dont toutes les règles évaluées sont PASS reste PASS. Un SKIP est toujours affiché avec son motif.
- **`check.main`** : `target` est un ELF ou un dossier (l'ELF le plus récent est retenu). Écrit
  `<out_dir>/<nom>.check.json` et `.html`. Retourne **0** (PASS/WARN), **1** (au moins une règle en
  FAIL), **2** (erreur : cible introuvable, cœur invalide, ELF illisible).

- Correspondance avec les règles de la fiche 03 : 1 `arch`, 2 `placement`, 3 `usage`, 4 `stack`,
  5 `boot`, 6 `atoc`. Une règle non applicable ou non implémentée est en `SKIP` avec un message
  explicite, jamais omise en silence.
- **`su_dir`** : dossier des fichiers `.su` (`-fstack-usage`) pour la règle `stack` (calcul de pile
  pire cas, module `stackcheck.py`). Par défaut, les `.su` sont cherchés dans le dossier de build de
  l'ELF ; sans `.su`, la règle `stack` est en `SKIP` avec le motif.
- **Règle 7 `npu`** (arena NPU) : `npu_report` = `reports/<modèle>.npu.json` (`npu.main`). FAIL si le plus
  grand `results[*].sram_bytes` dépasse `npu.sram_budget_bytes(board, core)` ; PASS sinon ; SKIP sans rapport
  (motif affiché) ; rapport illisible ou sans résultat → code 2.
- CLI : `e7twin check <cible> --core m55_hp|m55_he --atoc <json> --su-dir <dossier> --npu-report <json>` → code 0 si PASS/WARN, 1 si FAIL.
  Rapport écrit dans `reports/<nom>.check.json` (+ `.html`).
- `regions_usage` alimente aussi `metrics.Sample.memory[*].static_bytes`.

## `e7twin.sim` (agent 04)

```python
class Session:
    def __init__(self, board: Board | None = None, *, renode: Path | None = None,
                 port: int | None = None) -> None: ...      # (précision 00) port monitor auto si None
    def __enter__(self) -> "Session": ...                   # lance renode --disable-gui -P <port>
    def __exit__(self, *exc) -> None: ...                   # arrête Renode proprement (close())

    def load_elf(self, core: str, path: str | Path) -> None: ...
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def reset(self) -> None: ...
    def read_mem(self, addr: int, size: int) -> bytes: ...  # via sysbus (adresse globale)
    def run_until(self, pattern: str, timeout: float) -> bool: ...  # True si le motif apparaît sur une UART
    def uart_lines(self) -> list[UartLine]: ...             # lignes reçues depuis le démarrage
    def close(self) -> None: ...

    # --- mesures (cycle 2, agent 04 -> agent 05) ---
    def read_mem(self, addr: int, size: int, core: str | None = None) -> bytes: ...  # core : vue locale (TCM)
    def cpu_stats(self, core: str) -> dict: ...             # {"instructions": int, "halted_or_wfi": bool, "pc": int}
    def virtual_time(self) -> float: ...                    # s depuis le dé-halt des cœurs
    def paint_stack(self, core: str) -> bool: ...           # 0xDEADBEEF de __StackLimit au SP initial (auto au chargement)
    def stack_watermark(self, core: str) -> int | None: ... # octets de pile utilisés ; None si non peinte
    def mhu_events(self) -> list["MhuEvent"]: ...           # messages MHU depuis le dernier appel (file vidée)
    def faults(self) -> list["Fault"]: ...                  # abort Renode, lockup, HardFault (CFSR/HFSR non nuls)
    def unmapped_events(self) -> list["UnmappedAccess"]: ...  # accès non mappés (journal Renode), file vidée

@dataclass
class UartLine:                      # (précision 00)
    t_virtual_s: float
    core: str
    uart: str                        # nom du périphérique, ex. "uart2"
    text: str

@dataclass
class MhuEvent:                      # source de Sample.events[kind="mhu"]
    t_virtual_s: float
    kind: str                        # "send" (CH_SET côté TX) | "ack" (CH_CLR côté RX)
    link: str                        # ex. "m55hp_m55he_0"
    src: str                         # id de cœur émetteur ("ack" : celui qui acquitte)
    dst: str                         # id de cœur destinataire, "none" si l'autre extrémité n'est pas simulée
    channel: int
    value: int

@dataclass
class Fault:                         # (sim.py, agent 04 ; `__str__` sert de clé de dédoublonnage)
    core: str
    kind: str                        # "hardfault" | "lockup" | "abort" | "unmapped"
    pc: int | None
    cfsr: int | None = None          # CFSR si hardfault, sinon None
    hfsr: int | None = None          # HFSR si hardfault, sinon None
    message: str = ""

@dataclass
class UnmappedAccess:                # (sim.py) accès bus à une adresse sans périphérique ni mémoire
    core: str
    pc: int | None
    access: str                      # "read" | "write"
    size: int                        # octets
    addr: int
    value: int | None                # valeur écrite, ou renvoyée à la lecture
    fault: bool                      # True si hors espaces périphériques/système : la carte lèverait un BusFault
```

- `faults()` renvoie `kind="unmapped"` pour les `UnmappedAccess` avec `fault=True` (accès hors de tout
  espace de périphériques ou système). Les accès non mappés de type périphérique (`fault=False`) restent
  dans `unmapped_events()` sans être une faute.
- `unmapped_events() -> list[UnmappedAccess]` : file vidée à chaque appel.

- Coût mesuré par appel (Renode 1.17, hello du SDK en cours) : `cpu_stats` ≈ 18 ms, `virtual_time` ≈ 4 ms,
  `faults` ≈ 12 ms (par cœur démarré), `mhu_events` < 1 ms, `stack_watermark` ≈ 180 ms (pile de 8 Ko). Cadence réaliste :
  10 Hz pour cpu_stats/virtual_time/mhu_events/faults des deux cœurs (≈ 70 ms par échantillon), watermark à ≤ 1 Hz.
  Les appels partagent un verrou (monitor unique) : à appeler depuis un seul thread d'échantillonnage.
- `timeout` est en secondes de **temps réel** (garde-fou CI) ; le temps virtuel est exposé à part.
- Erreurs de pilotage Renode : exception `sim.SimError`.
- CLI : `e7twin run <elf...> --hp <elf> --he <elf> --atoc <json> --headless --expect <texte> --timeout <s>`
  appelle le point d'entrée :

  ```python
  def main(elfs: list[str], hp: str | None, he: str | None, atoc: str | None,
           headless: bool, expect: str | None, timeout: float) -> int: ...
  ```

  Codes : **0** texte attendu trouvé, **1** non trouvé (timeout, fault, accès non mappé…),
  **2** problème d'environnement (Renode absent, ELF introuvable…).

## `e7twin.metrics` (agent 05) — `Sample`

Un échantillon diffusé ≈ 10 Hz. Forme JSON :

```json
{
  "t_virtual_s": 1.25,
  "cores": {
    "m55_hp": {"load_pct": 37.5, "mips": 150.0, "wfi_pct": 62.5},
    "m55_he": {"load_pct": 4.0,  "mips": 6.4,   "wfi_pct": 96.0}
  },
  "memory": {
    "dtcm_hp": {"static_bytes": 40960, "watermark_bytes": 52224, "size": 1048576}
  },
  "events": [
    {"t": 1.2031, "kind": "mhu", "core": "m55_hp", "detail": "doorbell ch0 -> m55_he"}
  ]
}
```

- `cores` : clés = ids de cœurs exécutables. `load_pct = 100 - wfi_pct` sur la fenêtre ;
  `mips` = instructions de la fenêtre / durée virtuelle / 1e6. Hypothèse **CPI = 1** (approximation,
  à afficher comme telle). Les A32 sont absents de `cores` en v1.
- `memory` : clés = `Region.name` du modèle de carte. `watermark_bytes` peut être `null` si non mesuré.
- `events[].kind` : `"mhu" | "fault" | "unmapped" | "uart"` ; `t` en secondes virtuelles ;
  `detail` : texte libre lisible.
- Côté Python : dataclass `metrics.Sample` avec `to_dict()` produisant exactement cette forme.

## WebSocket `WS /ws` (agent 05 → agent 06)

Chaque message est un objet JSON avec un champ `type` :

| `type` | Champs | Fréquence |
|---|---|---|
| `"sample"` | `data`: un `Sample` | ≈ 10 Hz |
| `"uart"` | `core`, `uart`, `t`, `text` (une ligne) | à chaque ligne |
| `"event"` | `data`: un élément de `Sample.events` | immédiat (dupliqué dans le sample suivant) |
| `"status"` | `state`: `"stopped" \| "running" \| "paused" \| "error"`, `fake`: bool, `message`? | à chaque changement + à la connexion |

Exemple : `{"type": "uart", "core": "m55_hp", "uart": "uart2", "t": 0.0123, "text": "Hello E7 HP"}`.
Le client n'envoie rien sur le WebSocket en v1 ; le pilotage passe par REST.

## REST (agent 05, fiche `docs/agents/05-metriques-serveur.md`)

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/api/board` | `Board.to_dict()` |
| GET | `/api/check` | `{"images": [{id, name, dir, path, core}, ...]}` : ELF des dossiers connus |
| GET | `/api/check/{image:path}` | `Report.to_dict()` ; 404 image inconnue, 400 cœur non déductible du nom, 422 ELF refusé |
| GET | `/api/npu` | `[{model, file, mtime}, ...]` : rapports `reports/*.npu.json` (`model` = stem, `mtime` = epoch s), du plus récent au plus ancien ; `[]` si aucun |
| GET | `/api/npu/{stem}` | contenu JSON de `reports/<stem>.npu.json` (`NpuReport.to_dict()`) ; 404 si absent ou `stem` hors `[A-Za-z0-9_.-]` / contenant `..` |
| GET | `/api/examples` | `[{name, cores: ["m55_hp"\|"m55_he"], description, elf, elfs: {core: chemin}, built}]` : noms/descriptions de `examples/README.md` (tableau ou `nom — description — cœur(s)`), sinon dossiers de `examples/` ; paires `x_hp`/`x_he` regroupées en `x` ; `elf` = 1er ELF de `build/examples/` (ou `null`), `built` = au moins un ELF |
| POST | `/api/upload?name=<f>.elf` | corps brut `application/octet-stream` (max 32 Mo) → `{id: "uploads/<nom>", name: "<nom>", elf: "build/uploads/<nom>.elf", core_guess: "m55_hp"\|"m55_he"\|null}` ; nom assaini ; **400** si pas ELF 32 bits LE machine ARM (40), trop gros ; `name` vide → nom unique `upload_<AAAAMMJJ_HHMMSS>_<4 hex>` (renvoyé dans `name`) |
| POST | `/api/build/source?core=m55_hp\|m55_he&name=<nom>` | corps brut = texte UTF-8 de `main.c` (max 1 Mo, pas de multipart) ; écrit `build/uploads/<nom>.c`, appelle `build.build_source` dans un thread (~3 s) → son résultat tel quel `{ok, elf, su_dir, log, errors: [{file, line, col, message}]}` + `name` (nom effectif ; sans `name` : `upload_<horodatage>_<4 hex>`) + `core_guess` si un ELF est produit ; **409** si une compilation de ce nom est déjà en cours ; **400** cœur invalide, corps vide / non UTF-8 / trop gros ; une erreur de compilation reste un 200 avec `ok: false` |
| POST | `/api/check/run` | corps `{elf, core, npu_report?}` (`elf` sous `build/`, `npu_report` = chemin sous `reports/`) → `Report.to_dict()` + `report_html` (chemin) + `report_url` (`/api/report/<nom>.check.html`) ; **400** chemin hors zone, **404** ELF/rapport/cœur inconnu, **422** ELF refusé |
| GET | `/api/report/{name}` | `reports/<nom>.check.html` (ou `.json`) ; 404 sinon |
| — | `POST /api/sim/load` | accepte aussi un chemin relatif au dépôt (ex. `build/uploads/x.elf`) |
| GET | `/api/uart?n=` | `{"n": n, "uarts": {nom: [lignes]}}` : historique par UART, `n` borné à 200 (`UART_HISTORY`) |
| GET | `/api/sim/status` | état complet de la source (forme de `status()` ci-dessous) |
| POST | `/api/sim/start` · `/api/sim/pause` · `/api/sim/reset` | **état complet** (même objet que `GET /api/sim/status`) ; 404 si action inconnue ; **409** si la source lève `RuntimeError` (ex. start sans image chargée, simulation en erreur qui exige un reset) |
| POST | `/api/sim/load` | corps `{"core": "m55_hp", "path": "..."}` → `{"ok": true}` ; **404** si le cœur est inconnu, **422** si l'image est refusée (ELF invalide, mauvais cœur) |
| POST | `/api/sim/speed` | JSON `{"accelerated": bool}` → état complet ; temps réel par défaut, accéléré = `Session.set_advance_immediately` (agent 04), temps virtuel ~35-40× plus vite pendant les WFI ; **409** si aucune simulation chargée ; `GET /api/sim/status` expose `accelerated`. Charge = Δinstructions / (fréquence × Δt **virtuel**) donc juste dans les deux modes ; files UART/événements de `SimSource` bornées à 2000, le WS diffuse au plus 200 lignes / 200 événements par tick (les plus récents) |
| POST | `/api/shutdown` | `{"ok": true}` puis arrêt du serveur ~0,5 s plus tard (exit 0, Renode arrêté) ; **403** hors loopback ; sans `uvicorn.Server` (tests) : ne fait rien d'autre que répondre |
| GET | `/api/display` | `{enabled, width, height, format, address}` (`enabled:false` + champs null si écran inactif) |
| GET | `/api/display.png` | image `image/png` (RGB565 → RGB, `Cache-Control: no-store`) ; **404** si écran inactif. `--fake` : mire animée 480×800 (`framebuffer.test_pattern`). `SimSource` : `Session.read_framebuffer()` (agent 04), cache 200 ms (≤ 5 captures/s) |
| GET | `/api/profile/{core}` | fonctions chaudes (+ lien vers la trace Perfetto) |
| WS | `/ws` | flux décrit ci-dessus |

Erreurs : code HTTP 4xx/5xx avec `{"detail": "..."}` (convention FastAPI). `e7twin serve --fake`
remplace la simulation par un générateur réaliste, avec les mêmes formats.

Point d'entrée CLI :

```python
def main(host: str = "127.0.0.1", port: int = 8000, fake: bool = True,
         hp: str | None = None, he: str | None = None) -> int: ...
```

`fake=False` → simulation Renode (`SimSource`), avec `hp`/`he` chargés au démarrage. Codes : 0 arrêt
normal, 2 erreur d'environnement ou d'image (port occupé, Renode absent, ELF refusé…).

### `MetricsSource` (agent 05) — delta livré

Protocole commun à `FakeSource` et `SimSource` (`metrics.py`, `simsource.py`) :

```python
class MetricsSource(Protocol):
    def sample(self) -> Sample: ...
    def uart_lines(self) -> list[dict]: ...   # {core, uart, t, text} apparues depuis l'appel précédent (file VIDÉE)
    def events(self) -> list[dict]: ...       # {t, kind, core, detail} apparus depuis l'appel précédent (file VIDÉE)
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def reset(self) -> None: ...
    def status(self) -> dict: ...
    def load(self, core: str, path: str) -> None: ...   # KeyError : cœur inconnu (→ 404) ; ValueError : image refusée (→ 422)
    def profile(self, core: str) -> dict: ...           # fonctions chaudes ; KeyError/ValueError si cœur inconnu/non exécutable
    def framebuffer(self) -> dict | None: ...           # {width,height,format:"RGB565",pitch,address,data:bytes} ou None (écran inactif)
    def set_speed(self, accelerated: bool) -> None: ...  # RuntimeError (-> 409) si aucune simulation chargée
```

Forme de `status()` (identique pour `GET /api/sim/status`, les POST `/api/sim/*` et les messages WS
`status`, où elle est précédée de `"type": "status"`) :

```json
{"state": "stopped|running|paused|error", "fake": true,
 "cores": {"m55_hp": "actif|sans image|stopped|paused", "a32_0": "non exécuté"},
 "images": {"m55_hp": "build/spike/hello_hp.elf"}, "t_virtual_s": 1.25,
 "poll_hz": 9.8, "message": "présent seulement en cas d'erreur ou d'info"}
```

`poll_hz` n'existe que pour `SimSource` (cadence réelle d'interrogation de Renode) ; `message` est
optionnel. Un `uart_lines()`/`events()` appelé deux fois de suite ne renvoie pas deux fois les mêmes éléments.

Valeurs de `status()["cores"][id]` (SimSource) : `"actif"` (simulation en cours), `"stopped"`, `"paused"`,
`"error"` (états de la simulation), `"sans image"` (cœur exécutable sans ELF chargé), `"non exécuté"` (A32).
`images` : `{core: chemin de l'ELF chargé}`. `t_virtual_s` : temps virtuel. `message` : présent seulement
en cas d'erreur ou d'information (ex. message d'erreur de la simulation).

### `SimSource` (`simsource.py`, agent 05)

Source réelle adossée à `sim.Session` (Renode). Un thread interroge Renode à **4 Hz** (`period=0.25 s`,
`slow_every=4` : watermark de pile et fautes tous les 4 polls). Si un poll dure plus longtemps que `period`, le
suivant part immédiatement ; la cadence réelle est publiée dans `status()["poll_hz"]`.
`close()` arrête le thread et ferme la session Renode (`Session.__exit__`).

**Profil (`profile(core)`, mode réel)** : `SimSource(profile=True)` active le profiler Renode dès chaque `load`
(sinon l'activation se fait au premier appel de `profile(core)`, et une activation tardive ne voit pas le boot).
Sortie : `{"fake": false, "core", "available": true, "functions": [{"name", "instructions", "pct"}], "trace_url": null}`,
top 20, instructions attribuées à la fonction feuille, `pct` sur le total. `trace_url` reste `null` (pas de trace Perfetto).
En cas d'échec : `available: false` avec un `message` (image absente, activation ou lecture impossible, aucune donnée).

`Session` (`sim.py`) : `enable_profiler(core) -> Path` (format CollapsedStack, fichier `prof_<cœur>.txt` dans le dossier de travail de la session),
`flush_profiler(core) -> Path | None` (vide le tampon dans le fichier ; `None` si non activé),
`disable_profiler(core) -> None`.

## `e7twin.toolchain` (agent 02) — utilisé par `e7twin doctor`

```python
def doctor() -> list[dict]:
    # chaque élément : {"name": str, "found": bool, "path": str | None,
    #                   "version": str | None, "hint": str, "required": bool (optionnel, défaut True)}
```

La CLI affiche un tableau et sort en **1** si un outil `required` manque. *(précision 00 : la clé
`required` est optionnelle, pour marquer un outil comme Vela non bloquant.)*

## `e7twin.build`, `e7twin.package`, `e7twin.npu` (agents 02 et 08)

Points d'entrée CLI (signatures lues dans le code livré) :

```python
# build.py (02)
def main(target: str, core: str | None = None, build_type: str = "debug", out_dir: str = "build") -> int: ...
# package.py (02)
def main(elf: str, core: str | None = None, out_dir: str = "build/package",
         atoc_with: list[str] | None = None) -> int: ...
# npu.py (08)
def run(model: str | Path, npu: str | None = None, weights: str = "mram",
        brd: Board | None = None) -> NpuReport: ...
def main(model: str, npu: str | None = None, out_dir: str = "reports") -> int: ...
```

- **`build.main`** : `target` = `.csolution.yml` (suffixe `#projet` si plusieurs projets), dossier de
  csolution/cproject ou dossier CMake ; `core` par défaut `m55_hp`. Codes : 0, 1 (échec de
  compilation), 2 (environnement absent / usage).
- **`package.main`** : convertit l'ELF en `.bin` (objcopy), écrit le JSON ATOC, lance `check` sur chaque
  image avec l'ATOC généré, puis **affiche** les commandes SETOOLS (`app-gen-toc`, `app-write-mram`).
  `atoc_with` : ELF d'autres cœurs à combiner dans le même JSON (cœur déduit des LMA). Si `check`
  rend FAIL ou erreur, les fichiers générés sont supprimés et le code est repris (1 ou 2).
  **N'exécute jamais SETOOLS** et ne flashe jamais.
- **`npu.run(model, npu, weights="mram") -> NpuReport`** : estimation Vela. `npu` ∈ `{"u55_256", "u55_128"}`
  ou `None` (les deux). `weights` ∈ `{"mram", "ospi"}` : emplacement des poids encodés (`vela_ini`).
  Lève `NpuError` si le modèle ou l'environnement Vela est en défaut.
- **`NpuReport`** : `model`, `model_bytes`, `model_sha256`, `results: dict[str, NpuResult]`, `vela_version`,
  `weights_region`, `assumptions`, `fidelity` ; propriétés `ok` (tous les résultats OK) et `fits`
  (tous les résultats OK tiennent dans leur SRAM).
- **`npu.main(model, npu=None, out_dir="reports") -> int`** : écrit `<out_dir>/<stem>.npu.json` **seulement si
  `ok`**. Codes : **0** tous les NPU demandés tiennent ; **1** l'arena dépasse la SRAM d'au moins un cœur
  (le JSON est écrit) ; **2** erreur (`NpuError`, ou au moins un résultat en erreur, JSON non écrit).
- Forme JSON (`docs/NPU.md`) :

  ```json
  {"model": "ds_cnn_s_quantized.tflite", "model_bytes": 47616, "model_sha256": "…", "vela_version": "5.2.0",
   "weights_region": "mram", "ok": true, "fits": true,
   "results": {"u55_256": {"npu": "u55_256", "accelerator": "ethos-u55-256", "core": "m55_hp", "ok": true,
                           "error": null, "clock_hz": 400000000, "cycles_total": 53660, "cycles_npu": 53614,
                           "inference_time_s": 0.00013415, "inferences_per_s": 7454.3, "macs": 2664792,
                           "sram_bytes": 22208, "sram_budget_bytes": 4194304, "fits": true,
                           "weights_bytes": 24176, "weights_original_bytes": 22016, "weights_region": "mram",
                           "npu_operators": 39, "cpu_operators": 0, "cpu_op_types": {}, "vela_warnings": []},
               "u55_128": {"…": "même forme, core m55_he"}},
   "assumptions": ["…"], "fidelity": "Estimation statique Vela …"}
  ```

## CLI (`e7twin/cli.py`, agent 00)

- Chaque commande importe son module **paresseusement** (`importlib`), donc `e7twin --help` marche
  même si un module manque ou est cassé.
- Codes de sortie : **0** succès · **1** échec métier (check FAIL, `--expect` absent, outil requis
  manquant) · **2** non implémenté / module absent (et erreurs d'usage typer/click).
- Branchement (cycle 3), avec import paresseux de `e7twin.<module>` puis de sa fonction `main` :

  | Commande | Appel |
  |---|---|
  | `build <cible> --core --build-type --out-dir` | `build.main(target, core, build_type, out_dir)` |
  | `check <cible> --core --atoc --su-dir --npu-report` | `check.main(target, core, atoc, su_dir=…, npu_report=…)` |
  | `run <elf…> --hp --he --atoc --headless --expect --timeout` | `sim.main(elfs, hp, he, atoc, headless, expect, timeout)` |
  | `serve --host --port --fake --hp --he` | `server.main(host=, port=, fake=, hp=, he=)` |
  | `package <elf> --core --out-dir --with <elf>…` | `package.main(elf, core, out_dir, atoc_with)` |
  | `npu <modèle> --npu u55_256\|u55_128 --out-dir` | `npu.main(model, npu, out_dir)` (`npu=None` = les deux) |
  | `doctor` | `toolchain.doctor()` |

- Mots-clés optionnels (`su_dir`, `hp`, `he`) : la CLI ne transmet que ceux que la fonction `main`
  accepte (introspection). Une option ignorée avec une vraie valeur est signalée sur stderr.
- Si le module ou `main` manque : « non implémenté » et code 2.
- `serve` : `--fake` est transmis tel quel (`fake=False` par défaut côté CLI = simulation Renode).
