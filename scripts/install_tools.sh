#!/usr/bin/env bash
# Installe dans tools/ (ignore par git) les outils epingles d'E7 Twin sous macOS et Linux.
# Equivalent POSIX de scripts/install_tools.ps1 (agent 02). Versions identiques.
#
# STATUT : NON EXECUTE (aucun reseau pendant sa redaction, aucun Mac disponible).
#   - Les URL/SHA-256 Windows de install_tools.ps1 sont verifies ; ceux de ce script ne le sont PAS.
#   - Chaque valeur non sourcee est marquee "# a confirmer". Une empreinte vide = telechargement accepte
#     avec avertissement ; la calculer au premier telechargement et la reporter ici.
#
# Usage :
#   bash scripts/install_tools.sh [--accept-pack-licenses] [--skip-gcc] [--skip-cmsis-toolbox]
#                                 [--skip-renode] [--skip-packs] [--skip-template]
#
# Licences : sans --accept-pack-licenses, les licences embarquees des packs sont extraites puis le script
# s'arrete (code 2) sans installer les packs. L'accord est celui de l'humain, jamais automatique.
set -euo pipefail

ACCEPT=0; SKIP_GCC=0; SKIP_CTB=0; SKIP_RENODE=0; SKIP_PACKS=0; SKIP_TEMPLATE=0
for a in "$@"; do
  case "$a" in
    --accept-pack-licenses) ACCEPT=1 ;;
    --skip-gcc) SKIP_GCC=1 ;;
    --skip-cmsis-toolbox) SKIP_CTB=1 ;;
    --skip-renode) SKIP_RENODE=1 ;;
    --skip-packs) SKIP_PACKS=1 ;;
    --skip-template) SKIP_TEMPLATE=1 ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) echo "option inconnue : $a" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${E7TWIN_TOOLS_DIR:-$REPO/tools}"
DL="$TOOLS/_dl"
mkdir -p "$TOOLS" "$DL"

step() { printf '==> %s\n' "$*"; }
ok()   { printf '    OK  %s\n' "$*"; }
warn() { printf '    !!  %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------------------------
# Plate-forme
# ---------------------------------------------------------------------------------------------
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS/$ARCH" in
  Darwin/arm64)            GCC_HOST="darwin-arm64";  CTB_HOST="darwin-arm64"; RENODE_HOST="macos-arm64" ;;
  Darwin/x86_64)           GCC_HOST="darwin-x86_64"; CTB_HOST="darwin-amd64"; RENODE_HOST="macos-x86_64" ;;
  Linux/x86_64)            GCC_HOST="x86_64";        CTB_HOST="linux-amd64";  RENODE_HOST="linux" ;;
  Linux/aarch64|Linux/arm64) GCC_HOST="aarch64";     CTB_HOST="linux-arm64";  RENODE_HOST="" ;;
  *) echo "plate-forme non geree : $OS/$ARCH" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------------------------------------
# Versions epinglees (identiques a install_tools.ps1 et toolchain.py)
# ---------------------------------------------------------------------------------------------
GCC_VERSION="14.3.rel1"
GCC_DIR="arm-gnu-toolchain-$GCC_VERSION"
# Nom d'archive Arm : arm-gnu-toolchain-<ver>-<hote>-arm-none-eabi.tar.xz (hote = darwin-arm64,
# darwin-x86_64, x86_64, aarch64).                                                  # a confirmer
GCC_URL="https://developer.arm.com/-/media/Files/downloads/gnu/$GCC_VERSION/binrel/arm-gnu-toolchain-$GCC_VERSION-$GCC_HOST-arm-none-eabi.tar.xz"
# Empreintes : fichier .sha256asc publie a cote de chaque archive par Arm.            # a confirmer
case "$GCC_HOST" in
  darwin-arm64)  GCC_SHA="" ;;   # a confirmer
  darwin-x86_64) GCC_SHA="" ;;   # a confirmer
  x86_64)        GCC_SHA="" ;;   # a confirmer
  aarch64)       GCC_SHA="" ;;   # a confirmer
esac

CTB_VERSION="2.12.0"
CTB_DIR="cmsis-toolbox-$CTB_VERSION"
# Noms d'assets : cmsis-toolbox-<os>-<arch>.tar.gz (windows-amd64.zip est verifie).  # a confirmer
CTB_URL="https://github.com/Open-CMSIS-Pack/cmsis-toolbox/releases/download/$CTB_VERSION/cmsis-toolbox-$CTB_HOST.tar.gz"
CTB_SHA=""                       # cmsis-toolbox-checksums.txt de la release            # a confirmer

RENODE_VERSION="1.17.0"
RENODE_DIR="renode-$RENODE_VERSION"
# Linux : archive portable par analogie avec renode-1.17.0.windows-portable.zip.       # a confirmer
# macOS : aucune URL sourcee (paquet .dmg / formule Homebrew ; presence de la 1.17.0 en arm64 natif non
# verifiee). Installer a la main puis : PATH, /Applications/Renode.app, ou E7TWIN_RENODE=<binaire>.
RENODE_URL=""
if [ "$OS" = "Linux" ] && [ -n "$RENODE_HOST" ]; then
  RENODE_URL="https://github.com/renode/renode/releases/download/v$RENODE_VERSION/renode-$RENODE_VERSION.linux-portable.tar.gz"  # a confirmer
fi
RENODE_SHA=""                                                                           # a confirmer

PACK_INDEX="https://www.keil.com/pack/index.pidx"
PACKS=("ARM::CMSIS@6.1.0" "ARM::CMSIS-Compiler@2.1.0" "AlifSemiconductor::Ensemble@2.2.1")
TEMPLATE_URL="https://github.com/alifsemi/alif_vscode-template"
TEMPLATE_COMMIT="ebbf1513edaeffad912407543fe09da3c7ec9700"
TEMPLATE_DIR="alif_vscode-template"

# ---------------------------------------------------------------------------------------------
sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# install_archive <nom> <dossier> <url> <sha> <fichier-a-verifier>
install_archive() {
  local name="$1" dir="$2" url="$3" sha="$4" check="$5"
  local dest="$TOOLS/$dir"
  if [ -f "$dest/$check" ]; then ok "$name deja installe ($dest)"; return; fi
  step "$name : telechargement"
  local file="$DL/$(basename "$url")"
  [ -f "$file" ] || curl -fL --retry 3 -o "$file" "$url"
  local got; got="$(sha256_of "$file")"
  if [ -z "$sha" ]; then
    warn "empreinte SHA-256 non epinglee pour $name (a confirmer) ; obtenue : $got"
  elif [ "$got" != "$sha" ]; then
    echo "SHA-256 invalide pour $name : attendu $sha, obtenu $got" >&2; rm -f "$file"; exit 1
  fi
  step "$name : extraction"
  rm -rf "$dest"; mkdir -p "$dest"
  tar -xf "$file" -C "$dest" --strip-components=1
  # macOS : les binaires telecharges portent la quarantaine Gatekeeper (a confirmer sur un Mac).
  if [ "$OS" = "Darwin" ]; then xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true; fi
  [ -f "$dest/$check" ] || { echo "$name : $check introuvable apres extraction (disposition de l'archive a confirmer)" >&2; exit 1; }
  ok "$name -> $dest"
}

[ "$SKIP_GCC" = 1 ] || install_archive "Arm GNU Toolchain $GCC_VERSION" "$GCC_DIR" "$GCC_URL" "$GCC_SHA" "bin/arm-none-eabi-gcc"
[ "$SKIP_CTB" = 1 ] || install_archive "CMSIS-Toolbox $CTB_VERSION" "$CTB_DIR" "$CTB_URL" "$CTB_SHA" "bin/cbuild"

if [ "$SKIP_RENODE" != 1 ]; then
  if [ -n "$RENODE_URL" ]; then
    install_archive "Renode $RENODE_VERSION" "$RENODE_DIR" "$RENODE_URL" "$RENODE_SHA" "renode"
  else
    warn "Renode : pas d'installation automatique sur $OS/$ARCH. Installer Renode $RENODE_VERSION a la main"
    warn "         (macOS : /Applications/Renode.app ou 'renode' dans le PATH ; ou E7TWIN_RENODE=<binaire>)."
  fi
fi

# --- template Alif (commit epingle)
if [ "$SKIP_TEMPLATE" != 1 ]; then
  step "Template Alif @ ${TEMPLATE_COMMIT:0:8}"
  T="$TOOLS/$TEMPLATE_DIR"
  [ -d "$T/.git" ] || git clone -q "$TEMPLATE_URL" "$T"
  if [ "$(git -C "$T" rev-parse HEAD)" != "$TEMPLATE_COMMIT" ]; then
    git -C "$T" fetch -q origin
    git -C "$T" -c advice.detachedHead=false checkout -q "$TEMPLATE_COMMIT"
  fi
  ok "template au commit epingle"
fi

# --- packs CMSIS (licences : accord humain obligatoire)
if [ "$SKIP_PACKS" != 1 ]; then
  step "Packs CMSIS"
  CPACKGET="$TOOLS/$CTB_DIR/bin/cpackget"
  [ -x "$CPACKGET" ] || { echo "cpackget introuvable ($CPACKGET) : installer CMSIS-Toolbox d'abord" >&2; exit 1; }
  PACK_ROOT="$TOOLS/packs"
  export CMSIS_PACK_ROOT="$PACK_ROOT"
  export CMSIS_COMPILER_ROOT="$TOOLS/$CTB_DIR/etc"
  export PATH="$TOOLS/$CTB_DIR/bin:$TOOLS/$GCC_DIR/bin:$PATH"
  if [ ! -e "$PACK_ROOT/pack.idx" ] && [ ! -d "$PACK_ROOT/.Web" ]; then
    "$CPACKGET" init "$PACK_INDEX" -R "$PACK_ROOT"
  fi
  NEED_LICENSE=0
  for p in "${PACKS[@]}"; do
    vendor="${p%%::*}"; rest="${p#*::}"; name="${rest%@*}"; ver="${rest#*@}"
    pdir="$PACK_ROOT/$vendor/$name/$ver"
    [ -d "$pdir" ] && { ok "$p deja installe"; continue; }
    if [ "$ACCEPT" = 1 ]; then
      echo "    cpackget add --agree-embedded-license $p"
      "$CPACKGET" add "$p" --agree-embedded-license -R "$PACK_ROOT"
      [ -d "$pdir" ] || { echo "cpackget add $p a echoue (pack absent de $pdir)" >&2; exit 1; }
    else
      NEED_LICENSE=1
      "$CPACKGET" add "$p" --extract-embedded-license -R "$PACK_ROOT" || true
    fi
  done
  if [ "$NEED_LICENSE" = 1 ]; then
    echo "Licences des packs non acceptees : lire les textes extraits (voir sortie ci-dessus), puis relancer avec --accept-pack-licenses." >&2
    exit 2
  fi
fi

step "Termine. Verifier : python3 -m e7twin.cli doctor"
