#!/usr/bin/env bash
# Installe dans tools/ (ignore par git) les outils epingles d'E7 Twin sous macOS et Linux.
# Equivalent POSIX de scripts/install_tools.ps1 (agent 02). Versions identiques.
#
# STATUT (2026-11-27) :
#   - Execute sur macOS arm64 (Apple Silicon) : GCC, CMSIS-Toolbox et Renode (.dmg) s'installent, empreintes
#     verifiees, relance idempotente ; template au commit epingle ; packs sans --accept-pack-licenses
#     (licences extraites, code 2). L'ajout des packs avec --accept-pack-licenses n'a pas ete relance par
#     le script ; la meme commande cpackget a ete passee a la main sur ce Mac.
#   - URL et SHA-256 de toutes les plates-formes relevees aux sources officielles (voir chaque bloc). Sur Linux,
#     URL, empreintes et disposition de l'archive Renode sont relevees, mais le script n'y a pas tourne.
#   - Une empreinte vide = telechargement accepte avec avertissement.
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
  Darwin/arm64)            GCC_HOST="darwin-arm64";  CTB_HOST="darwin-arm64"; RENODE_HOST="osx-arm64" ;;
  Darwin/x86_64)           GCC_HOST="darwin-x86_64"; CTB_HOST="darwin-amd64"; RENODE_HOST="" ;;
  Linux/x86_64)            GCC_HOST="x86_64";        CTB_HOST="linux-amd64";  RENODE_HOST="linux" ;;
  Linux/aarch64|Linux/arm64) GCC_HOST="aarch64";     CTB_HOST="linux-arm64";  RENODE_HOST="linux-arm64" ;;
  *) echo "plate-forme non geree : $OS/$ARCH" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------------------------------------
# Versions epinglees (identiques a install_tools.ps1 et toolchain.py)
# ---------------------------------------------------------------------------------------------
GCC_VERSION="14.3.rel1"
GCC_DIR="arm-gnu-toolchain-$GCC_VERSION"
# Nom d'archive Arm : arm-gnu-toolchain-<ver>-<hote>-arm-none-eabi.tar.xz.
GCC_URL="https://developer.arm.com/-/media/Files/downloads/gnu/$GCC_VERSION/binrel/arm-gnu-toolchain-$GCC_VERSION-$GCC_HOST-arm-none-eabi.tar.xz"
# Empreintes : fichier .sha256asc publie par Arm a cote de chaque archive (releve le 2026-11-27).
case "$GCC_HOST" in
  darwin-arm64)  GCC_SHA="30f4d08b219190a37cded6aa796f4549504902c53cfc3c7e044a8490b6eba1f7" ;;
  x86_64)        GCC_SHA="8f6903f8ceb084d9227b9ef991490413014d991874a1e34074443c2a72b14dbd" ;;
  aarch64)       GCC_SHA="2d465847eb1d05f876270494f51034de9ace9abe87a4222d079f3360240184d3" ;;
  # Mac Intel : Arm ne publie pas darwin-x86_64 en 14.3.rel1 (404 le 2026-11-27 ; la 14.2.rel1 existe).
  darwin-x86_64) GCC_URL=""; GCC_SHA="" ;;
esac

CTB_VERSION="2.12.0"
CTB_DIR="cmsis-toolbox-$CTB_VERSION"
CTB_URL="https://github.com/Open-CMSIS-Pack/cmsis-toolbox/releases/download/$CTB_VERSION/cmsis-toolbox-$CTB_HOST.tar.gz"
# Empreintes : cmsis-toolbox-checksums.txt de la release.
case "$CTB_HOST" in
  darwin-arm64) CTB_SHA="a7ec124cd13266933ac52c2101df26749f6b1418fad114593a341e4ca1bcd562" ;;
  darwin-amd64) CTB_SHA="36e80e6535d9ba231a587353a0aa44e43aaa108e832be377b910c756e804cd5a" ;;
  linux-amd64)  CTB_SHA="ee3efa43eb4124c8029434ce59b6613d3a34570325addb9d5e354b0e3c420ba8" ;;
  linux-arm64)  CTB_SHA="d79fa1b1416b20cc412307c1169049dce42f8569166ac4f1253aceb417b149d1" ;;
esac

RENODE_VERSION="1.17.0"
RENODE_DIR="renode-$RENODE_VERSION"
# Assets portables de la release (empreintes : digest sha256 affiche par GitHub, releve le 2026-11-27).
#   macOS arm64 : .dmg contenant Renode.app (binaire Renode.app/Contents/MacOS/renode, runtime .NET embarque).
#   Linux       : .tar.gz, dossier renode_<ver>-portable/ avec le binaire renode a sa racine.
#   Mac Intel   : pas d'asset macOS x86_64 en 1.17.0 -> installation a la main (ou E7TWIN_RENODE).
RENODE_REL="https://github.com/renode/renode/releases/download/v$RENODE_VERSION"
RENODE_URL=""; RENODE_SHA=""
case "$RENODE_HOST" in
  osx-arm64)   RENODE_URL="$RENODE_REL/renode-$RENODE_VERSION.osx-arm64-portable.dmg"
               RENODE_SHA="63b1fb691207f503cea937e4ec8fad3e068a517d0abac3c24c0206c62f6c4d12" ;;
  linux)       RENODE_URL="$RENODE_REL/renode-$RENODE_VERSION.linux-portable.tar.gz"
               RENODE_SHA="4ba7c68b59e2447f188ef4b4b112fcccd2802582c460beedceb63b84e5605a5f" ;;
  linux-arm64) RENODE_URL="$RENODE_REL/renode-$RENODE_VERSION.linux-arm64-portable.tar.gz"
               RENODE_SHA="e9051578411441bfb47b77de0381cfba3badce03663367fab561472fd7807f1f" ;;
esac

PACK_INDEX="https://www.keil.com/pack/index.pidx"
PACKS=("ARM::CMSIS@6.1.0" "ARM::CMSIS-Compiler@2.1.0" "AlifSemiconductor::Ensemble@2.2.1")
TEMPLATE_URL="https://github.com/alifsemi/alif_vscode-template"
TEMPLATE_COMMIT="ebbf1513edaeffad912407543fe09da3c7ec9700"
TEMPLATE_DIR="alif_vscode-template"

# ---------------------------------------------------------------------------------------------
sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# fetch <nom> <url> <sha> : telecharge dans $DL et verifie l'empreinte ; affiche le chemin du fichier.
fetch() {
  local name="$1" url="$2" sha="$3"
  local file="$DL/$(basename "$url")"
  step "$name : telechargement" >&2
  [ -f "$file" ] || curl -fL --retry 3 -o "$file" "$url"
  local got; got="$(sha256_of "$file")"
  if [ -z "$sha" ]; then
    warn "empreinte SHA-256 non epinglee pour $name ; obtenue : $got"
  elif [ "$got" != "$sha" ]; then
    echo "SHA-256 invalide pour $name : attendu $sha, obtenu $got" >&2; rm -f "$file"; exit 1
  fi
  printf '%s\n' "$file"
}

# install_archive <nom> <dossier> <url> <sha> <fichier-a-verifier>
install_archive() {
  local name="$1" dir="$2" url="$3" sha="$4" check="$5"
  local dest="$TOOLS/$dir"
  if [ -f "$dest/$check" ]; then ok "$name deja installe ($dest)"; return; fi
  local file; file="$(fetch "$name" "$url" "$sha")"
  step "$name : extraction"
  rm -rf "$dest"; mkdir -p "$dest"
  tar -xf "$file" -C "$dest" --strip-components=1
  # macOS : curl ne pose pas l'attribut de quarantaine ; on le retire au cas ou l'archive viendrait d'un navigateur.
  if [ "$OS" = "Darwin" ]; then xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true; fi
  [ -f "$dest/$check" ] || { echo "$name : $check introuvable apres extraction" >&2; exit 1; }
  ok "$name -> $dest"
}

# install_dmg <nom> <dossier> <url> <sha> <fichier-a-verifier> : macOS, copie le contenu du .dmg dans tools/.
install_dmg() {
  local name="$1" dir="$2" url="$3" sha="$4" check="$5"
  local dest="$TOOLS/$dir"
  if [ -f "$dest/$check" ]; then ok "$name deja installe ($dest)"; return; fi
  local file; file="$(fetch "$name" "$url" "$sha")"
  step "$name : copie depuis l'image disque"
  local mnt; mnt="$(mktemp -d "${TMPDIR:-/tmp}/e7twin-dmg.XXXXXX")"
  hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "$file" >/dev/null
  rm -rf "$dest"; mkdir -p "$dest"
  cp -R "$mnt"/. "$dest"/ || { hdiutil detach "$mnt" >/dev/null; rmdir "$mnt"; exit 1; }
  hdiutil detach "$mnt" >/dev/null; rmdir "$mnt"
  xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true
  [ -f "$dest/$check" ] || { echo "$name : $check introuvable apres copie" >&2; exit 1; }
  ok "$name -> $dest"
}

if [ "$SKIP_GCC" != 1 ]; then
  if [ -n "$GCC_URL" ]; then
    install_archive "Arm GNU Toolchain $GCC_VERSION" "$GCC_DIR" "$GCC_URL" "$GCC_SHA" "bin/arm-none-eabi-gcc"
  else
    warn "Arm GNU Toolchain $GCC_VERSION : pas d'archive $GCC_HOST publiee par Arm. Installer une autre version"
    warn "         a la main (arm-none-eabi-gcc dans le PATH, ou sous tools/arm-gnu-toolchain-<ver>/), puis --skip-gcc."
  fi
fi
[ "$SKIP_CTB" = 1 ] || install_archive "CMSIS-Toolbox $CTB_VERSION" "$CTB_DIR" "$CTB_URL" "$CTB_SHA" "bin/cbuild"

if [ "$SKIP_RENODE" != 1 ]; then
  if [ "$RENODE_HOST" = "osx-arm64" ]; then
    install_dmg "Renode $RENODE_VERSION" "$RENODE_DIR" "$RENODE_URL" "$RENODE_SHA" "Renode.app/Contents/MacOS/renode"
  elif [ -n "$RENODE_URL" ]; then
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
